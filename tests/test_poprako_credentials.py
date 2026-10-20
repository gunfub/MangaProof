# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""Poprako 令牌存取单测（需求 §5.3、§12.5；设计文档 §13）。

不碰真实系统凭据库：用一个记录调用的假 keyring 模块替换
``mangaproof.poprako.credentials._keyring_module``。覆盖：

- keyring 可用 → 只写 keyring，settings.json 明文字段被清空；
- keyring 不可用（Android / 未安装 / 后端报错）→ 回退明文；
- 空值即清除、两处都删；
- 明文迁移成功/失败两条路径（失败必须保留明文，绝不禁用登录）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from mangaproof.poprako import credentials  # noqa: E402


class _FakeKeyring:
    """最小可用的 keyring 替身：记录调用、可注入失败。"""

    def __init__(self, *, fail: frozenset[str] = frozenset(), silent: bool = False):
        self.store: dict[tuple[str, str], str] = {}
        self.calls: list[tuple[str, str, str]] = []
        self._fail = fail
        self._silent = silent  # True = 写入被静默丢弃（get 读不回来）

    def set_password(self, service: str, username: str, value: str) -> None:
        if "set" in self._fail:
            raise RuntimeError("keyring set failed")
        self.calls.append(("set", service, username))
        if not self._silent:
            self.store[(service, username)] = value

    def get_password(self, service: str, username: str) -> str | None:
        if "get" in self._fail:
            raise RuntimeError("keyring get failed")
        self.calls.append(("get", service, username))
        return self.store.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        if "delete" in self._fail:
            raise RuntimeError("keyring delete failed")
        self.calls.append(("delete", service, username))
        self.store.pop((service, username), None)


class _Settings:
    """``PoprakoSettings`` 的轻量桩：本模块只要求一个可读写 ``token``。"""

    def __init__(self, token: str = ""):
        self.token = token


def _use(monkeypatch, module) -> None:
    monkeypatch.setattr(credentials, "_keyring_module", lambda: module)


# ---------------------------------------------------------------- keyring 可用


def test_save_token_goes_to_keyring_and_clears_plaintext(monkeypatch):
    fake = _FakeKeyring()
    _use(monkeypatch, fake)
    settings = _Settings()

    location = credentials.save_token(settings, "tok-1")

    assert location == credentials.STORE_KEYRING
    assert settings.token == "", "keyring 可用时明文必须清空"
    assert fake.store[(credentials.KEYRING_SERVICE, credentials.KEYRING_USERNAME)] == "tok-1"
    assert credentials.load_token(settings) == "tok-1"


def test_load_prefers_keyring_over_stale_plaintext(monkeypatch):
    fake = _FakeKeyring()
    _use(monkeypatch, fake)
    fake.store[(credentials.KEYRING_SERVICE, credentials.KEYRING_USERNAME)] = "from-keyring"
    settings = _Settings(token="stale-plain")

    assert credentials.load_token(settings) == "from-keyring"


def test_clear_token_removes_both_copies(monkeypatch):
    fake = _FakeKeyring()
    _use(monkeypatch, fake)
    settings = _Settings(token="plain")
    fake.store[(credentials.KEYRING_SERVICE, credentials.KEYRING_USERNAME)] = "plain"

    credentials.clear_token(settings)

    assert settings.token == ""
    assert (credentials.KEYRING_SERVICE, credentials.KEYRING_USERNAME) not in fake.store


def test_save_empty_value_clears_and_reports_nowhere(monkeypatch):
    fake = _FakeKeyring()
    _use(monkeypatch, fake)
    settings = _Settings(token="old")

    assert credentials.save_token(settings, "   ") == credentials.STORE_NOWHERE
    assert settings.token == ""


def test_save_strips_surrounding_whitespace(monkeypatch):
    fake = _FakeKeyring()
    _use(monkeypatch, fake)
    settings = _Settings()

    assert credentials.save_token(settings, "  tok-1  ") == credentials.STORE_KEYRING
    assert credentials.load_token(settings) == "tok-1"


# ---------------------------------------------------------------- keyring 不可用


def test_without_keyring_token_falls_back_to_plaintext(monkeypatch):
    _use(monkeypatch, None)
    settings = _Settings()

    location = credentials.save_token(settings, "tok-1")

    assert location == credentials.STORE_SETTINGS
    assert settings.token == "tok-1"
    assert credentials.load_token(settings) == "tok-1"


def test_keyring_write_failure_falls_back_to_plaintext(monkeypatch):
    fake = _FakeKeyring(fail=frozenset({"set"}))
    _use(monkeypatch, fake)
    settings = _Settings()

    location = credentials.save_token(settings, "tok-1")

    assert location == credentials.STORE_SETTINGS
    assert settings.token == "tok-1"


def test_keyring_silently_dropping_value_falls_back_to_plaintext(monkeypatch):
    """写进去但读不回来（某些后端的行为）也算不可用。"""
    fake = _FakeKeyring(silent=True)
    _use(monkeypatch, fake)
    settings = _Settings()

    assert credentials.save_token(settings, "tok-1") == credentials.STORE_SETTINGS
    assert settings.token == "tok-1"


# ---------------------------------------------------------------- 可用性探测


def test_keyring_available_probes_and_cleans_up(monkeypatch):
    fake = _FakeKeyring()
    _use(monkeypatch, fake)

    assert credentials.keyring_available() is True
    assert ("set", credentials.KEYRING_SERVICE, "__mangaproof_poprako_probe__") in fake.calls
    assert ("delete", credentials.KEYRING_SERVICE, "__mangaproof_poprako_probe__") in fake.calls
    assert not any("probe" in username for _service, username in fake.store)


def test_keyring_available_false_when_module_missing(monkeypatch):
    _use(monkeypatch, None)
    assert credentials.keyring_available() is False


def test_keyring_available_false_when_probe_raises(monkeypatch):
    _use(monkeypatch, _FakeKeyring(fail=frozenset({"set"})))
    assert credentials.keyring_available() is False


# ---------------------------------------------------------------- 明文迁移


def test_migrate_plaintext_moves_token_into_keyring(monkeypatch):
    fake = _FakeKeyring()
    _use(monkeypatch, fake)
    settings = _Settings(token="tok-plain")

    assert credentials.migrate_plaintext(settings) is True
    assert settings.token == ""
    assert fake.store[(credentials.KEYRING_SERVICE, credentials.KEYRING_USERNAME)] == "tok-plain"


def test_migrate_plaintext_noop_without_plaintext(monkeypatch):
    fake = _FakeKeyring()
    _use(monkeypatch, fake)

    assert credentials.migrate_plaintext(_Settings()) is False
    assert fake.calls == []


def test_migrate_plaintext_keeps_plaintext_when_keyring_missing(monkeypatch):
    _use(monkeypatch, None)
    settings = _Settings(token="tok-plain")

    assert credentials.migrate_plaintext(settings) is False
    assert settings.token == "tok-plain", "keyring 不可用时明文必须保留（登录不能被卡死）"


def test_migrate_plaintext_keeps_plaintext_when_write_is_lost(monkeypatch):
    _use(monkeypatch, _FakeKeyring(silent=True))
    settings = _Settings(token="tok-plain")

    assert credentials.migrate_plaintext(settings) is False
    assert settings.token == "tok-plain"


@pytest.mark.parametrize("fail", ["set", "get"])
def test_migrate_plaintext_survives_backend_errors(monkeypatch, fail: str):
    _use(monkeypatch, _FakeKeyring(fail=frozenset({fail})))
    settings = _Settings(token="tok-plain")

    assert credentials.migrate_plaintext(settings) is False
    assert settings.token == "tok-plain"

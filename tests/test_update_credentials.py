# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""更新凭证（MirrorChyan CDK / GitHub Token）的存取测试。

覆盖需求 §14/§15 的存储策略，以及 2026-10-20 追加的一条：

- 界面上把凭证**清空并保存**时，keyring 条目与 settings.json 明文都要删干净。
  只清明文不删 keyring 的话，下次打开更新页会把旧值读回来
  （"清掉了、重开又出现"）。

keyring 一律用内存假实现替换（``cdk_store._keyring_module``），
绝不碰开发机上真实的系统凭据库。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from mangaproof.config.settings import UpdateSettings  # noqa: E402
from mangaproof.update import cdk_store  # noqa: E402

SERVICE = cdk_store.KEYRING_SERVICE


class _FakeKeyring:
    """最小 keyring 假实现（行为对齐真实后端：删不存在的键会抛错）。"""

    def __init__(self, *, store: dict | None = None, fail_set: bool = False) -> None:
        self.store: dict[tuple[str, str], str] = dict(store or {})
        self.fail_set = fail_set

    def set_password(self, service: str, username: str, value: str) -> None:
        if self.fail_set:
            raise RuntimeError("keyring backend 不可写")
        self.store[(service, username)] = value

    def get_password(self, service: str, username: str) -> str | None:
        return self.store.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self.store:
            raise KeyError("no such entry")
        del self.store[(service, username)]


@pytest.fixture
def fake_keyring(monkeypatch):
    """装一个可用的假 keyring，并返回它（供断言条目变化）。"""
    module = _FakeKeyring()
    monkeypatch.setattr(cdk_store, "_keyring_module", lambda: module)
    return module


# --- 正常存取 ----------------------------------------------------------------

def test_save_cdk_goes_to_keyring_and_clears_plaintext(fake_keyring):
    draft = UpdateSettings(cdk="OLD")
    assert cdk_store.save_cdk(draft, "NEW-CDK") == cdk_store.STORED_IN_KEYRING
    assert fake_keyring.store[(SERVICE, cdk_store.KEYRING_USERNAME)] == "NEW-CDK"
    assert draft.cdk == "", "写进 keyring 后明文必须清空（需求 §14）"
    assert cdk_store.load_cdk(draft) == "NEW-CDK"


def test_save_github_token_uses_its_own_keyring_slot(fake_keyring):
    draft = UpdateSettings()
    assert cdk_store.save_github_token(draft, "ghp_1") == cdk_store.STORED_IN_KEYRING
    assert fake_keyring.store[(SERVICE, cdk_store.KEYRING_USERNAME_GITHUB_TOKEN)] == "ghp_1"
    assert draft.github_token == ""
    assert cdk_store.load_github_token(draft) == "ghp_1"


def test_credentials_do_not_shadow_each_other(fake_keyring):
    """CDK 与 Token 是两个独立条目：存一个不能把另一个挤掉。"""
    draft = UpdateSettings()
    cdk_store.save_cdk(draft, "CDK-1")
    cdk_store.save_github_token(draft, "ghp_1")
    assert cdk_store.load_cdk(draft) == "CDK-1"
    assert cdk_store.load_github_token(draft) == "ghp_1"


# --- 清空（2026-10-20 的核心约定） -------------------------------------------

def test_clearing_cdk_removes_keyring_entry_and_plaintext(fake_keyring):
    draft = UpdateSettings(cdk="PLAINTEXT")
    fake_keyring.store[(SERVICE, cdk_store.KEYRING_USERNAME)] = "OLD-IN-KEYRING"

    assert cdk_store.save_cdk(draft, "") == cdk_store.STORED_NOWHERE
    assert (SERVICE, cdk_store.KEYRING_USERNAME) not in fake_keyring.store, \
        "keyring 条目必须删掉，否则下次会被读回来"
    assert draft.cdk == "", "settings.json 明文也必须清掉"
    assert cdk_store.load_cdk(draft) == ""


def test_clearing_token_removes_keyring_entry_and_plaintext(fake_keyring):
    draft = UpdateSettings(github_token="PLAINTEXT")
    fake_keyring.store[(SERVICE, cdk_store.KEYRING_USERNAME_GITHUB_TOKEN)] = "OLD-IN-KEYRING"

    assert cdk_store.save_github_token(draft, "") == cdk_store.STORED_NOWHERE
    assert (SERVICE, cdk_store.KEYRING_USERNAME_GITHUB_TOKEN) not in fake_keyring.store
    assert draft.github_token == ""
    assert cdk_store.load_github_token(draft) == ""


def test_clearing_without_keyring_does_not_raise(monkeypatch):
    """keyring 根本不可用时，清空也必须安静成功（Android / 无桌面会话）。"""
    monkeypatch.setattr(cdk_store, "_keyring_module", lambda: None)
    draft = UpdateSettings(cdk="X", github_token="Y")
    assert cdk_store.save_cdk(draft, "") == cdk_store.STORED_NOWHERE
    assert cdk_store.save_github_token(draft, "") == cdk_store.STORED_NOWHERE
    assert draft.cdk == "" and draft.github_token == ""


def test_clear_helpers_are_equivalent(monkeypatch):
    monkeypatch.setattr(cdk_store, "_keyring_module", lambda: None)
    draft = UpdateSettings(cdk="X", github_token="Y")
    cdk_store.clear_cdk(draft)
    cdk_store.clear_github_token(draft)
    assert draft.cdk == "" and draft.github_token == ""


# --- 回退 --------------------------------------------------------------------

def test_without_keyring_values_land_in_settings(monkeypatch):
    monkeypatch.setattr(cdk_store, "_keyring_module", lambda: None)
    draft = UpdateSettings()
    assert cdk_store.save_github_token(draft, " ghp_x ") == cdk_store.STORED_IN_SETTINGS
    assert draft.github_token == "ghp_x", "两端空白要被 strip 掉"
    assert cdk_store.load_github_token(draft) == "ghp_x"


def test_keyring_write_failure_falls_back_and_drops_stale_entry(monkeypatch):
    """keyring 能读不能写时：写明文，并把旧条目删掉。

    否则 ``_load_secret`` 会继续读到 keyring 里的旧值，把这次刚写进
    settings.json 的新值盖掉 —— 表现为"保存了但没生效"。
    """
    module = _FakeKeyring(
        store={(SERVICE, cdk_store.KEYRING_USERNAME_GITHUB_TOKEN): "STALE"},
        fail_set=True,
    )
    monkeypatch.setattr(cdk_store, "_keyring_module", lambda: module)

    draft = UpdateSettings()
    assert cdk_store.save_github_token(draft, "ghp_new") == cdk_store.STORED_IN_SETTINGS
    assert draft.github_token == "ghp_new"
    assert (SERVICE, cdk_store.KEYRING_USERNAME_GITHUB_TOKEN) not in module.store
    assert cdk_store.load_github_token(draft) == "ghp_new"


def test_load_prefers_keyring_over_plaintext(fake_keyring):
    fake_keyring.store[(SERVICE, cdk_store.KEYRING_USERNAME_GITHUB_TOKEN)] = "FROM-KEYRING"
    assert cdk_store.load_github_token(UpdateSettings(github_token="FROM-FILE")) == "FROM-KEYRING"

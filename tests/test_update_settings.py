# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""自更新设置的读写单测（需求 §12、§13）。

这一层的两条硬要求：

- **默认值必须与需求 §12 完全一致**（stable / R2 / 无代理 / 不限速 / CDK 空）；
- **非法值一律回落默认**，且旧版 settings.json（没有 ``update`` 键）必须能直接读，
  不能因为加了新设置就让老用户的配置失效。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from mangaproof.config.settings import (  # noqa: E402
    DEFAULT_UPDATE_BRANCH,
    DEFAULT_UPDATE_CHANNEL,
    DEFAULT_UPDATE_PROXY,
    DEFAULT_UPDATE_SPEED_LIMIT,
    SPEED_LIMIT_CHOICES,
    UPDATE_CDK_KEY,
    UPDATE_GITHUB_TOKEN_KEY,
    SettingsManager,
    UpdateSettings,
)


def test_requirement_defaults():
    """需求 §12 的默认值逐项固定。"""
    s = UpdateSettings()
    assert s.branch == "stable" == DEFAULT_UPDATE_BRANCH
    assert s.channel == "r2" == DEFAULT_UPDATE_CHANNEL
    assert s.proxy == "" == DEFAULT_UPDATE_PROXY
    assert s.speed_limit == 0 == DEFAULT_UPDATE_SPEED_LIMIT
    assert s.cdk == ""


def test_speed_limit_choices_match_requirement():
    """需求 §30：不限速 / 10 / 20 / 30 / 40 / 50（MB/s）。"""
    assert SPEED_LIMIT_CHOICES == (0, 10, 20, 30, 40, 50)


def test_roundtrip_through_dict():
    s = UpdateSettings(
        branch="beta", channel="github", proxy="http://127.0.0.1:1080",
        speed_limit=20, cdk="SECRET",
    )
    raw = s.to_dict()
    assert raw["branch"] == "beta"
    assert raw[UPDATE_CDK_KEY] == "SECRET"
    assert UpdateSettings.from_dict(raw) == s


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {},
        "not-a-dict",
        [],
        {"branch": "nightly", "channel": "ftp", "speed_limit": 999},
        {"branch": 3, "channel": None, "proxy": 5, "speed_limit": "fast"},
    ],
)
def test_invalid_values_fall_back_to_defaults(raw):
    s = UpdateSettings.from_dict(raw)
    assert s.branch == DEFAULT_UPDATE_BRANCH
    assert s.channel == DEFAULT_UPDATE_CHANNEL
    assert s.speed_limit == DEFAULT_UPDATE_SPEED_LIMIT
    assert s.proxy == ""


def test_proxy_is_stripped():
    assert UpdateSettings.from_dict({"proxy": "  http://a:1  "}).proxy == "http://a:1"


def test_legacy_settings_without_update_key(tmp_path: Path):
    """旧版 settings.json（无 update 键）必须能读，且补上默认值。"""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"layer_display_ratio": 0.5}), encoding="utf-8")
    manager = SettingsManager(path)
    assert manager.settings.update == UpdateSettings()
    assert manager.settings.layer_display_ratio == 0.5


def test_save_writes_nested_update_object(tmp_path: Path):
    """需求 §13：更新设置存在 settings.json 的 "update" 对象里。"""
    path = tmp_path / "settings.json"
    manager = SettingsManager(path)
    manager.settings.update.branch = "alpha"
    manager.settings.update.speed_limit = 30
    manager.save()

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["update"]["branch"] == "alpha"
    assert raw["update"]["speed_limit"] == 30
    assert raw["update"]["channel"] == "r2"
    # 其它设置不受影响（扁平键仍在顶层）
    assert "layer_display_ratio" in raw


def test_saved_update_settings_are_reread(tmp_path: Path):
    path = tmp_path / "settings.json"
    m1 = SettingsManager(path)
    m1.settings.update.channel = "mirrorchyan"
    m1.settings.update.proxy = "socks5://127.0.0.1:1080"
    m1.save()

    m2 = SettingsManager(path)
    assert m2.settings.update.channel == "mirrorchyan"
    assert m2.settings.update.proxy == "socks5://127.0.0.1:1080"


def test_cdk_plaintext_is_only_present_when_set(tmp_path: Path):
    """CDK 明文默认不落盘（keyring 可用时由凭据库保存，需求 §14）。"""
    path = tmp_path / "settings.json"
    manager = SettingsManager(path)
    manager.save()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["update"][UPDATE_CDK_KEY] == ""


# --- GitHub Token（2026-10-20） ----------------------------------------------

def test_github_token_defaults_to_empty():
    """需求：新字段默认空 = 匿名请求，不影响老用户。"""
    assert UpdateSettings().github_token == ""
    assert UpdateSettings().to_dict()[UPDATE_GITHUB_TOKEN_KEY] == ""


def test_github_token_roundtrip_through_dict():
    s = UpdateSettings(github_token="ghp_abc")
    raw = s.to_dict()
    assert raw[UPDATE_GITHUB_TOKEN_KEY] == "ghp_abc"
    assert UpdateSettings.from_dict(raw) == s


def test_github_token_is_stripped_and_type_checked():
    assert UpdateSettings.from_dict({"github_token": "  ghp_x  "}).github_token == "ghp_x"
    # 非字符串（手改配置写成数字/布尔）一律当作"没填"，不做 str() 转换
    for bad in (123, True, None, ["ghp"], {"v": "ghp"}):
        assert UpdateSettings.from_dict({"github_token": bad}).github_token == ""


def test_legacy_settings_without_github_token_key(tmp_path: Path):
    """旧版 settings.json（update 里没有 github_token）必须能读，补默认空。"""
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"update": {"branch": "alpha", "channel": "github"}}), encoding="utf-8"
    )
    manager = SettingsManager(path)
    assert manager.settings.update.github_token == ""
    assert manager.settings.update.channel == "github"


def test_github_token_plaintext_saved_only_when_set(tmp_path: Path):
    path = tmp_path / "settings.json"
    manager = SettingsManager(path)
    manager.settings.update.github_token = "ghp_saved"
    manager.save()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["update"][UPDATE_GITHUB_TOKEN_KEY] == "ghp_saved"

    manager.settings.update.github_token = ""
    manager.save()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["update"][UPDATE_GITHUB_TOKEN_KEY] == "", "清空后配置里不该留痕"

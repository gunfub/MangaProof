# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""启动握手与异常中断恢复单测（需求 §57、§58、§60、§64）。

关键覆盖点：

- 安装器传来的 ``--update-*`` 参数必须被主程序摘掉（Qt 不认识它们）；
- 成功标记的内容必须带 ``token`` 与 ``version``（只判"文件存在"不幂等）；
- 断电/被杀后重启：marker 版本 == 当前版本 → 说明更新其实成功了，由新版收尾；
- 有 ``.old`` 残留但没有成功标记 → **只提示，不擅自删**（它可能是用户唯一能跑的副本）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from mangaproof.update import platform_dirs, startup  # noqa: E402


@pytest.fixture(autouse=True)
def temp_cache(monkeypatch, tmp_path):
    """把临时/CACHE 根指到 tmp_path，避免污染真实 ~/.cache。"""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))


def test_parse_handshake_extracts_and_leaves_rest():
    argv = [
        "MangaProof",
        "--update-token", "abc123",
        "--success-marker", "/tmp/success.marker",
        "--update-version", "1.1.0.alpha",
        "-platform", "offscreen",
    ]
    handshake, rest = startup.parse_handshake(argv)
    assert handshake.active
    assert handshake.token == "abc123"
    assert handshake.marker_path == Path("/tmp/success.marker")
    assert handshake.version == "1.1.0.alpha"
    assert rest == ["MangaProof", "-platform", "offscreen"], "其余参数要留给 Qt"


def test_parse_handshake_without_update_args_is_inactive():
    handshake, rest = startup.parse_handshake(["MangaProof"])
    assert not handshake.active
    assert rest == ["MangaProof"]


def test_mark_success_writes_token_and_version(tmp_path, monkeypatch):
    """需求 §57/§58：新版主动写标记，内容含 token + version + pid。"""
    marker = tmp_path / "success.marker"
    handshake = startup.UpdateHandshake(
        token="tok-1", marker_path=marker, version="1.1.0.alpha"
    )
    assert startup.mark_update_launch_success(handshake) is True

    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["token"] == "tok-1"
    assert payload["version"] == "1.1.0.alpha"
    assert isinstance(payload["pid"], int) and payload["pid"] > 0
    assert payload["ts"] > 0
    assert not marker.with_suffix(".tmp").exists(), "临时文件必须已被原子替换掉"


def test_mark_success_inactive_is_noop(tmp_path):
    assert startup.mark_update_launch_success(startup.UpdateHandshake()) is False


def test_mark_success_failure_does_not_raise(tmp_path):
    """写标记失败不能让新版起不来（用户至少还能用新版程序）。"""
    handshake = startup.UpdateHandshake(
        token="t", marker_path=tmp_path / "no" / "such" / "dir" / "m.json",
        version="1.1.0.alpha",
    )
    # 父目录不存在时会被 mkdir 出来，因此这里造一个"父路径是文件"的死局
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    handshake = startup.UpdateHandshake(
        token="t", marker_path=blocker / "m.json", version="1.1.0.alpha"
    )
    assert startup.mark_update_launch_success(handshake) is False


def test_recovery_detects_successful_but_uncleaned_update(monkeypatch, tmp_path):
    """marker 版本 == 当前版本 → 上次更新成功，只是没清理：由新版收尾。"""
    from mangaproof import __version__

    old_dir = tmp_path / "MangaProof.old"
    old_dir.mkdir()
    monkeypatch.setattr(startup, "_old_dir_candidates", lambda: [old_dir])

    marker = platform_dirs.success_marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"token": "t", "version": __version__}), encoding="utf-8")
    (platform_dirs.state_file_path()).write_text(
        json.dumps({"phase": "WAIT_SUCCESS"}), encoding="utf-8"
    )

    report = startup.detect_interrupted_update()
    assert report.finished_marker is True
    assert not report.needs_attention
    assert not old_dir.exists(), "成功收尾时必须删掉 .old"
    assert str(old_dir) in report.cleaned
    assert not platform_dirs.update_package_dir(create=False).exists()


def test_recovery_flags_interrupted_update_without_deleting(monkeypatch, tmp_path):
    """有 .old 残留但没有成功标记 → 提示用户，**不删**旧版本。"""
    old_dir = tmp_path / "MangaProof.old"
    old_dir.mkdir()
    (old_dir / "MangaProof").write_text("old binary", encoding="utf-8")
    monkeypatch.setattr(startup, "_old_dir_candidates", lambda: [old_dir])
    backup = tmp_path / "MangaProof-update-data"
    backup.mkdir()
    monkeypatch.setattr(startup.platform_dirs, "data_backup_dir", lambda **_kw: backup)

    report = startup.detect_interrupted_update()
    assert report.had_old_dir and report.old_dir == old_dir
    assert report.needs_attention is True
    assert old_dir.exists(), "绝不能擅自删除 .old —— 它可能是唯一能跑的副本"

    message = startup.recovery_message(report)
    assert "上一次更新没有正常完成" in message
    assert str(old_dir) in message
    assert "更新临时目录的备份里" in message, "备份还在时照旧告诉用户去哪找"


def test_recovery_message_notices_a_cleared_backup(tmp_path, monkeypatch):
    """「清理升级缓存」删掉备份之后，提示不许再说"备份在更新临时目录里"。"""
    old_dir = tmp_path / "MangaProof.old"
    old_dir.mkdir()
    monkeypatch.setattr(startup, "_old_dir_candidates", lambda: [old_dir])
    monkeypatch.setattr(
        startup.platform_dirs, "data_backup_dir",
        lambda **_kw: tmp_path / "不存在的备份目录",
    )

    message = startup.recovery_message(startup.detect_interrupted_update())

    assert "数据备份已不存在" in message
    assert "更新临时目录的备份里" not in message


def test_recovery_ignores_marker_of_another_version(monkeypatch, tmp_path):
    """别的版本的 marker 不能当作"本次成功"（否则会误删 .old）。"""
    old_dir = tmp_path / "MangaProof.old"
    old_dir.mkdir()
    monkeypatch.setattr(startup, "_old_dir_candidates", lambda: [old_dir])

    marker = platform_dirs.success_marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"token": "t", "version": "0.0.1"}), encoding="utf-8")

    report = startup.detect_interrupted_update()
    assert report.finished_marker is False
    assert report.needs_attention is True
    assert old_dir.exists()


def test_recovery_reads_installer_stage(monkeypatch, tmp_path):
    old_dir = tmp_path / "MangaProof.old"
    old_dir.mkdir()
    monkeypatch.setattr(startup, "_old_dir_candidates", lambda: [old_dir])
    platform_dirs.update_package_dir()
    platform_dirs.state_file_path().write_text(
        json.dumps({"phase": "EXTRACT"}), encoding="utf-8"
    )
    report = startup.detect_interrupted_update()
    assert report.stage == "EXTRACT"


def test_clean_state_reports_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(startup, "_old_dir_candidates", lambda: [tmp_path / "nope.old"])
    report = startup.detect_interrupted_update()
    assert report.needs_attention is False
    assert startup.recovery_message(report) == ""

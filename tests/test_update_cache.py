# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""升级缓存清理的单测（需求 §36/§37；**主程序侧**，与安装器无关）。

覆盖需求方 2026-09-24 指定的语义：

- 只清两个固定目录（更新包目录 + 安装器副本目录），**数据备份子目录一并删**；
- 删完**重建**空目录；目录本来不存在 → 跳过删除但仍然建回来；
- 防护：名字/位置不符、软链接一律拒绝删除；绝不递归删临时根目录；
- 删除失败如实报告、不影响另一个目录，也不抛异常；
- 确认文案必须说明清空哪两个目录，以及"正常更新时它们会被自动清理、
  升级失败时才值得手动清"。

不碰真实临时目录：`platform_dirs.temp_root` 被换成 tmp_path 下的沙箱。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from mangaproof.update import cache, platform_dirs  # noqa: E402


@pytest.fixture
def sandbox(monkeypatch, tmp_path) -> Path:
    """把"更新临时目录的根"换到 tmp_path 下（三个平台一致，不写真实 TEMP/CACHE）。"""
    root = tmp_path / "cache-root"
    root.mkdir()
    monkeypatch.setattr(platform_dirs, "temp_root", lambda: root)
    return root


def make_dirs(root: Path, *, with_files: bool = True) -> tuple[Path, Path]:
    package = root / platform_dirs.PACKAGE_DIRNAME
    installer = root / platform_dirs.INSTALLER_DIRNAME
    package.mkdir(parents=True, exist_ok=True)
    installer.mkdir(parents=True, exist_ok=True)
    if with_files:
        (package / "MangaProof-1.1.10.alpha-linux-x64.tar.gz").write_bytes(b"x" * 2048)
        backup = package / platform_dirs.DATA_BACKUP_DIRNAME
        backup.mkdir(exist_ok=True)
        (backup / "settings.json").write_text("{}", encoding="utf-8")
        (installer / platform_dirs.installer_filename()).write_bytes(b"y" * 512)
    return package, installer


# --- 主路径 ------------------------------------------------------------------


def test_clears_both_dirs_and_recreates_them(sandbox: Path):
    package, installer = make_dirs(sandbox)

    report = cache.clear_update_cache()

    assert report.ok, report.summary()
    assert [item.action for item in report.results] == [
        cache.ACTION_REMOVED, cache.ACTION_REMOVED,
    ]
    # 数据备份子目录也一并删掉（需求方 2026-09-24 决策：它只是升级时的临时备份）
    assert report.freed_bytes == 2048 + len("{}") + 512
    assert package.is_dir() and not any(package.iterdir()), "必须重建为空目录"
    assert installer.is_dir() and not any(installer.iterdir())
    assert "已删除并重建" in report.summary()
    assert platform_dirs.DATA_BACKUP_DIRNAME not in report.summary()


def test_missing_dirs_are_skipped_but_recreated(sandbox: Path):
    """目录不存在 → 跳过删除（不算失败），仍然建回来。"""
    report = cache.clear_update_cache()

    assert report.ok
    assert [item.action for item in report.results] == [
        cache.ACTION_MISSING, cache.ACTION_MISSING,
    ]
    assert report.freed_bytes == 0
    assert (sandbox / platform_dirs.PACKAGE_DIRNAME).is_dir()
    assert (sandbox / platform_dirs.INSTALLER_DIRNAME).is_dir()
    assert "无需清理" in report.summary()
    assert "原本不存在" in report.summary()


def test_cancel_between_dirs_stops_early(sandbox: Path):
    """取消请求在两个目录之间生效：已处理的不回滚，剩余目录标为已取消。"""
    package, installer = make_dirs(sandbox)

    report = cache.clear_update_cache(cancel=lambda: True)

    assert [item.action for item in report.results] == [
        cache.ACTION_SKIPPED, cache.ACTION_SKIPPED,
    ]
    assert package.is_dir() and installer.is_dir(), "取消时不许动目录内容"
    assert (package / "MangaProof-1.1.10.alpha-linux-x64.tar.gz").is_file()
    assert "已取消" in report.summary()


# --- 防护 --------------------------------------------------------------------


def test_guard_refuses_paths_outside_the_temp_root(sandbox: Path, tmp_path: Path):
    """名字或位置不符 → 拒绝删除（只记录），任何文件都不动。"""
    outsider = tmp_path / "important"
    outsider.mkdir()
    keep = outsider / "keep.txt"
    keep.write_text("别删我", encoding="utf-8")

    wrong_name = cache._clear_one("MangaProof-update-package", outsider)
    assert wrong_name.action == cache.ACTION_SKIPPED
    assert "不在更新临时目录内" in wrong_name.detail
    assert keep.is_file()

    # 名字对但不在 root 之下：同样拒绝
    nested = sandbox / "sub" / platform_dirs.PACKAGE_DIRNAME
    nested.mkdir(parents=True)
    (nested / "keep.txt").write_text("x", encoding="utf-8")
    result = cache._clear_one(platform_dirs.PACKAGE_DIRNAME, nested)
    assert result.action == cache.ACTION_SKIPPED
    assert (nested / "keep.txt").is_file()


def test_guard_never_touches_the_temp_root_itself(sandbox: Path):
    (sandbox / "other-file.txt").write_text("x", encoding="utf-8")

    report = cache.clear_update_cache()

    assert sandbox.is_dir()
    assert (sandbox / "other-file.txt").is_file(), "绝不递归删临时根目录"
    assert report.ok


def test_guard_skips_symlinks_without_following(sandbox: Path, tmp_path: Path):
    """软链接 → 跳过，绝不顺着链接去删别处的文件。"""
    outside = tmp_path / "user-data"
    outside.mkdir()
    precious = outside / "settings.json"
    precious.write_text("{}", encoding="utf-8")
    link = sandbox / platform_dirs.PACKAGE_DIRNAME
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):      # pragma: no cover - Windows 无权限时
        pytest.skip("本机不支持创建软链接")

    report = cache.clear_update_cache()

    package_result = report.results[0]
    assert package_result.action == cache.ACTION_SKIPPED
    assert "符号链接" in package_result.detail
    assert precious.is_file(), "链接目标必须原封不动"
    assert link.is_symlink()


# --- 失败路径 ----------------------------------------------------------------


def test_delete_failure_is_reported_and_other_dir_still_cleared(sandbox, monkeypatch):
    """一个目录删不掉：如实报告且不影响另一个目录，也不抛异常。"""
    package, installer = make_dirs(sandbox)
    real_rmtree = shutil.rmtree

    def flaky_rmtree(path, *args, **kwargs):
        if Path(path).name == platform_dirs.PACKAGE_DIRNAME:
            raise OSError(13, "Permission denied")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(cache.shutil, "rmtree", flaky_rmtree)

    report = cache.clear_update_cache()

    assert not report.ok
    assert report.results[0].action == cache.ACTION_FAILED
    assert "Permission denied" in report.results[0].detail
    assert package.is_dir() and (package / "MangaProof-1.1.10.alpha-linux-x64.tar.gz").is_file()
    assert report.results[1].action == cache.ACTION_REMOVED
    assert installer.is_dir() and not any(installer.iterdir())
    assert "删除失败" in report.summary()


def test_failed_report_marks_both_dirs_failed(sandbox: Path):
    report = cache.failed_report("磁盘炸了")

    assert not report.ok
    assert all(item.action == cache.ACTION_FAILED for item in report.results)
    assert "磁盘炸了" in report.summary()


# --- 确认文案 ----------------------------------------------------------------


def test_confirm_text_lists_dirs_and_explains_lifecycle(sandbox: Path):
    """弹窗必须说清：清空哪两个目录 + 正常更新时的清理行为 + 何时值得手动清。"""
    text = cache.confirm_text()

    assert str(sandbox / platform_dirs.PACKAGE_DIRNAME) in text
    assert str(sandbox / platform_dirs.INSTALLER_DIRNAME) in text
    assert "自动删除已下载的更新包" in text
    assert "Windows 上无法自行删除" in text and "覆盖" in text
    assert "Linux / macOS" in text and "自行删除" in text
    assert "升级失败" in text and "重新检查更新" in text


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))

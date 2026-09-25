# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""安装器状态机端到端单测（需求 §53~§64/§78）。

覆盖任务要求的四条主线：

- **成功路径**：``.old`` 生成 → 新目录就位 → 用户数据恢复 → 新版写 marker → 清理；
- **失败回滚**：新版"启动即退出且无 marker" → 旧目录恢复、旧数据完整；
- **校验拦截**：SHA-256 不符必须拒绝且**不触碰** ``--install-dir``；
- **marker 校验**：token 或 version 不匹配都不算成功。

这里的"新版主程序"是 POSIX sh 脚本（按跨进程契约解析参数并写 marker），
真的被 ``subprocess`` 拉起；等待/超时通过 ``Runtime`` 注入成小数值，不空等 60 秒。
测试不启动 GUI、不真的提权。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from updater import archive, rollback
from updater import state as state_mod
from updater.installer import ExitCode, Installer, InstallerOptions, Runtime

import test_updater_support as sup  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="假新版程序是 POSIX sh 脚本（真实流程在 CI 的 Windows 上人工验证）"
)

#: 全部等待都压到亚秒级；relocate_timeout 是"pid 消失后按名字找主程序"的宽限期，
#: 正常路径（test_updater_launcher.py 的 macOS 型场景）由用例自己调大。
FAST = dict(
    success_timeout=8.0, poll_interval=0.05, parent_timeout=1.0, retry_delay=0.05,
    relocate_timeout=0.5, lookup_interval=0.05,
)


def build_env(tmp_path: Path, *, main_content: bytes | None = None,
              extra: list[sup.ArchiveEntry] | None = None) -> sup.FlowEnv:
    install = sup.make_install_dir(tmp_path / "example")
    witness = tmp_path / "marker-witness.json"
    package = sup.make_linux_package(
        tmp_path / "MangaProof-1.1.0.alpha-linux-x64.tar.gz",
        main_content=main_content if main_content is not None else sup.main_script_ok(witness),
        extra=extra,
    )
    return sup.FlowEnv(
        install=install,
        package=package,
        backup=tmp_path / "MangaProof-update-data",
        marker=tmp_path / "tmp-update" / "success-marker.json",
        status=tmp_path / "state" / state_mod.STATE_FILE_NAME,
        witness=witness,
        sha256=sup.sha256_of(package),
    )


def make_options(env: sup.FlowEnv, **overrides) -> InstallerOptions:
    data = dict(
        install_dir=env.install,
        package=env.package,
        data_backup=env.backup,
        success_marker=env.marker,
        version=env.version,
        token=env.token,
        platform=env.platform,
        sha256=env.sha256,
        parent_pid=0,
        status_file=env.status,
        cli=True,
    )
    data.update(overrides)
    return InstallerOptions(**data)


def make_runtime(env: sup.FlowEnv, **overrides) -> Runtime:
    # ops 默认注入替身：**绝不扫描测试机上的真实进程表**（否则"有没有同名进程"
    # 会取决于开发机上是否正跑着 MangaProof，用例就会随机失败）
    kwargs = dict(
        reporter=env.reporter, allow_elevation=False,
        ops=sup.ScriptedLookupOps(), **FAST,
    )
    kwargs.update(overrides)
    return Runtime(**kwargs)


def read_state(path: Path) -> dict:
    payload = state_mod.read_json(path)
    assert payload is not None, f"状态文件必须存在：{path}"
    return payload


def wait_dead(ops, pid: int, timeout: float = 5.0) -> bool:
    """用**安装器自己的** ProcessOps 判断（它持有 Popen，能 poll 回收僵尸）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not ops.alive(pid):
            return True
        time.sleep(0.05)
    return False


# --------------------------------------------------------------------------- #
# ⑤ 成功路径
# --------------------------------------------------------------------------- #


def test_success_path_replaces_installs_data_and_cleans_up(tmp_path: Path):
    env = build_env(tmp_path)
    old_content = (env.install / "MangaProof").read_bytes()
    options = make_options(env)
    runtime = make_runtime(env)

    code = Installer(options, runtime).run()

    assert code == int(ExitCode.OK), env.reporter.logs
    assert env.reporter.result == (True, "")

    # 阶段顺序严格按 §78
    expected = ["INIT", "VALIDATE", "BACKUP_DATA", "VERIFY_PACKAGE", "RENAME_OLD",
                "EXTRACT", "RESTORE_DATA", "LAUNCH_NEW", "WAIT_SUCCESS", "CLEANUP", "SUCCESS"]
    assert env.reporter.phases == expected

    # 新版就位、旧版内容不再存在
    new_main = env.install / "MangaProof"
    assert new_main.is_file()
    assert new_main.read_bytes() != old_content
    assert new_main.stat().st_mode & 0o111, "新版主程序必须有执行位（需求 §41）"
    assert (env.install / "_internal" / "data.txt").read_text(encoding="utf-8") == "new build\n"
    assert not (env.install / "_internal" / "old-only.txt").exists()

    # 用户数据已恢复（§56）
    assert (env.install / "settings.json").read_text(encoding="utf-8") == sup.USER_SETTINGS
    assert (env.install / "recent.json").read_text(encoding="utf-8") == sup.USER_RECENT
    assert (env.install / "logs" / "app.log").read_text(encoding="utf-8") == sup.USER_LOG

    # 新版确实收到了契约参数并写对了 marker（witness 是脚本复制的副本）
    witness = json.loads(env.witness.read_text(encoding="utf-8"))
    assert witness["token"] == env.token
    assert witness["version"] == env.version
    assert int(witness["pid"]) > 0

    # §61 清理清单：.old / 数据备份 / 更新包 / marker 都必须删掉
    assert any(
        rel == "MangaProof → MangaProof.old" and action == "重命名"
        for action, rel in env.reporter.items
    ), "必须先执行 MangaProof → MangaProof.old（需求 §54）"
    assert not rollback.old_dir_for(env.install).exists()
    assert not env.backup.exists()
    assert not env.package.exists()
    assert not env.marker.exists()

    # §64 状态文件停在 SUCCESS，且不含任何 CDK 字段
    payload = read_state(env.status)
    assert payload["phase"] == "SUCCESS"
    assert payload["renamed_old"] is True
    assert payload["token"] == env.token and payload["version"] == env.version
    assert payload["package_sha256"] == env.sha256
    assert "cdk" not in json.dumps(payload, ensure_ascii=False).lower()

    # UI 三层信息都上报过（阶段 / 具体文件 / 计数）
    assert env.reporter.phases
    assert any(rel.startswith("logs/") for rel in env.reporter.item_paths)
    assert any(total > 0 for _done, total in env.reporter.progress_calls)
    assert any(total == -1 for _done, total in env.reporter.progress_calls), "改名/等待阶段应是不确定进度"


def test_stale_extraction_target_is_purged_not_merged(tmp_path: Path):
    """解压目标处的残留目录必须先清掉，否则会被"合并"（还可能借软链接逃逸）。"""
    env = build_env(tmp_path)
    example = tmp_path / "example"
    leftover = example / "leftover"
    leftover.mkdir()
    (leftover / "stale.txt").write_text("stale", encoding="utf-8")
    (leftover / "evil-link").symlink_to("/etc")

    original = Installer._extract

    def patched(self: Installer):
        # 解压前把残留目录摆到"归档顶层目录"的位置（模拟上次失败留下的现场）
        import shutil

        target = self.options.install_dir.parent / "MangaProof"
        if not target.exists():
            shutil.copytree(leftover, target, symlinks=True)
        return original(self)

    Installer._extract = patched  # type: ignore[method-assign]
    try:
        code = Installer(make_options(env), make_runtime(env)).run()
    finally:
        Installer._extract = original  # type: ignore[method-assign]

    assert code == int(ExitCode.OK), env.reporter.logs
    assert not (env.install / "stale.txt").exists(), "残留内容不得被合并进新版本"
    assert not (env.install / "evil-link").exists()
    assert (env.install / "_internal" / "data.txt").is_file()


def test_success_path_supports_renamed_install_dir(tmp_path: Path):
    """安装目录被用户改过名时也要就位（归档顶层名 ≠ 目录名 → 改名）。"""
    env = build_env(tmp_path)
    renamed = tmp_path / "example" / "MyMangaProof"
    env.install.rename(renamed)
    env.install = renamed
    code = Installer(make_options(env), make_runtime(env)).run()
    assert code == int(ExitCode.OK), env.reporter.logs
    assert (renamed / "MangaProof").is_file()
    assert (renamed / "_internal" / "data.txt").is_file()
    assert not (tmp_path / "example" / "MangaProof").exists()


# --------------------------------------------------------------------------- #
# ⑥ 失败路径：新版启动即退出且无 marker → 立即回滚
# --------------------------------------------------------------------------- #


def test_new_version_exits_without_marker_rolls_back(tmp_path: Path):
    env = build_env(tmp_path, main_content=sup.main_script_exit_early())
    old_content = (env.install / "MangaProof").read_bytes()
    started = time.monotonic()

    code = Installer(make_options(env), make_runtime(env)).run()
    elapsed = time.monotonic() - started

    assert code == int(ExitCode.SUCCESS_TIMEOUT)
    assert elapsed < 5.0, "新版提前退出必须立即回滚，不等满 60 秒（需求 §60）"

    # 旧版本恢复、旧数据完整（§63）
    assert (env.install / "MangaProof").read_bytes() == old_content
    assert (env.install / "_internal" / "old-only.txt").is_file()
    assert (env.install / "settings.json").read_text(encoding="utf-8") == sup.USER_SETTINGS
    assert (env.install / "recent.json").read_text(encoding="utf-8") == sup.USER_RECENT
    assert (env.install / "logs" / "app.log").read_text(encoding="utf-8") == sup.USER_LOG
    assert not rollback.old_dir_for(env.install).exists(), "回滚后不能留下 .old"

    # 状态文件如实记录失败与回滚原因（需求 §77）
    payload = read_state(env.status)
    assert payload["phase"] == "FAILED"
    assert payload["rollback_reason"]
    assert "需求 §60" in payload["error"] or "成功标记" in payload["error"]
    assert "ROLLBACK" in env.reporter.phases
    assert env.reporter.result is not None and env.reporter.result[0] is False


# --------------------------------------------------------------------------- #
# ③ SHA-256 不符：拒绝且不触碰安装目录
# --------------------------------------------------------------------------- #


def test_sha256_mismatch_rejects_without_touching_install_dir(tmp_path: Path):
    env = build_env(tmp_path)
    env.sha256 = "0" * 64
    options = make_options(env)

    def snapshot(root: Path) -> dict[str, bytes]:
        return {
            p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*"))
            if p.is_file()
        }

    before = snapshot(env.install)
    code = Installer(options, make_runtime(env)).run()

    assert code == int(ExitCode.VERIFY)
    assert snapshot(env.install) == before, "校验失败绝不能改动安装目录（需求 §53）"
    assert not rollback.old_dir_for(env.install).exists()
    assert not env.marker.exists()
    assert env.package.exists(), "检查失败时更新包保留，便于用户重试/排查"
    # 没发生替换 → 回滚是无操作；备份目录**保留**（数据备份宁可留着，不要擅自删）
    assert (env.backup / "settings.json").read_text(encoding="utf-8") == sup.USER_SETTINGS

    payload = read_state(env.status)
    assert payload["phase"] == "FAILED"
    assert "SHA-256" in payload["error"]
    # 没有发生替换 → 回滚应该是无操作
    assert any("未发生替换" in line for line in env.reporter.logs)


def test_missing_package_is_rejected(tmp_path: Path):
    env = build_env(tmp_path)
    env.package.unlink()
    code = Installer(make_options(env), make_runtime(env)).run()
    assert code == int(ExitCode.VALIDATION)
    assert (env.install / "settings.json").is_file()
    assert not rollback.old_dir_for(env.install).exists()


def test_relative_paths_are_rejected(tmp_path: Path):
    env = build_env(tmp_path)
    options = make_options(env)
    options.install_dir = Path("relative/MangaProof")
    code = Installer(options, make_runtime(env)).run()
    assert code == int(ExitCode.VALIDATION)
    assert "绝对路径" in read_state(env.status)["error"]
    assert any("无需回滚" in line for line in env.reporter.logs), (
        "非法参数下不得拿相对路径去回滚（会相对 cwd 乱动文件）"
    )


# --------------------------------------------------------------------------- #
# ⑦ marker 校验：token / version 不匹配都不算成功
# --------------------------------------------------------------------------- #


def test_marker_with_wrong_token_is_not_success(tmp_path: Path):
    env = build_env(tmp_path, main_content=sup.main_script_bad_token())
    old_content = (env.install / "MangaProof").read_bytes()
    started = time.monotonic()
    code = Installer(make_options(env), make_runtime(env)).run()
    elapsed = time.monotonic() - started

    assert code == int(ExitCode.SUCCESS_TIMEOUT)
    assert elapsed < 5.0, "token 不匹配必须继续等待/失败，不得判成功"
    assert (env.install / "MangaProof").read_bytes() == old_content, "必须回滚"
    payload = read_state(env.status)
    assert payload["phase"] == "FAILED"


def test_marker_with_wrong_version_is_not_success(tmp_path: Path):
    env = build_env(tmp_path, main_content=sup.main_script_bad_version())
    code = Installer(make_options(env), make_runtime(env)).run()
    assert code == int(ExitCode.SUCCESS_TIMEOUT)
    assert (env.install / "settings.json").read_text(encoding="utf-8") == sup.USER_SETTINGS
    assert not rollback.old_dir_for(env.install).exists()


def test_stale_marker_is_ignored_and_removed_before_launch(tmp_path: Path):
    """上一次更新残留的 marker 不能让本次误判成功（调研报告 §5.4）。"""
    env = build_env(tmp_path, main_content=sup.main_script_exit_early())
    env.marker.parent.mkdir(parents=True, exist_ok=True)
    state_mod.write_marker(env.marker, env.token, env.version, pid=1)

    code = Installer(make_options(env), make_runtime(env)).run()

    assert code == int(ExitCode.SUCCESS_TIMEOUT), "残留 marker 不得被判为成功"
    assert (env.install / "_internal" / "old-only.txt").is_file(), "必须回滚到旧版本"


def test_wrong_marker_keeps_waiting_until_timeout(tmp_path: Path):
    """新版还活着但迟迟写不出合法 marker → 等满上限后失败并终止新版。"""
    script = """#!/bin/sh
while [ $# -gt 0 ]; do
  case "$1" in
    --success-marker) MARKER="$2"; shift 2;;
    --update-version) VERSION="$2"; shift 2;;
    *) shift;;
  esac
done
printf '{"token":"bad","version":"%s","pid":%s,"ts":0}\\n' "$VERSION" "$$" > "$MARKER"
sleep 60
"""
    env = build_env(tmp_path, main_content=sup.sh_script(script))
    runtime = make_runtime(env, success_timeout=0.6, poll_interval=0.05)
    installer = Installer(make_options(env), runtime)

    code = installer.run()

    assert code == int(ExitCode.SUCCESS_TIMEOUT)
    child = installer.state.child_pid
    assert child > 0
    assert wait_dead(runtime.ops, child), "超时后必须尽力终止挂住的新版进程"
    assert (env.install / "settings.json").is_file()


# --------------------------------------------------------------------------- #
# §64 异常中断恢复
# --------------------------------------------------------------------------- #


def test_recover_previous_incomplete_update_before_starting(tmp_path: Path):
    env = build_env(tmp_path)
    old_content = (env.install / "MangaProof").read_bytes()

    # 造出"上次更新做了一半"的现场：新版本目录在，旧版本在 .old
    half = tmp_path / "half"
    (half / "MangaProof").parent.mkdir(parents=True, exist_ok=True)
    (half / "MangaProof").write_text("half-extracted", encoding="utf-8")
    rollback.remove_tree(env.install)
    import shutil

    shutil.copytree(half, env.install)
    old = rollback.old_dir_for(env.install)
    shutil.copytree(half, old)
    (old / "MangaProof").write_bytes(old_content)

    code = Installer(make_options(env), make_runtime(env)).run()

    assert code == int(ExitCode.VALIDATION)
    assert (env.install / "MangaProof").read_bytes() == old_content, "必须恢复到旧版本状态"
    assert not old.exists()
    assert "需求 §64" in read_state(env.status)["error"]


def test_rollback_is_noop_when_nothing_replaced(tmp_path: Path):
    env = build_env(tmp_path)
    st = state_mod.UpdateState(
        install_dir=str(env.install), old_dir=str(rollback.old_dir_for(env.install)),
        data_backup=str(env.backup), package=str(env.package),
        success_marker=str(env.marker), token=env.token, version=env.version,
    )
    result = rollback.rollback(st)
    assert result.recovered is False
    assert "未发生替换" in result.reason
    assert (env.install / "settings.json").is_file()


# --------------------------------------------------------------------------- #
# 参数/环境校验
# --------------------------------------------------------------------------- #


def test_waiting_for_parent_pid_times_out(tmp_path: Path):
    env = build_env(tmp_path)
    options = make_options(env, parent_pid=os.getpid())  # 当前进程还活着
    code = Installer(options, make_runtime(env, parent_timeout=0.2)).run()
    assert code == int(ExitCode.VALIDATION)
    assert "等待主程序退出超时" in read_state(env.status)["error"]


def test_elevation_path_is_injected_not_real(tmp_path: Path):
    """需要提权时只调用注入的提权函数，安装器本体不提权（需求 §42）。"""
    env = build_env(tmp_path)
    calls: list[list[str]] = []

    def fake_elevate(argv, platform=None):
        calls.append(list(argv))
        return 0

    runtime = make_runtime(
        env,
        needs_elevation=lambda _path: True,
        is_admin=lambda: False,
        elevate=fake_elevate,
        allow_elevation=True,
    )
    options = make_options(env, rerun_argv=("installer-exe", "--install-dir", "x"))
    code = Installer(options, runtime).run()

    assert code == 0
    assert len(calls) == 1
    assert calls[0][-1] == "--cli", "提权子进程只跑 CLI 逻辑（调研报告 §5.2）"
    assert calls[0][0] == "installer-exe"
    # 本进程没有做任何文件替换
    assert not rollback.old_dir_for(env.install).exists()


def test_elevation_cancelled_by_user_stops_everything(tmp_path: Path):
    from updater import privilege

    env = build_env(tmp_path)

    def fake_elevate(argv, platform=None):
        raise privilege.PrivilegeCancelled("用户取消")

    runtime = make_runtime(
        env,
        needs_elevation=lambda _path: True,
        is_admin=lambda: False,
        elevate=fake_elevate,
        allow_elevation=True,
    )
    code = Installer(make_options(env), runtime).run()
    assert code == int(ExitCode.CANCELLED)
    assert (env.install / "settings.json").is_file()


def test_dry_run_verify_only_does_not_touch_anything(tmp_path: Path):
    """§53 的只读性质：verify_package 不改动任何东西。"""
    from updater import verify

    env = build_env(tmp_path)
    before = {p.relative_to(env.install).as_posix() for p in env.install.rglob("*")}
    result = verify.verify_package(env.package, platform="linux", expected_sha256=env.sha256)
    after = {p.relative_to(env.install).as_posix() for p in env.install.rglob("*")}
    assert before == after
    assert result.info.main_rel == archive.main_executable_rel("linux")
    assert result.sha256 == env.sha256


# --------------------------------------------------------------------------- #
# ⑥ 收尾：安装日志留存 + 更新包目录清理（需求 §53/§61）
# --------------------------------------------------------------------------- #


def test_success_path_preserves_installer_log_then_removes_package_dir(tmp_path: Path):
    """安装日志先复制进程序目录 logs/，随后整个更新包目录被删掉（含日志原件）。

    留日志的本意是排查用；日志原本躺在更新包目录里，而那个目录要被清理。
    所以顺序必须是"先留存、后删除"，且留存下来的那份要能读出内容。
    """
    env = build_env(tmp_path)
    log_file = tmp_path / "MangaProof-update-package" / "installer-tok.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("安装器启动：版本=1.1.0.alpha\n失败（退出码 40）：示例\n", encoding="utf-8")

    options = make_options(env)
    runtime = make_runtime(env, log_path=log_file)

    code = Installer(options, runtime).run()
    assert code == int(ExitCode.OK), env.reporter.logs

    preserved = list((env.install / "logs").glob("installer-*.log"))
    assert preserved, f"安装日志没有留存到 logs/：{env.install}"
    text = preserved[0].read_text(encoding="utf-8")
    assert "安装器启动" in text
    # 文件名带版本与 token 片段，一次更新一个文件，互不覆盖
    assert "1.1.0.alpha" in preserved[0].name

    # 内容留存成功后才允许清掉原件（原目录不是/也曾是更新包目录都无妨）
    assert (env.install / "MangaProof").is_file(), "程序本体必须仍在"


def test_package_dir_is_removed_at_the_end(tmp_path: Path, monkeypatch):
    """成功收尾时更新包目录被整个删掉（包本体 + 备份 + 标记 + 安装日志）。"""
    from mangaproof.update import platform_dirs

    env = build_env(tmp_path)
    # 把"更新临时目录的根"指到 tmp_path，让 canonical 目录落在测试沙箱里
    monkeypatch.setattr(platform_dirs, "temp_root", lambda: tmp_path)
    canonical = platform_dirs.update_package_dir()
    (canonical / "installer-tok.log").write_text("日志", encoding="utf-8")
    assert canonical.is_dir()

    code = Installer(make_options(env), make_runtime(env)).run()
    assert code == int(ExitCode.OK), env.reporter.logs
    assert not canonical.exists(), "更新包目录收尾必须删掉（留日志是先复制再删）"
    assert (env.install / "MangaProof").is_file(), "程序本体不受影响"


# --------------------------------------------------------------------------- #
# §83：直接启动防护 —— 没给 --parent-pid 时不许替换"正在运行"的程序
# --------------------------------------------------------------------------- #


def test_manual_invocation_without_parent_pid_is_refused_while_app_runs(tmp_path: Path):
    """手工拼参数跑安装器（没给 --parent-pid）且主程序在运行 → 拒绝且不碰文件（§83）。"""
    env = build_env(tmp_path)
    before = (env.install / "MangaProof").read_bytes()
    ops = sup.ScriptedLookupOps(lookup=[[4242]])

    code = Installer(make_options(env, parent_pid=0), make_runtime(env, ops=ops)).run()

    assert code == int(ExitCode.VALIDATION)
    error = read_state(env.status)["error"]
    assert "正在运行" in error and "更新" in error
    assert (env.install / "MangaProof").read_bytes() == before, "不许触碰安装目录"
    assert not rollback.old_dir_for(env.install).exists(), "不许改名旧版本（§54）"
    assert env.package.is_file(), "更新包也不许动"


def test_manual_invocation_is_allowed_when_no_app_is_running(tmp_path: Path):
    """主程序确实没在运行 → 手工调用照常执行（排障路径不能被这条检查堵死）。"""
    env = build_env(tmp_path)
    ops = sup.ScriptedLookupOps(lookup=[[]])

    code = Installer(make_options(env, parent_pid=0), make_runtime(env, ops=ops)).run()

    assert code == int(ExitCode.OK), env.reporter.logs


def test_manual_invocation_is_allowed_when_the_process_table_is_unavailable(tmp_path: Path):
    """``ps`` 查不了 → 只记日志、不拦截（与 §46 的保守哲学一致）。"""
    env = build_env(tmp_path)
    ops = sup.ScriptedLookupOps(lookup=[None])

    code = Installer(make_options(env, parent_pid=0), make_runtime(env, ops=ops)).run()

    assert code == int(ExitCode.OK), env.reporter.logs
    assert any("跳过" in line and "检查" in line for line in env.reporter.logs)


def test_allow_direct_launch_skips_the_running_process_check(tmp_path: Path):
    """调试参数 --allow-direct-launch：主程序在运行也照跑（§45/§83）。"""
    env = build_env(tmp_path)
    ops = sup.ScriptedLookupOps(lookup=[[4242]])
    options = make_options(env, parent_pid=0, allow_direct_launch=True)

    code = Installer(options, make_runtime(env, ops=ops)).run()

    assert code == int(ExitCode.OK), env.reporter.logs


def test_parent_pid_present_never_scans_the_process_table(tmp_path: Path):
    """主程序发起的更新（带 pid）不该因这条检查去扫进程表 —— 免得干扰 §46 的编排。"""
    env = build_env(tmp_path)
    ops = sup.ScriptedLookupOps(lookup=[[4242]])
    installer = Installer(
        make_options(env, parent_pid=os.getpid()), make_runtime(env, ops=ops)
    )

    installer._check_manual_invocation_safety()

    assert ops.lookup_calls == 0

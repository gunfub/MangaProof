# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""安装器的"进程跟踪"回归（2026-09-24 实测的 macOS 误报失败）。

实测故障：macOS 上更新**每次都误报失败**。根因是启动新版走的是
``/usr/bin/open -n -a MangaProof.app``（经 LaunchServices），``subprocess.Popen``
拿到的 pid 属于 ``open`` 自己 —— 它把启动请求交给 launchd 后立刻退出，而真正的
app 是**另一个 pid**。安装器当时把"跟踪的 pid 消失"当成"新版提前退出"，于是
秒判失败并回滚（回滚还会删掉正在启动的新版目录）。

修正后的判据（需求方 2026-09-24 决策）：

- 等待期间**按进程名**找主程序：找到 → 换 pid 继续等标记；宽限 20 秒仍找不到才
  判失败（``open`` 退出到 app 出现本来就有几百毫秒~几秒）；
- 查不了（``ps`` 不可用）→ 当作"可能还活着"，等满超时，绝不早判失败；
- 等待期间不结束主程序；**只有超时才按既有语义结束它并回滚**；
- 安装前：pid 退出后**再按进程名复核一次**（换了 pid / 有第二个实例都不能开始替换）。

这里的"按进程名查进程"由 :class:`~test_updater_support.ScriptedLookupOps` 编排
（真实实现要 ``ps``，不能拿来扫测试机），其余全是真的：真的起进程、真写 marker、
真回滚。另有两个用例用"启动器型假新版"把 macOS 的 pid 变化端到端走一遍。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from updater import rollback
from updater.installer import ExitCode, Installer

import test_updater_support as sup  # noqa: E402
import test_updater_flow as flow  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="假新版程序是 POSIX sh 脚本（Windows 走同样的逻辑但本地不跑）"
)


def live_pid_from(pidfile: Path):
    """动态查找替身：把 pid 文件里的 app pid 当作"主程序进程"，已退出则报"没有"。"""

    def _lookup() -> list[int]:
        try:
            pid = int(pidfile.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return []
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return []
        except PermissionError:  # pragma: no cover - 同用户下不会发生
            return [pid]
        return [pid]

    return _lookup


# --------------------------------------------------------------------------- #
# ① 端到端：启动器型拉起（macOS 的 open 语义）
# --------------------------------------------------------------------------- #


def test_launcher_style_pid_change_succeeds(tmp_path: Path):
    """spawn 的 pid 秒退、app 以另一个 pid 起来 —— 必须继续等标记并成功。"""
    pidfile = tmp_path / "real-main.pid"
    env = flow.build_env(
        tmp_path,
        main_content=sup.main_script_launcher(pidfile),
        extra=sup.launcher_extra(sup.main_script_ok(delay=0.3)),
    )
    ops = sup.ScriptedLookupOps(lookup=[live_pid_from(pidfile)])
    runtime = flow.make_runtime(env, ops=ops, relocate_timeout=5.0)

    code = Installer(flow.make_options(env), runtime).run()

    assert code == int(ExitCode.OK), env.reporter.logs
    assert env.reporter.result == (True, "")
    # 关键证据：pid 变过，而且是被"按进程名找回来"的
    assert any("在运行，继续等待成功标记" in line for line in env.reporter.logs), \
        env.reporter.logs
    real_pid = int(pidfile.read_text(encoding="utf-8").strip())
    assert int(flow.read_state(env.status)["child_pid"]) == real_pid
    # 新版真的就位并写对了 marker；旧目录按 §61 清理
    assert (env.install / "MangaProof").is_file()
    assert not rollback.old_dir_for(env.install).exists()


def test_launcher_style_new_version_dies_without_marker_rolls_back(tmp_path: Path):
    """启动器起来了但 app 随即死掉（真失败）→ 宽限期后判失败并回滚。"""
    pidfile = tmp_path / "real-main.pid"
    env = flow.build_env(
        tmp_path,
        main_content=sup.main_script_launcher(pidfile),
        extra=sup.launcher_extra(sup.main_script_exit_early()),
    )
    old_content_before = (env.install / "MangaProof").read_bytes()
    ops = sup.ScriptedLookupOps(lookup=[live_pid_from(pidfile)])
    runtime = flow.make_runtime(env, ops=ops, relocate_timeout=1.0)

    started = time.monotonic()
    code = Installer(flow.make_options(env), runtime).run()
    elapsed = time.monotonic() - started

    assert code == int(ExitCode.SUCCESS_TIMEOUT)
    assert elapsed < 6.0, "宽限期一到就该判失败，不空等满 60 秒（§60）"
    assert (env.install / "MangaProof").read_bytes() == old_content_before, "必须回滚"
    assert not rollback.old_dir_for(env.install).exists()


# --------------------------------------------------------------------------- #
# ② 判据：什么才算"新版提前退出"
# --------------------------------------------------------------------------- #


def test_pid_exit_with_process_found_is_not_failure(tmp_path: Path):
    """编排出"跟踪的 pid 秒退、主程序以另一个 pid 在跑"：必须换 pid 继续等标记。"""
    env = flow.build_env(tmp_path, main_content=sup.main_script_ok(delay=0.3))
    extra_pid = 424242
    ops = sup.ScriptedLookupOps(lookup=[[extra_pid]], launcher_style=True)
    runtime = flow.make_runtime(env, ops=ops, relocate_timeout=5.0)

    # 本例测的是 §60 的判据（pid 换来换去时不许早判失败），不是 §83 的防误启动：
    # parent_pid=0 且"查得到主程序在运行"会被 §83 拒绝，所以显式走调试逃生参数。
    code = Installer(flow.make_options(env, allow_direct_launch=True), runtime).run()

    assert code == int(ExitCode.OK), env.reporter.logs
    assert ops.lookup_calls >= 1, "pid 秒退后必须按进程名找一次"
    assert int(flow.read_state(env.status)["child_pid"]) == extra_pid


def test_relocate_grace_expires_without_process(tmp_path: Path):
    """"pid 消失 + 查得到且没有进程" → 宽限期到就判失败（真失败路径）。"""
    env = flow.build_env(tmp_path, main_content=sup.main_script_exit_early())
    ops = sup.ScriptedLookupOps(lookup=[[]])
    runtime = flow.make_runtime(env, ops=ops, relocate_timeout=0.5)

    code = Installer(flow.make_options(env), runtime).run()

    assert code == int(ExitCode.SUCCESS_TIMEOUT)
    error = flow.read_state(env.status)["error"]
    assert "没有写入成功标记" in error


def test_lookup_unavailable_never_fails_early(tmp_path: Path):
    """``ps`` 查不了（``None``）时必须继续等：marker 稍后出现 → 成功。"""
    env = flow.build_env(tmp_path, main_content=sup.main_script_ok(delay=0.4))
    ops = sup.ScriptedLookupOps(lookup=[None])
    runtime = flow.make_runtime(env, ops=ops, relocate_timeout=0.5)

    code = Installer(flow.make_options(env), runtime).run()

    assert code == int(ExitCode.OK), env.reporter.logs


def test_timeout_with_live_process_kills_and_rolls_back(tmp_path: Path):
    """超时（进程一直活着、60 秒内没有 marker）：按需求方决策维持"杀进程 + 回滚"。"""
    env = flow.build_env(tmp_path, main_content=sup.main_script_hang())
    old_content_before = (env.install / "MangaProof").read_bytes()
    ops = sup.ScriptedLookupOps(lookup=[[]])
    runtime = flow.make_runtime(
        env, ops=ops, success_timeout=0.6, relocate_timeout=0.2
    )

    installer = Installer(flow.make_options(env), runtime)
    code = installer.run()

    assert code == int(ExitCode.SUCCESS_TIMEOUT)
    pid = 0
    assert "等待成功标记超时" in flow.read_state(env.status)["error"]
    assert (env.install / "MangaProof").read_bytes() == old_content_before
    # 被结束的假新版不该还在（PID 由安装器自己 spawn，poll 能回收）
    import re

    matches = [
        int(m.group(1))
        for m in (re.search(r"新版本已启动（pid=(\d+)）", line) for line in env.reporter.logs)
        if m
    ]
    assert matches, env.reporter.logs
    pid = matches[-1]
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and ops.alive(pid):
        time.sleep(0.05)
    assert not ops.alive(pid), "超时必须结束挂着的新版（需求 §59）"


# --------------------------------------------------------------------------- #
# ③ 安装前：pid 退出 + 进程名复核
# --------------------------------------------------------------------------- #


def _dead_pid() -> int:
    """拿一个"确实已经退出"的 pid（跑一个立刻结束的进程再回收它）。"""
    proc = subprocess.Popen(["/bin/sh", "-c", "exit 0"])
    proc.wait()
    return proc.pid


def test_wait_parent_confirms_by_name_before_replacing(tmp_path: Path):
    """pid 退出后仍有同名进程 → 必须继续等，不能开始替换；它消失后才继续。"""
    env = flow.build_env(tmp_path)
    ops = sup.ScriptedLookupOps(lookup=[[999999], []])
    runtime = flow.make_runtime(env, ops=ops, parent_timeout=3.0)

    code = Installer(flow.make_options(env, parent_pid=_dead_pid()), runtime).run()

    assert code == int(ExitCode.OK), env.reporter.logs
    assert ops.lookup_calls >= 2, "必须真的按名字确认过"
    assert any("MangaProof（pid=999999）" == item for _action, item in env.reporter.items)


def test_wait_parent_times_out_when_app_still_running(tmp_path: Path):
    """同名进程一直在 → 拒绝开始替换，并给出可操作的报错。"""
    env = flow.build_env(tmp_path)
    old_content_before = (env.install / "MangaProof").read_bytes()
    ops = sup.ScriptedLookupOps(lookup=[[999999]])
    runtime = flow.make_runtime(env, ops=ops, parent_timeout=0.3)

    code = Installer(flow.make_options(env, parent_pid=_dead_pid()), runtime).run()

    assert code == int(ExitCode.VALIDATION)
    error = flow.read_state(env.status)["error"]
    assert "仍有 MangaProof 进程在运行" in error and "请先退出" in error
    assert (env.install / "MangaProof").read_bytes() == old_content_before, "不许动安装目录"


def test_wait_parent_detects_recycled_pid(tmp_path: Path):
    """pid 还"活着"但不是主程序（系统复用）→ 视为主程序已退出，不再干等。"""
    env = flow.build_env(tmp_path)
    ops = sup.ScriptedLookupOps(lookup=[[]], exe_name="python3.12")  # 名字对不上
    runtime = flow.make_runtime(env, ops=ops, parent_timeout=5.0)

    code = Installer(flow.make_options(env, parent_pid=os.getpid()), runtime).run()

    assert code == int(ExitCode.OK), env.reporter.logs
    assert any("已被系统分配给其它进程" in line for line in env.reporter.logs)


def test_wait_parent_keeps_waiting_when_lookup_unavailable(tmp_path: Path):
    """名字查不了 → 退回"只等 pid"，并且 pid 一直活着就照旧超时失败。"""
    env = flow.build_env(tmp_path)
    ops = sup.ScriptedLookupOps(lookup=[None])
    runtime = flow.make_runtime(env, ops=ops, parent_timeout=0.3)

    code = Installer(flow.make_options(env, parent_pid=os.getpid()), runtime).run()

    assert code == int(ExitCode.VALIDATION)
    assert "等待主程序退出超时" in flow.read_state(env.status)["error"]


# --------------------------------------------------------------------------- #
# ④ 真实实现：按进程名查进程（POSIX 的 ps 路径）
# --------------------------------------------------------------------------- #


def test_process_ops_finds_running_process_by_name(tmp_path: Path):
    """真的跑一次 ``ps``：复制一份可执行文件、改成独特名字再拉起来，必须找得到。

    这条覆盖的是替身绕开的真实实现（``ps -A -o pid=,state=,comm=`` 的解析、
    macOS 的"全路径 comm"与 Linux 的"进程名 comm"差异、僵尸过滤）。
    """
    import shutil

    from updater.installer import ProcessOps

    name = "mpprobe9"                       # Linux 的 comm 截断到 15 字符，别超
    exe = tmp_path / name
    shutil.copy2("/bin/sleep", exe)
    proc = subprocess.Popen([str(exe), "30"])
    ops = ProcessOps()
    try:
        found: list[int] = []
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            found = ops.find_running(name) or []
            if proc.pid in found:
                break
            time.sleep(0.05)
        assert proc.pid in found, f"没找到 {exe}：find_running={found}"
        assert ops.exe_matches(proc.pid, name) is True
        assert ops.exe_matches(proc.pid, "MangaProof") is False
        assert ops.find_running("mangaproof-no-such-process") == []
    finally:
        proc.terminate()
        proc.wait()
    # 已退出的进程（且已被回收）不能再被认成"还在运行"
    assert proc.pid not in (ops.find_running(name) or [])


def test_parse_ps_output_matches_macos_and_linux_shapes():
    """``ps`` 输出解析：macOS 给全路径（可能带空格）、Linux 给进程名、僵尸要剔除。"""
    from updater.installer import _basename, _is_zombie, _parse_ps_table

    text = (
        "    1 S /Applications/My App.app/Contents/MacOS/MangaProof\n"
        "   42 Z <defunct>\n"
        "   43 S MangaProof\n"
        "   44 S /usr/bin/open\n"
        "     \n"
    )
    rows = _parse_ps_table(text, with_state=True)
    assert [(pid, state) for pid, state, _comm in rows] == [(1, "S"), (42, "Z"), (43, "S"), (44, "S")]
    assert _basename(rows[0][2]) == "MangaProof", "带空格的全路径也要能取到文件名"
    assert _is_zombie(rows[1][1]) and not _is_zombie(rows[2][1])

    # 不带 state 的降级形式（老 ps 不支持 state 列时）：整段 comm 原样保留
    rows2 = _parse_ps_table(
        "  7 /Applications/My App.app/Contents/MacOS/MangaProof\n", with_state=False
    )
    assert rows2 == [(7, "", "/Applications/My App.app/Contents/MacOS/MangaProof")]
    assert _basename(rows2[0][2]) == "MangaProof"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))

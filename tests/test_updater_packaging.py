# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""打包与 CLI 契约单测（需求 §43/§45/§46）。

三件事必须**由测试钉住**，否则很容易在后续改动里悄悄退化：

1. ``packaging/installer.spec`` 是 **onefile + windowed + 不 exclude tkinter**
   （CI 直接按 ``--distpath dist-installer`` 构建，产物名必须对得上）；
2. CLI 入口的参数名/退出码与主程序侧约定一致，且**不依赖 cwd**；
3. 三份 ``packaging/main_*.spec`` 把 ``mangaproof.update.platform.*`` 声明成
   hiddenimports —— 它们是拼接字符串的运行时导入，静态分析看不见，漏了就会
   打出"检查更新能用、点安装报 No module named '…platform.windows'"的残废包。
"""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SPEC = ROOT / "packaging" / "installer.spec"
MAIN_SPECS = sorted((ROOT / "packaging").glob("main_*.spec"))

from mangaproof.update.platform import PLATFORM_MODULES  # noqa: E402
from updater import main as updater_main  # noqa: E402
from updater.installer import ExitCode  # noqa: E402


def test_installer_spec_is_onefile_windowed_without_excluding_tkinter():
    text = SPEC.read_text(encoding="utf-8")
    code = "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("#")
    )
    assert 'name="MangaProof-update-installer"' in code
    assert "exclude_binaries=False" in code, "onefile 必须显式 exclude_binaries=False"
    assert "console=False" in code
    assert "upx=False" in code
    assert "COLLECT(" not in code, "onefile 不能写 COLLECT"
    assert 'ROOT / "updater" / "main.py"' in code, "入口必须是 updater/main.py"
    # 绝不能 exclude tkinter（GUI 就是它）
    excludes = re.search(r"_excludes\s*=\s*\[(.*?)\]", code, re.S)
    assert excludes is not None
    assert "tkinter" not in excludes.group(1)
    # 三个零依赖模块要显式声明 hiddenimports（安装器不依赖主程序运行环境）
    for module in ("mangaproof.config.user_data", "mangaproof.utils.hashing",
                   "mangaproof.update.checksum"):
        assert module in code
    # 说明这是由 CI 按平台构建
    assert "CI" in text


def test_cli_flags_match_the_cross_process_contract():
    parser = updater_main.build_parser()
    actions = {opt: action for action in parser._actions for opt in action.option_strings}
    for flag in ("--install-dir", "--package", "--data-backup", "--success-marker",
                 "--version", "--sha256", "--parent-pid", "--token", "--platform",
                 "--cli", "--status-file", updater_main.ALLOW_DIRECT_FLAG):
        assert flag in actions, f"缺少参数 {flag}（需求 §45 / 接口契约）"
    for flag in ("--install-dir", "--package", "--data-backup", "--success-marker",
                 "--version", "--token", "--platform"):
        assert actions[flag].required is True
    # 可选参数必须有不报错的默认值
    assert actions["--sha256"].default == ""
    assert actions["--parent-pid"].default == 0
    assert actions["--status-file"].default is None
    # §83 的调试逃生参数：可选、默认关（默认必须严格）
    assert actions[updater_main.ALLOW_DIRECT_FLAG].required is False
    assert actions[updater_main.ALLOW_DIRECT_FLAG].default is False


def test_marker_argv_contract_constants():
    from updater import state as st

    assert st.ARG_TOKEN == "--update-token"
    assert st.ARG_MARKER == "--success-marker"
    assert st.ARG_VERSION == "--update-version"
    assert st.STATE_FILE_NAME == "installer-state.json"


def test_normalize_path_requires_absolute(tmp_path: Path, monkeypatch):
    assert updater_main.normalize_path(str(tmp_path / "a" / ".." / "b")) == tmp_path / "b"
    with pytest.raises(ValueError):
        updater_main.normalize_path("relative/path")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError):
        updater_main.normalize_path("MangaProof")  # cwd 里有同名目录也不行（§45）


def test_parse_options_builds_absolute_paths(tmp_path: Path):
    argv = [
        "--install-dir", str(tmp_path / "MangaProof"),
        "--package", str(tmp_path / "pkg.tar.gz"),
        "--data-backup", str(tmp_path / "backup"),
        "--success-marker", str(tmp_path / "tmp" / "marker.json"),
        "--version", "1.1.0.alpha",
        "--token", "tok",
        "--platform", "linux",
        "--sha256", "a" * 64,
        "--parent-pid", "4242",
        "--cli",
    ]
    options = updater_main.parse_options(argv)
    assert options.install_dir.is_absolute()
    assert options.parent_pid == 4242
    assert options.cli is True
    assert options.state_path == tmp_path / "tmp" / "installer-state.json"


def test_main_returns_usage_code_on_bad_arguments(tmp_path: Path):
    code = updater_main.main(["--status-file", str(tmp_path / "installer-state.json")])
    assert code == int(ExitCode.USAGE)


def test_main_help_exits_zero(capsys):
    assert updater_main.main(["--help"]) == 0
    assert "--install-dir" in capsys.readouterr().out


def test_main_reports_failure_with_nonzero_code(tmp_path: Path):
    """参数齐全但安装目录不存在 → 非 0 退出码 + 可读原因（需求 §45/§62）。"""
    package = tmp_path / "pkg.tar.gz"
    package.write_bytes(b"not a real package")
    status = tmp_path / "installer-state.json"
    code = updater_main.main([
        "--install-dir", str(tmp_path / "missing-install"),
        "--package", str(package),
        "--data-backup", str(tmp_path / "backup"),
        "--success-marker", str(tmp_path / "tmp" / "marker.json"),
        "--version", "1.1.0.alpha",
        "--token", "tok-cli-test",
        "--platform", "linux",
        "--cli",
        "--status-file", str(status),
    ])
    assert code != 0
    assert status.is_file(), "状态文件必须被写出来（§64）"
    # 日志文件名按契约：--status-file 同目录下的 installer-<token>.log
    assert (tmp_path / "installer-tok-cli-test.log").is_file()


def test_setup_logging_redirects_none_streams(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(updater_main, "_LOGGER_READY", False)
    monkeypatch.setattr(updater_main, "_LOG_PATH", None)
    monkeypatch.setattr("sys.stdout", None)
    monkeypatch.setattr("sys.stderr", None)
    argv = ["--status-file", str(tmp_path / "state" / "installer-state.json"),
            "--token", "tok-log"]
    log_path = updater_main.setup_logging(argv)
    import sys

    assert log_path is not None and log_path.is_file()
    assert sys.stdout is not None and sys.stderr is not None
    print("hello")  # 不得 AttributeError
    sys.stdout.flush()
    assert "hello" in log_path.read_text(encoding="utf-8")


def test_scan_value_supports_both_forms():
    assert updater_main._scan_value(["--token", "abc"], "--token") == "abc"
    assert updater_main._scan_value(["--token=abc"], "--token") == "abc"
    assert updater_main._scan_value([], "--token") is None
    assert updater_main._safe_token("../../etc/passwd") == ".._.._etc_passwd"[:24]


def test_excepthook_records_crash(tmp_path: Path):
    status = tmp_path / "installer-state.json"
    updater_main._STATUS_PATH = status
    updater_main._record_crash("RuntimeError: boom")
    payload = updater_main.state_mod.read_json(status)
    assert payload is not None
    assert payload["phase"] == "FAILED" and payload["crashed"] is True
    assert "boom" in payload["error"]
    updater_main._STATUS_PATH = None


def test_rerun_argv_and_elevated_argv(tmp_path: Path):
    from updater.installer import InstallerOptions, build_elevated_argv

    options = InstallerOptions(
        install_dir=tmp_path / "MangaProof",
        package=tmp_path / "pkg.tar.gz",
        data_backup=tmp_path / "backup",
        success_marker=tmp_path / "marker.json",
        version="1.1.0.alpha",
        token="tok",
        platform="linux",
        rerun_argv=("/path/to/installer", "--install-dir", "/x", "--cli"),
    )
    argv = build_elevated_argv(options)
    assert argv == ["/path/to/installer", "--install-dir", "/x", "--cli"]
    assert argv.count("--cli") == 1


def test_state_file_path_follows_the_main_program_contract(tmp_path: Path):
    """状态文件固定叫 installer-state.json，且与 --status-file 同目录。"""
    from updater import state as st
    from updater.installer import InstallerOptions, resolve_state_path

    marker = tmp_path / "tmp" / "marker.json"
    canonical = tmp_path / "state" / st.STATE_FILE_NAME

    # ① 主程序按约定传 installer-state.json → 原样使用
    assert resolve_state_path(canonical, marker) == canonical
    # ② 传了别的名字且文件已存在 → 尊重现状（主程序确实在用它）
    other = tmp_path / "state" / "installer-status.json"
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text("{}", encoding="utf-8")
    assert resolve_state_path(other, marker) == other
    # ③ 传了别的名字但不存在 → 用同目录下的约定文件名，不另造新名字
    assert resolve_state_path(tmp_path / "state" / "whatever.json", marker) == canonical
    # ④ 完全没传 → 与 success-marker 同目录
    assert resolve_state_path(None, marker) == marker.parent / st.STATE_FILE_NAME

    options = InstallerOptions(
        install_dir=tmp_path / "MangaProof", package=tmp_path / "pkg.tar.gz",
        data_backup=tmp_path / "backup", success_marker=marker,
        version="1.1.0.alpha", token="t", platform="linux", status_file=canonical,
    )
    assert options.state_path == canonical


def test_privilege_run_direct_is_plain_subprocess():
    """不需要提权时直接普通执行（需求 §42 的第一条分支）。"""
    from updater import privilege

    class FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    calls = []

    def runner(argv, *, env=None):
        calls.append((list(argv), dict(env or {})))
        return FakeResult()

    assert privilege.run_direct(["true"], runner=runner, env={"_PYI_X": "1", "PATH": "/usr/bin"}) == 0
    argv, env = calls[0]
    assert argv == ["true"]
    assert "_PYI_X" not in env and env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"

    class Fail(FakeResult):
        returncode = 3

    with pytest.raises(privilege.PrivilegeError):
        privilege.run_direct(["false"], runner=lambda *a, **k: Fail(), env={})


# -- 主程序 spec：平台模块必须进包 -------------------------------------------


def test_platform_module_names_cover_every_branch():
    """``PLATFORM_MODULES`` 必须与 ``current()`` 可能导入的分支一一对应。

    少一个名字 = 那个平台的更新功能会在"点安装"时才炸（见下面 spec 断言）。
    """
    from mangaproof.update import platform as platform_pkg

    pkg_dir = Path(platform_pkg.__file__).parent
    for name in PLATFORM_MODULES:
        assert (pkg_dir / f"{name}.py").is_file(), f"{name}.py 不存在"
        assert importlib.import_module(
            f"mangaproof.update.platform.{name}"
        ) is not None
    # 当前平台的分支必须在名单里（否则本机打包就会漏）
    assert platform_pkg.current_name() in PLATFORM_MODULES
    # 模块名不要带包前缀（spec 里会拼），否则拼出来是错的
    assert all("." not in name for name in PLATFORM_MODULES)


@pytest.mark.parametrize("spec_path", MAIN_SPECS, ids=lambda p: p.name)
def test_main_specs_declare_platform_modules_as_hiddenimports(spec_path: Path):
    """三份 main_*.spec 都必须把平台模块加进 hiddenimports（本次踩的坑）。

    根因：``mangaproof/update/platform/__init__.py`` 用
    ``__import__(f"mangaproof.update.platform.{name}", …)`` 做运行时分发，
    PyInstaller 的静态分析完全看不到这些子模块。1.1.2.alpha 的发布包因此
    在 Windows 上报 ``No module named 'mangaproof.update.platform.windows'``。
    """
    text = spec_path.read_text(encoding="utf-8")
    assert "from mangaproof.update.platform import PLATFORM_MODULES" in text, (
        f"{spec_path.name} 没导入 PLATFORM_MODULES（名单必须与代码同源）"
    )
    match = re.search(r"hiddenimports\s*=\s*\[(.*?)\]", text, re.S)
    assert match is not None, f"{spec_path.name} 的 hiddenimports 不是字面量列表"
    assert "PLATFORM_MODULES" in match.group(1), (
        f"{spec_path.name} 的 hiddenimports 漏了平台模块"
    )
    assert "psd_tools" in match.group(1), "别把 psd-tools 的收集弄丢了"


def test_pyinstaller_can_resolve_every_platform_module():
    """PyInstaller 自己的模块图能找到这四个模块（= 打包时能真的收进去）。

    比字符串断言强一档：这里走 PyInstaller 的 import hook，
    路径/包结构有问题会直接 ImportError。
    """
    pytest.importorskip("PyInstaller")
    from PyInstaller.depend.analysis import initialize_modgraph

    graph = initialize_modgraph()
    graph.path.extend(str(p) for p in (ROOT, *sys.path) if p)
    for name in PLATFORM_MODULES:
        full = f"mangaproof.update.platform.{name}"
        nodes = graph.import_hook(full)     # 找不到会抛 ImportError
        assert nodes and nodes[0].identifier == full


def test_prepare_invocation_passes_sha256_to_the_installer(tmp_path: Path, monkeypatch):
    """主程序必须把 SHA-256 传下去（需求 §53），不能留空让安装器跳过复核。

    空 --sha256 时安装器只能打"未提供 --sha256，本次不做哈希校验"——
    而三个渠道其实都能给出文件名与哈希，缺的只是把它一路递过去。
    """
    from mangaproof.update import installer as installer_mod
    from mangaproof.update.platform import PLATFORM_MODULES  # noqa: F401

    exe = tmp_path / "MangaProof-update-installer"
    exe.write_bytes(b"fake installer")
    package = tmp_path / "MangaProof-1.1.5.alpha-linux-x64.tar.gz"
    package.write_bytes(b"pkg")

    monkeypatch.setattr(installer_mod, "find_installer", lambda: exe)
    monkeypatch.setattr(
        installer_mod.platform_dirs, "installer_dir", lambda **kw: tmp_path / "tmp-installer"
    )
    (tmp_path / "tmp-installer").mkdir(exist_ok=True)
    monkeypatch.setattr(
        installer_mod.platform_dirs, "success_marker_path", lambda: tmp_path / "marker.json"
    )
    monkeypatch.setattr(
        installer_mod.platform_dirs, "data_backup_dir", lambda **kw: tmp_path / "backup"
    )
    monkeypatch.setattr(
        installer_mod.platform_dirs, "state_file_path", lambda: tmp_path / "state.json"
    )

    digest = "a" * 64
    invocation = installer_mod.prepare_invocation(
        package=package, version="1.1.5.alpha", sha256=digest, parent_pid=4321
    )
    args = invocation.args
    assert "--sha256" in args
    assert args[args.index("--sha256") + 1] == digest
    assert args[args.index("--parent-pid") + 1] == "4321"

    # 没有哈希时也要给出空串（安装器据此判定"跳过并留痕"），而不是缺参数
    invocation2 = installer_mod.prepare_invocation(
        package=package, version="1.1.5.alpha", sha256=None
    )
    assert invocation2.args[invocation2.args.index("--sha256") + 1] == ""


# --------------------------------------------------------------------------- #
# §83：直接启动防护（弹窗 + 优雅退出；**不做**运行目录自检）
# --------------------------------------------------------------------------- #


class _NoticeSpy:
    """替身：记录提示调用，绝不真的弹窗（CI 无图形会话，也不该被挡住）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    def __call__(self, detail: str = "", *, gui: bool = True) -> bool:
        self.calls.append((detail, gui))
        return True

    @property
    def called(self) -> bool:
        return bool(self.calls)

    @property
    def detail(self) -> str:
        return self.calls[-1][0] if self.calls else ""


def _spy_notice(monkeypatch, tmp_path: Path) -> _NoticeSpy:
    """把提示函数换成替身，并把日志固定写进 tmp_path（不污染真实临时目录）。"""
    spy = _NoticeSpy()
    monkeypatch.setattr(updater_main.ui, "show_launch_notice", spy)
    monkeypatch.setattr(updater_main, "_LOGGER_READY", False)
    monkeypatch.setattr(updater_main, "_LOG_PATH", None)
    monkeypatch.setattr(updater_main, "_log_directory", lambda argv: tmp_path)
    return spy


def test_frozen_direct_launch_shows_notice_and_exits_usage(tmp_path: Path, monkeypatch):
    """打包后的安装器被直接双击（零参数）→ 必须弹提示 + 退出码 2（§83）。"""
    spy = _spy_notice(monkeypatch, tmp_path)
    monkeypatch.setattr(updater_main, "_is_frozen", lambda: True)

    assert updater_main.main([]) == int(ExitCode.USAGE)

    assert spy.called, "打包后缺参启动必须给出可见提示（§83：不许静默退出）"
    assert "--install-dir" in spy.detail and "--platform" in spy.detail


def test_frozen_partial_arguments_also_show_the_notice(tmp_path: Path, monkeypatch):
    """只给了一部分参数（或缺必填项）同样算直接启动 → 提示里列出缺的参数。"""
    spy = _spy_notice(monkeypatch, tmp_path)
    monkeypatch.setattr(updater_main, "_is_frozen", lambda: True)

    code = updater_main.main(["--install-dir", str(tmp_path)])

    assert code == int(ExitCode.USAGE)
    assert spy.called and "--install-dir" not in spy.detail, "已给的参数不该出现在缺失清单里"
    assert "--package" in spy.detail


def test_source_run_never_shows_the_notice(tmp_path: Path, monkeypatch):
    """源码运行（非打包）保持命令行行为：usage 文本 + 退出码 2，不弹窗（§83）。"""
    spy = _spy_notice(monkeypatch, tmp_path)
    monkeypatch.setattr(updater_main, "_is_frozen", lambda: False)

    code = updater_main.main(["--status-file", str(tmp_path / "installer-state.json")])

    assert code == int(ExitCode.USAGE)
    assert not spy.called, "源码运行时弹窗会挡住开发、排障与单测"


def test_allow_direct_launch_suppresses_the_notice(tmp_path: Path, monkeypatch):
    """``--allow-direct-launch``（调试）不弹窗，退出码仍是用法错误（§45/§83）。"""
    spy = _spy_notice(monkeypatch, tmp_path)
    monkeypatch.setattr(updater_main, "_is_frozen", lambda: True)

    code = updater_main.main([updater_main.ALLOW_DIRECT_FLAG])

    assert code == int(ExitCode.USAGE)
    assert not spy.called


def test_cli_direct_launch_degrades_to_the_command_line(tmp_path: Path, monkeypatch):
    """``--cli`` 是显式"不要 GUI"：提示降级为 stderr，不许弹窗（§83）。"""
    spy = _spy_notice(monkeypatch, tmp_path)
    monkeypatch.setattr(updater_main, "_is_frozen", lambda: True)

    assert updater_main.main(["--cli"]) == int(ExitCode.USAGE)

    assert spy.called and spy.calls[-1][1] is False, "必须以 gui=False 调用"


def test_help_never_shows_the_notice(tmp_path: Path, monkeypatch, capsys):
    spy = _spy_notice(monkeypatch, tmp_path)
    monkeypatch.setattr(updater_main, "_is_frozen", lambda: True)

    assert updater_main.main(["--help"]) == 0
    assert not spy.called
    assert "--install-dir" in capsys.readouterr().out


def test_frozen_run_outside_the_installer_temp_dir_is_not_refused(tmp_path: Path, monkeypatch):
    """**不做**运行目录（来源）自检：参数合法就照常进入安装器流程（§83）。

    这条同时是回归护栏：若以后有人加上"必须在 MangaProof-update-installer 目录里"
    的检查，这里会立刻失败（那会挡住单测、开发与排障）。
    """
    spy = _spy_notice(monkeypatch, tmp_path)
    monkeypatch.setattr(updater_main, "_is_frozen", lambda: True)
    package = tmp_path / "pkg.tar.gz"
    package.write_bytes(b"not a real package")

    code = updater_main.main([
        "--install-dir", str(tmp_path / "missing-install"),
        "--package", str(package),
        "--data-backup", str(tmp_path / "backup"),
        "--success-marker", str(tmp_path / "tmp" / "marker.json"),
        "--version", "1.1.0.alpha",
        "--token", "tok-direct-launch",
        "--platform", "linux",
        "--cli",
        "--status-file", str(tmp_path / "installer-state.json"),
    ])

    assert code == int(ExitCode.VALIDATION), "参数合法 → 该进状态机（安装目录不存在 → 10）"
    assert not spy.called, "不得因为运行目录不是临时目录就拒绝执行"


def test_log_directory_prefers_the_installer_temp_dir(tmp_path: Path, monkeypatch):
    """直接启动的日志落进安装器临时目录 → §82「清理升级缓存」能一起清掉（§83）。"""
    from mangaproof.update import platform_dirs

    installer_dir = tmp_path / "cache" / platform_dirs.INSTALLER_DIRNAME
    installer_dir.mkdir(parents=True)
    monkeypatch.setattr(platform_dirs, "installer_dir",
                        lambda create=True: installer_dir)

    assert updater_main._log_directory([]) == installer_dir


def test_log_directory_falls_back_without_creating_anything(tmp_path: Path, monkeypatch):
    """安装器临时目录不存在 → 退回系统临时目录，且**不创建**那个目录（§83）。"""
    from mangaproof.update import platform_dirs

    missing = tmp_path / "cache" / platform_dirs.INSTALLER_DIRNAME
    monkeypatch.setattr(platform_dirs, "installer_dir", lambda create=True: missing)
    monkeypatch.setattr(updater_main.tempfile, "gettempdir",
                        lambda: str(tmp_path / "system-temp"))

    assert updater_main._log_directory([]) == tmp_path / "system-temp"
    assert not missing.exists(), "一次误双击不该凭空造出安装器临时目录"

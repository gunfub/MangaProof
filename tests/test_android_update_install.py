# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""Android「安装更新」交给系统安装器（需求 §65，决策 A1 / B2 / C / D）。

与桌面的区别（本文件就是守这两条的）：

- **不使用独立安装器**：`prepare_invocation` / `launch` 一次都不许被调用；
- **不退出主程序**：系统安装界面会覆盖上来，安装时由系统结束本进程
  （`QDesktopServices.openUrl` 只能回报"是否成功发起"，拿不到安装结果）。

跑在离屏 Qt 上：真建 `MainWindow`，但**不联网、不启动任何进程、不开任何窗口**——
`QDesktopServices.openUrl` 与 `QMessageBox` 都换成捕获桩，桌面的安装器入口也换掉。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from mangaproof.config.settings import SettingsManager  # noqa: E402
from mangaproof.ui import main_window as mw  # noqa: E402

pytestmark = pytest.mark.usefixtures("_no_real_handoff")


@pytest.fixture(scope="module")
def qapp():
    """复用仓库既有约定：离屏 QApplication（QApplication 全局唯一）。"""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


class _BoxStub:
    """接住所有弹窗：模态框在无头测试里会卡住，这里只记录。"""

    criticals: list[tuple[str, str]] = []
    informations: list[tuple[str, str]] = []

    @classmethod
    def reset(cls) -> None:
        cls.criticals = []
        cls.informations = []

    @staticmethod
    def critical(parent, title, text, *args, **kwargs):  # noqa: ARG004
        _BoxStub.criticals.append((str(title), str(text)))

    @staticmethod
    def information(parent, title, text, *args, **kwargs):  # noqa: ARG004
        _BoxStub.informations.append((str(title), str(text)))


@pytest.fixture(autouse=True)
def _no_real_handoff(monkeypatch):
    """默认把所有"真的会碰系统"的出口换掉（需要时用例自行覆盖）。"""
    monkeypatch.setattr(mw, "QMessageBox", _BoxStub)
    _BoxStub.reset()
    yield
    _BoxStub.reset()


@pytest.fixture()
def opened_urls(monkeypatch):
    """捕获 `QDesktopServices.openUrl` 收到的 URL；默认返回 True（发起成功）。"""
    from PySide6.QtGui import QDesktopServices

    seen: list[str] = []
    result = {"ok": True}

    def _fake(url):
        seen.append(url.toString())
        return result["ok"]

    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(_fake))
    return SimpleNamespace(urls=seen, result=result)


@pytest.fixture()
def installer_calls(monkeypatch):
    """桌面安装器入口的调用记录（安卓分支必须一次都不碰）。"""
    from mangaproof.update import installer as installer_module

    calls: list[str] = []

    def _prepare(**kwargs):
        calls.append("prepare_invocation")
        return SimpleNamespace(token="tok", args=[], exe=Path("x"))

    def _launch(invocation):
        calls.append("launch")
        return 0

    monkeypatch.setattr(installer_module, "prepare_invocation", _prepare)
    monkeypatch.setattr(installer_module, "launch", _launch)
    return calls


@pytest.fixture()
def window(qapp, tmp_path, monkeypatch):
    """一个真 MainWindow（离屏）。用 monkeypatch 控制平台判定。

    `close` 换成"记录 + 转发"的桩，而**不是**纯记录：主窗口持有后台线程，
    真不关掉就会被 Qt 在析构时直接 abort（`QThread: Destroyed while thread is
    still running` —— 本仓库在更新页上踩过同一个坑）。收尾走原始 close。
    """
    from mangaproof.ui.main_window import MainWindow

    manager = SettingsManager(tmp_path / "settings.json")
    manager.save()
    win = MainWindow(manager)
    closed: list[int] = []
    real_close = win.close

    def _recording_close():
        closed.append(1)
        real_close()

    monkeypatch.setattr(win, "close", _recording_close)
    win._test_closed = closed          # noqa: SLF001 - 供断言
    yield win
    real_close()
    qapp.processEvents()


def _as_android(monkeypatch, value: bool) -> None:
    """切平台判定：`_start_update_installer` 读的是 main_window 里的这个名字。"""
    monkeypatch.setattr(mw, "is_android_strict", lambda: value)


def _apk(tmp_path: Path) -> Path:
    package = tmp_path / "MangaProof-1.1.14.alpha-android-aarch64.apk"
    package.write_bytes(b"fake apk bytes")
    return package


# --------------------------------------------------------------------------- #
# Android 分支
# --------------------------------------------------------------------------- #


def test_android_hands_the_apk_to_the_system_installer(
    window, tmp_path, monkeypatch, opened_urls, installer_calls
):
    """把 APK 的 file:// URL 交给系统；不允许走独立安装器。"""
    _as_android(monkeypatch, True)
    package = _apk(tmp_path)

    window._start_update_installer((package, "a" * 64))

    assert opened_urls.urls == [package.as_uri()], "必须把已下载的 APK 交给系统"
    assert installer_calls == [], "安卓不使用独立安装器（需求 §65）"
    assert _BoxStub.criticals == []


def test_android_does_not_exit_the_main_window(
    qapp, window, tmp_path, monkeypatch, opened_urls, installer_calls
):
    """与桌面相反：安卓**不退出主程序**（决策 C）。

    桌面是"启动安装器 → 确认拉起 → 主程序退出"；安卓交给系统后仍然留在原处，
    由系统在安装时结束本进程。这里给事件循环一次机会，确认 close 没有被排进去。
    """
    _as_android(monkeypatch, True)
    window._start_update_installer((_apk(tmp_path), "a" * 64))
    qapp.processEvents()
    assert window._test_closed == [], "安卓不该主动关闭主程序"


def test_desktop_still_exits_after_launching_the_installer(
    qapp, window, tmp_path, monkeypatch, opened_urls, installer_calls
):
    """反向守护：桌面路径不许被安卓分支改坏（仍要拉起安装器并退出）。"""
    _as_android(monkeypatch, False)
    window._start_update_installer((_apk(tmp_path), "a" * 64))

    assert installer_calls == ["prepare_invocation", "launch"]
    assert opened_urls.urls == [], "桌面不走系统安装器"
    qapp.processEvents()
    assert window._test_closed == [1], "桌面拉起成功后必须退出主程序（需求 §46）"


def test_android_missing_package_is_reported_not_opened(
    window, tmp_path, monkeypatch, opened_urls
):
    """APK 不见了：如实报错，不发 Intent（否则系统只会报一个看不懂的错）。"""
    _as_android(monkeypatch, True)
    missing = tmp_path / "not-there.apk"

    window._start_update_installer((missing, ""))

    assert opened_urls.urls == []
    assert len(_BoxStub.criticals) == 1
    assert "更新包不存在" in _BoxStub.criticals[0][1]


def test_android_openurl_failure_tells_the_user_what_to_do(
    window, tmp_path, monkeypatch, opened_urls
):
    """调起失败（最常见原因：没授"安装未知应用"）必须说清怎么办。"""
    _as_android(monkeypatch, True)
    opened_urls.result["ok"] = False

    window._start_update_installer((_apk(tmp_path), ""))

    assert len(opened_urls.urls) == 1
    assert len(_BoxStub.criticals) == 1
    _title, text = _BoxStub.criticals[0]
    assert "安装未知应用" in text, "要给出可操作的下一步"
    assert "文件管理器" in text, "要给出手动安装的退路"


def test_payload_may_be_a_bare_path(window, tmp_path, monkeypatch, opened_urls):
    """载荷容错：历史/异常情况下可能只传了路径（无元组）。"""
    _as_android(monkeypatch, True)
    package = _apk(tmp_path)

    window._start_update_installer(package)

    assert opened_urls.urls == [package.as_uri()]

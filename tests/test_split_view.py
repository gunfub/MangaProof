# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""左右并排对比（2026-10，第二种对比形态）回归。

覆盖：
- ``SplitLayout`` 几何：固定 50/50、4px 分隔、命中、适配尺寸；
- ``fit_zoom`` 的 contain 语义（单栏 / 窄栏都完整装下）；
- Viewer 分栏像素：左 bg（原图）/ 右 merged（成品图）、分隔条颜色；
- 角标计时：切分屏 3 秒、无任务常显、同任务换文件不重复；
- 无 bg 回退 merged；阶段 B 注入 bg 后左栏切换；
- 按栏滚轮锚点、拖框只截中线方向；
- compare_form 设置读写、设置页置灰、主窗口模式接线与设置保存后的重定位。

运行：QT_QPA_PLATFORM=offscreen uv run python -m pytest tests/test_split_view.py -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QImage, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication, QDialog

import mangaproof.ui.main_window as mw
from mangaproof.camera.zoom import fit_zoom
from mangaproof.config.settings import (
    DEFAULT_COMPARE_FORM,
    SettingsManager,
)
from mangaproof.ui.main_window import MainWindow
from mangaproof.ui.split_view import GAP_PX, SplitLayout
from mangaproof.ui.viewer_widget import (
    SOURCE_BG,
    SOURCE_MERGED,
    ViewerWidget,
    numpy_to_qimage,
)

app = QApplication.instance() or QApplication([])


class FakeDoc:
    """只够 Viewer 绘制/定位用：merged 绿、bg 红且可裁剪偏移。"""

    def __init__(
        self,
        size=(1600, 2400),
        bg_size=(1200, 2000),
        bg_offset=(200, 300),
        *,
        with_bg: bool = True,
        bg_available: bool = True,
    ):
        self.size = size
        self._merged = np.zeros((size[1], size[0], 4), dtype=np.uint8)
        self._merged[..., 1] = 255      # RGBA：绿
        self._merged[..., 3] = 255
        self._bg = None
        self._has_bg = with_bg
        if with_bg and bg_available:
            arr = np.zeros((bg_size[1], bg_size[0], 4), dtype=np.uint8)
            arr[..., 0] = 255           # RGBA：红
            arr[..., 3] = 255
            self._bg = (bg_offset[0], bg_offset[1], arr)

    def merged_np(self):
        return self._merged

    def bg_image(self):
        return self._bg

    def has_bg(self):
        return self._has_bg


def _viewer(doc=None, width: int = 1200, height: int = 800) -> ViewerWidget:
    v = ViewerWidget()
    v.resize(width, height)
    v.show()
    if doc is not None:
        v.set_document(doc)
    app.processEvents()
    return v


def _rgb(viewer: ViewerWidget):
    img = viewer.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
    buf = np.frombuffer(img.constBits(), dtype=np.uint8)
    buf = buf.reshape(img.height(), img.bytesPerLine() // 4, 4)[:, : img.width(), :]
    # 必须 copy：QImage 在函数返回后释放，返回视图会读到已释放内存（段错误）
    return buf[:, :, 2].copy(), buf[:, :, 1].copy(), buf[:, :, 0].copy()  # RGB32 内存序 BGRA


def _wait(seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


# --------------------------------------------------------------------------- #
# 几何 / 拟合
# --------------------------------------------------------------------------- #


def test_split_layout_geometry():
    layout = SplitLayout()
    panes = layout.panes(1200, 800, SOURCE_BG, SOURCE_MERGED)
    assert [p.size_px() for p in panes] == [(598, 800), (598, 800)]
    assert layout.gap == GAP_PX == 4
    assert layout.fit_size(1200, 800) == (598, 800)
    divider = layout.divider(1200, 800)
    assert (divider.x(), divider.y(), divider.width(), divider.height()) == (598, 0, 4, 800)

    # 命中：左栏 / 4px 分隔条 / 右栏
    assert SplitLayout.pane_at(QPointF(0, 10), panes).source == SOURCE_BG
    assert SplitLayout.pane_at(QPointF(597, 10), panes).source == SOURCE_BG
    assert SplitLayout.pane_at(QPointF(598, 10), panes) is None
    assert SplitLayout.pane_at(QPointF(601, 10), panes) is None
    assert SplitLayout.pane_at(QPointF(602, 10), panes).source == SOURCE_MERGED

    # 奇数宽度：左栏是较小侧，右栏多 1px，总数仍等于窗口宽
    odd = layout.panes(1201, 800, SOURCE_BG, SOURCE_MERGED)
    assert [p.size_px()[0] for p in odd] == [598, 599]
    assert odd[1].rect.x() == 602
    assert odd[1].rect.right() == 1201


def test_fit_zoom_contain():
    """Q2：单栏/窄栏都用 contain；100% = 宽高都能完整装下。"""
    # 1600×2400 放进 448×800 半栏 → 宽度是更紧的一侧
    assert fit_zoom((0, 0, 1600, 2400), (448, 800), 1.0) == pytest.approx(0.28)
    # 放进 900×800 整窗 → 高度是更紧的一侧（与旧算法相同）
    assert fit_zoom((0, 0, 1600, 2400), (900, 800), 1.0) == pytest.approx(1.0 / 3.0)
    # 放进 598×800 半栏 → 高度受限（旧算法也相同）
    assert fit_zoom((0, 0, 1600, 2400), (598, 800), 1.0) == pytest.approx(1.0 / 3.0)
    # 60% 比例乘在 contain 上
    expect = 0.6 * min(448 / 400.0, 800 / 300.0)
    assert fit_zoom((0, 0, 400, 300), (448, 800), 0.6) == pytest.approx(expect)
    # 退化输入
    assert fit_zoom(None, (100, 100), 0.6) is None
    assert fit_zoom((0, 0, 0, 10), (100, 100), 0.6) is None


# --------------------------------------------------------------------------- #
# Viewer：像素 / 角标 / 回退
# --------------------------------------------------------------------------- #


def test_split_paint_pixels_and_divider():
    doc = FakeDoc()
    v = _viewer(doc)
    v.set_split_mode(True)
    v.recenter_on_rect((0, 0, *doc.size), 1.0)
    app.processEvents()

    r, g, b = _rgb(v)
    # 左栏：bg 红（裁剪图 1200×2000 @ 偏移 (200,300)，fit 0.333）
    assert r[400, 300] > 200 and g[400, 300] < 60 and b[400, 300] < 60
    # 右栏：merged 绿
    assert g[400, 900] > 200 and r[400, 900] < 60 and b[400, 900] < 60
    # 4px 分隔条：COLOR_BORDER #4a4e54
    c = v.grab().toImage().pixelColor(599, 400)
    assert (c.red(), c.green(), c.blue()) == (0x4A, 0x4E, 0x54)


def test_split_labels_timer_and_empty_state():
    v = _viewer(FakeDoc())
    v.set_split_mode(True)
    app.processEvents()
    assert v.labels_visible, "切到并排时角标应出现"

    v.show_labels(60)
    _wait(0.25)
    assert not v.labels_visible, "到时后角标应消失"

    # 无任务：角标常显（Q5）
    v.set_document(None)
    assert v.labels_visible
    # 无任务 → 有任务：重新显示 3 秒（Q3）
    v.set_document(FakeDoc())
    assert v.labels_visible

    # 退出分屏：角标不再显示
    v.set_split_mode(False)
    assert not v.labels_visible


def test_split_left_source_fallback_and_injection():
    # 没有 bg 图层 / 0 图层：左栏回退 merged（E1/E4/E5）
    v = _viewer(FakeDoc(with_bg=False))
    v.set_split_mode(True)
    assert [p.source for p in v._panes()] == [SOURCE_MERGED, SOURCE_MERGED]

    # 阶段 B 注入 bg 后，左栏切回原图（不触发 UI 线程提取）
    doc = v.document
    qimg = numpy_to_qimage(np.full((200, 200, 4), 128, dtype=np.uint8))
    v.inject_qimage(doc, SOURCE_BG, qimg)
    assert v._panes()[0].source == SOURCE_BG

    # 尝试过提取但没有 bg 像素：仍回退 merged
    v.set_document(FakeDoc(with_bg=True, bg_available=False))
    assert v._panes()[0].source == SOURCE_MERGED


def test_split_exit_restores_merged():
    v = _viewer(FakeDoc())
    v.set_split_mode(True)
    v.set_source(SOURCE_BG)
    assert v.split_mode
    v.set_split_mode(False)
    assert v.source == SOURCE_MERGED


# --------------------------------------------------------------------------- #
# Viewer：滚轮 / 拖框
# --------------------------------------------------------------------------- #


def test_split_wheel_anchor_is_pane_local():
    v = _viewer(FakeDoc())
    v.set_split_mode(True)
    v.camera.zoom = 0.5
    v.camera.center_on(800.0, 1200.0)
    panes = v._panes()
    left = panes[0]
    vw, vh = left.size_px()
    sx, sy = vw * 0.3, vh * 0.4

    before = v._pane_world(left, sx, sy)
    event = QWheelEvent(
        QPointF(sx, sy), QPointF(sx, sy), QPoint(0, 0), QPoint(0, 120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
        Qt.ScrollPhase.NoScrollPhase, False,
    )
    v.wheelEvent(event)
    after = v._pane_world(left, sx, sy)
    assert after[0] == pytest.approx(before[0], abs=1e-6)
    assert after[1] == pytest.approx(before[1], abs=1e-6)

    # 两栏同宽、共享相机：同一局部坐标指向同一世界点
    right = v._panes()[1]
    right_world = v._pane_world(right, right.rect.x() + sx, sy)
    assert right_world[0] == pytest.approx(after[0], abs=1e-6)
    assert right_world[1] == pytest.approx(after[1], abs=1e-6)


def test_split_drag_clamps_x_at_divider():
    v = _viewer(FakeDoc())
    v.set_split_mode(True)
    v.set_redraw_mode(True)
    v.camera.zoom = 0.5
    v.camera.center_on(800.0, 1200.0)
    lw = v._split_layout.fit_size(v.width(), v.height())[0]

    rects = []
    v.rect_drawn.connect(lambda x, y, w, h: rects.append((x, y, w, h)))

    press = QMouseEvent(
        QEvent.Type.MouseButtonPress, QPointF(100, 300), QPointF(100, 300),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    v.mousePressEvent(press)
    move = QMouseEvent(
        QEvent.Type.MouseMove, QPointF(lw + 250, 500), QPointF(lw + 250, 500),
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    v.mouseMoveEvent(move)
    release = QMouseEvent(
        QEvent.Type.MouseButtonRelease, QPointF(lw + 250, 500),
        QPointF(lw + 250, 500), Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
    )
    v.mouseReleaseEvent(release)

    assert rects, "拖框应发出 rect_drawn"
    x, y, w, h = rects[0]
    pane_right_world = v._camera.screen_to_world(lw, 0, lw, v.height())[0]
    assert x + w == pytest.approx(pane_right_world, abs=1e-6), "越过中线应截在起手栏右缘"
    assert w > 0 and h > 0


# --------------------------------------------------------------------------- #
# 设置 / 主窗口接线
# --------------------------------------------------------------------------- #


def test_compare_form_settings_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "settings.json"
        sm = SettingsManager(p)
        assert sm.settings.compare_form == DEFAULT_COMPARE_FORM == "flash"

        sm.settings.compare_form = "split"
        sm.save()
        assert SettingsManager(p).settings.compare_form == "split"

        p.write_text(json.dumps({"compare_form": "bogus"}), encoding="utf-8")
        assert SettingsManager(p).settings.compare_form == DEFAULT_COMPARE_FORM

        p.write_text(json.dumps({"layer_display_ratio": 0.5}), encoding="utf-8")
        assert SettingsManager(p).settings.compare_form == "flash"


def test_settings_dialog_split_form_greys_flash_rows():
    from mangaproof.config.settings import Settings
    from mangaproof.ui.settings_dialog import SettingsDialog

    dlg = SettingsDialog(Settings(), None)
    try:
        split_idx = dlg.compare_form_combo.findData("split")
        assert split_idx >= 0
        dlg.compare_form_combo.setCurrentIndex(split_idx)
        assert not dlg.compare_mode_combo.isEnabled()
        assert not dlg.compare_speed_combo.isEnabled()

        # 回到闪切：模式可用；手动挡只灰速度
        dlg.compare_form_combo.setCurrentIndex(dlg.compare_form_combo.findData("flash"))
        assert dlg.compare_mode_combo.isEnabled()
        dlg.compare_mode_combo.setCurrentIndex(
            dlg.compare_mode_combo.findData("manual")
        )
        assert not dlg.compare_speed_combo.isEnabled()
        dlg.compare_mode_combo.setCurrentIndex(
            dlg.compare_mode_combo.findData("auto")
        )
        assert dlg.compare_speed_combo.isEnabled()

        # 复位 → 闪切；应用 → 写回 settings
        dlg.compare_form_combo.setCurrentIndex(split_idx)
        dlg._reset_defaults()
        assert dlg.compare_form_combo.currentData() == "flash"
        dlg.compare_form_combo.setCurrentIndex(split_idx)
        s = Settings()
        dlg.apply_to(s)
        assert s.compare_form == "split"
    finally:
        dlg.close()
        app.processEvents()


def test_main_window_split_wiring_and_space():
    with tempfile.TemporaryDirectory() as tmp:
        sm = SettingsManager(Path(tmp) / "settings.json")
        sm.settings.compare_form = "split"
        w = MainWindow(sm)
        try:
            assert w.viewer.split_mode is True
            assert "对比" not in w.hint_label.text(), "分屏下不应再提示 Space 对比"

            # Space 静默无效（A3/F6）
            w.toggle_compare()
            assert not w._compare.is_running
            assert w.viewer.split_mode

            # 假 task 让 _refresh_enabled_state 走 has_task 分支
            w.task = SimpleNamespace(files=[])
            w._refresh_enabled_state()
            assert not w.action_compare.isEnabled(), "并排下工具栏仅置灰"

            # 切回闪切：按钮恢复、底部提示恢复；再切回并排
            w.settings.compare_form = "flash"
            w._apply_compare_settings()
            assert w.viewer.split_mode is False
            assert w.action_compare.isEnabled()
            assert "对比" in w.hint_label.text()

            w.settings.compare_form = "split"
            w._apply_compare_settings()
            assert w.viewer.split_mode is True
            assert not w.action_compare.isEnabled()
            assert "对比" not in w.hint_label.text()
        finally:
            w.task = None
            w.close()
            w.deleteLater()
            app.processEvents()


def test_open_settings_form_change_repositions():
    """Q8：设置页把形式切到并排并保存后，按 auto_locate 规则重算一次相机。"""

    class _FakeDialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, settings, parent=None, intro=""):
            self._settings = settings

        def exec(self):  # noqa: A003 - 与 QDialog 接口一致
            return QDialog.DialogCode.Accepted

        def apply_to(self, settings):
            settings.compare_form = "split"

    with tempfile.TemporaryDirectory() as tmp:
        sm = SettingsManager(Path(tmp) / "settings.json")
        w = MainWindow(sm)
        calls = []
        w.recenter_current_layer = lambda: calls.append("layer")
        w._fit_page_view = lambda: calls.append("page")
        try:
            w.settings.auto_locate = True
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(mw, "SettingsDialog", _FakeDialog)
                w.open_settings_dialog()
            assert w.settings.compare_form == "split"
            assert w.viewer.split_mode is True
            assert calls == ["layer"], "auto_locate 开 → 当前图层适配半栏"
            assert "左原图" in w.statusBar().currentMessage()
            assert (
                SettingsManager(Path(tmp) / "settings.json").settings.compare_form
                == "split"
            ), "设置页正常保存后应落盘"

            # auto_locate 关 → 整页适配半栏
            calls.clear()
            w.settings.compare_form = "flash"
            w._apply_compare_settings()
            w.settings.auto_locate = False
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(mw, "SettingsDialog", _FakeDialog)
                w.open_settings_dialog()
            assert calls == ["page"], "auto_locate 关 → 整页适配半栏"
        finally:
            w.task = None
            w.close()
            w.deleteLater()
            app.processEvents()

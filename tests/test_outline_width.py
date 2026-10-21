# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""蓝色虚线边界框线宽（1～6px，默认 3px）与红框线宽（固定 6px）。

2026-10-21 需求方定稿：

- 红框线宽 3 → **6**（没有设置项）；
- 蓝框线宽加下拉：**1 / 2 / 3 / 4 / 5 / 6 px**，步进 1px；
  - 最小 1px = 旧版原始粗细；
  - 最大 6px = 与问题红框同宽（tooltip 里说明，选项文字保持朴素）；
  - **默认 3px**；
- 关闭「显示当前图层的蓝色虚线边界框」时，线宽下拉**置灰**（值保留）；
- 设置文件里出现三档之外的任何值（0 / 7 / 2.5 / 非数字）**一律回落到默认 3px**。

这里钉住四件事：

1. 常量：档位、默认值，以及「蓝框上限 == 红框线宽」这条跨模块约定
   （红框常量在 ui/viewer_widget.py，config 不能 import ui，所以用守卫用例）；
2. 设置：保存往返、缺键默认、非法值回落；
3. 设置页：六档选项、关闭蓝框时置灰、应用与恢复默认；
4. 绘制：按像素测量线宽（顶边厚度），并确认红框确实用 6px 画。

运行：QT_QPA_PLATFORM=offscreen uv run python -m pytest tests/test_outline_width.py -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mangaproof.config.settings import (  # noqa: E402
    DEFAULT_OUTLINE_WIDTH,
    OUTLINE_WIDTH_CHOICES,
    OUTLINE_WIDTH_MAX,
    OUTLINE_WIDTH_MIN,
    OUTLINE_WIDTH_STEP,
    Settings,
    SettingsManager,
)
from mangaproof.review.issue import Issue  # noqa: E402
from mangaproof.ui import main_window as mw_mod  # noqa: E402
from mangaproof.ui import viewer_widget as vw_mod  # noqa: E402
from mangaproof.ui.main_window import MainWindow  # noqa: E402
from mangaproof.ui.settings_dialog import SettingsDialog  # noqa: E402
from mangaproof.ui.viewer_widget import ViewerWidget  # noqa: E402

app = QApplication.instance() or QApplication([])


class _FakeDoc:
    """仅够 Viewer 绘制：纯灰 merged 画布，无 bg。"""

    def __init__(self, w: int, h: int):
        self._arr = np.full((h, w, 4), 128, dtype=np.uint8)
        self.layers: list = []

    def merged_np(self):
        return self._arr

    def bg_image(self):
        return None


def _grab(viewer: ViewerWidget) -> tuple:
    """抓一帧并按 BGRA 取出三个通道。

    必须 `.copy()`：`constBits()` 指向 QImage 自己的内存，QImage 一旦离开
    作用域，返回的 numpy 视图就成了悬空指针（实测会直接段错误）。
    """
    img: QImage = viewer.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
    buf = np.frombuffer(img.constBits(), dtype=np.uint8)
    buf = buf.reshape(img.height(), img.bytesPerLine() // 4, 4)[:, : img.width(), :].copy()
    return buf[:, :, 2], buf[:, :, 1], buf[:, :, 0]     # RGB32 内存序为 BGRA


def _blue_mask(viewer: ViewerWidget) -> np.ndarray:
    r, g, b = _grab(viewer)
    return (b > r + 40) & (b > 120)


def _red_mask(viewer: ViewerWidget) -> np.ndarray:
    r, g, b = _grab(viewer)
    return (r > b + 60) & (r > 150)


def _edge_thickness(mask: np.ndarray, x0: int, x1: int, y0: int, y1: int) -> int:
    """在给定列区间里取「竖向连续像素」的最大长度 = 横线的厚度。

    只看顶边/底边的中段（x0..x1 避开左右两条竖边），竖边自身的虚线长段
    不会混进来。
    """
    best = 0
    for x in range(x0, x1):
        run = 0
        for y in range(y0, y1):
            run = run + 1 if mask[y, x] else 0
            best = max(best, run)
    return best


@pytest.fixture()
def viewer():
    v = ViewerWidget()
    v.set_document(_FakeDoc(400, 600))
    v.resize(800, 600)
    # 视口 800x600、center=(200,300)、zoom=1 → 世界坐标 1:1 映射到屏幕
    v.camera.center_on(200.0, 300.0)
    v.camera.zoom = 1.0
    v.set_layer_outline((100, 200, 300, 400))     # 屏幕矩形 (300,200)-(500,400)
    app.processEvents()
    yield v
    v.deleteLater()


# --------------------------------------------------------------------------- #
# ① 常量与跨模块约定
# --------------------------------------------------------------------------- #


def test_choices_and_default():
    assert OUTLINE_WIDTH_MIN == 1.0
    assert OUTLINE_WIDTH_MAX == 6.0
    assert OUTLINE_WIDTH_STEP == 1.0
    assert OUTLINE_WIDTH_CHOICES == (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
    assert DEFAULT_OUTLINE_WIDTH == 3.0
    assert DEFAULT_OUTLINE_WIDTH in OUTLINE_WIDTH_CHOICES


def test_outline_max_equals_issue_rect_width():
    """蓝框线宽上限必须与问题红框线宽一致（红框固定、没有设置项）。"""
    assert vw_mod.ISSUE_RECT_PEN_WIDTH == 6.0
    assert OUTLINE_WIDTH_MAX == vw_mod.ISSUE_RECT_PEN_WIDTH


# --------------------------------------------------------------------------- #
# ② 设置：往返与回落
# --------------------------------------------------------------------------- #


def _load_with(raw: dict) -> Settings:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "settings.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        return SettingsManager(path).settings


def test_missing_key_uses_default():
    assert _load_with({}).layer_outline_width == DEFAULT_OUTLINE_WIDTH


@pytest.mark.parametrize("bad", [0, -1, 7, 100, 2.5, "abc", None, True])
def test_invalid_values_fall_back_to_default(bad):
    assert _load_with({"layer_outline_width": bad}).layer_outline_width == DEFAULT_OUTLINE_WIDTH


@pytest.mark.parametrize("good", [1, 2, 4, 5, 6, 1.0, 6.0])
def test_valid_values_are_kept(good):
    assert float(_load_with({"layer_outline_width": good}).layer_outline_width) == float(good)


def test_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "settings.json"
        manager = SettingsManager(path)
        assert manager.settings.layer_outline_width == DEFAULT_OUTLINE_WIDTH
        manager.settings.layer_outline_width = 5.0
        manager.save()
        assert json.loads(path.read_text(encoding="utf-8"))["layer_outline_width"] == 5.0
        assert SettingsManager(path).settings.layer_outline_width == 5.0


# --------------------------------------------------------------------------- #
# ③ 设置页
# --------------------------------------------------------------------------- #


def test_dialog_lists_six_widths_and_defaults_to_three():
    dlg = SettingsDialog(Settings())
    try:
        values = [
            dlg.outline_width_combo.itemData(i)
            for i in range(dlg.outline_width_combo.count())
        ]
        texts = [
            dlg.outline_width_combo.itemText(i)
            for i in range(dlg.outline_width_combo.count())
        ]
        assert values == list(OUTLINE_WIDTH_CHOICES)
        assert texts == ["1px", "2px", "3px", "4px", "5px", "6px"]
        assert dlg.outline_width_combo.currentData() == 3.0
        assert dlg.outline_width_combo.isEnabled() is True
        # 与红框同宽这条只写在 tooltip 里，选项文字保持朴素
        assert "6px 与问题红框同宽" in dlg.outline_width_combo.toolTip()
    finally:
        dlg.deleteLater()


def test_dialog_greys_out_width_when_outline_disabled():
    dlg = SettingsDialog(Settings())
    try:
        dlg.layer_outline_check.setChecked(False)
        assert dlg.outline_width_combo.isEnabled() is False
        dlg.layer_outline_check.setChecked(True)
        assert dlg.outline_width_combo.isEnabled() is True
    finally:
        dlg.deleteLater()


def test_dialog_apply_and_reset():
    dlg = SettingsDialog(Settings())
    try:
        dlg.outline_width_combo.setCurrentIndex(dlg.outline_width_combo.findData(6.0))
        out = Settings()
        dlg.apply_to(out)
        assert out.layer_outline_width == 6.0

        dlg._reset_defaults()
        assert dlg.outline_width_combo.currentData() == DEFAULT_OUTLINE_WIDTH
        dlg.apply_to(out)
        assert out.layer_outline_width == DEFAULT_OUTLINE_WIDTH
    finally:
        dlg.deleteLater()


# --------------------------------------------------------------------------- #
# ④ 绘制：线宽真的变了
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("width", [1.0, 2.0, 3.0, 6.0])
def test_paint_thickness_matches_width(viewer, width):
    viewer.set_outline_pen_width(width)
    app.processEvents()
    mask = _blue_mask(viewer)
    assert mask.any(), "未画出蓝色虚线框"
    top = _edge_thickness(mask, 320, 480, 190, 210)
    bottom = _edge_thickness(mask, 320, 480, 390, 410)
    assert top == int(width), f"线宽 {width}px 的顶边实测 {top}px"
    assert bottom == int(width), f"线宽 {width}px 的底边实测 {bottom}px"


def test_viewer_setter_falls_back_for_invalid_values(viewer):
    viewer.set_outline_pen_width(0)
    assert viewer.outline_pen_width == vw_mod.DEFAULT_OUTLINE_PEN_WIDTH
    viewer.set_outline_pen_width(-3)
    assert viewer.outline_pen_width == vw_mod.DEFAULT_OUTLINE_PEN_WIDTH
    viewer.set_outline_pen_width("abc")          # type: ignore[arg-type]
    assert viewer.outline_pen_width == vw_mod.DEFAULT_OUTLINE_PEN_WIDTH


def test_issue_rect_is_drawn_six_px_thick(viewer):
    """红框固定 6px：按像素测量一条横边，确认不是旧值 3。"""
    issue = Issue(
        file="001.psd",
        layer_id="0.1",
        layer_name="layer",
        type="居中错误",
        rect=(100.0, 200.0, 200.0, 200.0),     # 屏幕 (300,200)-(500,400)
    )
    viewer.set_issues([issue])
    app.processEvents()
    mask = _red_mask(viewer)
    assert mask.any(), "未画出问题红框"
    top = _edge_thickness(mask, 320, 480, 185, 215)
    assert top == int(vw_mod.ISSUE_RECT_PEN_WIDTH) == 6, f"红框横边实测 {top}px"


# --------------------------------------------------------------------------- #
# ⑤ 主窗口：启动下发 + 设置热应用
# --------------------------------------------------------------------------- #


def test_main_window_pushes_width_at_startup(tmp_path):
    manager = SettingsManager(tmp_path / "settings.json")
    manager.settings.layer_outline_width = 5.0
    window = MainWindow(manager)
    try:
        assert window.viewer.outline_pen_width == 5.0
    finally:
        window.close()
        app.processEvents()


def test_main_window_applies_width_from_settings_dialog(tmp_path):
    """点「确定」后线宽立即下发到 Viewer（无需重启、不需重新定位）。"""

    from PySide6.QtWidgets import QDialog

    class _FakeDialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, settings, parent=None, intro=""):
            self._settings = settings

        def exec(self):
            return QDialog.DialogCode.Accepted

        def apply_to(self, settings):
            settings.layer_outline_width = 6.0

    manager = SettingsManager(tmp_path / "settings.json")
    window = MainWindow(manager)
    try:
        assert window.viewer.outline_pen_width == 3.0
        with patch.object(mw_mod, "SettingsDialog", _FakeDialog):
            window.open_settings_dialog()
        assert window.viewer.outline_pen_width == 6.0
        assert manager.settings.layer_outline_width == 6.0
    finally:
        window.close()
        app.processEvents()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

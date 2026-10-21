# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""图层定位快速路径（「图层定位依据」= PSD 图层原始框）的回归测试。

三条主线：

1. **纯函数**（`camera.centering.position_bounds / position_center / auto_box_rect`）：
   - 视觉模式行为与改造前逐项一致（视觉边界 → 无内容回退 Bounds）；
   - 快速模式直接用 `LayerInfo.bounds`，退化矩形返回 None；
2. **零像素不变量**（本功能存在的理由）：快速模式下调用链上任何一处
   `load_image / load_bounds_image / visual_bounds` 被触发即失败 —— 用绊线
   断言，防止以后"顺手加个更准的补偿"把快速路径变回解析像素；
3. **接线**：主窗口在快速模式下切图层走同步路径（不排后台预热、不弹进度框）、
   蓝色虚线框与自动框选都用原始框、阶段 B 只补背景图且不重复排队；
   切回视觉模式时当前层未预热则走异步预热路径（不冻结 UI）。

平台默认：桌面 = 视觉边界，Android = 快速模式；显式写在 settings.json 里的
值一律尊重（旧配置缺键才落到平台默认）。

运行：QT_QPA_PLATFORM=offscreen uv run python -m pytest tests/test_position_mode.py -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

import mangaproof.config.settings as settings_mod  # noqa: E402
from mangaproof.camera.centering import (  # noqa: E402
    POSITION_FAST,
    POSITION_MODES,
    POSITION_VISUAL,
    auto_box_rect,
    position_bounds,
    position_center,
)
from mangaproof.camera.zoom import fit_zoom  # noqa: E402
from mangaproof.config.settings import (  # noqa: E402
    DEFAULT_POSITION_MODE,
    Settings,
    SettingsManager,
    default_position_mode,
)
from mangaproof.psd.layer_model import LayerInfo  # noqa: E402
from mangaproof.ui.main_window import MainWindow  # noqa: E402
from mangaproof.ui.settings_dialog import SettingsDialog  # noqa: E402
from mangaproof.ui.viewer_widget import ViewerWidget  # noqa: E402

DATA_DIR = Path(__file__).parent / "data" / "chapter01"

app = QApplication.instance() or QApplication([])


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #


def _boom(what: str):
    def _raise(*_args, **_kwargs):
        raise AssertionError(f"快速定位模式不该触碰像素：{what} 被调用了")

    return _raise


def make_layer(
    bounds=(100, 200, 300, 500),
    visual=None,
    *,
    tripwire: bool = False,
    id: str = "0.1",
    name: str = "layer",
) -> LayerInfo:
    """构造 LayerInfo：visual 给定时模拟视觉边界（相对 bbox 原点）。"""
    info = LayerInfo(
        id=id,
        name=name,
        bounds=bounds,
        visible=True,
        layer_type="pixel",
    )
    if tripwire:
        info.image_loader = _boom("image_loader")
        info.bounds_loader = _boom("bounds_loader")
        # 视觉边界也不许被问：直接钉死属性，任何询问都失败
        info.visual_bounds = _boom("visual_bounds")  # type: ignore[assignment]
        return info
    if visual is not None:
        info.visual_bounds = lambda alpha_threshold=0.0, image=None: visual  # type: ignore[assignment]
    else:
        info.visual_bounds = lambda alpha_threshold=0.0, image=None: None  # type: ignore[assignment]
    return info


class _StubDoc:
    """只实现主窗口定位/预加载判定用到的接口。"""

    def __init__(self, layers, *, has_bg: bool = True):
        self.layers = layers
        self.path = Path("/tmp/stub.psd")
        self.all_layers_warmed = False
        self._has_bg = has_bg

    def has_bg(self) -> bool:
        return self._has_bg

    def has_merged(self) -> bool:
        return True

    def bg_layer_id(self):
        return None

    def bg_copy_layer_id(self):
        return None


@pytest.fixture()
def window(tmp_path):
    w = MainWindow(SettingsManager(tmp_path / "settings.json"))
    w.resize(1200, 800)
    w.show()                 # 视口要有真实尺寸，fit_zoom 才不是 None
    app.processEvents()
    yield w
    w.close()
    app.processEvents()


# --------------------------------------------------------------------------- #
# ① 纯函数：两种模式的矩形来源
# --------------------------------------------------------------------------- #


def test_fast_mode_returns_psd_bounds_and_center():
    info = make_layer(bounds=(100, 200, 300, 500), visual=(10, 10, 50, 50))
    assert position_bounds(info, POSITION_FAST) == (100, 200, 300, 500)
    assert position_center(info, POSITION_FAST) == (200.0, 350.0)


def test_visual_mode_keeps_visual_bounds_and_world_offset():
    info = make_layer(bounds=(100, 200, 300, 500), visual=(10, 20, 60, 90))
    # 视觉边界相对 bbox 原点 → 世界坐标要加上 bbox 左上角（与改造前一致）
    assert position_bounds(info, POSITION_VISUAL) == (110, 220, 160, 290)
    assert position_center(info, POSITION_VISUAL) == (135.0, 255.0)


def test_visual_mode_falls_back_to_bounds_when_layer_transparent():
    """需求 §18.1：完全透明的图层回退 Layer Bounds（旧行为不变）。"""
    info = make_layer(bounds=(40, 50, 140, 150), visual=None)
    assert position_bounds(info, POSITION_VISUAL) == (40, 50, 140, 150)


def test_invalid_rect_returns_none_in_both_modes():
    empty = make_layer(bounds=(10, 10, 10, 10))
    assert position_bounds(empty, POSITION_FAST) is None
    assert position_bounds(empty, POSITION_VISUAL) is None
    assert position_center(empty, POSITION_FAST) is None
    assert position_center(empty, POSITION_VISUAL) is None


def test_auto_box_follows_mode():
    info = make_layer(bounds=(100, 200, 300, 500), visual=(0, 0, 100, 200))
    fast = auto_box_rect(info, position_mode=POSITION_FAST)
    visual = auto_box_rect(info, position_mode=POSITION_VISUAL)
    assert fast == (95.0, 195.0, 210.0, 310.0), "原始框 200×300 各外扩 5"
    assert visual == (95.0, 195.0, 110.0, 210.0), "视觉内容 100×200 各外扩 5"
    assert auto_box_rect(make_layer(bounds=(0, 0, 0, 0)), position_mode=POSITION_FAST) is None


def test_fast_mode_never_touches_pixels():
    """零像素不变量：绊线图层在快速模式下必须完全可用。"""
    info = make_layer(bounds=(5, 6, 105, 206), tripwire=True)
    assert position_bounds(info, POSITION_FAST) == (5, 6, 105, 206)
    assert position_center(info, POSITION_FAST) == (55.0, 106.0)
    assert auto_box_rect(info, position_mode=POSITION_FAST) is not None


# --------------------------------------------------------------------------- #
# ② Viewer：快速模式的相机结果就是"原始框居中 + 按原始框缩放"
# --------------------------------------------------------------------------- #


def test_viewer_recenter_fast_centers_on_bounds():
    viewer = ViewerWidget()
    viewer.resize(1000, 800)
    viewer._doc = _StubDoc([])          # recenter 只要求有文档
    info = make_layer(bounds=(100, 200, 500, 700), tripwire=True)   # 400×500
    viewer.recenter_on_layer(info, 0.4, POSITION_FAST)
    assert (viewer.camera.center_x, viewer.camera.center_y) == (300.0, 450.0)
    expected_zoom = fit_zoom((100, 200, 500, 700), (1000, 800), 0.4)
    assert viewer.camera.zoom == pytest.approx(expected_zoom)
    viewer.deleteLater()


def test_viewer_recenter_invalid_rect_keeps_camera():
    viewer = ViewerWidget()
    viewer.resize(1000, 800)
    viewer._doc = _StubDoc([])
    viewer.recenter_on_layer(make_layer(bounds=(0, 0, 0, 0), tripwire=True), 0.4, POSITION_FAST)
    assert (viewer.camera.center_x, viewer.camera.center_y) == (0.0, 0.0), "退化矩形不得把相机拖到 (0,0)"
    assert viewer.camera.zoom == 1.0
    viewer.deleteLater()


# --------------------------------------------------------------------------- #
# ③ 主窗口接线
# --------------------------------------------------------------------------- #


def _bind_stub(window: MainWindow, layers, *, has_bg: bool = True) -> _StubDoc:
    doc = _StubDoc(layers, has_bg=has_bg)
    window._docs["p01.psd"] = doc
    window._current_file = "p01.psd"
    window._current_index = -1
    window.viewer.set_document(doc)      # recenter 只认 viewer 自己持有的文档
    app.processEvents()
    return doc


def test_selection_fast_uses_bounds_for_camera_and_outline(window):
    window.settings.position_mode = POSITION_FAST
    layers = [
        make_layer(bounds=(100, 200, 500, 700), visual=(0, 0, 50, 50), tripwire=True, id="a"),
        make_layer(bounds=(600, 100, 900, 400), visual=(0, 0, 10, 10), tripwire=True, id="b"),
    ]
    _bind_stub(window, layers)
    window._select_layer_internal(1)

    info = layers[1]
    assert window.viewer.layer_outline == info.bounds
    assert (window.viewer.camera.center_x, window.viewer.camera.center_y) == (750.0, 250.0)
    expected = fit_zoom(info.bounds, (window.viewer.width(), window.viewer.height()),
                        window.settings.layer_display_ratio)
    assert window.viewer.camera.zoom == pytest.approx(expected)


def test_selection_visual_mode_outline_is_visual_bounds(window):
    window.settings.position_mode = POSITION_VISUAL
    info = make_layer(bounds=(100, 200, 500, 700), visual=(10, 20, 60, 90), id="a")
    _bind_stub(window, [info])
    window._select_layer_internal(0)
    assert window.viewer.layer_outline == (110, 220, 160, 290)


def test_fast_layer_switch_is_synchronous(window):
    """快速模式切图层不排后台预热、不弹进度框、不写 pending。"""
    window.settings.position_mode = POSITION_FAST
    layers = [
        make_layer(bounds=(0, 0, 100, 100), tripwire=True, id="a"),
        make_layer(bounds=(50, 50, 150, 150), tripwire=True, id="b"),
    ]
    _bind_stub(window, layers)
    window._current_index = 0
    with patch.object(window._preload, "submit_open") as submit, patch.object(
        window, "_show_open_progress"
    ) as progress, patch.object(window, "_mark_dirty"):
        window._request_layer_switch(1)
    submit.assert_not_called()
    progress.assert_not_called()
    assert window._pending_open is None
    assert window._current_index == 1


def test_visual_layer_switch_without_warm_goes_async(window):
    """对照组：视觉模式未预热 → 仍走原来的后台预热路径。"""
    window.settings.position_mode = POSITION_VISUAL
    layers = [
        make_layer(bounds=(0, 0, 100, 100), id="a"),   # visual_bounds → None
    ]
    _bind_stub(window, layers)
    window._current_index = 0
    with patch.object(window._preload, "submit_open") as submit, patch.object(
        window, "_show_open_progress"
    ):
        window._request_layer_switch(0)
    submit.assert_called_once()


def test_extra_warm_spec_skips_layers_in_fast_mode(window):
    window.settings.position_mode = POSITION_FAST
    cold = [make_layer(bounds=(0, 0, 10, 10), id="a")]
    assert window._layers_need_warm() is False
    # 背景图未准备 → 只排"背景图"任务（layer_id 空串 → 预加载线程早退）
    assert window._extra_warm_spec(_StubDoc(cold, has_bg=False)) == ""
    # 背景图已就绪 → 无需再排（不会像"图层未预热"那样反复入队）
    assert window._extra_warm_spec(_StubDoc(cold, has_bg=True)) is None


def test_extra_warm_spec_keeps_layers_in_visual_mode(window):
    window.settings.position_mode = POSITION_VISUAL
    cold = [make_layer(bounds=(0, 0, 10, 10), id="a")]
    warm = [make_layer(bounds=(0, 0, 10, 10), id="b")]
    warm[0].visual_bounds = lambda alpha_threshold=0.0, image=None: (0, 0, 10, 10)  # type: ignore[assignment]
    warm[0]._visual_bounds_computed = True    # 模拟"已经预热过"
    from mangaproof.ui.preloader import WARM_ALL

    assert window._extra_warm_spec(_StubDoc(cold, has_bg=True)) == WARM_ALL
    assert window._extra_warm_spec(_StubDoc(warm, has_bg=True)) is None


def test_switch_back_to_visual_requests_warm_instead_of_freezing(window):
    window.settings.position_mode = POSITION_VISUAL
    info = make_layer(bounds=(0, 0, 100, 100), id="a")     # visual_bounds → None（未预热）
    _bind_stub(window, [info])
    window._current_index = 0
    with patch.object(window, "_request_layer_switch") as request, patch.object(
        window, "_schedule_preloads"
    ), patch.object(window, "_refresh_position_mode_ui"):
        window._on_position_mode_changed(POSITION_FAST)
    request.assert_called_once_with(0)


def test_fast_mode_never_warms_layers_end_to_end(tmp_path):
    """真文档：快速模式打开任务后，没有任何图层被算过视觉边界。"""
    folder = tmp_path / "chapter01"
    folder.mkdir()
    for p in sorted(DATA_DIR.glob("*.psd")):
        (folder / p.name).write_bytes(p.read_bytes())
    w = MainWindow(SettingsManager(tmp_path / "settings.json"))
    w.settings.position_mode = POSITION_FAST
    try:
        with patch.object(QMessageBox, "information", return_value=QMessageBox.StandardButton.Ok):
            w.open_folder(folder)
            deadline = time.time() + 30
            while w.task is None or w._current_file == "":
                app.processEvents()
                if time.time() > deadline:
                    pytest.fail("打开任务超时")
        doc = w.current_doc
        assert doc is not None and doc.layers
        assert not any(i.has_visual_bounds() for i in doc.layers), "快速模式不得预热图层"
        current = doc.layers[w._current_index]
        assert w.viewer.layer_outline == current.bounds
        # 状态栏不再宣称"图层预热完成"
        assert "图层预热" not in w.warmup_label.text() or "不预热" in w.warmup_label.text()
    finally:
        w.close()
        app.processEvents()


# --------------------------------------------------------------------------- #
# ④ 设置：平台默认、持久化、设置页控件
# --------------------------------------------------------------------------- #


def test_default_mode_is_visual_on_desktop(monkeypatch):
    monkeypatch.setattr(settings_mod, "is_android_strict", lambda: False)
    assert default_position_mode() == POSITION_VISUAL
    assert DEFAULT_POSITION_MODE == POSITION_VISUAL
    assert set(POSITION_MODES) == {POSITION_VISUAL, POSITION_FAST}


def test_default_mode_is_fast_on_android(monkeypatch):
    monkeypatch.setattr(settings_mod, "is_android_strict", lambda: True)
    assert default_position_mode() == POSITION_FAST


def test_missing_key_falls_back_to_platform_default(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "settings.json"
        path.write_text(json.dumps({"layer_display_ratio": 0.4}), encoding="utf-8")

        monkeypatch.setattr(settings_mod, "is_android_strict", lambda: True)
        assert SettingsManager(path).settings.position_mode == POSITION_FAST

        monkeypatch.setattr(settings_mod, "is_android_strict", lambda: False)
        assert SettingsManager(path).settings.position_mode == POSITION_VISUAL


def test_explicit_value_wins_on_every_platform(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "settings.json"
        path.write_text(json.dumps({"position_mode": "visual"}), encoding="utf-8")
        monkeypatch.setattr(settings_mod, "is_android_strict", lambda: True)
        assert SettingsManager(path).settings.position_mode == POSITION_VISUAL


def test_invalid_value_falls_back_and_round_trips():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "settings.json"
        path.write_text(json.dumps({"position_mode": "bogus"}), encoding="utf-8")
        manager = SettingsManager(path)
        assert manager.settings.position_mode in POSITION_MODES

        manager.settings.position_mode = POSITION_FAST
        manager.save()
        assert json.loads(path.read_text(encoding="utf-8"))["position_mode"] == "fast"
        assert SettingsManager(path).settings.position_mode == POSITION_FAST


def test_fresh_settings_object_defaults_to_visual():
    """dataclass 默认值与平台无关（平台默认由 SettingsManager / 设置页负责）。"""
    assert Settings().position_mode == POSITION_VISUAL


def test_settings_dialog_position_combo():
    dlg = SettingsDialog(Settings())
    try:
        items = [dlg.position_combo.itemData(i) for i in range(dlg.position_combo.count())]
        assert items == [POSITION_VISUAL, POSITION_FAST]
        assert dlg.position_combo.currentData() == POSITION_VISUAL
        dlg.position_combo.setCurrentIndex(dlg.position_combo.findData(POSITION_FAST))
        out = Settings()
        dlg.apply_to(out)
        assert out.position_mode == POSITION_FAST
    finally:
        dlg.deleteLater()


def test_settings_dialog_reset_uses_platform_default(monkeypatch):
    monkeypatch.setattr(settings_mod, "is_android_strict", lambda: True)
    settings = Settings()
    settings.position_mode = POSITION_VISUAL
    dlg = SettingsDialog(settings)
    try:
        dlg._reset_defaults()
        assert dlg.position_combo.currentData() == POSITION_FAST
    finally:
        dlg.deleteLater()


def test_position_mode_ui_reflects_mode(window):
    window.settings.position_mode = POSITION_FAST
    window._refresh_position_mode_ui()
    assert "PSD 图层原始框" in window.action_recenter.toolTip()
    window.settings.position_mode = POSITION_VISUAL
    window._refresh_position_mode_ui()
    assert "视觉内容边界" in window.action_recenter.toolTip()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

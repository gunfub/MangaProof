# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""「切换图层时自动定位与缩放」开关（默认开）的回归测试。

2026-10 需求方：

- 设置页「缩放与标注显示」加一个总开关；
- **关闭后**：切图层画布保持不动，只用蓝色虚线框标出当前图层；
  翻页 / 打开文件回到「整页视角」（整页居中 + 显示比例 100%）；
  显式「定位当前图层」仍然可用，但只用**全局**比例；
- 关闭期间 `bg / bg 拷贝 使用独立的自动显示比例` 整块（开关 + 两个下拉）
  置灰且不生效——**程序化改勾选也不许把本该灰着的下拉点亮**；
- 整页视角的模板取**画布** `(0,0,W,H)`，不查 bg（`bg_layer_id()` 的回退要解像素）。

四条主线：

1. **设置**：默认 True、缺键默认、保存往返、关闭时 bg 值不丢；
2. **设置页**：双开关置灰真值表、程序化 toggled 不复活、复位刷新、apply_to 保留；
3. **运行期**：切图层不动相机、蓝框照常、显式定位仍生效、bg 比例短路不查图层；
4. **整页视角**：相机 = 画布中心 + fit_zoom(...,1.0)，与 bg / 显示比例无关。

⚠ 不变量（改这里之前先读）：`_layers_need_warm()` 只看定位依据，**不许**看本开关。
蓝框要的图层数据与自动定位同源，跳过预热会让 `_refresh_viewer_outline` 在 UI 线程
解像素（见 tests/test_position_mode.py 的零像素绊线）。

运行：QT_QPA_PLATFORM=offscreen uv run python -m pytest tests/test_auto_locate.py -v
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from mangaproof.camera.centering import (  # noqa: E402
    POSITION_FAST,
    POSITION_VISUAL,
    layer_visual_bounds,
    position_bounds,
)
from mangaproof.camera.zoom import fit_zoom  # noqa: E402
from mangaproof.config.settings import (  # noqa: E402
    DEFAULT_AUTO_LOCATE,
    DEFAULT_BG_DISPLAY_RATIO,
    Settings,
    SettingsManager,
)
from mangaproof.psd.layer_model import LayerInfo  # noqa: E402
from mangaproof.ui.main_window import MainWindow  # noqa: E402
from mangaproof.ui.settings_dialog import SettingsDialog, SettingsRow  # noqa: E402
from mangaproof.ui.viewer_widget import ViewerWidget  # noqa: E402
from mangaproof.ui.widgets import ToggleSwitch  # noqa: E402

DATA_DIR = Path(__file__).parent / "data" / "chapter01"

app = QApplication.instance() or QApplication([])


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #


def make_layer(
    bounds=(100, 200, 300, 500),
    visual=None,
    *,
    id: str = "0.1",
    name: str = "layer",
) -> LayerInfo:
    """构造 LayerInfo：visual 给定时模拟视觉边界（相对 bbox 原点）。"""
    info = LayerInfo(id=id, name=name, bounds=bounds, visible=True, layer_type="pixel")
    if visual is not None:
        info.visual_bounds = lambda alpha_threshold=0.0, image=None: visual  # type: ignore[assignment]
    else:
        info.visual_bounds = lambda alpha_threshold=0.0, image=None: None  # type: ignore[assignment]
    return info


class _StubDoc:
    """只实现主窗口定位 / 整页视角用到的接口。"""

    def __init__(self, layers, *, size=(2000, 3000), bg_id=None):
        self.layers = layers
        self.path = Path("/tmp/stub.psd")
        self.size = size
        self.all_layers_warmed = False
        self._bg_id = bg_id

    def has_bg(self):
        return True

    def has_merged(self):
        return True

    def bg_layer_id(self):
        return self._bg_id

    def resolved_bg_layer_id(self):
        return self._bg_id

    def bg_copy_layer_id(self):
        return None

    def merged_np(self):
        return np.zeros((4, 4, 4), dtype=np.uint8)


def _bind_stub(window: MainWindow, layers, **kwargs) -> _StubDoc:
    doc = _StubDoc(layers, **kwargs)
    window._docs["p01.psd"] = doc
    window._current_file = "p01.psd"
    window._current_index = -1
    window.viewer.set_document(doc)      # recenter 只认 viewer 自己持有的文档
    app.processEvents()
    return doc


@pytest.fixture()
def window(tmp_path):
    w = MainWindow(SettingsManager(tmp_path / "settings.json"))
    w.resize(1200, 800)
    w.show()                 # 视口要有真实尺寸，fit_zoom 才不是 None
    app.processEvents()
    yield w
    w.close()
    app.processEvents()


@pytest.fixture()
def qapp():
    return app


def _dialog(settings: Settings | None = None) -> SettingsDialog:
    return SettingsDialog(settings if settings is not None else Settings())


def _camera(window: MainWindow):
    cam = window.viewer.camera
    return (cam.center_x, cam.center_y, cam.zoom)


# --------------------------------------------------------------------------- #
# ① 设置：默认、缺键、往返、关闭时值不丢
# --------------------------------------------------------------------------- #


def test_default_is_true_and_platform_independent():
    assert DEFAULT_AUTO_LOCATE is True
    assert Settings().auto_locate is True


def test_missing_key_falls_back_to_true(tmp_path):
    """旧版 settings.json 没有这个键 → 默认开，行为与改造前完全一致。"""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"layer_display_ratio": 0.4}), encoding="utf-8")
    assert SettingsManager(path).settings.auto_locate is True


def test_save_round_trip_and_bg_values_survive_auto_off(tmp_path):
    path = tmp_path / "settings.json"
    manager = SettingsManager(path)
    manager.settings.auto_locate = False
    manager.settings.bg_ratio_enabled = True
    manager.settings.bg_display_ratio = 0.9
    manager.save()

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["auto_locate"] is False
    assert saved["bg_ratio_enabled"] is True, "关闭自动定位不得清掉独立比例"
    assert saved["bg_display_ratio"] == 0.9

    reloaded = SettingsManager(path).settings
    assert reloaded.auto_locate is False
    assert (reloaded.bg_ratio_enabled, reloaded.bg_display_ratio) == (True, 0.9)


# --------------------------------------------------------------------------- #
# ② 设置页：双开关置灰联动
# --------------------------------------------------------------------------- #


def test_row_is_pill_switch_and_sits_first(qapp):
    dialog = _dialog()
    try:
        assert isinstance(dialog.auto_locate_check, ToggleSwitch)
        row = dialog.auto_locate_check.parentWidget()
        assert isinstance(row, SettingsRow)
        assert row.label.text() == "切换图层时自动定位与缩放"
        assert dialog.auto_locate_check.accessibleName() == row.label.text()
        # 总开关必须排在本页第一行（bg 块依赖它，见 _update_bg_ratio_enabled）
        rows = dialog.page_rows["display"]
        assert rows.itemAt(0).widget() is row
    finally:
        dialog.deleteLater()


@pytest.mark.parametrize(
    "auto,bg,expect",
    [
        (True, False, (True, False, False)),
        (True, True, (True, True, True)),
        (False, True, (False, False, False)),   # 关自动：连 bg 开关本身一起灰
        (False, False, (False, False, False)),
    ],
)
def test_bg_rows_follow_both_switches(qapp, auto, bg, expect):
    settings = Settings()
    settings.auto_locate = auto
    settings.bg_ratio_enabled = bg
    dialog = _dialog(settings)
    try:
        before = (dialog.bg_ratio_check.isEnabled(), dialog.bg_ratio_combo.isEnabled(),
                  dialog.bg_copy_ratio_combo.isEnabled())
        assert before == expect
        # 切到另一侧再切回来，状态必须可逆
        dialog.auto_locate_check.setChecked(not auto)
        dialog.auto_locate_check.setChecked(auto)
        after = (dialog.bg_ratio_check.isEnabled(), dialog.bg_ratio_combo.isEnabled(),
                 dialog.bg_copy_ratio_combo.isEnabled())
        assert after == expect
    finally:
        dialog.deleteLater()


def test_programmatic_toggle_cannot_revive_greying(qapp):
    """自动定位关闭时，程序化 setChecked 不得把下拉点亮（复位 / 测试都会这么干）。"""
    settings = Settings()
    settings.auto_locate = False
    settings.bg_ratio_enabled = True
    dialog = _dialog(settings)
    try:
        for _ in range(3):
            dialog.bg_ratio_check.setChecked(not dialog.bg_ratio_check.isChecked())
            assert not dialog.bg_ratio_combo.isEnabled()
            assert not dialog.bg_copy_ratio_combo.isEnabled()
            assert not dialog.bg_ratio_check.isEnabled()
        # 行文字跟着置灰（SettingsRow 的 EnabledChange 联动）
        assert not dialog.bg_ratio_combo.parentWidget().label.isEnabled()
        assert not dialog.bg_ratio_check.parentWidget().label.isEnabled()
    finally:
        dialog.deleteLater()


def test_reset_defaults_refreshes_greying(qapp):
    settings = Settings()
    settings.auto_locate = False
    settings.bg_ratio_enabled = True
    dialog = _dialog(settings)
    try:
        dialog._reset_defaults()
        assert dialog.auto_locate_check.isChecked() is DEFAULT_AUTO_LOCATE
        assert dialog.bg_ratio_check.isChecked() is False
        # 复位到「自动定位开 + bg 关」：开关可用、两个下拉仍灰
        assert dialog.bg_ratio_check.isEnabled()
        assert not dialog.bg_ratio_combo.isEnabled()
        assert not dialog.bg_copy_ratio_combo.isEnabled()
        assert dialog.bg_ratio_combo.currentData() == DEFAULT_BG_DISPLAY_RATIO
    finally:
        dialog.deleteLater()


def test_apply_to_keeps_greying_values_and_writes_auto(qapp):
    """置灰的控件照样读值：关掉自动定位不得把用户填的独立比例擦掉。"""
    settings = Settings()
    settings.bg_ratio_enabled = True
    settings.bg_display_ratio = 0.9
    dialog = _dialog(settings)
    try:
        dialog.auto_locate_check.setChecked(False)
        assert not dialog.bg_ratio_check.isEnabled()

        out = Settings()
        dialog.apply_to(out)
        assert out.auto_locate is False
        assert out.bg_ratio_enabled is True
        assert out.bg_display_ratio == pytest.approx(0.9)

        dialog.auto_locate_check.setChecked(True)
        out2 = Settings()
        dialog.apply_to(out2)
        assert out2.auto_locate is True
    finally:
        dialog.deleteLater()


# --------------------------------------------------------------------------- #
# ③ 运行期：切图层不动相机，蓝框照常
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("mode", [POSITION_VISUAL, POSITION_FAST])
def test_layer_switch_keeps_camera_and_updates_outline(window, mode):
    window.settings.auto_locate = False
    window.settings.show_layer_outline = True
    window.settings.position_mode = mode
    layers = [
        make_layer(bounds=(100, 200, 500, 700), visual=(0, 0, 50, 50), id="a"),
        make_layer(bounds=(600, 100, 900, 400), visual=(10, 10, 90, 90), id="b"),
    ]
    _bind_stub(window, layers)
    window._select_layer_internal(0)             # 先选中 a（关闭状态下不动相机）
    before = _camera(window)
    assert before == (0.0, 0.0, 1.0), "关闭自动定位后首次选择也不得挪相机"

    window._select_layer_internal(1)
    assert _camera(window) == before, "切图层不得移动相机"
    # 蓝框照常更新（fast 模式即原始框；visual 模式见下一个用例）
    if mode == POSITION_FAST:
        assert window.viewer.layer_outline == layers[1].bounds


def test_layer_switch_visual_keeps_outline_despite_no_recenter(window):
    """短路不许偷走蓝框数据：visual 模式下 outline 仍是视觉边界。"""
    window.settings.auto_locate = False
    window.settings.show_layer_outline = True
    window.settings.position_mode = POSITION_VISUAL
    info = make_layer(bounds=(100, 200, 500, 700), visual=(10, 20, 60, 90), id="a")
    _bind_stub(window, [info])

    window._select_layer_internal(0)
    assert window.viewer.layer_outline == (110, 220, 160, 290)
    assert _camera(window) == (0.0, 0.0, 1.0)


def test_auto_on_still_recenters_as_before(window):
    """默认开：切图层照旧居中 + 按比例缩放（回归基线）。"""
    window.settings.auto_locate = True
    window.settings.layer_display_ratio = 0.4
    window.settings.position_mode = POSITION_FAST
    layers = [make_layer(bounds=(100, 200, 500, 700), id="a")]
    _bind_stub(window, layers)

    window._select_layer_internal(0)
    info = layers[0]
    assert (window.viewer.camera.center_x, window.viewer.camera.center_y) == (300.0, 450.0)
    expected = fit_zoom(info.bounds, (window.viewer.width(), window.viewer.height()), 0.4)
    assert window.viewer.camera.zoom == pytest.approx(expected)


def test_explicit_recenter_still_works_when_auto_off(window):
    window.settings.auto_locate = False
    window.settings.layer_display_ratio = 0.5
    window.settings.position_mode = POSITION_FAST
    info = make_layer(bounds=(100, 200, 500, 700), id="a")
    _bind_stub(window, [info])

    window._select_layer_internal(0)
    assert _camera(window) == (0.0, 0.0, 1.0)

    window.recenter_current_layer()          # 显式动作不受开关影响
    assert (window.viewer.camera.center_x, window.viewer.camera.center_y) == (300.0, 450.0)
    expected = fit_zoom(info.bounds, (window.viewer.width(), window.viewer.height()), 0.5)
    assert window.viewer.camera.zoom == pytest.approx(expected)


def test_ratio_change_does_not_move_camera_when_auto_off(window):
    window.settings.auto_locate = False
    window.settings.layer_display_ratio = 0.4
    window.settings.position_mode = POSITION_FAST
    info = make_layer(bounds=(100, 200, 500, 700), id="a")
    _bind_stub(window, [info])
    window._select_layer_internal(0)
    before = _camera(window)

    idx = window.ratio_combo.findData(0.8)
    window.ratio_combo.setCurrentIndex(idx)
    app.processEvents()
    assert window.settings.layer_display_ratio == pytest.approx(0.8)
    assert _camera(window) == before, "关闭自动定位时改比例只存值，不挪画布"


# --------------------------------------------------------------------------- #
# ④ bg 独立比例：关闭期间不生效，且一次图层查询都不做
# --------------------------------------------------------------------------- #


class _CountingDoc:
    """只实现 _effective_display_ratio 需要的接口，统计图层查询次数。"""

    def __init__(self):
        self.layers = [
            type("L", (), {"id": "bg-id", "name": "bg"})(),
            type("L", (), {"id": "text-id", "name": "文字层"})(),
        ]
        self.bg_queries = 0
        self.copy_queries = 0

    def bg_layer_id(self):
        self.bg_queries += 1
        return "bg-id"

    def bg_copy_layer_id(self):
        self.copy_queries += 1
        return None


def test_auto_off_forces_global_ratio_and_skips_bg_lookup(window):
    doc = _CountingDoc()
    window._docs["fake.psd"] = doc
    window._current_file = "fake.psd"
    window.settings.auto_locate = False
    window.settings.bg_ratio_enabled = True      # 故意打开：仍必须不生效
    window.settings.layer_display_ratio = 0.6
    window.settings.bg_display_ratio = 1.0

    by_id = {layer.id: layer for layer in doc.layers}
    assert window._effective_display_ratio(by_id["bg-id"]) == 0.6
    assert window._effective_display_ratio(by_id["text-id"]) == 0.6
    assert doc.bg_queries == 0 and doc.copy_queries == 0, "关闭期间不得查 bg id"

    window.settings.auto_locate = True           # 打开即恢复独立比例
    assert window._effective_display_ratio(by_id["bg-id"]) == 1.0
    assert doc.bg_queries == 1


# --------------------------------------------------------------------------- #
# ⑤ 整页视角：画布模板 + 100%
# --------------------------------------------------------------------------- #


def test_fit_page_view_centers_canvas_at_full_ratio(window):
    doc = _StubDoc([make_layer(id="a")], size=(2000, 3000), bg_id="bg-id")
    window._docs["p01.psd"] = doc
    window._current_file = "p01.psd"
    window.viewer.set_document(doc)
    app.processEvents()

    window._fit_page_view()
    vw, vh = window.viewer.width(), window.viewer.height()
    assert (window.viewer.camera.center_x, window.viewer.camera.center_y) == (1000.0, 1500.0)
    assert window.viewer.camera.zoom == pytest.approx(
        fit_zoom((0, 0, 2000, 3000), (vw, vh), 1.0)
    )
    # 与显示比例设置无关：哪怕全局比例是 40%，整页视角仍是 100%
    window.settings.layer_display_ratio = 0.4
    window._fit_page_view()
    assert window.viewer.camera.zoom == pytest.approx(
        fit_zoom((0, 0, 2000, 3000), (vw, vh), 1.0)
    )


def test_fit_page_view_never_queries_bg(window):
    """模板取画布：不查 bg（它的回退分支要解像素）。"""
    doc = _CountingDoc()
    doc.size = (1200, 1600)
    window._docs["fake.psd"] = doc
    window._current_file = "fake.psd"
    window.viewer.set_document(doc)

    window._fit_page_view()
    assert doc.bg_queries == 0 and doc.copy_queries == 0
    assert (window.viewer.camera.center_x, window.viewer.camera.center_y) == (600.0, 800.0)


def test_fit_page_view_ignores_degenerate_canvas(window):
    doc = _StubDoc([make_layer(id="a")], size=(0, 0))
    window._docs["p01.psd"] = doc
    window._current_file = "p01.psd"
    window.viewer.set_document(doc)

    window._fit_page_view()
    assert _camera(window) == (0.0, 0.0, 1.0), "退化画布必须保持当前 Camera"


def test_reposition_after_layout_branches(window):
    """打开任务后的补定位：开着 → 图层定位；关着 → 整页视角。"""
    doc = _StubDoc([make_layer(bounds=(100, 200, 500, 700), id="a")], size=(2000, 3000))
    window._docs["p01.psd"] = doc
    window._current_file = "p01.psd"
    window._current_index = 0
    window.viewer.set_document(doc)
    window.settings.position_mode = POSITION_FAST
    window.settings.layer_display_ratio = 0.5

    window.settings.auto_locate = False
    window._reposition_after_layout()
    assert (window.viewer.camera.center_x, window.viewer.camera.center_y) == (1000.0, 1500.0)

    window.settings.auto_locate = True
    window._reposition_after_layout()
    assert (window.viewer.camera.center_x, window.viewer.camera.center_y) == (300.0, 450.0)


def test_outline_data_not_stolen_from_warm_path(window):
    """不变量：预热判定只看定位依据，开关关闭也不许把它改成"不用预热"。"""
    window.settings.auto_locate = False
    window.settings.position_mode = POSITION_VISUAL
    info = make_layer(bounds=(100, 200, 500, 700), id="a")
    _bind_stub(window, [info])

    assert window._layers_need_warm() is True, "蓝框仍要视觉边界，预热不能跳过"
    assert window._layer_ready(info) is False, "未预热 → 仍走异步路径（不在 UI 线程解像素）"

    with patch.object(window._preload, "submit_open") as submit, patch.object(
        window, "_show_open_progress"
    ):
        window._request_layer_switch(0)
    submit.assert_called_once()


def test_recenter_action_tooltip_reports_auto_off(window):
    window.settings.auto_locate = False
    window._refresh_position_mode_ui()
    assert "自动定位：已关闭" in window.action_recenter.toolTip()

    window.settings.auto_locate = True
    window._refresh_position_mode_ui()
    assert "自动定位：已关闭" not in window.action_recenter.toolTip()


def test_viewer_recenter_on_rect_matches_layer_path():
    """相机数学只有一个实现：矩形入口与图层入口结果一致。"""
    viewer = ViewerWidget()
    viewer.resize(1000, 800)
    viewer._doc = _StubDoc([])
    info = make_layer(bounds=(100, 200, 500, 700))     # 400×500

    viewer.recenter_on_rect(info.bounds, 0.4)
    by_rect = (viewer.camera.center_x, viewer.camera.center_y, viewer.camera.zoom)

    viewer.recenter_on_rect((0, 0, 10, 10), 0.4)       # 先挪走
    viewer.recenter_on_layer(info, 0.4, POSITION_FAST)
    by_layer = (viewer.camera.center_x, viewer.camera.center_y, viewer.camera.zoom)

    assert by_rect == by_layer
    viewer.deleteLater()


# --------------------------------------------------------------------------- #
# ⑥ 端到端：真 PSD 上"打开 → 整页视角 → 切图层不动"
# --------------------------------------------------------------------------- #


def _wait_for_task(window: MainWindow, timeout_s: float = 60.0) -> None:
    deadline = time.time() + timeout_s
    while (window.task is None or window._current_file == "") and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert window.task is not None, "任务加载超时"
    assert window._current_file != "", "首文件打开超时"


def _wait_layer_settled(window: MainWindow, timeout_s: float = 60.0) -> None:
    deadline = time.time() + timeout_s
    while (
        window._pending_open is not None or window._open_dialog is not None
    ) and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()


def test_e2e_auto_off_opens_whole_page_then_keeps_camera(tmp_path):
    folder = tmp_path / "chapter01"
    folder.mkdir()
    for p in sorted(DATA_DIR.glob("*.psd")):
        shutil.copy2(p, folder / p.name)

    window = MainWindow(SettingsManager(tmp_path / "settings.json"))
    window.settings.auto_locate = False
    window.resize(1200, 800)
    window.show()
    app.processEvents()
    try:
        window.open_folder(folder)
        _wait_for_task(window)
        _wait_layer_settled(window)

        doc = window.current_doc
        assert doc is not None and window._current_index >= 0
        W, H = doc.size
        vw, vh = window.viewer.width(), window.viewer.height()
        assert (window.viewer.camera.center_x, window.viewer.camera.center_y) == (
            W / 2.0, H / 2.0,
        ), "打开文件后应处于整页视角（画布居中）"
        assert window.viewer.camera.zoom == pytest.approx(fit_zoom((0, 0, W, H), (vw, vh), 1.0))
        assert window.viewer.layer_outline is not None, "整页视角下蓝框仍要显示"

        # 切到下一个图层：相机一动不动，蓝框跟着换到新图层
        before = _camera(window)
        idx_before = window._current_index
        if idx_before < len(doc.layers) - 1:
            window.next_layer()
            _wait_layer_settled(window)
            assert window._current_index == idx_before + 1, "图层应当已切换"
            assert _camera(window) == before, "关闭自动定位后切图层不得移动画布"

            new_info = doc.layers[window._current_index]
            if window.settings.position_mode == POSITION_FAST:
                expected = position_bounds(new_info, POSITION_FAST)
            else:
                expected = layer_visual_bounds(new_info)
            if expected is not None:
                assert window.viewer.layer_outline == expected
    finally:
        window.close()
        app.processEvents()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

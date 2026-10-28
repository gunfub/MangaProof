# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""0 图层文件（需求 §88）的视图：整页居中 + 显示比例 100%。

背景：把图片拖进 Photoshop、未做处理直接保存的 PSD 里**一个图层都没有**
（layer count = 0，只有 merged image）。这类文件没有"当前图层"，定位链路
（`recenter_on_layer` 要 `LayerInfo`）根本走不到，改造前打开它画布停在
上一页的相机上——首次打开就是 `(0,0)@100%`，只能看到左上角一块。

2026-10 需求方口径：

- 0 图层文件一律按**画布**居中、显示比例 100%（= 整页完整装进视口，不裁边）；
- **不受「切换图层时自动定位与缩放」开关影响**：这类页没有可切换的对象，
  开关开或关都是整页视角；
- 工具栏「定位当前图层」对这类页也要有反应（退化成整页视角），不能点了没反应。

运行：QT_QPA_PLATFORM=offscreen uv run python -m pytest tests/test_zero_layer_view.py -v
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from mangaproof.camera.zoom import fit_zoom  # noqa: E402
from mangaproof.config.settings import SettingsManager  # noqa: E402
from mangaproof.psd.layer_model import LayerInfo  # noqa: E402
from mangaproof.ui.main_window import MainWindow  # noqa: E402

app = QApplication.instance() or QApplication([])

CANVAS = (1600, 2400)


class _ZeroLayerDoc:
    """只实现 _open_file_internal / 整页视角用到的接口：没有图层。"""

    def __init__(self, size=CANVAS):
        self.layers: list[LayerInfo] = []
        self.path = Path("/tmp/zero.psd")
        self.size = size
        self.all_layers_warmed = True

    def has_bg(self):
        return True

    def has_merged(self):
        return True

    def bg_layer_id(self):
        return None

    def resolved_bg_layer_id(self):
        return None

    def bg_copy_layer_id(self):
        return None

    def merged_np(self):
        return np.zeros((4, 4, 4), dtype=np.uint8)


@pytest.fixture()
def window(tmp_path):
    w = MainWindow(SettingsManager(tmp_path / "settings.json"))
    w.resize(1200, 800)
    w.show()
    app.processEvents()
    yield w
    w.close()
    app.processEvents()


def _bind_zero(window: MainWindow, rel: str = "zero.psd", doc=None) -> _ZeroLayerDoc:
    """把 0 图层文档挂进窗口（_ensure_doc 优先读 _docs，但要求 _base_dir 非空）。"""
    doc = doc or _ZeroLayerDoc()
    window._base_dir = Path("/tmp")
    window._docs[rel] = doc
    window._layer_ids_by_file[rel] = []
    return doc


def _page_camera(window: MainWindow, canvas=CANVAS):
    """期望的整页视角：(cx, cy, zoom)。"""
    return (
        canvas[0] / 2.0,
        canvas[1] / 2.0,
        fit_zoom((0, 0, *canvas), (window.viewer.width(), window.viewer.height()), 1.0),
    )


def _camera(window: MainWindow):
    cam = window.viewer.camera
    return (cam.center_x, cam.center_y, cam.zoom)


@pytest.mark.parametrize("auto_locate", [True, False])
def test_open_zero_layer_file_fits_whole_page(window, auto_locate):
    """打开 0 图层文件 → 整页视角；开关开或关结果都一样。"""
    window.settings.auto_locate = auto_locate
    _bind_zero(window)

    window._open_file_internal("zero.psd", restore=False)
    app.processEvents()

    assert window._current_index == -1, "0 图层文件没有当前图层"
    assert window._is_zero_layer_file("zero.psd") is True
    cx, cy, zoom = _camera(window)
    ex, ey, ezoom = _page_camera(window)
    assert (cx, cy) == (ex, ey), "应把画布中心对准视口中心"
    assert zoom == pytest.approx(ezoom), "应缩放到整页完整装进视口"
    assert window.viewer.layer_outline is None, "无图层 → 不画蓝色虚线框"


def test_switching_into_zero_layer_page_resets_camera(window):
    """从普通页翻到 0 图层页：不得残留上一页的相机位置。"""
    window.settings.auto_locate = True
    # 先把相机挪到别处（模拟上一页定位到某个图层后的状态）
    window.viewer.camera.center_x = 300.0
    window.viewer.camera.center_y = 700.0
    window.viewer.camera.zoom = 2.5
    _bind_zero(window)

    window._open_file_internal("zero.psd", restore=False)
    app.processEvents()
    assert _camera(window) == pytest.approx(_page_camera(window))


def test_recenter_current_layer_falls_back_to_page(window):
    """「定位当前图层」在 0 图层页上要有反应（退化成整页视角）。"""
    window.settings.auto_locate = True
    doc = _bind_zero(window)
    window._current_file = "zero.psd"
    window._current_index = -1
    window.viewer.set_document(doc)
    app.processEvents()

    window.viewer.camera.center_x = 12.0
    window.viewer.camera.zoom = 3.0
    window.recenter_current_layer()
    assert _camera(window) == pytest.approx(_page_camera(window))


@pytest.mark.parametrize("auto_locate", [True, False])
def test_reposition_after_layout_on_zero_layer(window, auto_locate):
    """任务打开后的布局补定位：0 图层页两种开关状态都落到整页视角。"""
    window.settings.auto_locate = auto_locate
    doc = _bind_zero(window)
    window._current_file = "zero.psd"
    window._current_index = -1
    window.viewer.set_document(doc)
    app.processEvents()

    window._reposition_after_layout()
    assert _camera(window) == pytest.approx(_page_camera(window))


def test_recenter_without_document_is_a_noop(window):
    """没有文档时不得凭空摆相机（recenter 的兜底只对"文档在"生效）。"""
    window.recenter_current_layer()
    assert _camera(window) == (0.0, 0.0, 1.0)


# --------------------------------------------------------------------------- #
# 端到端：真 0 图层 PSD（PSDImage.frompil 生成，与 local_samples/_001.psd 同构）
# --------------------------------------------------------------------------- #


def _make_zero_layer_psd(path: Path, size=(1200, 1800)) -> None:
    from PIL import Image
    from psd_tools import PSDImage

    PSDImage.frompil(Image.new("RGBA", size, (200, 30, 30, 255))).save(path)


def _wait_for_task(window: MainWindow, timeout_s: float = 60.0) -> None:
    deadline = time.time() + timeout_s
    while (window.task is None or window._current_file == "") and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert window.task is not None, "任务加载超时"
    assert window._current_file != "", "首文件打开超时"


def test_e2e_zero_layer_psd_opens_as_whole_page(tmp_path):
    folder = tmp_path / "chapter"
    folder.mkdir()
    _make_zero_layer_psd(folder / "zero.psd", (1200, 1800))

    window = MainWindow(SettingsManager(tmp_path / "settings.json"))
    window.resize(1200, 800)
    window.show()
    app.processEvents()
    try:
        with patch.object(
            QMessageBox, "information", return_value=QMessageBox.StandardButton.Ok
        ):
            window.open_folder(folder)
        _wait_for_task(window)
        app.processEvents()

        assert window._is_zero_layer_file("zero.psd") is True
        doc = window.current_doc
        assert doc.size == (1200, 1800)
        cx, cy, zoom = _camera(window)
        assert (cx, cy) == (600.0, 900.0), "整页居中"
        assert zoom == pytest.approx(
            fit_zoom((0, 0, 1200, 1800), (window.viewer.width(), window.viewer.height()), 1.0)
        )
    finally:
        window.close()
        app.processEvents()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

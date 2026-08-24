"""内存策略 GUI 集成测试（QT_QPA_PLATFORM=offscreen，无需显示器）。

覆盖：打开任务流式扫描（窗口外文档不驻留）、驱逐+重开（监制进度
完整保留、图层 id 重建一致）、档位热应用（LRU 预算即时变化）、
bg QImage 池配额驱逐。

运行：QT_QPA_PLATFORM=offscreen uv run python -m pytest tests/test_memory_gui.py -v
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from mangaproof.config.settings import SettingsManager
from mangaproof.review.state import PASSED
from mangaproof.ui.main_window import MainWindow
from mangaproof.ui.viewer_widget import numpy_to_qimage

DATA_DIR = Path(__file__).parent / "data" / "chapter01"

app = QApplication.instance() or QApplication([])


def _make_folder(tmp: Path, n: int) -> Path:
    """复制测试 PSD 到 n 页任务（名字 p01..pN，内容可重复）。"""
    folder = tmp / "book"
    folder.mkdir(parents=True, exist_ok=True)
    src = sorted(DATA_DIR.glob("*.psd"))
    for i in range(n):
        shutil.copy2(src[i % len(src)], folder / f"p{i + 1:02d}.psd")
    return folder


def _wait(window: MainWindow, pred, timeout_s: float = 30.0) -> None:
    deadline = time.time() + timeout_s
    while not pred() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert pred(), "等待超时"


@pytest.fixture()
def window(tmp_path):
    w = MainWindow(SettingsManager(tmp_path / "settings.json"))
    yield w
    w.close()
    app.processEvents()


def _open_task(window: MainWindow, folder: Path) -> None:
    window.open_folder(folder)
    _wait(window, lambda: window.task is not None and window._current_file != "")


def test_streaming_open_keeps_only_window_docs(window, tmp_path):
    folder = _make_folder(tmp_path, 8)
    _open_task(window, folder)
    _wait(window, lambda: window._preload_scheduled)
    # 进度数据全量（监制进度不依赖文档对象）
    assert len(window._layer_ids_by_file) == 8
    assert len(window._layer_names_by_file) == 8
    # 文档对象窗口有界（当前 + 后 3 + 前 1 + 前 2 松弛 = 最多 6 个）
    assert len(window._docs) <= 6


def test_neighbor_preload_coverage_forward_chain(window, tmp_path):
    """前向翻页链上每个新邻域文档必须入队预加载（底栏「完成」= 真就绪）。

    回归：流式加载后 _docs 缺失邻域文档时，_schedule_preloads 需惰性
    创建并排队，否则切页永远走慢路径重新提取。
    """
    folder = _make_folder(tmp_path, 8)
    _open_task(window, folder)
    _wait(window, lambda: window._preload_scheduled)
    # 等队列消化：所有目标完成（阶段 A + 阶段 B）
    _wait(
        window,
        lambda: not window._preload_targets and not window._extra_targets,
    )
    for rel in ("p02.psd", "p03.psd", "p04.psd"):
        doc = window._docs.get(rel)
        assert doc is not None, f"邻域文档 {rel} 应被惰性创建"
        assert doc.has_merged(), f"邻域 {rel} merged 应已预加载"

    # 逐页前向切换：下一邻居应已就绪（快速路径不弹重载）
    for nxt in ("p02.psd", "p03.psd", "p04.psd", "p05.psd"):
        window._request_open_file(nxt, restore=False)
        _wait(window, lambda n=nxt: window._current_file == n)
        app.processEvents()
        # 切换完成后再等一轮调度：新邻域 p06 入队并完成
        _wait(
            window,
            lambda: not window._preload_targets and not window._extra_targets,
        )
    assert window._current_file == "p05.psd"
    doc6 = window._docs.get("p06.psd")
    assert doc6 is not None and doc6.has_merged(), "前向链末端邻域应已就绪"


def test_eviction_reopen_preserves_progress(window, tmp_path):
    folder = _make_folder(tmp_path, 8)
    _open_task(window, folder)

    rel1 = "p01.psd"
    ids1 = list(window._layer_ids_by_file[rel1])
    names1 = list(window._layer_names_by_file[rel1])
    window.task.set_status(rel1, ids1[0], PASSED)

    # 跳到窗口外页面（p08）→ p01 应被驱逐
    window._request_open_file("p08.psd", restore=False)
    _wait(window, lambda: window._current_file == "p08.psd")
    app.processEvents()
    assert "p01.psd" not in window._docs, "窗口外文档对象应被驱逐"

    # 重开被驱逐页：惰性重建，进度与图层列表完整保留
    window._request_open_file(rel1, restore=False)
    _wait(window, lambda: window._current_file == rel1)
    assert window._layer_ids_by_file[rel1] == ids1, "重建后图层 id 应逐位一致"
    assert window._layer_names_by_file[rel1] == names1
    assert window.task.status_of(rel1, ids1[0]) == PASSED, "监制状态应保留"


def test_memory_policy_hot_apply(window):
    window.settings.memory_policy = "aggressive"
    window._apply_memory_policy()
    assert window._layer_cache.max_bytes == 256 * 1024 * 1024
    window.settings.memory_policy = "relaxed"
    window._apply_memory_policy()
    assert window._layer_cache.max_bytes == 768 * 1024 * 1024
    window.settings.memory_policy = "balanced"
    window._apply_memory_policy()
    assert window._layer_cache.max_bytes == 512 * 1024 * 1024


def test_bg_qimage_trim_keeps_current_evicts_oldest(window):
    arr = np.zeros((64, 64, 4), dtype=np.uint8)
    arr[:, :, 3] = 255
    qimg = numpy_to_qimage(arr)
    assert isinstance(qimg, QImage)

    window._pending_qimages.clear()
    window._bg_qimage_quota = 100  # 远小于一张图 → 只保留当前页
    window._current_file = "keep.psd"
    window._pending_qimages["old1.psd"] = {"merged": qimg, "bg": qimg}
    window._pending_qimages["keep.psd"] = {"merged": qimg, "bg": qimg}
    window._pending_qimages["old2.psd"] = {"merged": qimg, "bg": qimg}

    window._trim_bg_qimages()

    # 当前页 bg 保留；其余 bg 全部驱逐（merged 不受配额影响）
    assert window._pending_qimages["keep.psd"].get("bg") is not None
    assert window._pending_qimages["keep.psd"].get("merged") is not None
    assert window._pending_qimages["old1.psd"].get("bg") is None
    assert window._pending_qimages["old2.psd"].get("bg") is None
    assert window._pending_qimages["old1.psd"].get("merged") is not None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

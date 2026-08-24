"""内存策略相关单元测试：LRU pin/drop/resize、settings.memory_policy、
任务加载窗口集合。

运行：uv run python -m pytest tests/test_memory_policy.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from mangaproof.config.settings import (
    DEFAULT_MEMORY_POLICY,
    MEMORY_POLICIES,
    SettingsManager,
)
from mangaproof.psd.image_cache import LayerImageCache
from mangaproof.review.state import FileRecord, TaskState
from mangaproof.ui.task_loader import _window_set


def _arr(nbytes: int) -> np.ndarray:
    return np.zeros(nbytes, dtype=np.uint8)


# ---------------------------------------------------------------------------
# LayerImageCache
# ---------------------------------------------------------------------------

def test_cache_eviction_respects_budget():
    cache = LayerImageCache(max_bytes=100)
    cache.put("a", "1", _arr(60))
    cache.put("a", "2", _arr(60))
    assert len(cache) == 1            # 120 > 100，最旧的被逐出
    assert cache.get("a", "1") is None
    assert cache.get("a", "2") is not None


def test_cache_pin_skips_eviction():
    cache = LayerImageCache(max_bytes=100)
    cache.put("a", "1", _arr(60))
    cache.pin("a", "1")
    cache.put("a", "2", _arr(60))
    # 预算 100：非钉住条目 "2" 被逐出，钉住的 "1" 保留
    assert cache.get("a", "1") is not None
    assert cache.get("a", "2") is None


def test_cache_unpin_restores_eviction():
    cache = LayerImageCache(max_bytes=100)
    cache.put("a", "1", _arr(60))
    cache.pin("a", "1")
    cache.put("a", "2", _arr(60))
    assert cache.get("a", "1") is not None
    cache.unpin("a", "1")
    cache.put("a", "3", _arr(60))
    assert cache.get("a", "1") is None   # 解钉后参与淘汰


def test_cache_drop_only_target_path():
    cache = LayerImageCache(max_bytes=10_000)
    cache.put("a", "1", _arr(100))
    cache.pin("a", "1")
    cache.put("b", "1", _arr(100))
    cache.drop("a")
    assert cache.get("a", "1") is None
    assert cache.get("b", "1") is not None
    assert cache.pinned_count == 0       # 钉住集同步清理


def test_cache_set_max_bytes_shrinks_immediately():
    cache = LayerImageCache(max_bytes=10_000)
    cache.put("a", "1", _arr(100))
    cache.put("a", "2", _arr(100))
    cache.put("a", "3", _arr(100))
    assert len(cache) == 3
    cache.set_max_bytes(150)
    assert len(cache) == 1
    assert cache.max_bytes == 150


def test_cache_set_max_bytes_respects_pins():
    cache = LayerImageCache(max_bytes=10_000)
    cache.put("a", "1", _arr(100))
    cache.pin("a", "1")
    cache.put("a", "2", _arr(100))
    cache.set_max_bytes(100)
    assert cache.get("a", "1") is not None   # 钉住项保留，允许临时超预算
    assert cache.get("a", "2") is None


# ---------------------------------------------------------------------------
# settings.memory_policy
# ---------------------------------------------------------------------------

def test_settings_default_memory_policy():
    assert DEFAULT_MEMORY_POLICY == "balanced"
    assert DEFAULT_MEMORY_POLICY in MEMORY_POLICIES


def test_settings_memory_policy_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    manager = SettingsManager(path)
    manager.settings.memory_policy = "aggressive"
    manager.save()
    manager2 = SettingsManager(path)
    assert manager2.settings.memory_policy == "aggressive"


def test_settings_memory_policy_invalid_falls_back(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"settings_version": 1, "memory_policy": "turbo"}),
        encoding="utf-8",
    )
    manager = SettingsManager(path)
    assert manager.settings.memory_policy == DEFAULT_MEMORY_POLICY


def test_settings_memory_policy_missing_falls_back(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"settings_version": 1}), encoding="utf-8")
    manager = SettingsManager(path)
    assert manager.settings.memory_policy == DEFAULT_MEMORY_POLICY


# ---------------------------------------------------------------------------
# task_loader 窗口集合
# ---------------------------------------------------------------------------

def _task(rels, current):
    t = TaskState()
    for rel in rels:
        t.files.append(FileRecord(relative_path=rel, file_name=rel, size=1))
    t.current_file = current
    return t


def test_window_set_keeps_current_and_neighbors():
    task = _task([f"p{i:02d}.psd" for i in range(10)], "p04.psd")
    keep = _window_set(task)
    # 当前(4) + 后3(5,6,7) + 前1(3)
    assert keep == {"p03.psd", "p04.psd", "p05.psd", "p06.psd", "p07.psd"}


def test_window_set_current_invalid_falls_back_first_page():
    task = _task(["a.psd", "b.psd", "c.psd"], "missing.psd")
    keep = _window_set(task)
    assert keep == {"a.psd", "b.psd", "c.psd"}   # 首页 + 后 3 覆盖全部


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

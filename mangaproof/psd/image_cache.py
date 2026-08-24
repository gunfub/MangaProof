"""图层像素 LRU 缓存（需求 §59、§60）。

- merged image 与 background image 属于文档长期缓存（由 PSDDocument 持有）；
- 其余图层像素进入本 LRU 缓存，按字节预算淘汰，避免无限占用内存；
- 线程安全：缓存实例在后台预加载线程与 UI 线程间共享；
- 支持钉住（pin）：钉住条目不参与淘汰（当前文档 bg 等），
  支持按文档逐出（drop）与运行时调整预算（set_max_bytes）。
"""

import threading
from collections import OrderedDict
from typing import Optional, Tuple

import numpy as np


class LayerImageCache:
    def __init__(self, max_bytes: int = 512 * 1024 * 1024):
        self._max_bytes = max_bytes
        self._store: OrderedDict[Tuple[str, str], np.ndarray] = OrderedDict()
        self._bytes = 0
        self._pinned: set = set()   # 钉住键：淘汰时跳过
        self._lock = threading.Lock()

    def get(self, psd_path: str, layer_id: str) -> Optional[np.ndarray]:
        key = (psd_path, layer_id)
        with self._lock:
            img = self._store.get(key)
            if img is not None:
                self._store.move_to_end(key)
            return img

    def put(self, psd_path: str, layer_id: str, image: np.ndarray) -> None:
        key = (psd_path, layer_id)
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
                return
            size = int(image.nbytes)
            self._store[key] = image
            self._bytes += size
            self._evict_locked()

    # -- 钉住 --------------------------------------------------------------

    def pin(self, psd_path: str, layer_id: str) -> None:
        """钉住条目：不参与淘汰（用于当前文档 bg 等热数据）。"""
        with self._lock:
            self._pinned.add((psd_path, layer_id))

    def unpin(self, psd_path: str, layer_id: str) -> None:
        with self._lock:
            self._pinned.discard((psd_path, layer_id))

    # -- 文档逐出 / 预算 ----------------------------------------------------

    def drop(self, psd_path: str) -> None:
        """逐出某文档的全部条目（含钉住项，文档整体释放时调用）。"""
        with self._lock:
            for key in [k for k in self._store if k[0] == psd_path]:
                victim = self._store.pop(key)
                self._bytes -= int(victim.nbytes)
                self._pinned.discard(key)

    def set_max_bytes(self, max_bytes: int) -> None:
        """运行时调整预算；下调时立即收缩淘汰（钉住项除外）。"""
        with self._lock:
            self._max_bytes = max_bytes
            self._evict_locked()

    @property
    def max_bytes(self) -> int:
        with self._lock:
            return self._max_bytes

    @property
    def pinned_count(self) -> int:
        with self._lock:
            return len(self._pinned)

    # -- 内部 --------------------------------------------------------------

    def _evict_locked(self) -> None:
        """超预算时按插入序淘汰最旧的非钉住条目。

        全部条目都被钉住时允许临时超出预算（钉住是有意的保留）。
        """
        while self._bytes > self._max_bytes and len(self._store) > 1:
            victim_key = None
            for key in self._store:
                if key not in self._pinned:
                    victim_key = key
                    break
            if victim_key is None:
                return
            victim = self._store.pop(victim_key)
            self._bytes -= int(victim.nbytes)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._bytes = 0
            self._pinned.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)

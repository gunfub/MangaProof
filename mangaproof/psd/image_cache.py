"""图层像素 LRU 缓存（需求 §59、§60）。

- merged image 与 background image 属于文档长期缓存（由 PSDDocument 持有）；
- 其余图层像素进入本 LRU 缓存，按字节预算淘汰，避免无限占用内存。
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Optional, Tuple

import numpy as np


class LayerImageCache:
    def __init__(self, max_bytes: int = 512 * 1024 * 1024):
        self._max_bytes = max_bytes
        self._store: OrderedDict[Tuple[str, str], np.ndarray] = OrderedDict()
        self._bytes = 0

    def get(self, psd_path: str, layer_id: str) -> Optional[np.ndarray]:
        key = (psd_path, layer_id)
        img = self._store.get(key)
        if img is not None:
            self._store.move_to_end(key)
        return img

    def put(self, psd_path: str, layer_id: str, image: np.ndarray) -> None:
        key = (psd_path, layer_id)
        if key in self._store:
            self._store.move_to_end(key)
            return
        size = int(image.nbytes)
        self._store[key] = image
        self._bytes += size
        while self._bytes > self._max_bytes and len(self._store) > 1:
            _, victim = self._store.popitem(last=False)
            self._bytes -= int(victim.nbytes)

    def clear(self) -> None:
        self._store.clear()
        self._bytes = 0

    def __len__(self) -> int:
        return len(self._store)

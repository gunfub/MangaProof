"""PSD 文件扫描与打开（需求 §4、§5、§59）。

- 文件夹扫描：自然排序（需求 §5.2）；
- PSD 解析：每个 PSD 尽量只解析一次（需求 §59）；
- Original 严格使用 PSD 自身 merged image，绝不重新合成（需求 §2.3）。
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import List, Optional

from mangaproof.utils.natural_sort import natural_sorted

log = logging.getLogger("mangaproof.psd.loader")

SUPPORTED_SUFFIXES = (".psd", ".psb")


class PSDReadError(Exception):
    """PSD 无法读取。"""


class NoCompositeError(Exception):
    """PSD 不包含可用的 merged/composite image（需求 §61，禁止 fallback 合成）。"""


def is_psd_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES


def scan_psd_files(folder: Path, recursive: bool = False) -> List[Path]:
    """扫描文件夹中的 PSD/PSB，按自然排序返回（需求 §5.2）。"""
    if recursive:
        files = [
            p
            for p in folder.rglob("*")
            if is_psd_file(p) and ".mangaproof" not in p.name
        ]
    else:
        files = [p for p in folder.iterdir() if is_psd_file(p)]
    return natural_sorted(files)


def file_size(path: Path) -> int:
    return path.stat().st_size


def file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """流式计算完整 SHA-256（避免一次性读入内存）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def open_psd_tools(path: Path):
    """用 psd-tools 打开 PSD（惰性解析），失败抛 PSDReadError。"""
    from psd_tools import PSDImage

    try:
        return PSDImage.open(path)
    except Exception as exc:
        log.exception("无法读取该 PSD 文件：%s", path)
        raise PSDReadError(f"无法读取该 PSD 文件：{path.name}") from exc


def get_merged_pil(psd):
    """返回 PSD 自带的 merged/composite 图像（PIL）。

    若 PSD 不含 merged image，抛 NoCompositeError。
    绝不调用 psd.composite() 重新合成（需求 §2.3）。
    """
    try:
        img = psd.topil()
    except Exception as exc:
        raise NoCompositeError(str(exc)) from exc
    if img is None:
        raise NoCompositeError(
            "该 PSD 不包含可用的 merged/composite image，"
            "本程序无法提供 Original 显示。"
        )
    return img

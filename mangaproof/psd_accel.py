"""psd-tools 解码加速的运行时补丁（需求：预加载/提取提速）。

- RLE 解压核心：psd-tools 1.18 官方 wheel 已自带 Cython `_rle` 扩展
  （三平台均含编译产物，实测 50MB 通道仅 5ms），无需补丁；
- 本模块补丁 `decode_prediction`（ZIP_WITH_PREDICTION）：原
  `_delta_decode` 为逐像素纯 Python 循环（50MB 通道约 1s），
  纯 C 循环实现 8/16 位（实测约 200× 加速）；32 位回退原实现；
- 原生扩展（纯 C + ctypes，无需 Python.h）缺失时自动回退原实现，
  功能不受影响，仅速度下降。

调用方式：mangaproof.psd.loader 导入时调用一次 patch_psd_tools()。
"""

from __future__ import annotations

import ctypes
import logging
import sys
from pathlib import Path

log = logging.getLogger("mangaproof.psd_accel")

_PATCHED_ATTR = "_mangaproof_patched"

_lib = None
try:
    _name = "_psd_fast.pyd" if sys.platform == "win32" else "_psd_fast.so"
    _lib = ctypes.CDLL(str(Path(__file__).resolve().parent / _name))
    _lib.mp_delta_decode_8.argtypes = [
        ctypes.c_char_p, ctypes.c_longlong, ctypes.c_longlong, ctypes.c_char_p,
    ]
    _lib.mp_delta_decode_16.argtypes = [
        ctypes.c_char_p, ctypes.c_longlong, ctypes.c_longlong, ctypes.c_char_p,
    ]
except Exception:
    _lib = None


def is_accel_available() -> bool:
    return _lib is not None


def _delta_decode_8(data: bytes, w: int, h: int) -> bytes:
    total = w * h
    if len(data) < total:
        raise ValueError("insufficient data")
    out = bytearray(total)
    _lib.mp_delta_decode_8(data, w, h, (ctypes.c_char * total).from_buffer(out))
    return bytes(out)


def _delta_decode_16(data: bytes, w: int, h: int) -> bytes:
    total = w * h
    if len(data) < total * 2:
        raise ValueError("insufficient data")
    out = bytearray(total * 2)
    _lib.mp_delta_decode_16(
        data, w, h, (ctypes.c_char * (total * 2)).from_buffer(out)
    )
    return bytes(out)


def _fast_decode_prediction(data: bytes, w: int, h: int, depth: int) -> bytes:
    """decode_prediction 的 C 加速版（8/16 位）；32 位回退原实现。"""
    if depth == 8:
        return _delta_decode_8(data, w, h)
    if depth == 16:
        return _delta_decode_16(data, w, h)
    return _original_decode_prediction(data, w, h, depth)


_original_decode_prediction = None


def patch_psd_tools() -> bool:
    """把预测解码加速补丁进 psd_tools.compression。返回是否生效。

    幂等；仅在原生扩展可用时补丁；异常时不阻断启动。
    """
    global _original_decode_prediction
    if _lib is None:
        log.info("psd 解码加速扩展未构建，使用 psd-tools 原实现")
        return False
    try:
        import psd_tools.compression as comp

        if getattr(comp, _PATCHED_ATTR, False):
            return True

        _original_decode_prediction = comp.decode_prediction
        comp.decode_prediction = _fast_decode_prediction
        setattr(comp, _PATCHED_ATTR, True)
        log.info("已应用 psd 解码加速（ZIP 预测 C 解码）")
        return True
    except Exception:
        log.warning("psd 解码加速补丁失败，回退原实现", exc_info=True)
        return False

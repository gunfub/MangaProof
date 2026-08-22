"""生成 macOS .icns 图标（纯 Python，无需 macOS 的 iconutil）。

ICNS 容器格式：'icns' 魔数 + 大端 uint32 总长度，
后跟若干块：4 字节类型码 + uint32 块长度（含 8 字节块头）+ PNG 数据。

用法：
    uv run python scripts/make_icns.py [源PNG] [输出.icns]
    默认：ico/ico.png → ico/ico.icns
"""

from __future__ import annotations

import io
import struct
import sys
from pathlib import Path

# Windows 控制台默认 cp1252，打印中文会 UnicodeEncodeError → 强制 UTF-8
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from PIL import Image

# (类型码, 像素尺寸)：经典尺寸 + 现代尺寸 + Retina (@2x) 变体
ICNS_CHUNKS = [
    ("icp4", 16),   # 16x16（经典）
    ("icp5", 32),   # 32x32（经典）
    ("icp6", 64),   # 64x64（经典）
    ("ic07", 128),
    ("ic08", 256),
    ("ic09", 512),
    ("ic10", 1024),  # 512x512@2x
    ("ic11", 32),    # 16x16@2x
    ("ic12", 64),    # 32x32@2x
    ("ic13", 256),   # 128x128@2x
    ("ic14", 512),   # 256x256@2x
]


def build_icns(src_png: Path, out_icns: Path) -> Path:
    """从正方形 PNG 生成 .icns。"""
    with Image.open(src_png) as base_img:
        base = base_img.convert("RGBA")
        chunks: list[bytes] = []
        for code, size in ICNS_CHUNKS:
            resized = base.resize((size, size), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            resized.save(buf, format="PNG")
            data = buf.getvalue()
            chunks.append(code.encode("ascii") + struct.pack(">I", 8 + len(data)) + data)

    total = 8 + sum(len(c) for c in chunks)
    out_icns.parent.mkdir(parents=True, exist_ok=True)
    with open(out_icns, "wb") as f:
        f.write(b"icns" + struct.pack(">I", total))
        for chunk in chunks:
            f.write(chunk)
    return out_icns


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    project_root = Path(__file__).resolve().parent.parent
    src = Path(args[0]) if args else project_root / "ico" / "ico.png"
    dst = Path(args[1]) if len(args) > 1 else src.with_name(src.stem + ".icns")
    out = build_icns(src, dst)
    print(f"已生成 {out}（{out.stat().st_size} 字节，{len(ICNS_CHUNKS)} 个尺寸块）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

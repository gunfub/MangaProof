# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""生成 macOS .icns 图标（纯 Python，无需 macOS 的 iconutil）。

ICNS 容器格式：'icns' 魔数 + 大端 uint32 总长度，
后跟若干块：4 字节类型码 + uint32 块长度（含 8 字节块头）+ PNG 数据。

**为什么不能把满幅方图直接切块**（2026-09-25 实测修正）：macOS 的 App 图标有一套
固定网格 —— 1024×1024 画布上，**实体占 824×824 居中**，四边各留 100px 透明环，
圆角半径 **184px**（= 22.3% of 824）。实测 Apple 自家的 Xcode / iMovie（App Store
官方图）、第三方 Pixelmator Pro、以及 macOS 26 原生应用 CodexBar 的 `.app` 内
`Icon.icns`：四者的实体包围盒**逐像素一致**，都是 `(100, 100, 924, 924)`，
圆角用 184px 圆角矩形拟合的 IoU ≈ 0.998。而 `ico/ico.png` 是**满幅**的
（实体铺满 1024，圆角仅 146px）：直接切块会让 Dock/Finder 里比别的应用
**线性大约 24.3%（面积 55%）**，且看起来更"方"。

所以本脚本先把源图适配到 macOS 网格，再切块：

1. 源图缩放到 824×824；
2. 乘上半径 184px 的圆角矩形遮罩（超采样绘制，边缘不锯齿）——
   现有素材的圆角（缩放后约 111px）比目标小，遮罩只裁到底色卡片，不切内容；
3. 居中贴到 1024×1024 透明画布（偏移 100,100）；
4. 按下面的尺寸块逐级缩放打包，并把这张"macOS 主图"落盘留档。

不改阴影：网格里的 100px 环足以容纳原生图标那层柔和阴影，但本项目图标是平面风格，
先从简（要加再说）。

用法：
    uv run python scripts/make_icns.py [源PNG] [输出.icns]
    默认：ico/ico.png → ico/ico.icns（同时产出 ico/ico-macos-1024.png）
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

from PIL import Image, ImageChops, ImageDraw

#: macOS 图标网格（Big Sur 11 起统一，macOS 26 仍沿用；实测见模块文档）
MACOS_CANVAS = 1024        # 画布边长
MACOS_CONTENT = 824        # 实体边长
MACOS_RADIUS = 184         # 实体圆角半径（= 22.3% of 824）
MACOS_MARGIN = (MACOS_CANVAS - MACOS_CONTENT) // 2      # 100：四边透明环

#: 遮罩超采样倍数：PIL 的 draw 不做抗锯齿，放大画再缩小才平滑
_MASK_SUPERSAMPLE = 4

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


def rounded_mask(size: int = MACOS_CONTENT, radius: int = MACOS_RADIUS) -> Image.Image:
    """半径 ``radius`` 的圆角矩形遮罩（``L`` 模式，边缘抗锯齿）。"""
    big = size * _MASK_SUPERSAMPLE
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, big - 1, big - 1), radius=radius * _MASK_SUPERSAMPLE, fill=255
    )
    return mask.resize((size, size), Image.Resampling.LANCZOS)


def macos_grid_master(base: Image.Image) -> Image.Image:
    """把满幅方图适配成 macOS 网格主图：824 实体 + 100px 透明环。"""
    art = base.resize((MACOS_CONTENT, MACOS_CONTENT), Image.Resampling.LANCZOS)
    # 与源图自身的圆角取交集（乘法）：源图更"方"时以本遮罩为准
    art.putalpha(ImageChops.multiply(art.getchannel("A"), rounded_mask()))
    canvas = Image.new("RGBA", (MACOS_CANVAS, MACOS_CANVAS), (0, 0, 0, 0))
    canvas.paste(art, (MACOS_MARGIN, MACOS_MARGIN))
    return canvas


def master_path_for(out_icns: Path) -> Path:
    """macOS 主图的落盘路径（与 .icns 同目录、同名带 ``-macos-1024``）。"""
    return out_icns.with_name(f"{out_icns.stem}-macos-{MACOS_CANVAS}.png")


def build_icns(src_png: Path, out_icns: Path) -> Path:
    """从正方形 PNG 生成 .icns（先做 macOS 网格适配，再切块）。"""
    with Image.open(src_png) as base_img:
        master = macos_grid_master(base_img.convert("RGBA"))
        master_path_for(out_icns).parent.mkdir(parents=True, exist_ok=True)
        master.save(master_path_for(out_icns))

        chunks: list[bytes] = []
        for code, size in ICNS_CHUNKS:
            resized = master.resize((size, size), Image.Resampling.LANCZOS)
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
    master = master_path_for(out)
    print(
        f"已生成 {out}（{out.stat().st_size} 字节，{len(ICNS_CHUNKS)} 个尺寸块）\n"
        f"macOS 网格：{MACOS_CANVAS} 画布 / {MACOS_CONTENT} 实体 / "
        f"圆角 {MACOS_RADIUS}px / 四边留白 {MACOS_MARGIN}px\n"
        f"主图已落盘：{master}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

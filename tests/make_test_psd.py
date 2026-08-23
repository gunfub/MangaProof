"""生成测试 PSD 夹具（用 psd-tools 构造，含 merged image）。

夹具结构：
  tests/data/chapter01/
  ├── 001.psd    bg 层 + 3 个对话框图层 + 1 个隐藏图层
  │              + text1（手工维护的 type 文字图层，psd-tools 文字信息
  │                只读、无法程序化生成——见下方保护逻辑）
  │              + text2（已栅格化文字 pixel 图层，本脚本生成）
  ├── 002.psd    无精确 "bg" 名（测试背景回退）
  └── 10.psd     测试自然排序（001, 002, 10）

注意：psd-tools 1.18 的迭代顺序为自下而上（第一个即最底部图层，
已用合成结果实证）；append 追加到最上层（文档末尾）。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from psd_tools import PSDImage
from psd_tools.api.layers import PixelLayer

DATA_DIR = Path(__file__).parent / "data" / "chapter01"
FONT_PATH = Path(__file__).parent.parent / "font" / "MiSans-Medium.ttf"
W, H = 400, 600


def _canvas() -> Image.Image:
    return Image.new("RGBA", (W, H), (255, 255, 255, 255))


def _box(size, color) -> Image.Image:
    img = Image.new("RGBA", size, color)
    return img


def _text_layer_image(text: str, size=(147, 52), color=(0, 0, 0, 255)) -> Image.Image:
    """绘制文字栅格（模拟已栅格化文字图层）。"""
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(str(FONT_PATH), 36)
    d.text((8, 4), text, font=font, fill=color)
    return img


def _make_001() -> None:
    target = DATA_DIR / "001.psd"
    if target.exists():
        try:
            old = PSDImage.open(target)
            if any(l.kind == "type" and l.name == "text1" for l in old):
                # text1 是手工维护的 type 图层（psd-tools 文字信息只读，
                # 无法程序化生成），重建会丢失——跳过并提示。
                print(f"note: {target.name} 含手工 type 图层 text1，跳过重建以保留")
                return
        except Exception:
            pass  # 旧文件损坏则重建

    merged = _canvas()
    d = ImageDraw.Draw(merged)
    d.rectangle([40, 60, 160, 120], fill=(30, 30, 30, 255))      # dialogue_01 气泡
    d.rectangle([60, 240, 200, 300], fill=(60, 60, 60, 255))     # dialogue_02
    d.rectangle([220, 420, 380, 480], fill=(90, 90, 90, 255))    # dialogue_03
    merged.alpha_composite(_text_layer_image("TEXT2"), (194, 177))  # text2 栅格

    psd = PSDImage.frompil(merged)
    # 注意：psd-tools 迭代顺序为自下而上，第一个即最底部图层；
    # append 追加到最上层
    PixelLayer.frompil(_canvas(), psd, name="bg", top=0, left=0)
    PixelLayer.frompil(_box((120, 60), (30, 30, 30, 255)), psd, name="dialogue_01", top=60, left=40)
    PixelLayer.frompil(_box((140, 60), (60, 60, 60, 255)), psd, name="dialogue_02", top=240, left=60)
    PixelLayer.frompil(_box((160, 60), (90, 90, 90, 255)), psd, name="dialogue_03", top=420, left=220)
    hidden = PixelLayer.frompil(_box((80, 40), (200, 0, 0, 255)), psd, name="hidden_draft", top=500, left=20)
    hidden.visible = False
    # 已栅格化文字图层（pixel 层，非最底部 → composite 路径）
    PixelLayer.frompil(_text_layer_image("TEXT2"), psd, name="text2", top=177, left=194)
    # 注：text1（type 层）无法程序化生成，由手工维护
    psd.save(target)


def _make_002() -> None:
    merged = _canvas()
    d = ImageDraw.Draw(merged)
    d.rectangle([100, 100, 300, 200], fill=(10, 120, 200, 255))   # 台词层
    d.rectangle([0, 400, 400, 600], fill=(240, 220, 180, 255))    # 背景画（底部）

    psd = PSDImage.frompil(merged)
    PixelLayer.frompil(_box((200, 100), (10, 120, 200, 255)), psd, name="text_01", top=100, left=100)
    # 无精确 "bg" 名 → 应回退到最底部有像素图层 background_painting
    PixelLayer.frompil(_box((400, 200), (240, 220, 180, 255)), psd, name="background_painting", top=400, left=0)
    psd.save(DATA_DIR / "002.psd")


def _make_010() -> None:
    merged = _canvas()
    d = ImageDraw.Draw(merged)
    d.rectangle([80, 80, 320, 220], fill=(120, 60, 180, 255))

    psd = PSDImage.frompil(merged)
    PixelLayer.frompil(_canvas(), psd, name="bg", top=0, left=0)
    PixelLayer.frompil(_box((240, 140), (120, 60, 180, 255)), psd, name="sfx_01", top=80, left=80)
    psd.save(DATA_DIR / "10.psd")


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _make_001()
    _make_002()
    _make_010()
    print("fixtures written to", DATA_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())

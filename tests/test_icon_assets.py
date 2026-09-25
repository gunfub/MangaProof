# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""macOS 图标网格回归（2026-09-25 实测修正）。

背景：`ico/ico.png` 是**满幅**图（实体铺满整张 1024 画布、四边零留白），而 macOS 的
App 图标有一套固定网格 —— **1024 画布 / 824×824 实体居中 / 四边各 100px / 圆角
184px**。直接拿满幅图切 `.icns`，Dock、Finder、应用切换器里都会比别的应用**线性大约
24.3%（面积 55%）**，而且圆角只有 14%（Apple 是 22%），看起来像一块铺满格子的方板。

实测口径（alpha≥128 把阴影与实体分开；按 alpha>0 量会被阴影骗到 85–89%）：

======================  ==========  ==============  ==================
图标                    实体框      占画布          184px 圆角拟合 IoU
======================  ==========  ==============  ==================
Apple Xcode（官方图）    (100,100,924,924)  80.5%     0.9979
Apple iMovie（官方图）   (100,100,924,924)  80.5%     0.9979
MangaProof 新图标        (100,100,924,924)  80.5%     0.9989
======================  ==========  ==============  ==================

本文件守三件事：

1. 源图 `ico/ico.png` **保持满幅** —— 网格适配只在生成 icns 时做，Windows `.ico`、
   Linux `.png`、Android 自适应图标继续用满幅源图；
2. 入库的 `ico/ico.icns` 每个尺寸块的实体包围盒都落在网格上，圆角与 Apple 同档；
3. 入库产物与 `scripts/make_icns.py` **逐字节一致** —— 改了脚本却忘了重新生成，
   测试会直接失败（CI 不重新生成图标，只读仓库里那份）。
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import struct
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

ICNS = ROOT / "ico" / "ico.icns"
MASTER = ROOT / "ico" / "ico-macos-1024.png"
SOURCE = ROOT / "ico" / "ico.png"

CANVAS = 1024
CONTENT = 824
MARGIN = 100
RADIUS = 184

#: 实测的实体包围盒（alpha≥128），按各尺寸块等比换算，容差 ±1px（抗锯齿边界）
EXPECTED_BOXES = {
    "icp4": 16, "icp5": 32, "icp6": 64, "ic07": 128, "ic08": 256, "ic09": 512,
    "ic10": 1024, "ic11": 32, "ic12": 64, "ic13": 256, "ic14": 512,
}


def load_make_icns():
    """按路径加载 scripts/make_icns.py（scripts/ 不是包）。"""
    spec = importlib.util.spec_from_file_location(
        "make_icns", ROOT / "scripts" / "make_icns.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def icns_chunks(path: Path) -> list[tuple[str, bytes]]:
    data = path.read_bytes()
    assert data[:4] == b"icns", f"{path} 不是 icns 容器"
    assert struct.unpack(">I", data[4:8])[0] == len(data), "icns 声明长度与实际不符"
    off, out = 8, []
    while off < len(data):
        code = data[off:off + 4].decode("ascii")
        size = struct.unpack(">I", data[off + 4:off + 8])[0]
        assert size > 8 and off + size <= len(data), f"{code} 块长度非法：{size}"
        out.append((code, data[off + 8:off + size]))
        off += size
    return out


def solid_bbox(image: Image.Image, threshold: int = 128) -> tuple[int, int, int, int]:
    mask = image.getchannel("A").point(lambda v: 255 if v >= threshold else 0)
    return mask.getbbox()


def solid_mask(image: Image.Image, threshold: int = 128) -> np.ndarray:
    return (np.array(image.getchannel("A")) >= threshold).astype(np.uint8)


def expected_box(size: int) -> tuple[int, int, int, int]:
    edge = round(size * MARGIN / CANVAS)
    return (edge, edge, size - edge, size - edge)


def ideal_rounded_rect(size: int, radius: int) -> np.ndarray:
    """解析画出的圆角矩形掩码（不用 PIL 的 draw，避免实现差异）。"""
    yy, xx = np.mgrid[0:size, 0:size]
    cx = np.clip(xx, radius, size - 1 - radius)
    cy = np.clip(yy, radius, size - 1 - radius)
    return (((xx - cx) ** 2 + (yy - cy) ** 2) <= radius * radius).astype(np.uint8)


def iou(a: np.ndarray, b: np.ndarray) -> float:
    union = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / union) if union else 1.0


# --- ① 源图保持满幅（别的平台继续用它） --------------------------------------


def test_source_art_stays_full_bleed():
    with Image.open(SOURCE) as im:
        im = im.convert("RGBA")
        assert im.size == (CANVAS, CANVAS)
        assert solid_bbox(im) == (0, 0, CANVAS, CANVAS), (
            "ico/ico.png 必须保持满幅：Windows .ico / Linux .png / Android 都依赖它；"
            "macOS 的网格适配只在 scripts/make_icns.py 里做"
        )


# --- ② 入库 .icns 落在 macOS 网格上 ------------------------------------------


def test_icns_container_shape():
    codes = [code for code, _ in icns_chunks(ICNS)]
    assert codes == list(EXPECTED_BOXES), f"尺寸块与预期不符：{codes}"


@pytest.mark.parametrize("code,size", sorted(EXPECTED_BOXES.items()))
def test_every_chunk_uses_the_macos_grid(code: str, size: int):
    payload = dict(icns_chunks(ICNS))[code]
    with Image.open(io.BytesIO(payload)) as im:
        im = im.convert("RGBA")
        assert im.size == (size, size)
        got = solid_bbox(im)
    want = expected_box(size)
    assert all(abs(a - b) <= 1 for a, b in zip(got, want)), (
        f"{code}（{size}px）实体包围盒 {got} 偏离 macOS 网格 {want}；"
        "图标比别的应用大就是因为这里没有 100px 安全区"
    )


def test_largest_chunk_equals_the_committed_master():
    """ic10 必须就是落盘的那张主图（评审看的就是它）。"""
    payload = dict(icns_chunks(ICNS))["ic10"]
    with Image.open(io.BytesIO(payload)) as chunk, Image.open(MASTER) as master:
        assert chunk.convert("RGBA").tobytes() == master.convert("RGBA").tobytes()


def test_master_geometry_and_transparent_ring():
    with Image.open(MASTER) as im:
        im = im.convert("RGBA")
        assert im.size == (CANVAS, CANVAS)
        assert solid_bbox(im) == (MARGIN, MARGIN, CANVAS - MARGIN, CANVAS - MARGIN)
        px = im.load()
        assert px[CANVAS // 2, CANVAS // 2][3] == 255, "中心必须是实体"
        for x, y in ((0, 0), (CANVAS - 1, 0), (0, CANVAS - 1), (CANVAS - 1, CANVAS - 1)):
            assert px[x, y][3] == 0, f"角 ({x},{y}) 必须透明"
        # 100px 环整圈透明（不加阴影；要加也得先在这里改口）
        for x, y in ((MARGIN // 2, CANVAS // 2), (CANVAS // 2, MARGIN // 2),
                     (CANVAS - 1 - MARGIN // 2, CANVAS // 2)):
            assert px[x, y][3] == 0, f"安全区 ({x},{y}) 必须透明"


def test_corner_matches_apples_geometry():
    """圆角与 Apple 自家图标同档：对 184px 理想圆角矩形的 IoU ≥ 0.995。

    实测参照：Apple Xcode / iMovie = 0.9979，本图标 = 0.9989。
    """
    with Image.open(MASTER) as im:
        mask = solid_mask(im.convert("RGBA"))[
            MARGIN:MARGIN + CONTENT, MARGIN:MARGIN + CONTENT
        ]
    score = iou(mask, ideal_rounded_rect(CONTENT, RADIUS))
    assert score >= 0.995, f"圆角与 Apple 轮廓差太多（IoU={score:.4f}）"
    # 半径 184 应当就是最优解，偏离说明遮罩被改过
    best = max(range(160, 210), key=lambda r: iou(mask, ideal_rounded_rect(CONTENT, r)))
    assert abs(best - RADIUS) <= 2, f"最佳拟合半径 {best}px 偏离 {RADIUS}px"


# --- ③ 入库产物 == 生成脚本的输出（防止改了脚本忘了重新生成） ------------------


def test_committed_artifacts_match_the_generator(tmp_path: Path):
    make_icns = load_make_icns()
    out = tmp_path / "ico.icns"
    make_icns.build_icns(SOURCE, out)
    rebuilt_master = make_icns.master_path_for(out)

    def digest(path: Path) -> str:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    assert digest(out) == digest(ICNS), (
        "ico/ico.icns 与 scripts/make_icns.py 的输出不一致："
        "请运行 `uv run python scripts/make_icns.py` 重新生成并提交"
    )
    assert digest(rebuilt_master) == digest(MASTER), (
        "ico/ico-macos-1024.png 与生成脚本的输出不一致：请重新生成并提交"
    )


def test_grid_constants_are_the_measured_ones():
    make_icns = load_make_icns()
    assert (make_icns.MACOS_CANVAS, make_icns.MACOS_CONTENT) == (CANVAS, CONTENT)
    assert make_icns.MACOS_RADIUS == RADIUS
    assert make_icns.MACOS_MARGIN == MARGIN


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))

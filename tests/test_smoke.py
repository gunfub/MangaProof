"""核心逻辑冒烟测试（无 GUI）：加载、视觉边界、背景选择、相机、持久化验证、PDF。

运行：uv run python -m pytest tests/test_smoke.py -v
     （或直接 uv run python tests/test_smoke.py）
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mangaproof.camera.camera import Camera
from mangaproof.camera.centering import layer_visual_bounds, layer_visual_center
from mangaproof.camera.zoom import fit_zoom
from mangaproof.psd.document import PSDDocument
from mangaproof.report.generator import generate_report, resolve_report_path
from mangaproof.review import persistence
from mangaproof.review.state import FAILED, PASSED, UNREVIEWED
from mangaproof.utils.natural_sort import natural_sorted

DATA_DIR = Path(__file__).parent / "data" / "chapter01"


def test_natural_sort():
    assert natural_sorted(["10.psd", "001.psd", "003.psd", "002.psd"]) == [
        "001.psd", "002.psd", "003.psd", "10.psd",
    ]
    assert natural_sorted(["010.psd", "2.psd", "002.psd"]) == ["002.psd", "2.psd", "010.psd"]


def test_document_load_001():
    doc = PSDDocument(DATA_DIR / "001.psd")
    names = [info.name for info in doc.layers]
    # 隐藏图层不应进入可监制列表；顺序为文档顺序（自上而下）
    assert names == ["bg", "dialogue_01", "dialogue_02", "dialogue_03"], names
    # merged image 来自 PSD 自带数据
    merged = doc.merged_np()
    assert merged.shape == (600, 400, 4)
    # 图层像素中心颜色
    img = doc.layer_image(doc.layers[1].id)
    assert img.shape == (60, 120, 4)
    # bg 选择：精确 "bg"
    assert doc.bg_layer_id() == doc.layers[0].id


def test_visual_bounds_and_center():
    doc = PSDDocument(DATA_DIR / "001.psd")
    info = doc.layers[1]  # dialogue_01: (40,60,160,120)
    vb = layer_visual_bounds(info)
    assert vb == (40, 60, 160, 120), vb
    cx, cy = layer_visual_center(info)
    assert (cx, cy) == (100.0, 90.0)
    # 全透明图层 → fallback 到 bounds
    import numpy as np
    from mangaproof.psd.layer_model import LayerInfo
    empty = LayerInfo(
        id="x", name="empty", bounds=(10, 10, 30, 30), visible=True,
        layer_type="pixel",
        image_loader=lambda: np.zeros((20, 20, 4), dtype=np.uint8),
    )
    assert layer_visual_bounds(empty) is None
    assert layer_visual_center(empty) is None


def test_bg_fallback_bottom_most():
    doc = PSDDocument(DATA_DIR / "002.psd")
    bg_id = doc.bg_layer_id()
    info = doc.layer_by_id(bg_id)
    assert info.name == "background_painting", info.name
    bg = doc.bg_image()
    assert bg is not None and bg[2].shape == (200, 400, 4)


def test_camera():
    cam = Camera(center_x=100, center_y=200, zoom=2.0)
    wx, wy = cam.screen_to_world(*cam.world_to_screen(50, 60, 800, 600), 800, 600)
    assert abs(wx - 50) < 1e-9 and abs(wy - 60) < 1e-9
    # 锚点缩放后，锚点处世界坐标不变
    sx, sy = 300.0, 200.0
    before = cam.screen_to_world(sx, sy, 800, 600)
    cam.zoom_around(sx, sy, 800, 600, 1.5)
    after = cam.screen_to_world(sx, sy, 800, 600)
    assert abs(before[0] - after[0]) < 1e-9 and abs(before[1] - after[1]) < 1e-9
    assert abs(cam.zoom - 3.0) < 1e-9
    cam.pan_by_screen(80, -40)
    moved = cam.screen_to_world(sx, sy, 800, 600)
    assert abs(moved[0] - (before[0] - 80 / 3.0)) < 1e-6
    assert abs(moved[1] - (before[1] + 40 / 3.0)) < 1e-6


def test_fit_zoom():
    z = fit_zoom((0, 0, 800, 1200), (1000, 1000), 0.8)
    assert abs(z - 1000 * 0.8 / 1200) < 1e-9
    z2 = fit_zoom((0, 0, 1200, 800), (1000, 1000), 0.6)
    assert abs(z2 - 1000 * 0.6 / 1200) < 1e-9
    assert fit_zoom(None, (100, 100), 0.6) is None
    assert fit_zoom((0, 0, 0, 0), (100, 100), 0.6) is None


def _copy_fixtures(dst: Path) -> Path:
    dst.mkdir(parents=True, exist_ok=True)
    for p in sorted(DATA_DIR.glob("*.psd")):
        shutil.copy2(p, dst / p.name)
    return dst


def test_persistence_roundtrip_and_verify():
    with tempfile.TemporaryDirectory() as tmp:
        folder = _copy_fixtures(Path(tmp) / "chapter01")
        files = sorted(folder.glob("*.psd"))
        task, samples = persistence.create_task_folder(folder, files)
        assert task.task_type == "folder"
        assert len(task.files) == 3
        # 3 个文件 → 2 个抽样 Hash
        sampled = [f for f in task.files if f.sample_sha256]
        assert len(sampled) == 2, [f.file_name for f in sampled]
        assert sampled[0].relative_path == "001.psd"
        assert sampled[-1].relative_path == "10.psd"

        ok, reason = persistence.verify_folder(task, files, folder)
        assert ok, reason

        # 保存 + 重载
        path = persistence.progress_path_for_folder(folder)
        task.set_status("001.psd", "x", FAILED)  # 写入一些状态
        persistence.save_task(task, path)
        loaded = persistence.load_task(path)
        assert loaded.schema_version == 1
        assert loaded.status_of("001.psd", "x") == FAILED

        # 篡改：改变文件大小 → 验证失败
        with open(folder / "002.psd", "ab") as f:
            f.write(b"tamper")
        ok, reason = persistence.verify_folder(task, files, folder)
        assert not ok and "002.psd" in reason
        # 篡改抽样的 001 但大小不变 → Hash 不匹配
        with open(folder / "001.psd", "r+b") as f:
            f.seek(0)
            data = bytearray(f.read(8))
            data[0] ^= 0xFF
            f.seek(0)
            f.write(bytes(data))
        ok, reason = persistence.verify_folder(task, files, folder)
        assert not ok and "001.psd" in reason


def test_single_psd_identity():
    with tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp)
        src = DATA_DIR / "001.psd"
        shutil.copy2(src, dst / "001.psd")
        psd_path = dst / "001.psd"
        task, _ = persistence.create_task_single(psd_path)
        assert task.task_type == "single"
        assert task.files[0].sample_sha256  # 单文件 = 完整 Hash
        ok, reason = persistence.verify_single(task, psd_path)
        assert ok, reason
        with open(psd_path, "ab") as f:
            f.write(b"x")
        ok, reason = persistence.verify_single(task, psd_path)
        assert not ok


def test_report_generation():
    with tempfile.TemporaryDirectory() as tmp:
        folder = _copy_fixtures(Path(tmp) / "chapter01")
        files = sorted(folder.glob("*.psd"))
        task, _ = persistence.create_task_folder(folder, files)

        doc1 = PSDDocument(folder / "001.psd")
        ids1 = [i.id for i in doc1.layers]
        task.set_status("001.psd", ids1[1], FAILED)
        task.add_issue("001.psd", ids1[1], "dialogue_01", "字体选择错误",
                       "这里应使用 Bold，而不是 Regular。", (40, 60, 120, 60))
        task.add_issue("001.psd", ids1[2], "dialogue_02", "漏字", "", (60, 240, 140, 60))
        task.set_status("001.psd", ids1[2], FAILED)
        task.set_status("001.psd", ids1[3], PASSED)
        task.set_status("001.psd", ids1[0], PASSED)
        for other in ("002.psd", "10.psd"):
            doc = PSDDocument(folder / other)
            for i in doc.layers:
                task.set_status(other, i.id, PASSED)

        layer_ids = {
            "001.psd": [i.id for i in PSDDocument(folder / "001.psd").layers],
            "002.psd": [i.id for i in PSDDocument(folder / "002.psd").layers],
            "10.psd": [i.id for i in PSDDocument(folder / "10.psd").layers],
        }
        out = resolve_report_path(folder, "Chapter01_Final_Review.pdf", "chapter01")
        assert out.name == "Chapter01_Final_Review.pdf"
        out2 = resolve_report_path(folder, "", "chapter01")
        assert out2.name == "chapter01.pdf"

        generate_report(task, layer_ids, out, image_provider=lambda rel: PSDDocument(folder / rel))
        assert out.exists() and out.stat().st_size > 1000
        with open(out, "rb") as f:
            assert f.read(5) == b"%PDF-"
        # 任务未完成 → 封面应包含“未完成”字样（PDF 内码验证粗略跳过，仅确认生成成功）
        print("PDF OK:", out, out.stat().st_size, "bytes")


def _decode_pdf_streams(pdf_bytes: bytes) -> list[bytes]:
    """解出 PDF 内容流（ASCII85/FlateDecode），返回字节列表。"""
    import base64
    import re
    import zlib

    out = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", pdf_bytes, re.S):
        body = m.group(1).strip()
        data = None
        try:
            data = zlib.decompress(body)
        except Exception:
            try:
                data = zlib.decompress(base64.a85decode(body, adobe=True))
            except Exception:
                try:
                    data = base64.a85decode(body, adobe=True)
                except Exception:
                    data = body
        if data:
            out.append(data)
    return out


def test_pdf_badge_number_outside_rect():
    """回归：PDF 徽标必须含纯数字（Helvetica-Bold）且位于红框外侧（需求 §52/§53）。"""
    import re
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        folder = _copy_fixtures(Path(tmp) / "chapter01")
        task, _ = persistence.create_task_folder(folder, sorted(folder.glob("*.psd")))
        doc1 = PSDDocument(folder / "001.psd")
        ids = [i.id for i in doc1.layers]
        task.set_status("001.psd", ids[1], FAILED)
        task.add_issue("001.psd", ids[1], "dialogue_01", "字体选择错误",
                       "这里应使用 Bold", (40, 60, 120, 60))
        out = folder / "badge.pdf"
        generate_report(
            task,
            {"001.psd": ids, "002.psd": [], "10.psd": []},
            out,
            image_provider=lambda rel: PSDDocument(folder / rel),
        )
        streams = _decode_pdf_streams(out.read_bytes())
        raw = out.read_bytes()
        # 徽标字体资源注册为 Helvetica-Bold（内容流中被子集引用为 /F<n>）
        assert b"Helvetica-Bold" in raw, "徽标未使用 Helvetica-Bold"
        annotated = next(
            (s for s in streams if b" re S" in s and b"(1)" in s), None
        )
        assert annotated is not None, "未找到带红框与徽标的页面流"

        text = annotated.decode("latin-1")
        # 徽标数字：纯 ASCII "(1)"，7pt（Helvetica-Bold 子集字体）
        assert re.search(r"/F\d+ 7 Tf", text)
        # 红框矩形与徽标数字基线位置（PDF y 轴向上：数字基线应在红框顶边之上）
        m_rect = re.search(
            r"([\d.]+) ([\d.]+) ([\d.]+) ([\d.]+) re S", text
        )
        m_num = re.search(
            r"([\d.]+) ([\d.]+) Tm \(1\) Tj", text
        )
        assert m_rect and m_num, "未解析到红框或徽标文本"
        rx, ry, rw, rh = (float(v) for v in m_rect.groups())
        num_y = float(m_num.group(2))
        assert num_y > ry + rh, (
            f"徽标应在红框外侧上方：徽标基线 y={num_y}，红框顶边 y={ry + rh}"
        )
        print("PDF badge OK：数字在框外", num_y, ">", ry + rh)


def test_default_report_name_output_folder():
    """回归：output 固定路径格式下默认名取上一级文件夹名。"""
    from mangaproof.report.generator import default_report_name

    # 文件夹任务
    assert default_report_name("folder", Path("/manga/Chapter01")) == "Chapter01"
    assert default_report_name("folder", Path("/manga/Chapter01/output")) == "Chapter01"
    assert default_report_name("folder", Path("/manga/output")) == "manga"
    assert default_report_name("folder", Path("/manga/Ch/OUTPUT")) == "Ch"  # 大小写不敏感
    assert default_report_name("folder", Path("/output")) == "output"       # 根目录回退

    # 单 PSD 任务
    assert default_report_name("single", Path("/manga/Ch"), "001.psd") == "001"
    assert default_report_name("single", Path("/manga/Ch/output"), "001.psd") == "Ch"
    assert default_report_name("single", Path("/output"), "001.psd") == "001"


def test_console_visibility_rules():
    """回归：直接运行 py 始终保留控制台；打包产物跟随 hide_console 设置。"""
    from mangaproof.console import apply_console_visibility, decide_console_hidden
    from mangaproof.config.settings import Settings

    # 直接运行 python：开关不生效，始终保留控制台
    assert decide_console_hidden(False, "win32", True) is False
    assert decide_console_hidden(False, "win32", False) is False
    # 打包产物（Windows）：默认隐藏，关闭开关后恢复显示
    assert decide_console_hidden(True, "win32", True) is True
    assert decide_console_hidden(True, "win32", False) is False
    # 非 Windows：运行时不可切换
    assert decide_console_hidden(True, "linux", True) is False
    assert decide_console_hidden(True, "darwin", False) is False
    # Linux 下 apply 为 no-op，不抛异常
    s = Settings()
    assert s.hide_console is True
    apply_console_visibility(s)


def test_settings_hide_console_persisted():
    """hide_console 设置随 settings.json 持久化。"""
    import json
    import tempfile

    from mangaproof.config.settings import SettingsManager

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "settings.json"
        sm = SettingsManager(path)
        assert sm.settings.hide_console is True  # 默认开启隐藏
        sm.settings.hide_console = False
        sm.save()
        sm2 = SettingsManager(path)
        assert sm2.settings.hide_console is False


def test_zoom_scaling_policy():
    """回归：缩放 <100% 平滑下采样，>=100% 最近邻显示像素块（类 PS）。"""
    from mangaproof.ui.viewer_widget import smooth_scaling_for

    assert smooth_scaling_for(0.2) is True
    assert smooth_scaling_for(0.6) is True
    assert smooth_scaling_for(0.99) is True
    assert smooth_scaling_for(1.0) is False
    assert smooth_scaling_for(2.5) is False
    assert smooth_scaling_for(8.0) is False


def test_visual_bounds_computed_flag():
    """回归：视觉边界缓存区分「未计算」与「计算后无内容」（透明图层不反复提取）。"""
    import numpy as np

    from mangaproof.psd.layer_model import LayerInfo

    info = LayerInfo(
        id="x", name="t", bounds=(0, 0, 10, 10), visible=True,
        layer_type="pixel",
        image_loader=lambda: np.zeros((10, 10, 4), dtype=np.uint8),  # 全透明
    )
    assert not info.has_visual_bounds()
    assert info.visual_bounds() is None
    assert info.has_visual_bounds()          # 已计算（结果为空）
    assert info.visual_bounds() is None      # 二次调用命中缓存，不再提取

    filled = LayerInfo(
        id="y", name="f", bounds=(0, 0, 10, 10), visible=True,
        layer_type="pixel",
        image_loader=lambda: np.full((10, 10, 4), 255, dtype=np.uint8),
    )
    assert filled.visual_bounds() == (0, 0, 10, 10)
    assert filled.has_visual_bounds()


if __name__ == "__main__":
    import traceback
    tests = [
        (k, v) for k, v in sorted(globals().items()) if k.startswith("test_")
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    sys.exit(1 if failed else 0)

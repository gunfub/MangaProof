"""GUI 冒烟测试（QT_QPA_PLATFORM=offscreen，无需显示器）。

覆盖：打开文件夹 → 自动恢复 → Enter// 状态流转 → 问题红框 →
←→↑↓ 导航 → Space 自动对比 → 自动保存 → 重启恢复 → 返修单生成。

运行：QT_QPA_PLATFORM=offscreen uv run python tests/test_gui_smoke.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QMessageBox, QSizePolicy, QWidget

from mangaproof.config.settings import SettingsManager
from mangaproof.review import persistence
from mangaproof.review.state import FAILED, PASSED, UNREVIEWED
from mangaproof.ui.dialogs import IssueDialog
from mangaproof.ui.main_window import MainWindow
from mangaproof.ui.task_loader import TaskLoadWorker

DATA_DIR = Path(__file__).parent / "data" / "chapter01"

app = QApplication.instance() or QApplication([])


def _copy_fixtures(dst: Path) -> Path:
    dst.mkdir(parents=True, exist_ok=True)
    for p in sorted(DATA_DIR.glob("*.psd")):
        shutil.copy2(p, dst / p.name)
    return dst


def _wait_for_task(window: MainWindow, timeout_s: float = 30.0) -> None:
    """打开为后台异步流程：轮询事件循环直到任务绑定且首文件打开完成。"""
    deadline = time.time() + timeout_s
    while (window.task is None or window._current_file == "") and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert window.task is not None, "任务加载超时（后台 worker 未完成）"
    assert window._current_file != "", "首文件异步打开超时"


def _wait_for_file(window: MainWindow, rel: str, timeout_s: float = 30.0) -> None:
    """文件切换为异步流程（未命中预加载时经后台线程 + 进度框）。"""
    deadline = time.time() + timeout_s
    while window._current_file != rel and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert window._current_file == rel, (
        f"切换文件超时：期望 {rel}，实际 {window._current_file}"
    )


def test_preload_worker() -> None:
    """预加载线程：open 请求缓存 merged/背景/目标图层；队列可整体替换。"""
    from mangaproof.psd.document import PSDDocument
    from mangaproof.ui.preloader import KIND_OPEN, PreloadWorker

    with tempfile.TemporaryDirectory() as tmp:
        folder = _copy_fixtures(Path(tmp) / "chapter01")
        docs = {
            p.name: PSDDocument(p) for p in sorted(folder.glob("*.psd"))
        }
        for doc in docs.values():
            doc.build_layers()   # 模拟 attach 已解析图层树

        done = []
        warm = {}
        worker = PreloadWorker(lambda rel: docs.get(rel))
        worker.task_done.connect(
            lambda rel, kind, ok, images: (
                done.append((rel, kind, ok)),
                warm.setdefault(rel, {}).update(
                    {k: v for k, v in (images or {}).items() if v is not None}
                ),
            )
        )
        worker.start()

        # open 请求：merged + 背景 + 目标图层像素/视觉边界全部预热
        layer_id = docs["001.psd"].layers[1].id
        worker.submit_open("001.psd", layer_id)
        deadline = time.time() + 30
        while not any(d[0] == "001.psd" for d in done) and time.time() < deadline:
            app.processEvents()
            time.sleep(0.02)
        assert ("001.psd", KIND_OPEN, True) in done
        doc = docs["001.psd"]
        # open 请求 = merged + 目标图层（关键路径）；背景图在阶段 B 补提
        assert doc.has_merged()
        assert doc.layer_image(layer_id) is not None
        assert doc.layers[1].visual_bounds() is not None
        # 显示用 QImage 已后台预热（切换后首帧免转换）
        assert warm.get("001.psd", {}).get("merged") is not None

        # 两阶段队列：阶段 A 先铺 merged，阶段 B 补背景图与图层像素
        jobs = [
            ("002.psd", docs["002.psd"].layers[0].id),
            ("10.psd", docs["10.psd"].layers[0].id),
        ]
        worker.set_preloads(jobs, list(jobs))
        deadline = time.time() + 30
        while time.time() < deadline:
            ready = all(
                d.has_merged() and d.bg_image() is not None
                for d in (docs["002.psd"], docs["10.psd"])
            )
            if ready:
                break
            app.processEvents()
            time.sleep(0.02)
        assert docs["002.psd"].has_merged() and docs["002.psd"].bg_image() is not None
        assert docs["10.psd"].has_merged() and docs["10.psd"].bg_image() is not None
        # 目标图层像素与视觉边界已预热（快速路径定位免等待）
        assert docs["002.psd"].layer_image(docs["002.psd"].layers[0].id) is not None
        assert docs["002.psd"].layers[0].visual_bounds() is not None
        assert docs["10.psd"].layer_image(docs["10.psd"].layers[0].id) is not None

        # 快速切换：cancel_open 丢弃未处理请求，新 open 请求立即生效
        worker.cancel_open()
        worker.submit_open("002.psd", docs["002.psd"].layers[0].id)
        deadline = time.time() + 30
        while not any(d[0] == "002.psd" and d[1] == KIND_OPEN for d in done) \
                and time.time() < deadline:
            app.processEvents()
            time.sleep(0.02)
        assert any(d[0] == "002.psd" and d[1] == KIND_OPEN for d in done)

        # WARM_ALL 哨兵：预热文档全部图层的视觉边界（图层切换免等待）
        from mangaproof.ui.preloader import WARM_ALL

        worker.set_preloads([], [("001.psd", WARM_ALL)])
        deadline = time.time() + 30
        while not all(
            info.has_visual_bounds() for info in docs["001.psd"].layers
        ) and time.time() < deadline:
            app.processEvents()
            time.sleep(0.02)
        assert all(
            info.has_visual_bounds() for info in docs["001.psd"].layers
        )

        worker.stop()
        worker.wait(5000)

    print("PASS test_preload_worker")


def test_task_loader_progress() -> None:
    """后台加载 worker：进度消息覆盖扫描/解析阶段，任务正确产出。"""
    with tempfile.TemporaryDirectory() as tmp:
        folder = _copy_fixtures(Path(tmp) / "chapter01")
        messages = []
        results = []
        worker = TaskLoadWorker("folder", folder)
        worker.progress.connect(lambda d, t, m: messages.append((d, t, m)))
        worker.succeeded.connect(lambda r: results.append(r))
        worker.start()
        deadline = time.time() + 30
        while not results and time.time() < deadline:
            app.processEvents()
            time.sleep(0.02)
        assert results, "worker 未完成"
        result = results[0]
        assert result.kind == "ok"
        assert result.task is not None
        assert any("扫描 PSD" in m for _, _, m in messages)
        parse_msgs = [m for _, _, m in messages if "解析 PSD" in m]
        assert len(parse_msgs) == 3, parse_msgs
        assert messages[-1][2] == "加载完成"

    print("PASS test_task_loader_progress")


def test_dark_titlebar_installed() -> None:
    """暗色标题栏：应用级过滤器安装成功；Linux 下应用调用为 no-op。"""
    from mangaproof.ui.dark_titlebar import apply_dark_title_bar, install_dark_titlebar

    install_dark_titlebar(app)
    assert getattr(app, "_dark_titlebar_filter", None) is not None

    probe = QWidget()
    probe.resize(200, 100)
    probe.show()
    app.processEvents()
    apply_dark_title_bar(probe)   # 非 Windows 平台必须静默 no-op
    probe.close()

    print("PASS test_dark_titlebar_installed")


def test_app_icon_loaded() -> None:
    """应用图标：从 ico/ico.png 加载到 QApplication，窗口默认继承。"""
    from mangaproof.main import apply_app_icon

    icon_path = Path(__file__).parent.parent / "ico" / "ico.png"
    assert icon_path.exists(), "缺少 ico/ico.png"
    result = apply_app_icon(app, icon_path)
    assert result == icon_path
    assert not app.windowIcon().isNull()

    # 图标缺失时不阻塞启动
    assert apply_app_icon(app, icon_path.parent / "missing.png") is None

    print("PASS test_app_icon_loaded")


def test_console_switch_platform_aware() -> None:
    """控制台开关仅 Windows 可用；其他平台置灰且不影响直接运行 py。"""
    import sys

    from mangaproof.config.settings import Settings
    from mangaproof.ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog(Settings())
    # 仅 Windows 打包产物支持运行时切换 → 非 Windows 上复选框应禁用
    assert dialog.console_check.isEnabled() == (sys.platform == "win32")
    assert dialog.console_check.isChecked()  # 默认开启隐藏

    print("PASS test_console_switch_platform_aware")


def test_font_loading() -> None:
    """统一字体：MiSans 注册为应用字体与主题首选字体族；缺失回退。"""
    from mangaproof.fonts import load_app_fonts
    from mangaproof.ui.theme import apply_dark_theme

    font_path = Path(__file__).parent.parent / "font" / "MiSans-Medium.ttf"
    assert font_path.exists(), "缺少 font/MiSans-Medium.ttf"

    family = load_app_fonts(app, [font_path])
    assert family == "MiSans", family
    assert app.font().family() == "MiSans"

    # 缺失时回退，不阻塞启动
    assert load_app_fonts(app, [font_path.parent / "missing.ttf"]) is None

    # 主题样式表字体族首位为 MiSans
    apply_dark_theme(app, primary_family=family)
    css = app.styleSheet()
    assert '"MiSans"' in css
    first = css.split("font-family:", 1)[1].strip().split(";", 1)[0]
    assert first.startswith('"MiSans"'), first

    print("PASS test_font_loading")


def test_license_page() -> None:
    """第三方许可页：与「关于」分离，覆盖全部依赖/库/打包工具/字体。"""
    from mangaproof.third_party import build_third_party_items
    from mangaproof.ui.license_dialog import LicenseDialog

    items = build_third_party_items()
    names = [i.name for i in items]
    # 覆盖：运行时、PSD 解析、图像分析、GUI、PDF、图像处理、打包工具及其依赖、字体
    for keyword in ("Python", "psd-tools", "NumPy", "PySide6", "reportlab",
                    "Pillow", "PyInstaller", "altgraph", "MiSans"):
        assert any(keyword in n for n in names), f"缺少组件：{keyword}"
    for item in items:
        assert item.name and item.version and item.spdx and item.copyright
        assert item.homepage.startswith("http")
        assert len(item.license_text) > 100
    # MiSans 条目包含完整协议与出处
    misans = next(i for i in items if "MiSans" in i.name)
    assert "小米" in misans.license_text and "hyperos.mi.com" in misans.homepage
    # 版本解析：已安装包应返回真实版本
    psd = next(i for i in items if "psd-tools" in i.name)
    assert psd.version == "1.18.0"

    # 对话框：组件列表与详情联动
    dialog = LicenseDialog()
    assert dialog.component_list.count() == len(items)
    dialog.component_list.setCurrentRow(1)
    app.processEvents()
    detail = dialog.detail_view.toPlainText()
    assert "许可证" in detail and "psd-tools" in detail
    dialog.close()

    print("PASS test_license_page")


def test_full_workflow() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        folder = _copy_fixtures(root / "chapter01")

        sm = SettingsManager(root / "settings.json")
        window = MainWindow(sm)
        window.resize(1200, 800)
        window.show()
        app.processEvents()

        with patch.object(
            QMessageBox, "information", return_value=QMessageBox.StandardButton.Ok
        ):
            window.open_folder(folder)
        _wait_for_task(window)
        app.processEvents()

        assert window.task is not None
        assert window.task.task_type == "folder"
        assert window._current_file == "001.psd"
        assert window._current_index == 0  # 第一个未监制图层
        # 第一个图层是 bg（整幅画布）→ 自动缩放为画布比例、中心在画布中心
        assert 0.5 < window.viewer.camera.zoom < 1.0
        assert abs(window.viewer.camera.center_x - 200.0) < 1.0
        assert abs(window.viewer.camera.center_y - 300.0) < 1.0

        doc = window.current_doc
        ids = [info.id for info in doc.layers]

        # 回归：统计面板卡片必须按富文本渲染（否则 HTML 源码会直接显示）
        for cell in window.stats_panel.total_cells.values():
            assert cell.textFormat() == Qt.TextFormat.RichText
            assert "<br/>" in cell.text()
        assert "通过" in window.stats_panel.total_cells["passed"].text()
        assert "3" in window.stats_panel.total_cells["files"].text()  # 3 个 PSD
        assert "8" in window.stats_panel.total_cells["layers"].text()  # 8 个图层

        # 回归：按钮动态显示当前绑定（需求 §30），重绑定后文案跟随更新
        assert "Enter" in window.issue_panel.pass_btn.text()
        assert "/" in window.issue_panel.fail_btn.text()
        assert "Ctrl+Return" in window.issue_panel.custom_btn.text()
        assert "R" in window.issue_panel.add_btn.text()
        assert "居中错误" in window.issue_panel.add_btn.toolTip()
        assert "Enter" in window.hint_label.text() and "↑/↓" in window.hint_label.text()
        window.settings.keybindings["pass_layer"] = "Ctrl+P"
        window.settings.keybindings["fail_layer"] = "F2"
        window._rebuild_shortcuts()
        assert "Ctrl+P" in window.issue_panel.pass_btn.text()
        assert "F2" in window.issue_panel.fail_btn.text()
        window.settings.keybindings["pass_layer"] = "Return"
        window.settings.keybindings["fail_layer"] = "/"
        window._rebuild_shortcuts()
        assert "Enter" in window.issue_panel.pass_btn.text()
        assert "/" in window.issue_panel.fail_btn.text()

        # 预加载状态标签：两阶段独立显示
        # （图像预加载=阶段A / 图层预热=阶段B / 双完成后切换零等待）
        assert window.preload_label.text() != "", "预加载标签应有内容"
        deadline = time.time() + 30
        while (
            window.preload_label.text() != "图像预加载完成"
            or window.warmup_label.text() != "图层预热完成"
        ) and time.time() < deadline:
            app.processEvents()
            time.sleep(0.02)
        assert window.preload_label.text() == "图像预加载完成"
        assert window.warmup_label.text() == "图层预热完成"
        # 完成后，邻域文件的目标图层像素必须已预热（否则切换会卡 UI 线程）
        for rel, doc in window._docs.items():
            if rel == window._current_file:
                continue
            index = window._choose_layer_index(rel, restore=False)
            if index is None:
                continue
            lid = window._layer_ids_by_file[rel][index]
            info = doc.layer_by_id(lid)
            assert info is not None and info.has_visual_bounds(), (
                f"预加载完成后 {rel} 目标图层未预热"
            )
        # 当前文件全部图层视觉边界已预热（←→ 图层切换零等待）
        assert all(
            info.has_visual_bounds() for info in window.current_doc.layers
        )

        # Enter → 通过并跳到下一个未监制
        window.mark_pass()
        app.processEvents()
        assert window.task.status_of("001.psd", ids[0]) == PASSED
        assert window._current_index == 1
        # 切换到 dialogue_01（120x60）→ 视觉中心定位 + 按比例缩放（需求 §17、§20）
        assert abs(window.viewer.camera.center_x - 100.0) < 1.0
        assert abs(window.viewer.camera.center_y - 90.0) < 1.0
        assert window.viewer.camera.zoom > 2.0

        # / → 未通过，停留在当前图层
        window.mark_fail()
        app.processEvents()
        assert window.task.status_of("001.psd", ids[1]) == FAILED
        assert window._current_index == 1

        # 方式 B：拖框 → 问题对话框（patched）→ 问题入库 + 红框世界坐标
        with patch.object(
            IssueDialog, "exec", return_value=IssueDialog.DialogCode.Accepted
        ), patch.object(
            IssueDialog, "result_values", return_value=("字体选择错误", "这里应使用 Bold")
        ):
            window._on_rect_drawn(40, 60, 120, 60)
        assert len(window.task.issues) == 1
        issue = window.task.issues[0]
        assert issue.issue_no == 1
        assert issue.rect == (40.0, 60.0, 120.0, 60.0)
        assert len(window.viewer._issues) == 1  # Overlay 已挂到 Viewer

        # 方式 A：快捷键类型 → pending → 拖框 → 问题入库（需求 §35、§37）
        window._on_issue_key("漏字")
        assert window.viewer.pending_type == "漏字"
        with patch.object(
            IssueDialog, "exec", return_value=IssueDialog.DialogCode.Accepted
        ), patch.object(
            IssueDialog, "result_values", return_value=("漏字", "")
        ):
            window._on_issue_drawn("漏字", 60, 240, 140, 60)
        assert len(window.task.issues) == 2
        assert window.task.issues[1].type == "漏字"

        # Enter on FAILED → 保持未通过，跳到下一个未监制（需求 §38）
        window.mark_pass()
        assert window.task.status_of("001.psd", ids[1]) == FAILED
        assert window._current_index == 2

        # ← → 图层导航
        window.prev_layer()
        assert window._current_index == 1
        window.next_layer()
        assert window._current_index == 2

        # Esc 取消 pending 批注操作（需求 §30）
        window._on_issue_key("错字")
        assert window.viewer.pending_type == "错字"
        window.cancel_operation()
        assert window.viewer.pending_type is None

        # ↑↓ PSD 导航（异步切换：预加载命中走快路径，未命中经后台线程）
        window.next_psd()
        _wait_for_file(window, "002.psd")
        # 切换后 Viewer 已有后台预热好的显示图（首帧免转换）
        assert any(key[1] == "merged" for key in window.viewer._qimages)
        window.prev_psd()
        _wait_for_file(window, "001.psd")

        # Space 自动对比：merged ↔ bg 闪切
        window.toggle_compare()
        assert window._compare.is_running
        seen = set()
        for _ in range(10):
            seen.add(window.viewer.source)
            app.processEvents()
            time.sleep(0.12)
        assert "bg" in seen and "merged" in seen
        window.toggle_compare()
        assert not window._compare.is_running
        assert window.viewer.source == "merged"  # 停止后恢复 Original（需求 §26）

        # 保存 + 磁盘校验
        window.save_task()
        progress = persistence.progress_path_for_folder(folder)
        assert progress.exists()
        loaded = persistence.load_task(progress)
        assert loaded.schema_version == 1
        assert loaded.status_of("001.psd", ids[0]) == PASSED
        assert loaded.status_of("001.psd", ids[1]) == FAILED
        assert len(loaded.issues) == 2
        window.close()
        app.processEvents()

        # ---- 重启恢复（需求 §6：自动恢复进度，无需手动加载） ----
        sm2 = SettingsManager(root / "settings2.json")
        window2 = MainWindow(sm2)
        window2.resize(1200, 800)
        window2.show()
        app.processEvents()
        with patch.object(
            QMessageBox, "information", return_value=QMessageBox.StandardButton.Ok
        ):
            window2.open_folder(folder)
        _wait_for_task(window2)
        app.processEvents()

        assert window2.task.task_id == loaded.task_id  # 恢复的是同一个任务
        assert window2._current_file == "001.psd"
        assert window2._current_index == 2  # 上次工作位置
        assert window2.task.status_of("001.psd", ids[0]) == PASSED
        assert window2.task.status_of("001.psd", ids[1]) == FAILED
        assert len(window2.task.issues_for("001.psd", ids[1])) == 2

        # 生成返修单（非交互）
        with patch.object(
            QMessageBox, "information", return_value=QMessageBox.StandardButton.Ok
        ):
            window2._generate_report(interactive=False)
        out = folder / "chapter01.pdf"
        assert out.exists() and out.stat().st_size > 1000
        with open(out, "rb") as f:
            assert f.read(5) == b"%PDF-"

        window2.close()
        app.processEvents()

    print("PASS test_full_workflow")


def test_compare_controller_unit() -> None:
    """CompareController：频率换算、间隔设置、手动切换、打断。"""
    from mangaproof.compare.controller import (
        BG_ONLY,
        DEFAULT_SPEED_HZ,
        ORIGINAL,
        CompareController,
        hz_to_interval_ms,
    )

    # 档位 → 每张停留时长（均为整数毫秒）
    assert hz_to_interval_ms(1) == 1000
    assert hz_to_interval_ms(2) == 500
    assert hz_to_interval_ms(4) == 250
    assert hz_to_interval_ms(5) == 200
    assert hz_to_interval_ms(8) == 125

    c = CompareController()
    assert c.interval_ms == hz_to_interval_ms(DEFAULT_SPEED_HZ)
    c.set_interval_ms(200)
    assert c.interval_ms == 200
    c.set_interval_ms(0)   # 下限钳制
    assert c.interval_ms == 1

    # 手动挡：swap_once 不启动定时器
    assert c.display_state == ORIGINAL
    c.swap_once()
    assert c.display_state == BG_ONLY and not c.is_running
    c.swap_once()
    assert c.display_state == ORIGINAL

    # interrupt：手动挡下强制回原图
    c.swap_once()
    c.interrupt()
    assert c.display_state == ORIGINAL and not c.is_running

    # interrupt：自动挡运行中 → 停止并回原图
    c.set_interval_ms(250)
    c.start()
    assert c.is_running
    c.interrupt()
    assert not c.is_running and c.display_state == ORIGINAL

    print("PASS test_compare_controller_unit")


def test_settings_compare_options() -> None:
    """设置对话框：对比模式/速度档位、手动挡灰显速度、应用与恢复默认。"""
    from mangaproof.config.settings import Settings
    from mangaproof.ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog(Settings())
    # 默认：自动模式，速度=正常 4 次/秒
    assert dialog.compare_mode_combo.currentData() == "auto"
    assert dialog.compare_speed_combo.isEnabled()
    assert dialog.compare_speed_combo.currentData() == 4
    assert "4 次/秒（每张 250ms）" in dialog.compare_speed_combo.currentText()

    # 切手动挡 → 速度灰显
    dialog.compare_mode_combo.setCurrentIndex(
        dialog.compare_mode_combo.findData("manual")
    )
    assert not dialog.compare_speed_combo.isEnabled()

    s = Settings()
    dialog.apply_to(s)
    assert s.compare_mode == "manual"

    # 自动挡选 8 次/秒 → 应用到设置
    dialog.compare_mode_combo.setCurrentIndex(
        dialog.compare_mode_combo.findData("auto")
    )
    assert dialog.compare_speed_combo.isEnabled()
    dialog.compare_speed_combo.setCurrentIndex(
        dialog.compare_speed_combo.findData(8)
    )
    s2 = Settings()
    dialog.apply_to(s2)
    assert s2.compare_mode == "auto" and s2.compare_speed_hz == 8

    # 恢复默认 → 自动 + 4 次/秒
    dialog._reset_defaults()
    s3 = Settings()
    dialog.apply_to(s3)
    assert s3.compare_mode == "auto" and s3.compare_speed_hz == 4

    # 自定义速度（档位外数值）也能回显选中
    dialog.compare_speed_combo.setCurrentIndex(
        dialog.compare_speed_combo.findData(8)
    )
    s_custom = Settings()
    s_custom.compare_speed_hz = 3
    dialog2 = SettingsDialog(s_custom)
    assert dialog2.compare_speed_combo.currentData() == 3
    s_custom_out = Settings()
    dialog2.apply_to(s_custom_out)
    assert s_custom_out.compare_speed_hz == 3

    print("PASS test_settings_compare_options")


def test_compare_manual_mode_workflow() -> None:
    """手动挡：Space/按钮按一下切一次；Esc、通过等操作打断并强制回原图。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        folder = _copy_fixtures(root / "chapter01")

        sm = SettingsManager(root / "settings.json")
        sm.settings.compare_mode = "manual"
        window = MainWindow(sm)
        window.resize(1200, 800)
        window.show()
        app.processEvents()

        # 工具栏按钮文案随模式变化
        assert window.action_compare.text() == "对比切换 (Space)"

        with patch.object(
            QMessageBox, "information", return_value=QMessageBox.StandardButton.Ok
        ):
            window.open_folder(folder)
        _wait_for_task(window)
        app.processEvents()

        # 等待背景图预热（对比依赖 bg 图）
        deadline = time.time() + 30
        while window.current_doc.bg_image() is None and time.time() < deadline:
            app.processEvents()
            time.sleep(0.02)
        assert window.current_doc.bg_image() is not None

        # 快捷键：按一下切一次，无运行状态
        assert window.viewer.source == "merged"
        window.toggle_compare()
        assert window.viewer.source == "bg"
        assert not window._compare.is_running
        window.toggle_compare()
        assert window.viewer.source == "merged"

        # 工具栏按钮点击：切换一次且不保持勾选
        window.action_compare.setChecked(True)
        app.processEvents()
        assert window.viewer.source == "bg"
        assert not window.action_compare.isChecked()

        # 打断：Esc 回原图
        window.cancel_operation()
        assert window.viewer.source == "merged"

        # 打断：通过当前图层（mark_pass）回原图
        window.toggle_compare()
        assert window.viewer.source == "bg"
        window.mark_pass()
        assert window.viewer.source == "merged"

        # 切回自动挡：按钮文案与 Space 行为恢复自动闪切
        window.settings.compare_mode = "auto"
        window._apply_compare_settings()
        assert window.action_compare.text() == "自动对比 (Space)"
        window.toggle_compare()
        assert window._compare.is_running
        window.toggle_compare()
        assert not window._compare.is_running
        assert window.viewer.source == "merged"

        window.close()
        app.processEvents()

    print("PASS test_compare_manual_mode_workflow")


def test_settings_keybindings_subdialog() -> None:
    """快捷键子对话框：独立编辑/恢复默认；主对话框 OK 才写回。"""
    from PySide6.QtGui import QKeySequence

    from mangaproof.config.settings import (
        DEFAULT_ISSUE_TYPES,
        DEFAULT_KEYBINDINGS,
        DEFAULT_WHEEL_MODE,
        Settings,
    )
    from mangaproof.ui.settings_dialog import KeybindingsDialog, SettingsDialog

    s = Settings()
    # 未打开子对话框 → 主对话框 apply 不动快捷键
    dlg = SettingsDialog(s)
    dlg.apply_to(s)
    assert s.keybindings["prev_psd"] == "Up"

    # 子对话框编辑并应用
    kb = KeybindingsDialog(s)
    kb._core_edits["prev_psd"].setKeySequence(QKeySequence("F1"))
    kb._issue_edits[0].setKeySequence(QKeySequence(""))
    kb.apply_to(s)
    assert s.keybindings["prev_psd"] == "F1"
    assert s.issue_types[0]["key"] == ""

    # 子对话框内恢复默认快捷键
    kb._reset_defaults()
    kb.apply_to(s)
    assert s.keybindings["prev_psd"] == DEFAULT_KEYBINDINGS["prev_psd"]
    assert s.issue_types[0]["key"] == DEFAULT_ISSUE_TYPES[0]["key"]
    assert s.custom_comment_key == DEFAULT_KEYBINDINGS["custom_comment"]

    # 主对话框"恢复默认设置"→ apply 后快捷键回默认
    s2 = Settings()
    s2.keybindings["prev_psd"] = "F2"
    dlg2 = SettingsDialog(s2)
    dlg2._reset_defaults()
    dlg2.apply_to(s2)
    assert s2.keybindings["prev_psd"] == DEFAULT_KEYBINDINGS["prev_psd"]
    assert s2.custom_comment_key == DEFAULT_KEYBINDINGS["custom_comment"]

    # 主对话框接受过子对话框 → apply 写回子对话框修改
    s3 = Settings()
    dlg3 = SettingsDialog(s3)
    kb3 = KeybindingsDialog(s3)
    kb3._core_edits["next_psd"].setKeySequence(QKeySequence("F3"))
    dlg3._kb_dialog = kb3
    dlg3.apply_to(s3)
    assert s3.keybindings["next_psd"] == "F3"

    # 主对话框 Cancel（不 apply）→ 设置不变
    s4 = Settings()
    dlg4 = SettingsDialog(s4)
    dlg4._kb_dialog = kb3
    assert s4.keybindings["next_psd"] == DEFAULT_KEYBINDINGS["next_psd"]

    # 滚轮模式下拉框：默认上下移动；切换缩放后 apply 写回；恢复默认复位
    s5 = Settings()
    assert s5.wheel_mode == DEFAULT_WHEEL_MODE == "pan"
    dlg5 = SettingsDialog(s5)
    zidx = dlg5.wheel_mode_combo.findData("zoom")
    assert zidx >= 0
    dlg5.wheel_mode_combo.setCurrentIndex(zidx)
    dlg5.apply_to(s5)
    assert s5.wheel_mode == "zoom"
    dlg5._reset_defaults()
    dlg5.apply_to(s5)
    assert s5.wheel_mode == DEFAULT_WHEEL_MODE

    print("PASS test_settings_keybindings_subdialog")


def _send_wheel(
    viewer: QWidget,
    angle: tuple[int, int] = (0, 0),
    pixel: tuple[int, int] = (0, 0),
    modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
) -> None:
    """构造真实 QWheelEvent 并同步派发。"""
    w, h = viewer.width(), viewer.height()
    ev = QWheelEvent(
        QPointF(w / 2.0, h / 2.0),  # pos
        QPointF(w / 2.0, h / 2.0),  # globalPos
        QPoint(*pixel),             # pixelDelta
        QPoint(*angle),             # angleDelta
        Qt.MouseButton.NoButton,
        modifiers,
        Qt.ScrollPhase.NoScrollPhase,
        False,                      # inverted
    )
    QApplication.sendEvent(viewer, ev)


def test_wheel_modes() -> None:
    """滚轮交互：裸滚轮默认上下平移/可设置切换为缩放；Ctrl=缩放；
    Alt=左右平移；触控板双指滚（pixelDelta）恒为双轴平移。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        folder = _copy_fixtures(root / "chapter01")
        sm = SettingsManager(root / "settings.json")
        window = MainWindow(sm)
        window.resize(1200, 800)
        window.show()
        app.processEvents()

        with patch.object(
            QMessageBox, "information", return_value=QMessageBox.StandardButton.Ok
        ):
            window.open_folder(folder)
        _wait_for_task(window)
        app.processEvents()

        viewer = window.viewer
        cam = viewer.camera
        assert viewer._doc is not None

        cx0, cy0, zoom0 = cam.center_x, cam.center_y, cam.zoom

        # 默认：裸鼠标滚轮 → 上下平移，不缩放
        _send_wheel(viewer, angle=(0, 120))
        assert cam.zoom == zoom0
        assert cam.center_x == cx0
        assert cam.center_y < cy0  # 滚轮向上 → 视图向上

        # Ctrl+滚轮 → 缩放（锚点=视口中心 → 相机中心不动）
        cx1, cy1, zoom1 = cam.center_x, cam.center_y, cam.zoom
        _send_wheel(viewer, angle=(0, 120), modifiers=Qt.KeyboardModifier.ControlModifier)
        assert cam.zoom == pytest.approx(zoom1 * 1.25)
        assert cam.center_x == cx1 and cam.center_y == cy1

        # Alt+滚轮 → 左右平移（纵向刻度映射为横向）
        cx2, cy2, zoom2 = cam.center_x, cam.center_y, cam.zoom
        _send_wheel(viewer, angle=(0, 120), modifiers=Qt.KeyboardModifier.AltModifier)
        assert cam.zoom == zoom2
        assert cam.center_x < cx2
        assert cam.center_y == cy2

        # 设置切换：裸滚轮 → 缩放
        viewer.set_wheel_mode("zoom")
        cx3, cy3, zoom3 = cam.center_x, cam.center_y, cam.zoom
        _send_wheel(viewer, angle=(0, 120))
        assert cam.zoom == pytest.approx(zoom3 * 1.25)
        assert cam.center_x == cx3 and cam.center_y == cy3

        # 触控板双指滚（pixelDelta）：无论 wheel_mode 如何，恒为双轴平移
        cx4, cy4, zoom4 = cam.center_x, cam.center_y, cam.zoom
        _send_wheel(viewer, pixel=(100, 60))
        assert cam.zoom == zoom4
        assert cam.center_x < cx4 and cam.center_y < cy4

        # 恢复默认模式后，裸滚轮再次为上下平移
        viewer.set_wheel_mode("pan")
        cx5, cy5, zoom5 = cam.center_x, cam.center_y, cam.zoom
        _send_wheel(viewer, angle=(0, -120))
        assert cam.zoom == zoom5
        assert cam.center_x == cx5 and cam.center_y > cy5

        window.close()

    print("PASS test_wheel_modes")


def test_issue_panel_long_layer_name() -> None:
    """当前图层问题面板：超长图层名单行省略显示（不撑宽、不换行）。"""
    from mangaproof.ui.issue_panel import IssuePanel

    panel = IssuePanel()
    long_name = "同一段文字内容被用作图层名" * 50
    panel.set_current(long_name, UNREVIEWED, [])
    assert panel.layer_name_label.text() == f"图层：{long_name}"
    assert panel.layer_name_label.wordWrap() is False
    assert panel.layer_name_label.sizePolicy().horizontalPolicy() == (
        QSizePolicy.Policy.Ignored
    )
    assert panel.layer_name_label.toolTip() == f"图层：{long_name}"

    print("PASS test_issue_panel_long_layer_name")


if __name__ == "__main__":
    import traceback

    failed = 0
    for name, fn in [
        (k, v) for k, v in sorted(globals().items()) if k.startswith("test_")
    ]:
        try:
            fn()
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    sys.exit(1 if failed else 0)

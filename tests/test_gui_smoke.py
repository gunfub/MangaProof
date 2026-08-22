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

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from mangaproof.config.settings import SettingsManager
from mangaproof.review import persistence
from mangaproof.review.state import FAILED, PASSED
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
    """打开为后台异步流程：轮询事件循环直到任务绑定完成。"""
    deadline = time.time() + timeout_s
    while window.task is None and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert window.task is not None, "任务加载超时（后台 worker 未完成）"


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

        # ↑↓ PSD 导航
        window.next_psd()
        app.processEvents()
        assert window._current_file == "002.psd"
        window.prev_psd()
        assert window._current_file == "001.psd"

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

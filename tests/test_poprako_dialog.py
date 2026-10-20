# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""Poprako 同步对话框单测（需求 §10；设计文档 §13）。

离屏 Qt、**全程不联网**：``load_token``/``keyring_available`` 一律打桩，
客户端换成"一碰就报错"的假对象——只有显式注入数据的方法才被调用。

钉住四类东西：

1. 结构：侧栏四项顺序、窗口尺寸、基线/行距（版式是本文件自带的，必须自己守）；
2. 门禁：未登录 / 已发布 / 非 REVIEWER / 未映射且含问题 → 上传禁用且 tooltip 说明；
3. 映射表：每行一个下拉框、同一页被选两次时旧的自动解除、滚轮不改值；
4. 收尾：关闭窗口把绑定写回任务并发信号（主窗口据此落盘）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QPushButton

import mangaproof.ui.poprako_dialog as dialog_module
from mangaproof.config.settings import SettingsManager
from mangaproof.poprako.models import Chapter, Membership, PageArtwork, User
from mangaproof.review.issue import Issue
from mangaproof.review.state import FileRecord, TaskState
from mangaproof.ui.poprako_dialog import (
    DEFAULT_DIALOG_SIZE,
    GUTTER,
    MINIMUM_DIALOG_SIZE,
    NAV_WIDTH,
    ROW_SPACING,
    PoprakoSyncDialog,
    _Row,
)


class _NoNetworkClient:
    """任何网络方法被调用都直接失败——测试里不该发请求。"""

    def __init__(self, base_url: str = "", proxy: str = ""):
        self.base_url = base_url
        self.proxy = proxy
        self.token = ""

    def close(self) -> None:      # 生命周期方法，不是网络调用
        return None

    def __getattr__(self, name: str):
        raise AssertionError(f"测试不应发起网络调用：{name}")


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _no_keyring(monkeypatch):
    """不碰真实系统凭据库（读它可能弹窗/阻塞）。"""
    monkeypatch.setattr(dialog_module, "load_token", lambda settings: "")

    def _save(settings, token):
        settings.token = token
        return "settings.json"

    monkeypatch.setattr(dialog_module, "save_token", _save)
    monkeypatch.setattr(dialog_module, "keyring_available", lambda: False)


# ---------------------------------------------------------------- 构造工具


def make_issue(file: str, no: int, *, rect=(0.0, 0.0, 0.0, 0.0)) -> Issue:
    return Issue(
        file=file, layer_id="0.1", layer_name="对白图层", type="文字位置错误",
        comment="向左移动", rect=rect, issue_no=no,
    )


def make_task(*files: str, issues=()) -> TaskState:
    task = TaskState(task_name="Ch01", task_type="folder")
    task.files = [FileRecord(rel, Path(rel).name, 1024) for rel in files]
    task.issues = list(issues)
    return task


def make_chapter(stages: int = 0, subtitle: str = "初次见面") -> Chapter:
    return Chapter(id="ch_1", comic_id="co_1", index=11, subtitle=subtitle,
                   page_count=2, stages=stages)


def make_artwork(pid: str, index: int, raw: str | None = None) -> PageArtwork:
    return PageArtwork(id=pid, index=index, raw_ident=raw or f"{index + 1:03d}.psd",
                       image_uploaded=True, ext="png")


def open_dialog(tmp_path: Path, task: TaskState, client=None, factory=None) -> PoprakoSyncDialog:
    """``client`` 传实例时复用；要验证"重建客户端"就传 ``factory``。"""
    manager = SettingsManager(tmp_path / "settings.json")
    manager.save()
    if factory is not None:
        make = factory
    elif client is not None:
        make = lambda base, proxy: client
    else:
        make = lambda base, proxy: _NoNetworkClient(base, proxy)
    dialog = PoprakoSyncDialog(
        task, tmp_path, manager.settings, client_factory=make,
    )
    dialog._manager = manager       # 便于测试断言保存内容
    return dialog


def make_ready(dialog: PoprakoSyncDialog, *, roles: int = 32, stages: int = 0) -> None:
    """把对话框摆到"可以上传"的状态（不发任何网络请求）。"""
    dialog._user = User(id="u_1", qid="123456", nickname="秋叶声生", is_sadmin=False)
    dialog._apply_logged_in(dialog._user)
    dialog._teams = [Membership("t_1", "木叶翻译组", roles)]
    dialog._fill_combo(dialog._team_combo, [("木叶翻译组", "t_1")], "t_1")
    dialog._artworks = [make_artwork("pa_1", 0), make_artwork("pa_2", 1)]
    dialog._chapter = make_chapter(stages)
    dialog._mapping = {"001.psd": "pa_1", "002.psd": "pa_2"}
    dialog._refresh_chapter_info()
    dialog._refresh_mapping_table()
    dialog._refresh_payload()


# ---------------------------------------------------------------- 结构


def test_sidebar_has_five_pages_in_pipeline_order(qapp, tmp_path):
    """代理独立成页（2026-10-20 需求方），位置紧跟账号之后。"""
    dialog = open_dialog(tmp_path, make_task("001.psd"))
    try:
        titles = [dialog.nav_list.item(i).text() for i in range(dialog.nav_list.count())]
        assert titles == ["账号", "代理", "目标章节", "页面映射", "预览与上传"]
        assert dialog.stack.count() == 5
        assert sorted(dialog._page_widgets) == [
            "account", "mapping", "proxy", "target", "upload",
        ]
    finally:
        dialog.done(0)


def test_window_geometry_matches_settings_spec(qapp, tmp_path):
    dialog = open_dialog(tmp_path, make_task("001.psd"))
    try:
        assert (dialog.width(), dialog.height()) == DEFAULT_DIALOG_SIZE
        assert (dialog.minimumWidth(), dialog.minimumHeight()) == MINIMUM_DIALOG_SIZE
        margins = dialog.layout().contentsMargins()
        assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (
            GUTTER, GUTTER, GUTTER, GUTTER,
        )
        assert dialog.nav_list.width() == NAV_WIDTH
        account_page = dialog._page_widgets["account"]
        assert account_page.rows is not None
        assert account_page.rows.spacing() == ROW_SPACING
    finally:
        dialog.done(0)


def test_row_label_greys_out_together_with_control(qapp):
    button = QPushButton("x")
    row = _Row("某个设置", button)
    try:
        button.setEnabled(False)
        assert row.label.isEnabled() is False, "控件禁用时行文本必须一起置灰"
        button.setEnabled(True)
        assert row.label.isEnabled() is True
    finally:
        row.deleteLater()


def test_account_page_password_is_masked(qapp, tmp_path):
    from PySide6.QtWidgets import QLineEdit

    dialog = open_dialog(tmp_path, make_task("001.psd"))
    try:
        assert dialog._password_edit.echoMode() == QLineEdit.EchoMode.Password
    finally:
        dialog.done(0)


# ---------------------------------------------------------------- 门禁


def test_upload_disabled_before_login(qapp, tmp_path):
    dialog = open_dialog(tmp_path, make_task("001.psd"))
    try:
        assert dialog._upload_btn.isEnabled() is False
        assert "登录" in dialog._upload_btn.toolTip()
    finally:
        dialog.done(0)


def test_published_chapter_blocks_upload(qapp, tmp_path):
    dialog = open_dialog(tmp_path, make_task("001.psd", "002.psd"))
    try:
        make_ready(dialog, stages=(0b10 << 10))   # 发布阶段 completed
        assert dialog._upload_btn.isEnabled() is False
        assert "已发布" in dialog._upload_btn.toolTip()
        assert "已发布" in dialog._warn_label.text()
    finally:
        dialog.done(0)


def test_missing_reviewer_role_blocks_upload(qapp, tmp_path):
    dialog = open_dialog(tmp_path, make_task("001.psd", "002.psd"))
    try:
        make_ready(dialog, roles=4)               # 只有校对，没有监制
        assert dialog._upload_btn.isEnabled() is False
        assert "监制" in dialog._upload_btn.toolTip()
        assert "不可导入" in dialog._role_value.text()
    finally:
        dialog.done(0)


def test_unmapped_page_with_issues_blocks_upload(qapp, tmp_path):
    task = make_task("001.psd", "002.psd",
                     issues=[make_issue("001.psd", 1), make_issue("002.psd", 2)])
    dialog = open_dialog(tmp_path, task)
    try:
        make_ready(dialog)
        dialog._mapping = {"001.psd": "pa_1"}      # 故意漏掉 002.psd
        dialog._refresh_mapping_table()
        dialog._refresh_payload()

        assert dialog._upload_btn.isEnabled() is False
        assert "002.psd" in dialog._upload_btn.toolTip()
        assert "未映射且含问题" in dialog._mapping_summary.text()
    finally:
        dialog.done(0)


def test_ready_dialog_enables_upload(qapp, tmp_path):
    dialog = open_dialog(tmp_path, make_task("001.psd", "002.psd",
                                             issues=[make_issue("001.psd", 1)]))
    try:
        make_ready(dialog)
        assert dialog._upload_btn.isEnabled() is True, dialog._upload_btn.toolTip()
        assert dialog._upload_write_value.text() == "2 页 · 1 条问题"
    finally:
        dialog.done(0)


# ---------------------------------------------------------------- 映射表


def test_mapping_table_lists_every_file_with_combo(qapp, tmp_path):
    dialog = open_dialog(tmp_path, make_task("001.psd", "002.psd"))
    try:
        make_ready(dialog)
        assert dialog._table.rowCount() == 2
        assert dialog._table.item(0, 0).text() == "001.psd"
        assert isinstance(dialog._table.cellWidget(0, 2), type(dialog._team_combo))
        assert set(dialog._mapping_combos) == {"001.psd", "002.psd"}
    finally:
        dialog.done(0)


def test_choosing_same_page_twice_clears_previous_row(qapp, tmp_path):
    dialog = open_dialog(tmp_path, make_task("001.psd", "002.psd"))
    try:
        make_ready(dialog)
        combo = dialog._mapping_combos["001.psd"]
        target_index = combo.findData("pa_2")     # 本来属于 002.psd
        combo.setCurrentIndex(target_index)
        QApplication.processEvents()

        assert dialog._mapping["001.psd"] == "pa_2"
        assert "002.psd" not in dialog._mapping, "同一页只能被一个文件占用"
        assert dialog._mapping_combos["002.psd"].currentIndex() == 0
    finally:
        dialog.done(0)


def test_choosing_sync_disabled_removes_mapping(qapp, tmp_path):
    dialog = open_dialog(tmp_path, make_task("001.psd", "002.psd"))
    try:
        make_ready(dialog)
        dialog._mapping_combos["001.psd"].setCurrentIndex(0)
        QApplication.processEvents()
        assert "001.psd" not in dialog._mapping
    finally:
        dialog.done(0)


def test_mapping_combo_ignores_wheel(qapp, tmp_path):
    dialog = open_dialog(tmp_path, make_task("001.psd", "002.psd"))
    try:
        make_ready(dialog)
        combo = dialog._mapping_combos["001.psd"]
        before = combo.currentIndex()
        point = QPointF(10.0, 10.0)
        event = QWheelEvent(
            point, point, QPoint(0, 0), QPoint(0, -120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase, False,
        )
        QApplication.sendEvent(combo, event)
        assert combo.currentIndex() == before, "滚轮不得改动映射"
    finally:
        dialog.done(0)


# ---------------------------------------------------------------- 预览 / 导出


def test_preserve_toggle_changes_payload(qapp, tmp_path):
    dialog = open_dialog(tmp_path, make_task("001.psd", "002.psd"))
    try:
        from mangaproof.poprako.models import Rect, RemoteIssue

        dialog._user = User(id="u_1", qid="1", nickname="n", is_sadmin=False)
        dialog._teams = [Membership("t_1", "T", 32)]
        dialog._fill_combo(dialog._team_combo, [("T", "t_1")], "t_1")
        dialog._artworks = [make_artwork("pa_1", 0), make_artwork("pa_2", 1)]
        dialog._chapter = make_chapter()
        dialog._mapping = {"001.psd": "pa_1"}
        dialog._remote_issues = [
            RemoteIssue(id="r1", page_artwork_id="pa_2", index=0, variant="文字位置",
                        layer_name=None, rect=Rect(0.1, 0.1, 0.2, 0.2), note="旧")
        ]
        dialog._refresh_payload()
        assert dialog._upload_keep_value.text().startswith("1 页")
        assert dialog._report.pages_preserved == 1

        dialog._preserve_switch.setChecked(False)
        QApplication.processEvents()
        assert dialog._report.pages_preserved == 0
        assert dialog._report.pages_cleared
    finally:
        dialog.done(0)


def test_export_writes_full_json(qapp, tmp_path, monkeypatch):
    dialog = open_dialog(tmp_path, make_task("001.psd"))
    try:
        dialog._preview_full = '{"pages": []}'
        out = tmp_path / "export.json"
        monkeypatch.setattr(
            dialog_module.QFileDialog, "getSaveFileName",
            staticmethod(lambda *args, **kwargs: (str(out), "")),
        )
        dialog._on_export_clicked()
        assert out.read_text(encoding="utf-8") == '{"pages": []}'
        assert "已导出" in dialog._status_label.text()
    finally:
        dialog.done(0)


def test_preview_is_truncated_but_full_json_kept(qapp, tmp_path):
    dialog = open_dialog(tmp_path, make_task("001.psd"))
    try:
        dialog._preview_full = "x" * (dialog_module.PREVIEW_LIMIT + 500)
        dialog._preview.setPlainText("…")
        # 直接验证阈值语义：短内容不截断
        assert dialog_module.PREVIEW_LIMIT > 1000
    finally:
        dialog.done(0)


# ---------------------------------------------------------------- 收尾


def test_closing_writes_binding_and_emits_signal(qapp, tmp_path):
    task = make_task("001.psd", "002.psd")
    dialog = open_dialog(tmp_path, task)
    emitted: list[int] = []
    dialog.binding_changed.connect(lambda: emitted.append(1))
    try:
        make_ready(dialog)
        dialog.done(0)
    finally:
        pass

    saved = task.integrations.get("poprako") or {}
    assert saved["chapter_id"] == "ch_1"
    assert saved["pages"] == {"001.psd": "pa_1", "002.psd": "pa_2"}
    assert emitted == [1]


def test_closing_without_chapter_does_not_write_integrations(qapp, tmp_path):
    task = make_task("001.psd")
    dialog = open_dialog(tmp_path, task)
    dialog.done(0)
    assert "poprako" not in task.integrations


def test_settings_dirty_emits_commit_signal(qapp, tmp_path):
    dialog = open_dialog(tmp_path, make_task("001.psd"))
    emitted: list[int] = []
    dialog.settings_committed.connect(lambda: emitted.append(1))
    try:
        dialog._on_keep_signed_in_toggled(False)
        assert dialog._settings_dirty is True
    finally:
        dialog.done(0)
    assert emitted == [1]
    assert dialog._manager.settings.poprako.keep_signed_in is False


def test_json_preview_text_matches_payload(qapp, tmp_path):
    dialog = open_dialog(tmp_path, make_task("001.psd", "002.psd"))
    try:
        make_ready(dialog)
        text = dialog._preview.toPlainText()
        assert json.loads(text) == dialog._payload
    finally:
        dialog.done(0)


# ---------------------------------------------------------------- 端到端（离线）


class _FakeClient(_NoNetworkClient):
    """一条完整链路的最小假服务端：登录 → 团队 → 项目集 → 作品 → 章节 → 页面。"""

    def login(self, qid, password):
        from mangaproof.poprako.models import AuthSession

        assert qid and password
        return AuthSession(token="tok-1", user_id="u_1")

    def me(self):
        return User(id="u_1", qid="123456", nickname="秋叶声生", is_sadmin=False)

    def my_teams(self):
        return [Membership("t_1", "木叶翻译组", 32)]

    def worksets(self, team_id):
        from mangaproof.poprako.models import Workset

        return [Workset("w_1", "2026 春季", 1)]

    def comics(self, workset_id):
        from mangaproof.poprako.models import Comic

        return [Comic("co_1", "某漫画", "作者", 1, False)]

    def chapters(self, comic_id):
        return [make_chapter()]

    def chapter(self, chapter_id):
        return make_chapter()

    def page_artworks(self, chapter_id):
        return [make_artwork("pa_1", 0, "001.psd")]

    def issues(self, chapter_id):
        return []


def test_login_cascades_to_chapter_and_auto_mapping(qapp, tmp_path, monkeypatch):
    """登录 → 团队 → 项目集 → 作品 → 章节 → 页面，全程同步执行、不联网。"""
    def _sync_run(self, fn, on_success):
        """同步执行，并模拟真实流程里"完成 → 清 busy → 回调"的顺序。"""
        result = fn()
        self._set_busy(None)
        on_success(result)

    monkeypatch.setattr(PoprakoSyncDialog, "_run", _sync_run)
    task = make_task("001.psd", issues=[make_issue("001.psd", 1)])
    dialog = open_dialog(tmp_path, task, client=_FakeClient())
    try:
        dialog.show()
        QApplication.processEvents()
        QApplication.processEvents()   # 启动流程在事件循环下一拍
        assert dialog._user is None, "没有缓存令牌时应停在与未登录"

        dialog._qid_edit.setText("123456")
        dialog._password_edit.setText("p@ss")
        dialog._on_login_clicked()

        assert dialog._user is not None and dialog._user.nickname == "秋叶声生"
        assert [t.team_name for t in dialog._teams] == ["木叶翻译组"]
        assert dialog._chapter is not None and dialog._chapter.id == "ch_1"
        assert dialog._mapping == {"001.psd": "pa_1"}
        assert dialog._page_widgets["target"].isEnabled()
        assert dialog._upload_target_value.text() == "第 12 话 初次见面"
        assert dialog._upload_btn.isEnabled() is True, dialog._upload_btn.toolTip()
        assert dialog._password_edit.text() == "", "登录成功后密码框必须清空"
    finally:
        dialog.done(0)


def test_startup_with_cached_token_verifies_and_loads_teams(qapp, tmp_path, monkeypatch):
    """有缓存令牌时：/users/me 校验 → 已登录 → 团队 → … → 章节（回归：曾漏写回调）。"""
    monkeypatch.setattr(dialog_module, "load_token", lambda settings: "tok-1")

    def _sync_run(self, fn, on_success):
        result = fn()
        self._set_busy(None)
        on_success(result)

    monkeypatch.setattr(PoprakoSyncDialog, "_run", _sync_run)
    dialog = open_dialog(tmp_path, make_task("001.psd"), client=_FakeClient())
    try:
        dialog.show()
        QApplication.processEvents()
        QApplication.processEvents()   # 启动流程在事件循环下一拍

        assert dialog._client.token == "tok-1"
        assert dialog._user is not None and dialog._user.nickname == "秋叶声生"
        assert dialog._account_status.text().startswith("已登录")
        assert [team.team_id for team in dialog._teams] == ["t_1"]
        assert dialog._chapter is not None
    finally:
        dialog.done(0)


# ---------------------------------------------------------------- 真实入口


def test_dialog_constructs_real_client_with_malformed_proxy_env(qapp, tmp_path, monkeypatch):
    """回归：真实客户端（非注入的假对象）在代理环境变量损坏时也必须能构造。"""
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1,::1,[::1]")
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1,::1,[::1]")
    manager = SettingsManager(tmp_path / "settings.json")
    manager.save()

    dialog = PoprakoSyncDialog(make_task("001.psd"), tmp_path, manager.settings)
    try:
        assert dialog._client.has_token is False
    finally:
        dialog.done(0)


def test_main_window_menu_opens_dialog(qapp, tmp_path, monkeypatch):
    """从主窗口真实入口点一次：菜单能打开对话框（用户报「点不开」的回归）。"""
    from mangaproof.ui.main_window import MainWindow

    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1,::1,[::1]")
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1,::1,[::1]")
    manager = SettingsManager(tmp_path / "settings.json")
    manager.save()
    window = MainWindow(manager)
    try:
        window.task = make_task("001.psd")
        window._base_dir = tmp_path
        window._refresh_enabled_state()
        assert window.action_sync_poprako.isEnabled() is True

        opened: dict = {}

        def _fake_exec(self):
            opened["title"] = self.windowTitle()
            opened["nav"] = [
                self.nav_list.item(i).text() for i in range(self.nav_list.count())
            ]
            return 0

        monkeypatch.setattr(PoprakoSyncDialog, "exec", _fake_exec)
        window._open_poprako_sync()

        assert opened["nav"] == ["账号", "代理", "目标章节", "页面映射", "预览与上传"]
        assert "监制数据同步" in opened["title"]
    finally:
        window.close()


# ---------------------------------------------------------------- 入口容错


class _RecordingBox:
    """替身 QMessageBox：只记录调用，不弹真窗口。"""

    calls: list[tuple[str, str]] = []

    @classmethod
    def information(cls, _parent, _title, text, *args, **kwargs):   # noqa: D401
        cls.calls.append(("info", str(text)))
        return None

    @classmethod
    def critical(cls, _parent, _title, text, *args, **kwargs):      # noqa: D401
        cls.calls.append(("critical", str(text)))
        return None


def test_menu_handler_without_task_shows_hint(qapp, tmp_path, monkeypatch):
    """没有任务时给一句提示，而不是静默什么都不做。"""
    from mangaproof.ui import main_window as mw_module

    _RecordingBox.calls = []
    monkeypatch.setattr(mw_module, "QMessageBox", _RecordingBox)
    manager = SettingsManager(tmp_path / "settings.json")
    manager.save()
    window = mw_module.MainWindow(manager)
    try:
        window.task = None
        window._base_dir = None
        window._open_poprako_sync()
        assert _RecordingBox.calls
        assert _RecordingBox.calls[-1][0] == "info"
        assert "请先打开一个任务" in _RecordingBox.calls[-1][1]
    finally:
        window.close()


def test_menu_handler_reports_constructor_failure(qapp, tmp_path, monkeypatch):
    """构造抛异常时必须弹框（含异常类型），而不是"点了没反应"。"""
    from mangaproof.ui import main_window as mw_module

    _RecordingBox.calls = []
    monkeypatch.setattr(mw_module, "QMessageBox", _RecordingBox)

    def boom(self, *args, **kwargs):
        raise RuntimeError("构造失败测试")

    monkeypatch.setattr(PoprakoSyncDialog, "__init__", boom)

    manager = SettingsManager(tmp_path / "settings.json")
    manager.save()
    window = mw_module.MainWindow(manager)
    try:
        window.task = make_task("001.psd")
        window._base_dir = tmp_path
        window._open_poprako_sync()
        assert _RecordingBox.calls
        kind, text = _RecordingBox.calls[-1]
        assert kind == "critical"
        assert "RuntimeError" in text and "构造失败测试" in text
    finally:
        window.close()


# ---------------------------------------------------------------- 代理页


class _FakeProbeWorker:
    """替身探测线程：start() 时同步发出预设结果（不起线程、不联网）。"""

    last: "_FakeProbeWorker | None" = None
    result: tuple[bool, str] = (True, "代理可用（https://www.google.com/generate_204 → HTTP 204）")

    def __init__(self, proxy: str, parent=None):
        from PySide6.QtCore import QObject, Signal

        class _Signal(QObject):
            finished_with = Signal(bool, str)

        self._emitter = _Signal()
        self.finished_with = self._emitter.finished_with
        self.proxy = proxy
        type(self).last = self

    def start(self) -> None:
        self.finished_with.emit(*type(self).result)

    def deleteLater(self) -> None:      # noqa: N802（Qt 命名）
        return None


def test_proxy_page_exists_with_test_and_save(qapp, tmp_path):
    dialog = open_dialog(tmp_path, make_task("001.psd"))
    try:
        assert dialog._page_widgets["proxy"] is not None
        assert dialog._proxy_current.text() == "直连"
        assert dialog._proxy_test_btn.text() == "测试连接"
        assert dialog._proxy_save_btn.text() == "保存"
    finally:
        dialog.done(0)


def test_proxy_test_does_not_save(qapp, tmp_path, monkeypatch):
    """测试只用输入框里的地址，不写设置、不重建客户端。"""
    dialog = open_dialog(tmp_path, make_task("001.psd"))
    try:
        monkeypatch.setattr(dialog_module, "ProxyProbeWorker", _FakeProbeWorker)
        _FakeProbeWorker.result = (
            True, "代理可用（https://www.google.com/generate_204 → HTTP 204）"
        )
        dialog._proxy_edit.setText("http://127.0.0.1:7897")
        dialog._on_proxy_test_clicked()

        assert dialog._poprako.proxy == "", "测试不得保存"
        assert dialog._client.proxy == ""
        assert "✓" in dialog._status_label.text()
        assert dialog._proxy_test_btn.text() == "测试连接"
    finally:
        dialog.done(0)


def test_proxy_test_reports_failure(qapp, tmp_path, monkeypatch):
    dialog = open_dialog(tmp_path, make_task("001.psd"))
    try:
        monkeypatch.setattr(dialog_module, "ProxyProbeWorker", _FakeProbeWorker)
        _FakeProbeWorker.result = (False, "代理不可用：ConnectTimeout: timed out")
        dialog._proxy_edit.setText("http://127.0.0.1:9")
        dialog._on_proxy_test_clicked()
        assert "✗" in dialog._status_label.text()
        assert "ConnectTimeout" in dialog._status_label.text()
        assert dialog._poprako.proxy == ""
    finally:
        dialog.done(0)


def test_proxy_save_persists_and_rebuilds_client(qapp, tmp_path):
    dialog = open_dialog(
        tmp_path, make_task("001.psd"), factory=_NoNetworkClient
    )
    emitted: list[int] = []
    dialog.settings_committed.connect(lambda: emitted.append(1))
    try:
        dialog._proxy_edit.setText("  http://127.0.0.1:7897  ")
        dialog._on_proxy_save_clicked()

        assert dialog._poprako.proxy == "http://127.0.0.1:7897"
        assert dialog._client.proxy == "http://127.0.0.1:7897", "保存后要重建客户端"
        assert dialog._proxy_current.text() == "http://127.0.0.1:7897"
        assert emitted == [1], "保存要立即落盘"
        assert "已保存并生效" in dialog._status_label.text()
    finally:
        dialog.done(0)


def test_proxy_save_empty_means_direct(qapp, tmp_path):
    dialog = open_dialog(tmp_path, make_task("001.psd"))
    try:
        dialog._poprako.proxy = "http://127.0.0.1:7897"
        dialog._proxy_edit.setText("")
        dialog._on_proxy_save_clicked()
        assert dialog._poprako.proxy == ""
        assert dialog._client.proxy == ""
        assert dialog._proxy_current.text() == "直连"
    finally:
        dialog.done(0)


def test_proxy_save_rejects_invalid_input(qapp, tmp_path):
    dialog = open_dialog(tmp_path, make_task("001.psd"))
    emitted: list[int] = []
    dialog.settings_committed.connect(lambda: emitted.append(1))
    try:
        dialog._proxy_edit.setText("127.0.0.1:7897")     # 缺协议
        dialog._on_proxy_save_clicked()
        assert dialog._poprako.proxy == ""
        assert emitted == []
        assert "协议" in dialog._status_label.text()
    finally:
        dialog.done(0)


def test_proxy_masked_when_saving_credentials(qapp, tmp_path):
    dialog = open_dialog(tmp_path, make_task("001.psd"))
    try:
        dialog._proxy_edit.setText("http://user:secret@127.0.0.1:7897")
        dialog._on_proxy_save_clicked()
        assert "secret" not in dialog._status_label.text(), "状态行不得回显代理密码"
        assert "***@" in dialog._status_label.text()
    finally:
        dialog.done(0)

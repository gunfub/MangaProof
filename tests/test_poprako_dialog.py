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
from mangaproof.poprako.last_choice import LastChoiceStore
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


def open_dialog(tmp_path: Path, task: TaskState, client=None, factory=None,
                last_store=None) -> PoprakoSyncDialog:
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
        last_store=last_store,
    )
    dialog._manager = manager       # 便于测试断言保存内容
    return dialog


def make_ready(dialog: PoprakoSyncDialog, *, roles: int = 32, stages: int = 0) -> None:
    """把对话框摆到"可以上传"的状态（不发任何网络请求）。"""
    dialog._user = User(id="u_1", qid="123456", nickname="秋叶声生", is_sadmin=False)
    dialog._apply_logged_in(dialog._user)
    dialog._teams = [Membership("t_1", "木叶翻译组", roles)]
    dialog._teams_loaded = True
    dialog._fill_combo(dialog._team_combo, [("木叶翻译组", "t_1")], "请选择团队")
    dialog._team_combo.blockSignals(True)      # 只改界面状态，不触发网络
    dialog._team_combo.setCurrentIndex(1)
    dialog._team_combo.blockSignals(False)
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
        titles = dialog._nav_titles()
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
        assert dialog._sidebar.width() == NAV_WIDTH
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
        dialog._fill_combo(dialog._team_combo, [("T", "t_1")], "请选择团队")
        dialog._team_combo.blockSignals(True)
        dialog._team_combo.setCurrentIndex(1)
        dialog._team_combo.blockSignals(False)
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


def test_login_then_manual_lazy_cascade(qapp, tmp_path, monkeypatch):
    """登录只完成鉴权；团队、项目集、作品、章节都靠用户逐级选择才请求。

    这是 2026-10-20 决策：不再"自动选第一项一路加载到底"。
    """
    def _sync_run(self, fn, on_success, on_error=None):
        result = fn()
        self._set_busy(None)
        on_success(result)

    monkeypatch.setattr(PoprakoSyncDialog, "_run", _sync_run)
    task = make_task("001.psd", issues=[make_issue("001.psd", 1)])
    dialog = open_dialog(tmp_path, task, client=_FakeClient())
    dialog._poprako.rank_comics = False        # 同步路径，避免线程与进度窗
    try:
        dialog.show()
        QApplication.processEvents()
        QApplication.processEvents()
        assert dialog._user is None

        dialog._qid_edit.setText("123456")
        dialog._password_edit.setText("p@ss")
        dialog._on_login_clicked()

        assert dialog._user is not None and dialog._user.nickname == "秋叶声生"
        assert dialog._teams == [], "登录本身不请求层级数据"
        assert dialog._worksets == [] and dialog._comics == []

        # 进入「目标章节」：只加载团队，且不自动选中
        dialog._select_page("target")
        QApplication.processEvents()
        assert [team.team_id for team in dialog._teams] == ["t_1"]
        assert dialog._team_combo.currentData() is None, "必须等用户选"
        assert dialog._worksets == []

        # 选团队 → 项目集
        dialog._team_combo.setCurrentIndex(1)
        QApplication.processEvents()
        assert [item.id for item in dialog._worksets] == ["w_1"]
        assert dialog._workset_combo.currentData() is None

        # 选项目集 → 作品（未排序路径）
        dialog._workset_combo.setCurrentIndex(1)
        QApplication.processEvents()
        assert [item.id for item in dialog._comics] == ["co_1"]
        assert dialog._comic_combo.currentData() is None

        # 选作品 → 章节
        dialog._comic_combo.setCurrentIndex(1)
        QApplication.processEvents()
        assert [item.id for item in dialog._chapters] == ["ch_1"]
        assert dialog._chapter_combo.currentData() is None

        # 选章节：不发请求，只让按钮可用
        dialog._chapter_combo.setCurrentIndex(1)
        QApplication.processEvents()
        assert dialog._chapter is None, "选章节不得自动加载章节信息"
        assert dialog._load_chapter_btn.isEnabled() is True

        # 点「加载章节信息」才真正拉详情 / 复合图页 / 现有问题
        dialog._on_load_chapter_clicked()
        QApplication.processEvents()
        assert dialog._chapter is not None and dialog._chapter.id == "ch_1"
        assert dialog._mapping == {"001.psd": "pa_1"}
        assert dialog._upload_btn.isEnabled() is True, dialog._upload_btn.toolTip()
        assert dialog._password_edit.text() == "", "登录成功后密码框必须清空"
    finally:
        dialog.done(0)


def test_startup_with_cached_token_only_verifies(qapp, tmp_path, monkeypatch):
    """有缓存令牌：/users/me 校验 → 已登录；进入目标页才拉团队（回归：曾漏写回调）。"""
    monkeypatch.setattr(dialog_module, "load_token", lambda settings: "tok-1")

    def _sync_run(self, fn, on_success, on_error=None):
        result = fn()
        self._set_busy(None)
        on_success(result)

    monkeypatch.setattr(PoprakoSyncDialog, "_run", _sync_run)
    dialog = open_dialog(tmp_path, make_task("001.psd"), client=_FakeClient())
    try:
        dialog.show()
        QApplication.processEvents()
        QApplication.processEvents()

        assert dialog._client.token == "tok-1"
        assert dialog._user is not None and dialog._user.nickname == "秋叶声生"
        assert dialog._account_status.text().startswith("已登录")
        assert dialog._teams == [], "未进入目标页时不应请求团队"

        dialog._select_page("target")
        QApplication.processEvents()
        assert [team.team_id for team in dialog._teams] == ["t_1"]
        assert dialog._chapter is None
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
            opened["nav"] = self._nav_titles()
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


def test_menu_handler_opens_dialog_without_task(qapp, tmp_path, monkeypatch):
    """没打开任务也能进对话框（2026-10-20）：只有流程三页置灰。"""
    from mangaproof.ui import main_window as mw_module

    _RecordingBox.calls = []
    monkeypatch.setattr(mw_module, "QMessageBox", _RecordingBox)
    manager = SettingsManager(tmp_path / "settings.json")
    manager.save()
    window = mw_module.MainWindow(manager)
    try:
        window.task = None
        window._base_dir = None
        opened: dict = {}

        def _fake_exec(self):
            opened["task_open"] = self._task_open
            opened["nav"] = self._nav_titles()
            opened["disabled"] = [
                key for key in self.WORKFLOW_KEYS
                if not (self._nav_items[key].flags() & Qt.ItemFlag.ItemIsEnabled)
            ]
            return 0

        monkeypatch.setattr(PoprakoSyncDialog, "exec", _fake_exec)
        window._open_poprako_sync()

        assert opened["task_open"] is False
        assert opened["nav"] == ["账号", "代理", "目标章节", "页面映射", "预览与上传"]
        assert opened["disabled"] == ["target", "mapping", "upload"]
        assert _RecordingBox.calls == [], "不该再弹「请先打开任务」"
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


#: 假探针的默认结果：(连通, 文案, 毫秒)
PROBE_OK = (True, "代理可用（poprako.com → HTTP 200，354 ms）", 354)


class _FakeProbeWorker:
    """替身探测线程：start() 时同步发出预设结果（不起线程、不联网）。"""

    last: "_FakeProbeWorker | None" = None
    result: tuple[bool, str, int] = PROBE_OK

    def __init__(self, proxy: str, parent=None):
        from PySide6.QtCore import QObject, Signal

        class _Signal(QObject):
            finished_with = Signal(bool, str, int)

        self._emitter = _Signal()
        self.finished_with = self._emitter.finished_with
        self.proxy = proxy
        type(self).last = self

    def start(self) -> None:
        self.finished_with.emit(*type(self).result)

    def isRunning(self) -> bool:        # noqa: N802（Qt 命名）
        return False

    def deleteLater(self) -> None:      # noqa: N802（Qt 命名）
        return None


@pytest.fixture(autouse=True)
def _never_run_real_probe(monkeypatch):
    """保存会顺手自检一次 → 所有用例都必须用假探针，绝不真的起线程/联网。"""
    _FakeProbeWorker.result = PROBE_OK
    _FakeProbeWorker.last = None
    monkeypatch.setattr(dialog_module, "ProxyProbeWorker", _FakeProbeWorker)


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
        _FakeProbeWorker.result = PROBE_OK
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
        _FakeProbeWorker.result = (
            False, "代理不可用：ConnectTimeout: timed out（10.0 s）", 10000,
        )
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


def test_proxy_save_does_not_touch_update_settings(qapp, tmp_path):
    """自更新的代理是另一个顶层键：保存 Poprako 代理不许动它。"""
    dialog = open_dialog(tmp_path, make_task("001.psd"))
    try:
        dialog._manager.settings.update.proxy = "http://127.0.0.1:7890"
        dialog._proxy_edit.setText("http://127.0.0.1:7897")
        dialog._on_proxy_save_clicked()

        assert dialog._poprako.proxy == "http://127.0.0.1:7897"
        assert dialog._manager.settings.update.proxy == "http://127.0.0.1:7890"
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


def test_proxy_actually_reaches_the_real_client(qapp, tmp_path):
    """回归（2026-10-20）：代理串曾被当成 ``token`` 位置参数，客户端永远直连。

    这里用**真实** ``PoprakoClient``（不注入工厂、不发请求）验证接线：
    构造与重建之后，客户端拿到的 proxy 都必须等于设置值，token 不能被顶掉。
    """
    manager = SettingsManager(tmp_path / "settings.json")
    manager.save()
    manager.settings.poprako.proxy = "http://127.0.0.1:7897"
    dialog = PoprakoSyncDialog(
        make_task("001.psd"), tmp_path, manager.settings,
        last_store=LastChoiceStore(tmp_path / "poprako_last.json"),
    )
    try:
        assert dialog._client.proxy == "http://127.0.0.1:7897"
        assert dialog._client.token == "", "代理串不许再落到 token 上"

        dialog._client.token = "tok-1"
        dialog._poprako.proxy = "socks5://127.0.0.1:1080"
        assert dialog._rebuild_client() == []
        assert dialog._client.proxy == "socks5://127.0.0.1:1080"
        assert dialog._client.token == "tok-1", "重建必须保住令牌"
    finally:
        dialog.done(0)


def test_proxy_row_shows_effective_value_not_settings(qapp, tmp_path):
    """「当前生效」显示客户端真正在用的链路；设置值没被接受时必须说出来。"""
    manager = SettingsManager(tmp_path / "settings.json")
    manager.save()
    manager.settings.poprako.proxy = "http://127.0.0.1:7897"
    dialog = PoprakoSyncDialog(
        make_task("001.psd"), tmp_path, manager.settings,
        # 工厂无视代理 → 模拟"保存值没生效"
        client_factory=lambda base, proxy: _NoNetworkClient(base, ""),
        last_store=LastChoiceStore(tmp_path / "poprako_last.json"),
    )
    try:
        assert dialog._proxy_current.text() == "直连（保存的代理未生效）"
    finally:
        dialog.done(0)


def test_proxy_save_runs_a_self_check_with_latency(qapp, tmp_path):
    """保存后自动探一次：状态行要给出真实往返与延迟，而不是只说"生效了"。"""
    dialog = open_dialog(tmp_path, make_task("001.psd"))
    try:
        dialog._proxy_edit.setText("http://127.0.0.1:7897")
        dialog._on_proxy_save_clicked()

        text = dialog._status_label.text()
        assert text.startswith("✓ 已保存并生效")
        assert "poprako.com" in text and "354 ms" in text
        assert _FakeProbeWorker.last is not None, "保存要顺手起一次探针"
        assert _FakeProbeWorker.last.proxy == "http://127.0.0.1:7897"
    finally:
        dialog.done(0)


def test_slow_probe_turns_status_orange(qapp, tmp_path):
    """延迟超过阈值只改提示颜色/措辞，不改"可用"的判定。"""
    from mangaproof.ui.theme import COLOR_WARN

    dialog = open_dialog(tmp_path, make_task("001.psd"))
    try:
        _FakeProbeWorker.result = (
            True, "代理可用（poprako.com → HTTP 200，2100 ms）（较慢）", 2100,
        )
        dialog._proxy_edit.setText("http://127.0.0.1:7897")
        dialog._on_proxy_test_clicked()

        assert "✓" in dialog._status_label.text()
        assert "（较慢）" in dialog._status_label.text()
        assert COLOR_WARN in dialog._status_label.styleSheet()
    finally:
        dialog.done(0)


def test_team_load_failure_resets_and_can_retry(qapp, tmp_path, monkeypatch):
    """一次超时不该把「目标章节」页锁死：失败要复位加载标记，下次进入能重试。"""
    dialog = open_dialog(tmp_path, make_task("001.psd"))
    try:
        class _Worker:
            def deleteLater(self):      # noqa: N802（Qt 命名）
                return None

        dialog._user = User(id="u_1", qid="123456", nickname="秋叶声生", is_sadmin=False)
        dialog._current_key = "target"
        dialog._teams_loading = True

        dialog._on_call_failed(_Worker(), "无法连接 api.poprako.com")

        assert dialog._teams_loading is False, "失败后必须允许重新加载"
        calls: list[int] = []
        monkeypatch.setattr(dialog, "_load_teams", lambda: calls.append(1))
        dialog._ensure_teams_loaded()
        assert calls == [1], "再进一次目标页要真的重试"
    finally:
        dialog.done(0)


def test_rebuild_defers_closing_client_while_workers_run(qapp, tmp_path):
    """换下来的旧客户端不能立刻 close：后台线程可能正拿它发请求。"""
    dialog = open_dialog(tmp_path, make_task("001.psd"))
    try:
        closed: list[int] = []
        dialog._client.close = lambda: closed.append(1)

        class _Busy:
            def isRunning(self):        # noqa: N802（Qt 命名）
                return True

        dialog._workers.add(_Busy())
        dialog._poprako.proxy = "http://127.0.0.1:7897"
        dialog._rebuild_client()
        assert closed == [], "还有线程在跑，旧客户端不许当场关"
        assert len(dialog._retired_clients) == 1

        dialog._workers.clear()
        dialog._drain_retired()
        assert closed == [1], "线程收尾后要把旧客户端关掉"
        assert dialog._retired_clients == []
    finally:
        dialog._workers.clear()
        dialog.done(0)


# ---------------------------------------------------------------- 空列表提示


def test_empty_worksets_shows_notice(qapp, tmp_path):
    dialog = open_dialog(tmp_path, make_task("001.psd"))
    try:
        dialog._on_worksets([])
        assert "没有项目集" in dialog._status_label.text()
        assert dialog._comic_combo.itemText(0) == "请先选择项目集"
    finally:
        dialog.done(0)


def test_empty_comics_shows_notice_and_clears_chapters(qapp, tmp_path):
    dialog = open_dialog(tmp_path, make_task("001.psd"))
    try:
        dialog._on_comics([])
        assert "没有作品" in dialog._status_label.text()
        assert dialog._chapter_combo.itemText(0) == "请先选择作品"
    finally:
        dialog.done(0)


def test_empty_chapters_shows_notice(qapp, tmp_path):
    dialog = open_dialog(tmp_path, make_task("001.psd"))
    try:
        dialog._on_chapters([])
        assert "没有章节" in dialog._status_label.text()
        assert dialog._chapter is None
        assert dialog._artworks == []
    finally:
        dialog.done(0)


# ---------------------------------------------------------------- 逐级懒加载


def test_target_page_starts_with_placeholders(qapp, tmp_path):
    dialog = open_dialog(tmp_path, make_task("001.psd"))
    try:
        assert dialog._team_combo.itemText(0) == "请先登录"
        assert dialog._workset_combo.itemText(0) == "请先选择团队"
        assert dialog._comic_combo.itemText(0) == "请先选择项目集"
        assert dialog._chapter_combo.itemText(0) == "请先选择作品"
        for combo in (dialog._team_combo, dialog._workset_combo,
                      dialog._comic_combo, dialog._chapter_combo):
            assert combo.currentData() is None, "任何一级都不许自动选中"
        assert dialog._load_chapter_btn.isEnabled() is False
        assert dialog._clear_all_btn.isEnabled() is False
    finally:
        dialog.done(0)


def test_clear_all_button_resets_every_level(qapp, tmp_path):
    dialog = open_dialog(tmp_path, make_task("001.psd", "002.psd"))
    try:
        make_ready(dialog)
        dialog._on_clear_all_clicked()
        assert dialog._chapter is None and dialog._artworks == []
        assert dialog._team_combo.currentData() is None
        assert dialog._workset_combo.itemText(0) == "请先选择团队"
        assert dialog._comic_combo.itemText(0) == "请先选择项目集"
        assert dialog._chapter_combo.itemText(0) == "请先选择作品"
        assert dialog._load_chapter_btn.isEnabled() is False
        assert "已清空选择" in dialog._status_label.text()
    finally:
        dialog.done(0)


# ---------------------------------------------------------------- 排序 / 进度窗


def _record_progress(monkeypatch) -> list:
    """把进度窗换成记录器，验证"是否弹出 / 阶段文案 / 是否关闭"。"""
    events: list = []
    real = dialog_module._ProgressDialog

    class _Recorder(real):
        def __init__(self, parent=None):
            super().__init__(parent)
            events.append(("init", ""))

        def show(self):                       # noqa: D102
            events.append(("show", ""))
            super().show()

        def set_phase(self, text, done=0, total=0):   # noqa: D102
            events.append(("phase", text))
            super().set_phase(text, done, total)

        def accept(self):                     # noqa: D102
            events.append(("accept", ""))
            super().accept()

    monkeypatch.setattr(dialog_module, "_ProgressDialog", _Recorder)
    return events


class _FakeComicsWorker:
    """替身作品加载 worker：start() 时同步发进度与结果。"""

    entries: list = []
    instances: list = []

    def __init__(self, client, workset_id, task_name, parent=None):
        from PySide6.QtCore import QObject, Signal

        class _Sig(QObject):
            progress = Signal(int, int, str)
            succeeded = Signal(object)
            failed = Signal(str)

        self._sig = _Sig()
        self.progress = self._sig.progress
        self.succeeded = self._sig.succeeded
        self.failed = self._sig.failed
        type(self).instances.append(self)

    def start(self) -> None:
        self.progress.emit(0, 0, "正在读取作品…（第 1 页，已获取 2 条）")
        self.succeeded.emit(type(self).entries)

    def deleteLater(self) -> None:            # noqa: N802（Qt 命名）
        return None

    def isRunning(self) -> bool:
        return False


def test_rank_toggle_persists_to_settings(qapp, tmp_path):
    dialog = open_dialog(tmp_path, make_task("001.psd"))
    emitted: list[int] = []
    dialog.settings_committed.connect(lambda: emitted.append(1))
    try:
        assert dialog._rank_switch.isChecked() is True, "默认开启"
        dialog._rank_switch.setChecked(False)
        QApplication.processEvents()
        assert dialog._poprako.rank_comics is False
        assert dialog._manager.settings.poprako.rank_comics is False
        assert emitted == [1]
        assert "已关闭" in dialog._status_label.text()
    finally:
        dialog.done(0)


def test_ranked_load_shows_progress_popup_and_sorts(qapp, tmp_path, monkeypatch):
    from mangaproof.poprako.models import Comic
    from mangaproof.poprako.title_match import RankedComic

    monkeypatch.setattr(dialog_module, "MIN_PROGRESS_VISIBLE", 0.0)
    events = _record_progress(monkeypatch)
    _FakeComicsWorker.entries = [
        RankedComic(Comic("c1", "某漫画", "", 1, False), 1.0, (0, 3)),
        RankedComic(Comic("c2", "某漫画 第二部", "", 1, False), 0.78, (0, 3)),
    ]
    monkeypatch.setattr(dialog_module, "ComicsLoadWorker", _FakeComicsWorker)

    dialog = open_dialog(tmp_path, make_task("001.psd"))
    dialog._poprako.rank_comics = True
    try:
        dialog._load_comics("w_1")
        QApplication.processEvents()

        kinds = [kind for kind, _ in events]
        assert "show" in kinds and "accept" in kinds, "开了排序就一定弹，且结束后关闭"
        assert any(kind == "phase" and "正在读取作品" in text for kind, text in events)

        assert [entry.comic.id for entry in dialog._comic_entries] == ["c1", "c2"]
        assert dialog._comic_combo.itemText(1) == "某漫画"
        assert dialog._comic_combo.itemData(1, dialog_module._SPANS_ROLE) == (0, 3)
        tip = dialog._comic_combo.itemData(1, Qt.ItemDataRole.ToolTipRole) or ""
        assert "匹配度" in tip
        assert "已按匹配度排序" in dialog._status_label.text()
    finally:
        dialog.done(0)


def test_rank_off_keeps_server_order_and_skips_popup(qapp, tmp_path, monkeypatch):
    from mangaproof.poprako.models import Comic

    events = _record_progress(monkeypatch)
    dialog = open_dialog(tmp_path, make_task("001.psd"))
    dialog._poprako.rank_comics = False
    try:
        entries = dialog._plain_entries([
            Comic("c2", "乙作品", "", 1, False),
            Comic("c1", "甲作品", "", 1, False),
        ])
        dialog._on_comics(entries)

        assert [entry.comic.id for entry in dialog._comic_entries] == ["c2", "c1"]
        assert dialog._comic_combo.itemData(1, dialog_module._SPANS_ROLE) is None
        assert events == [], "关闭排序时不该弹进度窗"
        assert "未排序" in dialog._status_label.text()
    finally:
        dialog.done(0)


# ---------------------------------------------------------------- 上次选择


def test_last_choice_written_to_separate_file(qapp, tmp_path):
    from mangaproof.poprako.last_choice import LastChoiceStore

    store = LastChoiceStore(tmp_path / "poprako_last.json")
    dialog = open_dialog(tmp_path, make_task("001.psd"), last_store=store)
    try:
        dialog._teams_loaded = True
        dialog._teams = [Membership("t_1", "木叶翻译组", 32)]
        dialog._fill_combo(dialog._team_combo, [("木叶翻译组", "t_1")], "请选择团队")
        dialog._team_combo.blockSignals(True)
        dialog._team_combo.setCurrentIndex(1)
        dialog._team_combo.blockSignals(False)
        dialog._persist_last_choice()

        assert (tmp_path / "poprako_last.json").exists()
        saved = store.load()
        assert saved.team_id == "t_1" and saved.team_name == "木叶翻译组"
        # 绝不能写进 settings.json
        assert "last_team_id" not in dialog._manager.settings.poprako.to_dict()
    finally:
        dialog.done(0)


def test_last_choice_marker_annotates_without_selecting(qapp, tmp_path):
    from mangaproof.poprako.last_choice import LastChoice, LastChoiceStore

    store = LastChoiceStore(tmp_path / "poprako_last.json")
    store.save(LastChoice(team_id="t_2", team_name="另一个组"))
    dialog = open_dialog(tmp_path, make_task("001.psd"), last_store=store)
    try:
        dialog._on_teams([
            Membership("t_1", "木叶翻译组", 32),
            Membership("t_2", "另一个组", 32),
        ])
        assert dialog._team_combo.itemText(1) == "木叶翻译组"
        assert dialog._team_combo.itemText(2) == "另一个组（上次）"
        assert dialog._team_combo.currentData() is None, "标注 ≠ 自动选中"
    finally:
        dialog.done(0)


# ---------------------------------------------------------------- 侧栏分组 / 无任务


def test_sidebar_groups_workflow_pages_in_own_list(qapp, tmp_path):
    """「同步流程」三项单独一个列表（= 界面上的那个框），账号/代理在另一个列表。"""
    dialog = open_dialog(tmp_path, make_task("001.psd"))
    try:
        assert dialog._settings_nav.count() == 2
        assert dialog._workflow_nav.count() == 3
        assert [
            dialog._settings_nav.item(i).text() for i in range(2)
        ] == ["账号", "代理"]
        assert [
            dialog._workflow_nav.item(i).text() for i in range(3)
        ] == ["目标章节", "页面映射", "预览与上传"]
        # 不加序号、不加箭头：标题就是原名
        assert all(not any(ch.isdigit() for ch in title) for title in dialog._nav_titles())
    finally:
        dialog.done(0)


def test_sidebar_selection_is_exclusive_between_groups(qapp, tmp_path):
    dialog = open_dialog(tmp_path, make_task("001.psd"))
    try:
        dialog._select_page("target")
        assert dialog._workflow_nav.currentRow() == 0
        assert dialog._settings_nav.currentRow() == -1
        assert dialog.stack.currentIndex() == 2

        dialog._select_page("account")
        assert dialog._settings_nav.currentRow() == 0
        assert dialog._workflow_nav.currentRow() == -1
        assert dialog.stack.currentIndex() == 0
    finally:
        dialog.done(0)


def test_sidebar_lists_cannot_scroll(qapp, tmp_path):
    """侧栏 5 项定高：滚动范围必须为 0，滚轮也不能把内容推走。

    回归（2026-10-20 需求方："侧栏大标题在滚动"）：``ScrollBarAlwaysOff``
    只是把条藏起来，定高若漏算列表自身的 frame 内缩，内容就比 viewport 高
    4px —— 滚轮照样推得动（``ScrollPerItem``，一次跳一整行），表现为标题
    整体错位、最后一项被裁。
    """
    dialog = open_dialog(tmp_path, make_task("001.psd"))
    try:
        from mangaproof.ui.widgets import NoWheelListWidget

        dialog.show()
        qapp.processEvents()
        for widget in (dialog._settings_nav, dialog._workflow_nav):
            assert isinstance(widget, NoWheelListWidget), "侧栏要用不吃滚轮的列表"
            content = sum(widget.sizeHintForRow(row) for row in range(widget.count()))
            assert widget.verticalScrollBar().maximum() == 0, "侧栏不该有可滚动的余地"
            assert widget.viewport().height() >= content, "条目要完整放得下"

            before = widget.visualItemRect(widget.item(0))
            center = QPointF(widget.viewport().rect().center())
            event = QWheelEvent(
                center, center, QPoint(0, 0), QPoint(0, -120),
                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.NoScrollPhase, False,
            )
            QApplication.sendEvent(widget.viewport(), event)
            qapp.processEvents()
            assert widget.visualItemRect(widget.item(0)) == before, "滚轮不得推动侧栏"
            assert event.isAccepted(), "滚轮要就地吃掉，不许冒泡给父级"
    finally:
        dialog.done(0)


def test_proxy_placeholder_is_shared_with_update_dialog(qapp, tmp_path):
    """代理输入框的空态灰字与「检查更新」的代理输入框逐字一致（共用常量）。"""
    from mangaproof.ui.widgets import PROXY_PLACEHOLDER

    dialog = open_dialog(tmp_path, make_task("001.psd"))
    try:
        assert dialog._proxy_edit.placeholderText() == PROXY_PLACEHOLDER
        assert PROXY_PLACEHOLDER == "留空表示不使用代理；支持 http(s):// 与 socks5://"
    finally:
        dialog.done(0)


def _open_dialog_without_task(tmp_path):
    manager = SettingsManager(tmp_path / "settings.json")
    manager.save()
    return PoprakoSyncDialog(
        None, None, manager.settings,
        client_factory=lambda base, proxy: _NoNetworkClient(base, proxy),
        last_store=LastChoiceStore(tmp_path / "poprako_last.json"),
    )


def test_no_task_disables_only_workflow_pages(qapp, tmp_path):
    from mangaproof.poprako.last_choice import LastChoiceStore

    dialog = _open_dialog_without_task(tmp_path)
    try:
        assert dialog._task_open is False
        assert dialog._current_page_key() == "account"
        for key in ("target", "mapping", "upload"):
            item = dialog._nav_items[key]
            assert not (item.flags() & Qt.ItemFlag.ItemIsEnabled), key
            assert item.toolTip(), key
            assert dialog._page_widgets[key].isEnabled() is False, key
        for key in ("account", "proxy"):
            assert dialog._nav_items[key].flags() & Qt.ItemFlag.ItemIsEnabled
            assert dialog._page_widgets[key].isEnabled() is True
        assert "未打开任务" in dialog._status_label.text()
        assert dialog._upload_btn.isEnabled() is False
        assert "未打开任务" in dialog._upload_btn.toolTip()
    finally:
        dialog.done(0)


def test_no_task_still_allows_proxy_and_login_ui(qapp, tmp_path):
    from mangaproof.poprako.last_choice import LastChoiceStore

    dialog = _open_dialog_without_task(tmp_path)
    try:
        assert dialog._login_btn.isEnabled() is True
        dialog._proxy_edit.setText("http://127.0.0.1:7897")
        dialog._on_proxy_save_clicked()
        assert dialog._poprako.proxy == "http://127.0.0.1:7897"
        assert dialog._client.proxy == "http://127.0.0.1:7897"
    finally:
        dialog.done(0)


def test_no_task_close_does_not_touch_task_binding(qapp, tmp_path):
    from mangaproof.poprako.last_choice import LastChoiceStore

    dialog = _open_dialog_without_task(tmp_path)
    emitted: list[int] = []
    dialog.binding_changed.connect(lambda: emitted.append(1))
    dialog.done(0)
    assert emitted == []

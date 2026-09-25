# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""更新页面的单测（需求 §11、§13、§31~§34）。

跑在离屏 Qt 上，不联网、不真的启动 worker（只验界面结构与"保存时机"语义）。
需要 `qapp` fixture（见 tests/test_android_ui_scale.py 的同名 fixture 定义）。

2026-09-24 改版后的两条核心语义，各有一组用例守着：

- **保存不再绑在"会变身的按钮"上**：左侧「保存并检查更新」恒为保存+检查，
  右侧「下载更新」在下载前同样先保存（旧版只在检查那一支保存，检查完之后改的
  代理/限速既不下发也不落盘）；
- **包与选择必须对应**：检查后改分支/渠道 → 右侧按钮置灰并要求重查；
  再点检查会作废上一轮下载的包（防误装旧版本）；MirrorChyan 缺 CDK 时只提示、
  不发请求、不落盘。

另有三条**布局**回归（都是实测踩到过的坑）：
- 下拉框不响应滚轮（与设置页一致）；
- 「代理」行的输入框左右边界、测试按钮右边界与其他行严格对齐；
- 进入「检查中」时进度条与状态文案的变化不许压扁表单行高。
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from mangaproof.config.settings import SettingsManager, UpdateSettings  # noqa: E402
from mangaproof.ui.update_dialog import (  # noqa: E402
    CHECK_BTN_TEXT,
    DOWNLOAD_BTN_TEXT,
    INSTALL_BTN_TEXT,
    MIRRORCHYAN_NEEDS_CDK,
    STALE_SELECTION_HINT,
    CHANNEL_LABELS,
    UpdateDialog,
)


@pytest.fixture(scope="module")
def qapp():
    """复用仓库既有约定：离屏 QApplication（qtbot 未使用，QApplication 全局唯一）。"""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _no_keyring_and_temp_cache(monkeypatch, tmp_path):
    """测试里不碰真实系统凭据库，也不往 ~/.cache 写东西。"""
    from mangaproof.update import cdk_store

    monkeypatch.setattr(cdk_store, "keyring_available", lambda: False)
    # save_cdk() 走的是 _keyring_module()（不是 keyring_available），必须一起挡掉：
    # 否则测试里填的假 CDK 会被真的写进开发机的系统凭据库
    monkeypatch.setattr(cdk_store, "_keyring_module", lambda: None)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))


class _SignalStub:
    """QThread 信号桩：只要能 .connect() 就行。"""

    def connect(self, *_args, **_kwargs) -> None:
        pass


class _FakeWorker:
    """假后台线程：只记录构造参数，不起线程、不联网、不碰 Qt 事件循环。"""

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.progress = _SignalStub()
        self.succeeded = _SignalStub()
        self.failed = _SignalStub()
        self.finished_with = _SignalStub()      # ProxyTestWorker 用

    def start(self) -> None:
        pass

    def isRunning(self) -> bool:      # noqa: N802 - 模仿 QThread 接口
        return False


@pytest.fixture(autouse=True)
def fake_workers(monkeypatch):
    """三个后台 worker 一律换成假的（本模块绝不起真线程），并按类别记录构造参数。"""
    import mangaproof.ui.update_dialog as ui

    made: dict[str, list[_FakeWorker]] = {"check": [], "download": [], "proxy": []}

    def _factory(kind: str):
        def _make(**kwargs):
            worker = _FakeWorker(**kwargs)
            made[kind].append(worker)
            return worker

        return _make

    monkeypatch.setattr(ui, "UpdateCheckWorker", _factory("check"))
    monkeypatch.setattr(ui, "UpdateDownloadWorker", _factory("download"))
    monkeypatch.setattr(ui, "ProxyTestWorker", _factory("proxy"))
    # 平台判定与网络无关，固定住以免测试机架构影响结果
    monkeypatch.setattr("mangaproof.update.detector.current_target", lambda: object())
    return made


def _check(
    dlg,
    *,
    branch: str = "stable",
    channel: str = "r2",
    has_update: bool = True,
    note: str = "新增更新功能",
    start: bool = True,
):
    """走真实的「保存并检查更新」入口（worker 已换成假的），再喂回检查结果。

    ``start=False`` 表示只喂结果、不再点一次检查（用于"已经处于 checking"的用例）。
    """
    from mangaproof.ui.update_worker import CheckOutcome
    from mangaproof.update.models import CheckResult, ReleaseInfo
    from mangaproof.update.version import AppVersion

    dlg._select(dlg.branch_combo, branch)
    dlg._select(dlg.channel_combo, channel)
    if start:
        dlg._on_check_clicked()

    target = AppVersion.parse("v1.1.0") if has_update else AppVersion.parse("v1.0.0")
    outcome = CheckOutcome(
        kind="ok",
        result=CheckResult(
            current=AppVersion.parse("1.0.0"),
            release=ReleaseInfo(
                version=target, version_name=str(target), release_note=note
            ),
            branch=branch,
        ),
        filename="pkg.tar.gz",
        filesize=1024,
    )
    dlg._on_check_ok(outcome)
    return outcome


@pytest.fixture
def dialog(qapp, tmp_path):
    manager = SettingsManager(tmp_path / "settings.json")
    manager.save()
    dlg = UpdateDialog(manager.settings)
    yield dlg, manager
    dlg.close()


def test_form_has_all_controls_from_requirement(dialog):
    """需求 §11 的页面元素一个都不能少。"""
    dlg, _ = dialog
    assert dlg.branch_combo.count() == 3
    assert [dlg.branch_combo.itemData(i) for i in range(3)] == ["stable", "beta", "alpha"]
    assert [dlg.channel_combo.itemData(i) for i in range(3)] == [
        "r2", "github", "mirrorchyan",
    ]
    assert dlg.channel_combo.currentData() == "r2"          # 需求 §12 默认渠道
    assert dlg.branch_combo.currentData() == "stable"       # 需求 §12 默认分支
    assert dlg.cdk_edit.isEnabled()
    assert dlg.proxy_edit.text() == ""
    assert dlg.speed_combo.currentData() == 0               # 不限速
    assert dlg.proxy_test_btn.text() == "测试代理"


def test_buttons_are_primary_left_cancel_right(dialog):
    """需求 §11.7：底部主按钮在左、「取消」在右。

    不用 QDialogButtonBox 就是为了这个 —— 它会按平台规范重排。
    2026-09-24 起主按钮是**语义恒定**的「保存并检查更新」，右侧多一个常驻置灰的
    「下载更新」（它按状态在下载/安装之间切换，见下面的用例）。
    """
    dlg, _ = dialog
    assert dlg.check_btn.text() == CHECK_BTN_TEXT == "保存并检查更新"
    assert dlg.cancel_btn.text() == "取消"
    row = dlg.action_row
    assert row.indexOf(dlg.check_btn) == 0, "主按钮必须在最左"
    assert row.indexOf(dlg.cancel_btn) == row.count() - 1, "取消必须在最右"
    # 两者之间必须有 stretch（否则会被拉成等宽或贴在一起）
    assert row.itemAt(1).spacerItem() is not None
    # 右侧按钮常驻占位（不是 show/hide）：避免状态切换时按钮左右位置抖动。
    # 还没 show() 的窗口里 isVisible() 恒为 False，所以看"有没有被显式藏起来"
    assert not dlg.download_btn.isHidden(), "下载按钮必须常驻占位"
    assert dlg.download_btn.text() == DOWNLOAD_BTN_TEXT
    assert not dlg.download_btn.isEnabled(), "还没有检查结果时应当置灰"


def test_progress_is_hidden_initially(dialog):
    dlg, _ = dialog
    assert not dlg.progress.isVisible()
    assert not dlg.detail_label.isVisible()


def test_speed_limit_labels(dialog):
    dlg, _ = dialog
    labels = [dlg.speed_combo.itemText(i) for i in range(dlg.speed_combo.count())]
    assert labels == ["不限速", "10 M", "20 M", "30 M", "40 M", "50 M"]


def test_cancel_does_not_commit_changes(dialog):
    """需求 §13：点「取消」不保存本次未执行检查的修改。"""
    dlg, manager = dialog
    dlg.branch_combo.setCurrentIndex(dlg.branch_combo.findData("beta"))
    dlg.proxy_edit.setText("http://127.0.0.1:7890")
    saved_before = UpdateSettings.from_dict(manager.settings.update.to_dict())

    dlg._on_cancel()          # 等价于点「取消」

    assert manager.settings.update == saved_before, "取消不该写回设置"
    assert manager.settings.update.branch == "stable"


def test_commit_applies_draft_and_signals(dialog):
    """点「检查更新」才提交（需求 §13），并发信号让主窗口落盘。"""
    dlg, manager = dialog
    dlg.branch_combo.setCurrentIndex(dlg.branch_combo.findData("beta"))
    dlg.channel_combo.setCurrentIndex(dlg.channel_combo.findData("github"))
    dlg.proxy_edit.setText("socks5://127.0.0.1:1080")
    dlg.speed_combo.setCurrentIndex(dlg.speed_combo.findData(20))

    emitted: list[int] = []
    dlg.settings_committed.connect(lambda: emitted.append(1))
    dlg._commit()

    assert emitted == [1]
    assert manager.settings.update.branch == "beta"
    assert manager.settings.update.channel == "github"
    assert manager.settings.update.proxy == "socks5://127.0.0.1:1080"
    assert manager.settings.update.speed_limit == 20


def test_no_update_text_matches_requirement(qapp, tmp_path, monkeypatch):
    """需求 §32 的"无更新"文案。"""
    from mangaproof.ui.update_worker import CheckOutcome
    from mangaproof.update.models import CheckResult, ReleaseInfo
    from mangaproof.update.version import AppVersion

    manager = SettingsManager(tmp_path / "settings.json")
    manager.save()
    dlg = UpdateDialog(manager.settings)
    try:
        current = AppVersion.parse("1.1.0.alpha")
        result = CheckResult(
            current=current,
            release=ReleaseInfo(
                version=AppVersion.parse("v1.0.0"), version_name="v1.0.0"
            ),
            branch="stable",
        )
        dlg._commit()
        dlg._on_check_ok(CheckOutcome(kind="ok", result=result))
        text = dlg.status_label.text()
        assert "当前已经是最新版本" in text
        assert "当前版本：v1.1.0.alpha" in text
        assert "更新分支：stable" in text
        assert dlg.check_btn.text() == CHECK_BTN_TEXT, "主按钮文案永远不变"
        assert not dlg.download_btn.isEnabled(), "没有更新可下载"
    finally:
        dlg.close()


def test_update_available_text_matches_requirement(qapp, tmp_path):
    """需求 §33 的"有更新"文案（含文件与大小）。"""
    from mangaproof.ui.update_worker import CheckOutcome
    from mangaproof.update.models import CheckResult, ReleaseInfo
    from mangaproof.update.version import AppVersion

    manager = SettingsManager(tmp_path / "settings.json")
    manager.save()
    dlg = UpdateDialog(manager.settings)
    try:
        result = CheckResult(
            current=AppVersion.parse("1.0.0"),
            release=ReleaseInfo(
                version=AppVersion.parse("v1.1.0"), version_name="v1.1.0",
                release_note="新增更新功能",
            ),
            branch="stable",
        )
        # 走真实入口：_start_check 会记下"本轮用的分支/渠道"，右侧按钮据此判断
        # 结果是否还有效（worker 已由 fake_workers 换成假的）
        dlg._on_check_clicked()
        dlg._on_check_ok(
            CheckOutcome(
                kind="ok", result=result,
                filename="MangaProof-1.1.0-linux-x64.tar.gz",
                filesize=99862975,
            )
        )
        text = dlg.status_label.text()
        assert "发现新版本" in text
        assert "当前版本：v1.0.0" in text
        assert "最新版本：v1.1.0" in text
        assert "MangaProof-1.1.0-linux-x64.tar.gz" in text
        assert "95.2 MB" in text
        assert "新增更新功能" in text
        # 不自动下载，等用户点右侧的「下载更新」；主按钮不参与变身
        assert dlg.check_btn.text() == CHECK_BTN_TEXT
        assert dlg.download_btn.text() == DOWNLOAD_BTN_TEXT
        assert dlg.download_btn.isEnabled()
    finally:
        dlg.close()


def test_download_progress_uses_indeterminate_without_total(qapp, tmp_path):
    """需求 §34：无 Content-Length 时用不确定进度条。"""
    manager = SettingsManager(tmp_path / "settings.json")
    manager.save()
    dlg = UpdateDialog(manager.settings)
    try:
        dlg._on_download_progress(1024 * 1024, None, 2048.0, "正在下载")
        assert dlg.progress.minimum() == 0 and dlg.progress.maximum() == 0
        assert "1.0 MB" in dlg.detail_label.text()
    finally:
        dlg.close()


def test_channel_labels_cover_all_values():
    from mangaproof.config.settings import UPDATE_CHANNELS

    assert set(CHANNEL_LABELS) == set(UPDATE_CHANNELS)


# -- 两个固定动作按钮：保存时机 / 失效规则 / 安装（2026-09-24 改版的核心）--------


def test_check_button_text_never_changes(dialog, fake_workers, tmp_path):
    """左侧按钮永远叫「保存并检查更新」：它不再变身成下载/安装。

    旧版三个动作挤在同一个按钮上（检查 → 立即更新 → 立即安装并重启），
    而"保存"只挂在第一支上——检查完之后改的设置恰好落在缝里。
    """
    dlg, _ = dialog
    package = tmp_path / "pkg.tar.gz"
    package.write_bytes(b"fake package bytes")

    labels = [dlg.check_btn.text()]
    dlg._on_check_clicked()                     # checking
    labels.append(dlg.check_btn.text())
    _check(dlg)                                 # update_available
    labels.append(dlg.check_btn.text())
    dlg._on_download_ok(package)                # done
    labels.append(dlg.check_btn.text())

    assert labels == [CHECK_BTN_TEXT] * 4, labels
    assert dlg.check_btn.isEnabled(), "非忙时左键必须可用（随时能重新检查）"


def test_busy_states_disable_both_action_buttons(dialog, fake_workers):
    """检查/下载进行中两个动作按钮都不可点（取消仍可用）。"""
    dlg, _ = dialog

    dlg._on_check_clicked()
    assert dlg._state == "checking"
    assert not dlg.check_btn.isEnabled() and not dlg.download_btn.isEnabled()
    assert dlg.cancel_btn.isEnabled()

    _check(dlg)
    dlg._on_download_clicked()
    assert dlg._state == "downloading"
    assert not dlg.check_btn.isEnabled() and not dlg.download_btn.isEnabled()
    assert dlg.cancel_btn.isEnabled()


def test_download_picks_up_and_persists_settings_edited_after_check(dialog, fake_workers):
    """检查完之后改的代理/限速/CDK：下载要用新值，并且必须落盘。

    这就是本次改版要修的主症状——旧版只把"保存"挂在检查上，且下载线程读的是
    检查时刻的 draft，于是检查后改的代理/限速既不下发也不保存，分支/渠道更是
    被静默忽略。
    """
    dlg, manager = dialog
    _check(dlg)

    dlg.proxy_edit.setText("socks5://127.0.0.1:1080")
    dlg.speed_combo.setCurrentIndex(dlg.speed_combo.findData(30))
    dlg.cdk_edit.setText("NEW-CDK")

    dlg._on_download_clicked()

    assert len(fake_workers["download"]) == 1, "必须真的发起下载"
    kwargs = fake_workers["download"][0].kwargs
    assert kwargs["proxy"] == "socks5://127.0.0.1:1080"
    assert kwargs["speed_limit_mbps"] == 30
    assert kwargs["cdk"] == "NEW-CDK"
    assert kwargs["branch"] == "stable" and kwargs["source"] == "r2"

    # 同一份配置必须已经写进 settings（主窗口的 settings_committed → _save_settings）
    assert manager.settings.update.proxy == "socks5://127.0.0.1:1080"
    assert manager.settings.update.speed_limit == 30
    assert manager.settings.update.cdk == "NEW-CDK"      # 测试环境没有 keyring


def test_changing_branch_or_channel_invalidates_result(dialog, fake_workers):
    """检查后改分支/渠道 → 右侧按钮置灰 + 提示重查；改回原值自动恢复。"""
    dlg, _ = dialog
    _laid_out(dlg)          # 断言 isVisible() 需要真正 show 过
    _check(dlg, branch="stable", channel="r2")
    assert dlg.download_btn.isEnabled()

    dlg.branch_combo.setCurrentIndex(dlg.branch_combo.findData("beta"))
    assert not dlg.download_btn.isEnabled(), "分支变了就不该还能下载"
    assert dlg.detail_label.isVisible()
    assert dlg.detail_label.text() == STALE_SELECTION_HINT
    assert "发现新版本" in dlg.output_text(), "状态正文不许被提示顶掉"

    dlg.branch_combo.setCurrentIndex(dlg.branch_combo.findData("stable"))
    assert dlg.download_btn.isEnabled(), "改回原值应恢复"
    assert not dlg.detail_label.isVisible()

    dlg.channel_combo.setCurrentIndex(dlg.channel_combo.findData("github"))
    assert not dlg.download_btn.isEnabled()
    assert dlg.detail_label.text() == STALE_SELECTION_HINT

    # 失效状态下即使硬点也不许发请求（防信号/时序绕过）
    dlg._on_download_clicked()
    assert fake_workers["download"] == []


def test_mirrorchyan_without_cdk_shows_friendly_hint(dialog, fake_workers):
    """MirrorChyan 渠道缺 CDK：给可读提示，不发请求、不落盘（需求 §11.8/§18）。"""
    dlg, manager = dialog
    _laid_out(dlg)
    _check(dlg, channel="mirrorchyan")
    dlg.proxy_edit.setText("http://127.0.0.1:7890")      # 故意留一处未保存的修改

    dlg._on_download_clicked()

    assert fake_workers["download"] == [], "CDK 为空时不得发起下载请求"
    assert dlg.detail_label.text() == MIRRORCHYAN_NEEDS_CDK
    assert "CDK 为空：不得发起" not in dlg.output_text(), "不许把内部措辞丢给用户"
    assert "发现新版本" in dlg.status_label.text()
    assert manager.settings.update.proxy == "", "被拒绝的动作不该有副作用"

    dlg.cdk_edit.setText("REAL-CDK")
    dlg._on_download_clicked()
    assert len(fake_workers["download"]) == 1
    assert fake_workers["download"][0].kwargs["cdk"] == "REAL-CDK"


def test_download_button_switches_to_install_and_requests_install(
    dialog, fake_workers, tmp_path
):
    """下载完成 → 右侧按钮变「安装更新」，点它才发安装请求（需求 §46 链路不变）。"""
    dlg, _ = dialog
    _check(dlg)
    package = tmp_path / "MangaProof-1.1.8.alpha-linux-x64.tar.gz"
    package.write_bytes(b"fake package bytes")

    dlg._on_download_ok(package)
    assert dlg.download_btn.text() == INSTALL_BTN_TEXT
    assert dlg.download_btn.isEnabled()
    assert dlg.cancel_btn.text() == "稍后"

    seen: list[object] = []
    dlg.install_requested.connect(seen.append)
    dlg._on_download_clicked()
    assert seen and Path(str(seen[0][0])) == package


def test_recheck_clears_previous_package_and_install_state(dialog, fake_workers, tmp_path):
    """再点「保存并检查更新」必须清掉上一轮的包：否则可能误装旧版本。"""
    dlg, _ = dialog
    _check(dlg)
    package = tmp_path / "MangaProof-1.1.8.alpha-linux-x64.tar.gz"
    package.write_bytes(b"fake package bytes")
    dlg._on_download_ok(package)
    assert dlg.take_package() == package

    dlg._on_check_clicked()                     # 重新检查

    assert dlg.take_package() is None, "上一轮的包必须作废"
    assert dlg.take_package_sha256() == ""
    assert dlg.download_btn.text() == DOWNLOAD_BTN_TEXT
    assert not dlg.download_btn.isEnabled()

    # 这一轮没查到更新 → 依然是"没有可下载/可安装的东西"
    _check(dlg, has_update=False, start=False)
    assert not dlg.download_btn.isEnabled()
    assert dlg.download_btn.text() == DOWNLOAD_BTN_TEXT


def test_download_button_geometry_is_stable(dialog, fake_workers, tmp_path):
    """右侧按钮的常驻占位：文案/可用性变化都不许挪动按钮位置或撑大窗口。"""
    from PySide6.QtWidgets import QApplication

    dlg, _ = dialog
    _laid_out(dlg)
    before = (_box(dlg, dlg.download_btn), _box(dlg, dlg.check_btn)[0], dlg.width())

    _check(dlg)
    QApplication.processEvents()
    assert (_box(dlg, dlg.download_btn), _box(dlg, dlg.check_btn)[0], dlg.width()) == before

    package = tmp_path / "pkg.tar.gz"
    package.write_bytes(b"fake package bytes")
    dlg._on_download_ok(package)
    QApplication.processEvents()
    assert (_box(dlg, dlg.download_btn), _box(dlg, dlg.check_btn)[0], dlg.width()) == before


# -- 布局回归 ---------------------------------------------------------------


def _form_layout(dialog):
    """取出对话框里的 QFormLayout（表单区）。"""
    from PySide6.QtWidgets import QFormLayout

    for i in range(dialog.layout().count()):
        item = dialog.layout().itemAt(i)
        if isinstance(item, QFormLayout):
            return item
    raise AssertionError("更新页面里找不到表单布局")


def _laid_out(dialog):
    """让对话框真正走一遍布局（不 show 的话各控件几何值都是 0）。"""
    from PySide6.QtWidgets import QApplication

    dialog.show()
    for _ in range(2):
        QApplication.processEvents()
    return dialog


def _box(dialog, widget):
    """控件在对话框坐标系里的 ``(左, 右)``（代理行嵌在复合控件里，必须换算）。"""
    top_left = widget.mapTo(dialog, widget.rect().topLeft())
    return top_left.x(), top_left.x() + widget.width()


def test_combos_ignore_wheel(dialog):
    """下拉框不许被滚轮改值（与设置页同款 NoWheelComboBox）。

    Fusion 风格默认允许滚轮直接改下拉框的值，误滚改掉分支/渠道后很难察觉，
    而且这里改的还是"检查更新用哪个分支"。
    """
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QWheelEvent

    from mangaproof.ui.widgets import NoWheelComboBox

    dlg, _ = dialog
    for combo in (dlg.branch_combo, dlg.channel_combo, dlg.speed_combo):
        assert isinstance(combo, NoWheelComboBox)
        before = combo.currentIndex()
        event = QWheelEvent(
            QPoint(5, 5), combo.mapToGlobal(QPoint(5, 5)),
            QPoint(0, -120), QPoint(0, -120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase, False,
        )
        combo.wheelEvent(event)
        assert combo.currentIndex() == before, "滚轮不该改变下拉框选中项"


def test_proxy_row_aligns_with_other_rows(dialog):
    """「代理」行两端与其他输入行严格对齐（曾经左缩 9px、右缩 9px）。

    复合控件（QWidget 包 QHBoxLayout）自带内容边距，且默认按 sizeHint 摆放，
    所以必须清零边距 + Expanding，才能贴齐字段列的两端。
    """
    dlg, _ = dialog
    _laid_out(dlg)

    proxy_left, proxy_right = _box(dlg, dlg.proxy_edit)
    cdk_left, cdk_right = _box(dlg, dlg.cdk_edit)
    btn_left, btn_right = _box(dlg, dlg.proxy_test_btn)
    branch_left, _ = _box(dlg, dlg.branch_combo)

    assert proxy_left == cdk_left, "代理输入框左边界要与 CDK 一致"
    assert proxy_left == branch_left, "代理输入框左边界要与下拉框一致"
    assert btn_right == cdk_right, "测试代理按钮右边界要与其他输入框一致"
    assert proxy_right < btn_left, "输入框与按钮不能重叠"

    form = _form_layout(dlg)
    # 「代理」两个字与 CDK 标签同为右对齐，右边界必须齐平
    proxy_label = form.labelForField(dlg.proxy_edit.parentWidget())
    cdk_label = form.labelForField(dlg.cdk_edit)
    assert proxy_label is not None and cdk_label is not None
    assert _box(dlg, proxy_label)[1] == _box(dlg, cdk_label)[1]


def test_progress_bar_does_not_squeeze_form(dialog):
    """进入「检查中」时不许压扁表单（曾经「代理」行被压到 12px）。

    Qt 在"窗口大小不变"的前提下重排布局，空间不够时 QFormLayout 会自己压缩
    行高。修法：进度条常驻占位（空闲禁用置灰）+ 状态区固定高度 + 最小高度一次
    算准，于是状态切换只改内容，不会重新抢高度。
    """
    dlg, _ = dialog
    _laid_out(dlg)
    heights_before = [dlg.proxy_edit.height(), dlg.cdk_edit.height()]
    dialog_height_before = dlg.height()

    dlg.status_label.setText("正在检查更新……")
    dlg.progress.setRange(0, 0)          # 需求 §31：不确定进度条
    dlg._show_progress()
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()

    assert dlg.progress.isVisible(), "进度条占位常驻（空闲只是禁用置灰）"
    assert [dlg.proxy_edit.height(), dlg.cdk_edit.height()] == heights_before, \
        "代理行被压扁了"
    assert dlg.height() == dialog_height_before, "窗口高度不该被内容变化改掉"

    dlg._reset_progress()
    assert not dlg.progress.isEnabled(), "空闲时进度条置灰"
    assert [dlg.proxy_edit.height(), dlg.cdk_edit.height()] == heights_before


def test_output_area_scrolls_instead_of_growing(qapp, tmp_path):
    """输出区（对话框下半）文案再长也只滚动，不撑大窗口、不裁掉内容。

    更新说明是外部文本（长度不可控），必须能滚：曾经这里没有滚动容器，
    长说明直接把窗口顶着长；后来换成滚动容器但留了个 addStretch，
    弹性空间顶掉滚动条，长文案就只剩裁掉、滚不动。
    """
    from PySide6.QtWidgets import QApplication

    manager = SettingsManager(tmp_path / "settings.json")
    manager.save()
    dlg = UpdateDialog(manager.settings)
    rows = (dlg.branch_combo, dlg.channel_combo, dlg.cdk_edit,
            dlg.proxy_edit, dlg.speed_combo)
    try:
        _laid_out(dlg)
        height_before = dlg.height()
        area_before = dlg.status_area.height()
        rows_before = [w.height() for w in rows]

        dlg.status_label.setText(
            "发现新版本\n\n"
            + "\n".join(f"{i}. 第 {i} 条更新说明，故意写得很长很长。" for i in range(200))
        )
        for _ in range(3):
            QApplication.processEvents()

        scrollbar = dlg.status_area.verticalScrollBar()
        assert scrollbar.maximum() > 0, "长文案必须产生可滚动范围（有内容被裁掉）"
        assert scrollbar.pageStep() == dlg.status_area.viewport().height()
        assert dlg.height() == height_before, "输出区不许把窗口撑大"
        assert dlg.status_area.height() == area_before, "输出区高度固定"
        # 上面各行的行高不许因为输出变长而改变（逐行与原值比对：
        # 行与行之间本来就有 1px 取整差异，不能拿两行互相比）
        assert [w.height() for w in rows] == rows_before, "表单行高被输出区挤掉了"

        # 用户滚到底，再开始新动作 → 视口回到顶部，且旧内容被清空
        scrollbar.setValue(scrollbar.maximum())
        dlg._clear_output()
        QApplication.processEvents()
        assert scrollbar.value() == 0, "清空输出后视口要回到顶部"
        assert dlg.output_text() == ""
    finally:
        dlg.close()


def test_new_action_clears_previous_output(qapp, tmp_path):
    """开始新动作时清空上一轮输出（否则分不清哪条是本次结论）。"""
    from PySide6.QtWidgets import QApplication

    from mangaproof.ui.update_worker import CheckOutcome
    from mangaproof.update.models import CheckResult, ReleaseInfo
    from mangaproof.update.version import AppVersion

    manager = SettingsManager(tmp_path / "settings.json")
    manager.save()
    dlg = UpdateDialog(manager.settings)
    try:
        _laid_out(dlg)
        result = CheckResult(
            current=AppVersion.parse("1.0.0"),
            release=ReleaseInfo(
                version=AppVersion.parse("v1.1.0"), version_name="v1.1.0",
                release_note="新增更新功能",
            ),
            branch="stable",
        )
        dlg._on_check_ok(
            CheckOutcome(kind="ok", result=result, filename="pkg.tar.gz", filesize=1024)
        )
        assert "发现新版本" in dlg.output_text()
        assert dlg.cancel_btn.text() == "取消"

        # 点「下载更新」：旧结论先被清掉，再写本轮开头文案
        dlg._result = result
        dlg._clear_output()
        dlg.status_label.setText("正在准备下载……")
        QApplication.processEvents()
        text = dlg.output_text()
        assert "发现新版本" not in text
        assert "正在准备下载……" in text

        # 下载完成后「取消」变「稍后」；状态与文案也要能被下一轮整体清掉
        dlg.cancel_btn.setText("稍后")
        dlg._clear_output()
        QApplication.processEvents()
        assert dlg.output_text() == ""
        assert dlg.status_area.verticalScrollBar().value() == 0
    finally:
        dlg.close()


def test_proxy_result_goes_to_output_and_clears_it(qapp, tmp_path):
    """测试代理的结果写进输出区，且新一次测试会清掉上一次的结果。"""
    from PySide6.QtWidgets import QApplication

    manager = SettingsManager(tmp_path / "settings.json")
    manager.save()
    dlg = UpdateDialog(manager.settings)
    try:
        _laid_out(dlg)
        dlg._on_proxy_result(True, "代理可用（耗时 123 ms）")
        QApplication.processEvents()
        assert "代理可用" in dlg.output_text()

        dlg._on_proxy_result(False, "连接被拒绝")
        QApplication.processEvents()
        text = dlg.output_text()
        assert "连接被拒绝" in text
        assert "代理可用" not in text, "上一次的结果要被替换掉"
    finally:
        dlg.close()


def test_download_progress_shows_size_and_speed(qapp, tmp_path):
    """下载中必须在进度条下面显示「已下载 / 总量 + 速度」（曾经整行消失）。

    回归点：_clear_output() 在下载开始时会把详情行藏起来，而进度回调只 setText
    不 setVisible —— 于是数字永远看不见。
    """
    manager = SettingsManager(tmp_path / "settings.json")
    manager.save()
    dlg = UpdateDialog(manager.settings)
    try:
        _laid_out(dlg)
        dlg._clear_output()                      # 模拟"下载开始"先清输出
        assert not dlg.detail_label.isVisible()

        dlg._on_download_progress(5 * 1024 * 1024, 100 * 1024 * 1024, 2.5e6, "正在下载")
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

        assert dlg.detail_label.isVisible(), "详情行必须重新显示"
        text = dlg.detail_label.text()
        assert "5.0 MB" in text and "100.0 MB" in text, text
        assert "速度" in text, text
        assert dlg.progress.maximum() == 100 and dlg.progress.value() == 5

        # 无 Content-Length：不确定进度 + "未知"总量，但仍要有速度
        dlg._on_download_progress(1024 * 1024, None, 2048.0, "正在下载")
        QApplication.processEvents()
        assert dlg.progress.minimum() == 0 and dlg.progress.maximum() == 0
        assert "未知" in dlg.detail_label.text()
        assert "速度" in dlg.detail_label.text()
    finally:
        dlg.close()


def test_install_request_carries_sha256(qapp, tmp_path):
    """点「立即安装并重启」时，SHA-256 必须随信号递给主窗口（需求 §53）。

    否则安装器拿到空 --sha256，只能打一句"未提供 --sha256，本次不做哈希校验"
    就跳过替换前的复核 —— 而三个下载渠道其实都能给出文件名与 SHA-256。
    """
    from PySide6.QtWidgets import QApplication

    manager = SettingsManager(tmp_path / "settings.json")
    manager.save()
    dlg = UpdateDialog(manager.settings)
    try:
        _laid_out(dlg)
        package = tmp_path / "MangaProof-1.1.5.alpha-linux-x64.tar.gz"
        package.write_bytes(b"fake package bytes")
        dlg._on_download_ok(package)
        QApplication.processEvents()

        digest = dlg.take_package_sha256()
        assert len(digest) == 64, "应当是完整的 SHA-256（不再是截断的预览）"
        assert digest == hashlib.sha256(package.read_bytes()).hexdigest()
        assert digest in dlg.status_label.text(), "界面也要给出完整哈希供用户核对"

        seen: list[object] = []
        dlg.install_requested.connect(seen.append)
        dlg._on_download_clicked()             # done 状态 → 发安装请求
        assert seen, "必须发出 install_requested"
        payload = seen[0]
        assert isinstance(payload, tuple) and len(payload) == 2
        assert Path(str(payload[0])) == package
        assert payload[1] == digest
    finally:
        dlg.close()


# -- 取消/关窗时的后台线程收尾（防"卡死一下然后闪退"）------------------------


class _HangingWorker:
    """最小假 worker：永远"还在跑"，用来逼出取消路径。"""

    def __init__(self) -> None:
        self.cancelled = False
        self.disowned = False
        self.waited: list[int] = []

    def isRunning(self) -> bool:      # noqa: N802 - 模仿 QThread 接口
        return True

    def request_cancel(self) -> None:
        self.cancelled = True

    def wait(self, ms: int) -> bool:  # noqa: N802
        self.waited.append(ms)
        return False                  # 模拟"阻塞在 socket 读上，等不到"

    def disown(self) -> None:
        self.disowned = True


def test_abort_disowns_stuck_workers_instead_of_blocking(qapp, tmp_path):
    """下载线程卡在网络读上时：发取消、短暂等待、立即脱离（不许长阻塞）。

    这是"窗口消失 → 卡死一小会 → 闪退"的根因回归：
    - 长等待（原来固定 2000ms）会让主界面卡住；
    - 不脱离则线程随对话框一起析构 → Qt 报
      ``QThread: Destroyed while thread is still running`` 并 abort 进程。
    """
    manager = SettingsManager(tmp_path / "settings.json")
    manager.save()
    dlg = UpdateDialog(manager.settings)
    try:
        stuck = _HangingWorker()
        dlg._download_worker = stuck
        orphans = dlg._abort_workers()

        assert stuck.cancelled, "必须先发取消"
        assert stuck.waited and stuck.waited[0] <= 500, \
            f"等待必须是短等待，实际 {stuck.waited}"
        assert stuck.disowned, "等不到就必须脱离，否则线程会随窗口一起被析构"
        assert orphans == [stuck], "脱离出去的线程要交给主窗口托管"
    finally:
        dlg.close()


def test_abort_ignores_idle_workers(qapp, tmp_path):
    """没在跑的线程不碰（不 wait、不 disown）。"""
    manager = SettingsManager(tmp_path / "settings.json")
    manager.save()
    dlg = UpdateDialog(manager.settings)
    try:
        class _Idle(_HangingWorker):
            def isRunning(self):      # noqa: N802
                return False

        idle = _Idle()
        dlg._check_worker = idle
        assert dlg._abort_workers() == []
        assert not idle.cancelled and not idle.disowned
    finally:
        dlg.close()


def test_cancel_and_close_record_orphans(qapp, tmp_path):
    """点「取消」与直接关窗都要把脱离的线程记在 _orphaned_workers 上。"""
    manager = SettingsManager(tmp_path / "settings.json")
    manager.save()

    dlg = UpdateDialog(manager.settings)
    stuck = _HangingWorker()
    dlg._download_worker = stuck
    dlg._on_cancel()
    assert dlg._orphaned_workers == [stuck]

    dlg2 = UpdateDialog(manager.settings)
    stuck2 = _HangingWorker()
    dlg2._download_worker = stuck2
    dlg2.close()
    assert dlg2._orphaned_workers == [stuck2]


def test_worker_cancel_flag_is_shared_and_pending():
    """三个 worker 都用同一套线程安全取消标志（request_cancel → _cancel_pending）。"""
    from mangaproof.ui.update_worker import (
        UpdateCheckWorker,
        UpdateDownloadWorker,
    )

    check = UpdateCheckWorker(branch="stable", source="r2", proxy="")
    assert check._cancel_pending() is False
    check.request_cancel()
    assert check._cancel_pending() is True

    # 下载 worker 构造参数较多，只验证它继承了同一套接口（不真的启动线程）
    assert hasattr(UpdateDownloadWorker, "request_cancel")
    assert hasattr(UpdateDownloadWorker, "disown")
    for cls in (UpdateCheckWorker, UpdateDownloadWorker):
        assert issubclass(cls, __import__(
            "mangaproof.ui.update_worker", fromlist=["x"]
        )._AbortableWorker)

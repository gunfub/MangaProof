# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""更新后台线程（worker）的单测：GitHub Token 透传与提示行信号。

这里不联网、不起线程：直接把 ``run()`` 当普通函数调用，信号在**同一线程**里
直连到收集列表。网络函数（``check_update`` / ``sources.*`` /
``download_and_verify``）一律 monkeypatch 掉，所以本文件跑得很快。

三条 2026-10-20 的约定，各有用例守着：

1. 下载线程把 ``DownloadPlan.notes`` 逐条通过 ``notice`` 信号发出
   （界面把它显示在提示行里，而不是塞进每 100ms 重写一次的状态正文）；
2. GitHub Token 从对话框 → 检查线程 → ``sources.github_plan()`` 一路透传
   （检查阶段的大小探测与下载阶段是同一个请求，token 必须一致）；
3. 大小探测失败不影响"有没有新版本"的结论，但失败原因要进 ``CheckOutcome.notice``
   （否则用户只看到"大小获取失败"，不知道是 Token 坏了还是 IP 被限流）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from mangaproof import __version__  # noqa: E402
from mangaproof.ui import update_worker  # noqa: E402
from mangaproof.update import detector, sources  # noqa: E402
from mangaproof.update.errors import NetworkError  # noqa: E402
from mangaproof.update.models import DownloadPlan, ReleaseInfo  # noqa: E402
from mangaproof.update.version import AppVersion  # noqa: E402

TARGET = detector.target_for("linux", "x64") if detector.target_for("linux", "x64") else None
NEW_RELEASE = ReleaseInfo(version=AppVersion.parse("9.9.9"), version_name="v9.9.9")


@pytest.fixture(scope="module")
def qapp():
    """QThread 是 QObject，构造时要求有 QApplication（离屏即可，不 exec）。"""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _plan(**kw) -> DownloadPlan:
    base = dict(filename="MangaProof-x.zip", url="https://example/x.zip", source="github")
    base.update(kw)
    return DownloadPlan(**base)


# --- 下载线程：notes → notice ------------------------------------------------

def test_download_worker_emits_plan_notes_as_notice(qapp, monkeypatch, tmp_path):
    plan = _plan(notes=["GitHub Token 无效，已自动改用匿名请求", "GitHub 未提供 digest"])
    pytest.importorskip("httpx")
    monkeypatch.setattr(update_worker.sources, "build_plan", lambda **kw: plan)
    landed = tmp_path / plan.filename
    monkeypatch.setattr(update_worker, "download_and_verify", lambda *a, **kw: landed)

    worker = update_worker.UpdateDownloadWorker(
        branch="stable", source="github", release=NEW_RELEASE,
        target=TARGET, dest_dir=tmp_path,
    )
    notices: list[str] = []
    done: list[object] = []
    worker.notice.connect(notices.append)
    worker.succeeded.connect(done.append)

    worker.run()

    assert notices == plan.notes, "提示要逐条发出，顺序与 notes 一致"
    assert done == [landed]
    assert not worker.isRunning()


def test_download_worker_passes_github_token_to_build_plan(qapp, monkeypatch, tmp_path):
    captured: dict = {}
    plan = _plan()

    def fake_build_plan(**kw):
        captured.update(kw)
        return plan

    monkeypatch.setattr(update_worker.sources, "build_plan", fake_build_plan)
    monkeypatch.setattr(update_worker, "download_and_verify", lambda *a, **kw: tmp_path / "x")

    worker = update_worker.UpdateDownloadWorker(
        branch="beta", source="github", release=NEW_RELEASE, target=TARGET,
        dest_dir=tmp_path, proxy="http://127.0.0.1:1", github_token="ghp_worker",
    )
    worker.run()

    assert captured["token"] == "ghp_worker"
    assert captured["source"] == "github"
    assert captured["proxy"] == "http://127.0.0.1:1"


def test_download_worker_emits_no_notice_when_plan_is_clean(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(update_worker.sources, "build_plan", lambda **kw: _plan())
    monkeypatch.setattr(update_worker, "download_and_verify", lambda *a, **kw: tmp_path / "x")

    worker = update_worker.UpdateDownloadWorker(
        branch="stable", source="r2", release=NEW_RELEASE, target=TARGET, dest_dir=tmp_path,
    )
    notices: list[str] = []
    worker.notice.connect(notices.append)
    worker.run()
    assert notices == []


# --- 检查线程：大小探测带 token ----------------------------------------------

def test_check_worker_probe_carries_github_token(qapp, monkeypatch):
    monkeypatch.setattr(
        update_worker, "check_update", lambda **kw: (NEW_RELEASE, __version__)
    )
    captured: dict = {}

    def fake_plan(**kw):
        captured.update(kw)
        return _plan(filesize=123456, notes=["GitHub Token 已回退匿名"])

    monkeypatch.setattr(update_worker.sources, "github_plan", fake_plan)

    worker = update_worker.UpdateCheckWorker(
        branch="stable", source="github", github_token="ghp_check",
    )
    outcomes: list[object] = []
    worker.succeeded.connect(outcomes.append)

    worker.run()

    outcome = outcomes[0]
    assert captured["token"] == "ghp_check", "检查阶段的大小探测必须带同一个 token"
    assert outcome.filesize == 123456
    assert outcome.notice == "GitHub Token 已回退匿名"


def test_check_worker_probe_without_token_is_anonymous(qapp, monkeypatch):
    monkeypatch.setattr(
        update_worker, "check_update", lambda **kw: (NEW_RELEASE, __version__)
    )
    captured: dict = {}

    def fake_plan(**kw):
        captured.update(kw)
        return _plan(filesize=1)

    monkeypatch.setattr(update_worker.sources, "github_plan", fake_plan)
    worker = update_worker.UpdateCheckWorker(branch="stable", source="github")
    worker.succeeded.connect(lambda _o: None)
    worker.run()
    assert captured["token"] == ""


def test_check_worker_probe_failure_still_reports_new_version(qapp, monkeypatch):
    """探测失败不能把"发现新版本"这个结论吞掉，但原因必须给出来。"""
    monkeypatch.setattr(
        update_worker, "check_update", lambda **kw: (NEW_RELEASE, __version__)
    )

    def boom(**kw):
        raise NetworkError("查询 GitHub Release", sources.ANON_RATE_LIMIT_HINT)

    monkeypatch.setattr(update_worker.sources, "github_plan", boom)

    worker = update_worker.UpdateCheckWorker(branch="stable", source="github")
    outcomes: list[object] = []
    worker.succeeded.connect(outcomes.append)
    worker.run()

    outcome = outcomes[0]
    assert outcome.kind == "ok"
    assert outcome.result is not None and outcome.result.has_update
    assert outcome.size_note == "大小获取失败，将在下载时显示"
    assert "GitHub Token" in outcome.notice, "原因要指向可操作的那一项"

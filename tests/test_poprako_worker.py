# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""作品加载 worker 单测（``ui/poprako_worker.ComicsLoadWorker``）。

直接调用 ``run()``（不开线程），用信号收集器断言"读取 → 匹配 → 排序"三阶段与失败文案。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from mangaproof.poprako.errors import PoprakoNetworkError  # noqa: E402
from mangaproof.poprako.models import Comic, ImportResult, RemoteIssue  # noqa: E402
from mangaproof.ui.poprako_worker import ComicsLoadWorker, UploadWorker  # noqa: E402


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def comic(cid: str, title: str) -> Comic:
    return Comic(id=cid, title=title, author="", chapter_count=1, is_archived=False)


class _Client:
    def __init__(self, rows, *, error: Exception | None = None):
        self._rows = rows
        self._error = error
        self.pages: list[tuple[int, int]] = []

    def comics(self, workset_id: str, *, progress=None):
        assert workset_id == "w_1"
        if self._error is not None:
            raise self._error
        if progress is not None:
            progress(1, len(self._rows))
        return list(self._rows)


def _run(worker: ComicsLoadWorker) -> tuple[list, list, list]:
    progress: list = []
    rows: list = []
    errors: list = []
    worker.progress.connect(lambda done, total, text: progress.append((done, total, text)))
    worker.succeeded.connect(lambda value: rows.append(value))
    worker.failed.connect(lambda message: errors.append(message))
    worker.run()
    return progress, rows, errors


def test_worker_ranks_and_reports_three_phases(qapp):
    client = _Client([comic("c1", "无关作品"), comic("c2", "某漫画"),
                      comic("c3", "某漫画 第二部")])
    progress, rows, errors = _run(ComicsLoadWorker(client, "w_1", "某漫画"))

    assert errors == []
    assert len(rows) == 1
    assert [entry.comic.id for entry in rows[0]] == ["c2", "c3", "c1"]
    texts = [text for _done, _total, text in progress]
    assert any("正在读取作品" in text for text in texts)
    assert any("正在匹配名称" in text for text in texts)
    assert texts[-1] == "正在排序…"
    # 匹配阶段用的是"确定进度"（total > 0），读取阶段是"不确定"（total == 0）
    assert any(total == 0 for _done, total, _text in progress)
    assert any(total > 0 for _done, total, _text in progress)


def test_worker_empty_list_skips_ranking(qapp):
    progress, rows, errors = _run(ComicsLoadWorker(_Client([]), "w_1", "某漫画"))
    assert errors == [] and rows == [[]]
    assert all("正在匹配名称" not in text for _done, _total, text in progress)


def test_worker_reports_network_error_in_chinese(qapp):
    client = _Client([], error=PoprakoNetworkError())
    _progress, rows, errors = _run(ComicsLoadWorker(client, "w_1", "某漫画"))
    assert rows == []
    # 直连失败 → 文案直接给出下一步（去代理页）
    assert errors == [
        "无法连接 api.poprako.com（当前为直连，可在「代理」页设置代理后重试）；"
        "整体替换是幂等的，可安全重试"
    ]


def test_worker_network_error_via_proxy_keeps_generic_hint(qapp):
    """走代理失败时不该再劝用户"去设代理"——应该去查代理本身。"""
    client = _Client([], error=PoprakoNetworkError(via_proxy=True))
    _progress, rows, errors = _run(ComicsLoadWorker(client, "w_1", "某漫画"))
    assert errors == [
        "无法连接 api.poprako.com（请检查网络或代理）；整体替换是幂等的，可安全重试"
    ]


def test_worker_wraps_unexpected_exception(qapp):
    client = _Client([], error=RuntimeError("boom"))
    _progress, rows, errors = _run(ComicsLoadWorker(client, "w_1", "某漫画"))
    assert rows == []
    assert errors and "内部错误" in errors[0]


# ---------------------------------------------------------------- 上传（2026-10-21）


class _UploadClient:
    """导入 + 回读两个请求的假客户端；``readback_error`` 用来模拟"导入成功但校验失败"。"""

    def __init__(self, *, imported=(2, 3), readback=None, readback_error=None):
        self._imported = imported
        self._readback = readback or []
        self._readback_error = readback_error
        self.calls: list[str] = []

    def import_issues(self, chapter_id, payload):
        self.calls.append("import")
        return ImportResult(imported_page_count=self._imported[0],
                            imported_issue_count=self._imported[1])

    def issues(self, chapter_id):
        self.calls.append("issues")
        if self._readback_error is not None:
            raise self._readback_error
        return list(self._readback)


def _run_upload(worker: UploadWorker) -> tuple[list, list, list, list]:
    progress: list = []
    outcomes: list = []
    errors: list = []
    partials: list = []
    worker.progress.connect(lambda done, total, text: progress.append((done, total, text)))
    worker.succeeded.connect(lambda value: outcomes.append(value))
    worker.failed.connect(lambda message: errors.append(message))
    worker.failed_partial.connect(lambda message: partials.append(message))
    worker.run()
    return progress, outcomes, errors, partials


def test_upload_worker_reports_two_phases_and_counts(qapp):
    rows = [
        RemoteIssue(id=f"ri_{i}", page_artwork_id="pa_1", index=i, variant="v",
                    layer_name=None, rect=None, note="")
        for i in range(3)
    ]
    progress, outcomes, errors, partials = _run_upload(
        UploadWorker(_UploadClient(readback=rows), "ch_1", {"pages": []})
    )

    assert partials == []

    assert errors == []
    assert [(done, total) for done, total, _text in progress] == [(0, 2), (1, 2)]
    assert progress[0][2].startswith("正在导入")
    assert progress[1][2].startswith("正在回读校验")
    assert len(outcomes) == 1
    assert outcomes[0].counts_match is True


def test_upload_worker_says_imported_when_readback_fails(qapp):
    """导入已经成功了，回读失败不能笼统说"上传失败"。"""
    _progress, outcomes, errors, partials = _run_upload(
        UploadWorker(
            _UploadClient(readback_error=PoprakoNetworkError()), "ch_1", {"pages": []}
        )
    )

    assert outcomes == [] and errors == [], "导入成功就不该走整条失败分支"
    assert len(partials) == 1
    assert partials[0].startswith("已导入，但回读校验失败：")

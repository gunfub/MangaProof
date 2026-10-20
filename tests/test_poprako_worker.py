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
from mangaproof.poprako.models import Comic  # noqa: E402
from mangaproof.ui.poprako_worker import ComicsLoadWorker  # noqa: E402


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
    assert errors == ["无法连接 api.poprako.com（请检查网络或代理）；整体替换是幂等的，可安全重试"]


def test_worker_wraps_unexpected_exception(qapp):
    client = _Client([], error=RuntimeError("boom"))
    _progress, rows, errors = _run(ComicsLoadWorker(client, "w_1", "某漫画"))
    assert rows == []
    assert errors and "内部错误" in errors[0]

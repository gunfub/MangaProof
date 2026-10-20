# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""Poprako 同步的后台线程（需求 §13；设计文档 §9）。

网络动作一律在 QThread 里跑，界面线程只收发结果：

- :class:`CallWorker`：跑一个无参可调用，成功把返回值原样递回主线程；
- :class:`UploadWorker`：导入 + 回读校验（两次请求），结果合成
  :class:`UploadOutcome`。

两条与既有 ``ui/update_worker.py`` 相同的约定：

1. 线程**不碰任何控件**，只发信号；异常在这里就翻译成中文文案
   （:func:`mangaproof.poprako.errors.user_message`），界面只负责显示；
2. 对话框关闭时若线程还在跑，:func:`adopt_worker` 把它从对话框名下"摘"出来托管
   ——QThread 在被析构时仍在运行会让 Qt 直接 abort 进程。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

from PySide6.QtCore import QThread, Signal

from mangaproof.poprako.client import PoprakoClient, mask_proxy, probe_connection
from mangaproof.poprako.errors import PoprakoError, user_message
from mangaproof.poprako.models import ImportResult

log = logging.getLogger("mangaproof.ui.poprako_worker")

__all__ = [
    "CallWorker",
    "ProxyProbeWorker",
    "UploadWorker",
    "UploadOutcome",
    "adopt_worker",
]

#: 已从对话框"脱离"、尚未结束的线程。必须留引用，否则 QThread 被 GC 时
#: 若仍在运行，Qt 会 abort 整个进程。
_LINGERING: set[QThread] = set()


def adopt_worker(worker: QThread) -> None:
    """接管一个仍在运行的线程：断开界面回调，等它自己收尾后回收。"""
    for signal in (getattr(worker, "succeeded", None), getattr(worker, "failed", None)):
        if signal is None:
            continue
        try:
            signal.disconnect()
        except (RuntimeError, TypeError):
            pass
    _LINGERING.add(worker)
    worker.finished.connect(lambda: _release(worker))


def _release(worker: QThread) -> None:
    _LINGERING.discard(worker)
    worker.deleteLater()


class CallWorker(QThread):
    """跑一个无参可调用，把结果或错误文案发回主线程。"""

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[], object], parent: Optional[object] = None):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:  # noqa: D102 —— QThread 约定
        try:
            result = self._fn()
        except PoprakoError as exc:
            log.info("Poprako 操作失败：%s", type(exc).__name__)
            self.failed.emit(user_message(exc))
            return
        except Exception:
            log.exception("Poprako 操作出现未预期异常")
            self.failed.emit("操作失败：内部错误（已记录日志）")
            return
        self.succeeded.emit(result)


@dataclass(frozen=True, slots=True)
class UploadOutcome:
    """一次上传的结果：服务端计数 + 回读校验得到的实际计数。"""

    result: ImportResult
    remote_page_count: int
    remote_issue_count: int

    @property
    def counts_match(self) -> bool:
        return (
            self.result.imported_issue_count == self.remote_issue_count
            and self.result.imported_page_count >= self.remote_page_count
        )


class UploadWorker(QThread):
    """导入问题 → 回读校验（需求 §9.4：只有校验通过才算成功）。"""

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        client: PoprakoClient,
        chapter_id: str,
        payload: dict,
        parent: Optional[object] = None,
    ):
        super().__init__(parent)
        self._client = client
        self._chapter_id = chapter_id
        self._payload = payload

    def run(self) -> None:  # noqa: D102 —— QThread 约定
        try:
            result = self._client.import_issues(self._chapter_id, self._payload)
            rows = self._client.issues(self._chapter_id)
        except PoprakoError as exc:
            log.info("Poprako 上传失败：%s", type(exc).__name__)
            self.failed.emit(user_message(exc))
            return
        except Exception:
            log.exception("Poprako 上传出现未预期异常")
            self.failed.emit("上传失败：内部错误（已记录日志）")
            return

        pages = {row.page_artwork_id for row in rows}
        self.succeeded.emit(UploadOutcome(result=result,
                                          remote_page_count=len(pages),
                                          remote_issue_count=len(rows)))


class ProxyProbeWorker(QThread):
    """用候选代理访问谷歌，**只测不存**（需求：测试不落盘）。"""

    finished_with = Signal(bool, str)     # 是否连通, 说明文本

    def __init__(self, proxy: str, parent: Optional[object] = None):
        super().__init__(parent)
        self._proxy = proxy

    def run(self) -> None:  # noqa: D102 —— QThread 约定
        try:
            ok, message = probe_connection(self._proxy)
        except Exception as exc:      # pragma: no cover —— probe_connection 已兜底
            ok, message = False, f"代理测试失败：{exc}"
        # 日志只记遮罩后的地址（代理可能带账号密码）
        log.info("Poprako 代理测试：%s（%s）", "成功" if ok else "失败", mask_proxy(self._proxy))
        self.finished_with.emit(ok, message)

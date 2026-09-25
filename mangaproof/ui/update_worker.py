# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""自更新的后台线程（需求 §5：继续采用现有项目的 QThread + Signal 异步方式）。

与 :mod:`mangaproof.ui.report_worker` / :mod:`numbering_worker` 保持**同一套写法**：

- ``QThread`` 子类（不是 moveToThread）；
- 三个信号：``progress`` / ``succeeded`` / ``failed``；
- ``request_cancel()`` 置标志，运行体在回调处检查并抛自定义 ``Cancelled``；
- ``run()`` 内 try/except，异常记 ``log.exception`` 后经 ``failed`` 回传。

与那两个 worker 的唯一差异：``failed`` 传的是**异常对象**而不是字符串。
原因：更新失败的文案取决于错误类型（CDK 过期 / 限流 / 版本信息异常 / 校验失败…），
UI 需要拿到 :class:`mangaproof.update.errors.UpdateError` 的子类才能给出正确提示。

网络逻辑全部在 :mod:`mangaproof.update.*`（无 Qt），本模块只做"搬运 + 取消 + 信号"。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Signal

from mangaproof import __version__
from mangaproof.update import detector, sources
from mangaproof.update.downloader import DownloadCancelled, download_and_verify
from mangaproof.update.errors import UpdateError
from mangaproof.update.mirrorchyan import check_update, fetch_download_info
from mangaproof.update.models import CheckResult, DownloadPlan, ReleaseInfo
from mangaproof.update.version import is_newer

log = logging.getLogger("mangaproof.ui.update_worker")


class _Cancelled(Exception):
    """内部取消信号（不对外暴露，UI 用 KIND_CANCELLED 区分）。"""


@dataclass
class CheckOutcome:
    """检查更新的结果：要么拿到 :class:`CheckResult`，要么是"当前架构不支持"。"""

    kind: str                      # ok / unsupported
    result: Optional[CheckResult] = None
    message: str = ""
    #: 需求 §33 要显示的"文件 / 大小"。检查响应里没有这些字段（检查不带 CDK），
    #: 所以 R2/GitHub 渠道额外探一次；探不到就留空，UI 显示"—"，不阻断流程。
    filename: str = ""
    filesize: Optional[int] = None
    size_note: str = ""


class _AbortableWorker(QThread):
    """可以**安全地中断并脱离**的 QThread（更新页三个 worker 的共同基类）。

    为什么需要这层基类（实测故障：下载途中点取消 → 更新窗口消失后主界面卡死
    一小会然后闪退）：

    1. **取消标志必须线程安全**：``run()`` 在工作线程里读标志，``request_cancel()``
       在主线程里写。原来写的是裸 ``bool``，GIL 下"没崩"不代表"看得见"
       —— 工作线程可能长时间读到旧值，取消迟迟不生效（表现为卡一下）。
    2. **脱离（disown）不能靠 wait() 兜底**：窗口是模态对话框，``exec()`` 返回后
       主程序若让它析构，作为子对象的 QThread 会触发
       ``QThread: Destroyed while thread is still running`` —— 这是 Qt 的
       **致命错误，直接 abort 进程**（闪退）。而网络读一旦阻塞在 socket 上，
       工作线程可能要等 read 超时（30 秒）才看得到取消标志，等不起。
       所以：先 ``disconnect()`` 摘掉所有信号（此后不再回写任何 UI，也就不会再
       触碰即将析构的窗口），再把 QThread 的父子关系解除交给"应用级托管"
       （见 ``MainWindow._adopt_update_workers``），工作线程在后台自然收尾。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancel_lock = threading.Lock()
        self._cancel_requested = False

    # -- 取消 --------------------------------------------------------------- #

    def request_cancel(self) -> None:
        """请求取消（线程安全；可重复调用）。"""
        with self._cancel_lock:
            self._cancel_requested = True

    def _cancel_pending(self) -> bool:
        with self._cancel_lock:
            return self._cancel_requested

    # -- 脱离 --------------------------------------------------------------- #

    def disown(self) -> None:
        """摘掉信号并解除父子关系：让本线程**在窗口析构后**还能安全收尾。

        调用方负责持有引用直到 ``finished``（否则 Python 侧回收包装对象，
        而 Qt 不允许销毁仍在运行的 QThread）。
        """
        try:
            self.disconnect()
        except (RuntimeError, TypeError):  # pragma: no cover - 无连接/已断开
            pass
        self.setParent(None)


class UpdateCheckWorker(_AbortableWorker):
    """检查更新（**不带 CDK**，需求 §17）。"""

    progress = Signal(str)          # 阶段文案（"正在检查更新……"）
    succeeded = Signal(object)      # CheckOutcome
    failed = Signal(object)         # UpdateError

    def __init__(self, *, branch: str, source: str = "r2", proxy: str = "", parent=None):
        super().__init__(parent)
        self._branch = branch
        self._source = source
        self._proxy = proxy

    def run(self) -> None:
        try:
            target = detector.current_target()
            if target is None:
                self.succeeded.emit(
                    CheckOutcome(
                        kind="unsupported",
                        message=(
                            "当前系统架构暂不支持更新\n"
                            f"（系统 {detector.current_os()}，"
                            f"架构 {detector._machine() or '未知'}）"
                        ),
                    )
                )
                return

            self.progress.emit("正在检查更新……")
            release, current = check_update(
                target=target, channel=self._branch, current_version=__version__
            )
            result = CheckResult(
                current=_parse_current(current), release=release, branch=self._branch
            )
            outcome = CheckOutcome(kind="ok", result=result)
            if result.has_update:
                self._fill_file_and_size(outcome, target)
            log.info(
                "检查更新完成：当前 %s，目标 %s，有更新=%s",
                result.current_display, result.latest_display, result.has_update,
            )
            self.succeeded.emit(outcome)
        except UpdateError as exc:
            log.warning("检查更新失败：%s", exc)
            self.failed.emit(exc)
        except Exception as exc:  # pragma: no cover - 兜底
            log.exception("检查更新出现未预期异常")
            self.failed.emit(UpdateError(f"检查更新失败：{exc}"))

    def _fill_file_and_size(self, outcome: CheckOutcome, target) -> None:
        """补齐需求 §33 的"文件 / 大小"（R2/GitHub 渠道）。

        MirrorChyan 渠道**故意不预取**：那需要 CDK，而 CDK 只能用在
        "立即更新"阶段（§18）。该渠道在点击后再把大小补显到下载界面。
        """
        from mangaproof.update.version import AppVersion

        version = str(outcome.result.release.version)
        outcome.filename = detector.artifact_name(version, target)
        if self._source == "mirrorchyan":
            outcome.size_note = "大小将在点击「立即更新」后获取"
            return
        try:
            if self._source == "r2":
                plan = sources.r2_plan(
                    version=version, target=target, branch=self._branch,
                    proxy=self._proxy, with_sha256=False,
                )
                outcome.filesize = sources.probe_size(plan.url, proxy=self._proxy)
            else:
                plan = sources.github_plan(
                    version=version, target=target, proxy=self._proxy,
                    with_sha256=True,
                )
                outcome.filesize = plan.filesize
        except UpdateError as exc:
            log.info("补齐文件大小失败（可忽略）：%s", exc)
            outcome.size_note = "大小获取失败，将在下载时显示"
        if outcome.filesize is None and not outcome.size_note:
            outcome.size_note = "大小未知，将在下载时显示"


class ProxyTestWorker(_AbortableWorker):
    """测试代理连通性（需求 §29：必须明确显示成功/失败及错误原因）。"""

    finished_with = Signal(bool, str)   # 成功?, 说明文本

    def __init__(self, *, proxy: str, parent=None):
        super().__init__(parent)
        self._proxy = proxy

    def run(self) -> None:
        try:
            ok, message = sources.probe_proxy(self._proxy)
        except Exception as exc:  # pragma: no cover - probe_proxy 已兜底
            ok, message = False, f"代理测试失败：{exc}"
        log.info("代理测试结果：%s（%s）", "成功" if ok else "失败", message)
        self.finished_with.emit(ok, message)


class CacheClearWorker(_AbortableWorker):
    """清理升级缓存（需求 §36/§37 的两个临时目录）。

    走后台线程的原因：目录里可能有上百 MB 的更新包，Windows 上还会被杀毒软件
    逐个文件扫描——同步删会让界面卡住，而"整个 GUI 必须保持响应"是这个更新页
    一贯的要求（§31）。

    删除逻辑全在 :mod:`mangaproof.update.cache`（**主程序侧**，与安装器无关），
    本类只负责搬结果；取消请求在两个目录之间被检查，删到一半不做回滚。
    """

    finished_with = Signal(object)      # CacheClearReport

    def run(self) -> None:
        from mangaproof.update import cache

        try:
            report = cache.clear_update_cache(cancel=self._cancel_pending)
        except Exception as exc:  # pragma: no cover - cache 模块已兜住 OSError
            log.exception("清理升级缓存出现未预期异常")
            report = cache.failed_report(str(exc))
        self.finished_with.emit(report)


class UpdateDownloadWorker(_AbortableWorker):
    """下载更新包（含 MirrorChyan 的"带 CDK 取下载信息"步骤）。

    渠道差异（需求 §28）在这里体现：

    - R2 / GitHub：直接按版本拼资产名并取哈希；
    - MirrorChyan：**先**用 CDK 请求下载信息（只有用户点「立即更新」才会走到这里），
      再用服务端下发的短效 URL 下载。
    """

    progress = Signal(int, object, object, str)   # 已下载, 总数|None, 速度|None, 阶段
    succeeded = Signal(object)                    # Path（校验通过后的正式文件）
    failed = Signal(object)                       # UpdateError

    def __init__(
        self,
        *,
        branch: str,
        source: str,
        release: ReleaseInfo,
        target: detector.Target,
        dest_dir: Path,
        proxy: str = "",
        speed_limit_mbps: int = 0,
        cdk: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._branch = branch
        self._source = source
        self._release = release
        self._target = target
        self._dest_dir = Path(dest_dir)
        self._proxy = proxy
        self._speed_limit = speed_limit_mbps
        self._cdk = cdk

    def run(self) -> None:
        try:
            plan = self._build_plan()
            path = download_and_verify(
                plan,
                self._dest_dir,
                proxy=self._proxy,
                speed_limit_mbps=self._speed_limit,
                on_progress=self._on_progress,
                cancel=self._cancel_pending,
            )
            self.succeeded.emit(path)
        except DownloadCancelled:
            log.info("下载被用户取消")
            self.failed.emit(DownloadCancelled())
        except UpdateError as exc:
            log.warning("下载失败：%s", exc)
            self.failed.emit(exc)
        except Exception as exc:  # pragma: no cover - 兜底
            log.exception("下载出现未预期异常")
            self.failed.emit(UpdateError(f"下载失败：{exc}"))

    def _build_plan(self) -> DownloadPlan:
        version = str(self._release.version)
        if self._source == "mirrorchyan":
            # 需求 §18：CDK 只在这里、且只在用户点击「立即更新」之后使用
            self.progress.emit(0, None, None, "正在获取下载地址…")
            fresh = fetch_download_info(
                target=self._target, channel=self._branch, cdk=self._cdk
            )
            return sources.mirrorchyan_plan(release=fresh, target=self._target)
        return sources.build_plan(
            source=self._source,
            branch=self._branch,
            version=version,
            target=self._target,
            proxy=self._proxy,
        )

    def _on_progress(
        self, done: int, total: int | None, speed: float | None, phase: str
    ) -> None:
        self.progress.emit(done, total, speed, phase)
        if self._cancel_pending():
            raise DownloadCancelled()


def _parse_current(raw: str):
    """当前版本串 → :class:`AppVersion`（失败时抛 UpdateError，由 run() 兜住）。"""
    from mangaproof.update.version import AppVersion, InvalidVersion

    try:
        return AppVersion.parse(raw)
    except InvalidVersion as exc:  # pragma: no cover - 除非源码版本号写坏
        raise UpdateError(f"本程序版本号异常：{raw!r}") from exc


__all__ = [
    "CacheClearWorker",
    "CheckOutcome",
    "ProxyTestWorker",
    "UpdateCheckWorker",
    "UpdateDownloadWorker",
    "is_newer",
]

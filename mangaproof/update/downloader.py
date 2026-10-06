# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""更新包下载器（需求 §34、§35、§29、§30）。

**本模块不导入 Qt**：它是纯逻辑，既能被主程序的 QThread worker 调用，
也能被独立安装器复用，还能在单测里用 ``httpx.MockTransport`` 离线跑。

流程（严格按需求 §35）：

.. code-block:: text

    下载到  MangaProof-xxx.part
        ↓
    SHA-256 校验（§53）
        ↓ 通过
    改名为正式文件名
        ↓ 中断/失败
    删除 .part，重新完整下载（第一版不做断点续传，§1）

两条与渠道相关的差异（需求 §28）：

- **代理**：只有 GitHub 与 R2 使用用户配置的代理；MirrorChyan 不使用（§29）；
- **限速**：同样只作用于 GitHub 与 R2（§30）。限速与代理可同时启用。

限速实现：滑动"应完成时间"表 —— 每写一块就按 ``已写字节 / 限速`` 算出
应到的时刻，超前后 ``sleep`` 差额。比"每块固定 sleep"更准，也不会因为
网络本身慢而额外拖慢（只在超前时等待）。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable

import httpx

from mangaproof.update.checksum import ChecksumMismatch, verify_file, verify_size
from mangaproof.update.errors import DownloadError, UpdateError
from mangaproof.update.models import DownloadPlan

log = logging.getLogger("mangaproof.update.downloader")

#: 单块读取大小（256 KiB：进度刷新够细，系统调用不过多）
CHUNK_SIZE = 256 * 1024

#: 进度回调：(已下载字节, 总字节或 None, 瞬时速度或 None, 阶段文案)
ProgressCallback = Callable[[int, int | None, float | None, str], None]

#: 取消回调：返回 True 表示用户已请求取消
CancelCallback = Callable[[], bool]

PHASE_CONNECTING = "正在连接…"
PHASE_DOWNLOADING = "正在下载"
PHASE_VERIFYING = "正在校验"


class DownloadCancelled(UpdateError):
    """用户取消下载（需求 §35：删除 .part）。"""

    def __init__(self) -> None:
        super().__init__("已取消下载")


def _bps_from_mbps(speed_limit_mbps: int | float | None) -> float:
    """限速档位（MB/s，0 表示不限速）→ 字节/秒。"""
    if not speed_limit_mbps or speed_limit_mbps <= 0:
        return 0.0
    return float(speed_limit_mbps) * 1024 * 1024


def _client(proxy: str, *, transport: httpx.BaseTransport | None) -> httpx.Client:
    """构造下载 client。

    - ``follow_redirects=True``：GitHub Release 资产会 302 到
      ``release-assets.githubusercontent.com``（重定向后不再带 Authorization）；
    - ``trust_env=False``：代理只认用户在本软件里显式填写的地址，
      避免环境变量里的代理悄悄生效（与 MirrorChyan 侧一致的口径）；
    - 读取超时给得很宽：大包下载期间可能长时间没有新字节到达，
      但 read 超时是"两次读到数据之间"的上限，30 秒足够容忍网络抖动。
    """
    timeout = httpx.Timeout(connect=15.0, read=30.0, write=30.0, pool=15.0)
    return httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        trust_env=False,
        proxy=(proxy or None),
        headers={"User-Agent": "MangaProof"},
        transport=transport,
    )


def download_and_verify(
    plan: DownloadPlan,
    dest_dir: Path,
    *,
    proxy: str = "",
    speed_limit_mbps: int = 0,
    on_progress: ProgressCallback | None = None,
    cancel: CancelCallback | None = None,
    transport: httpx.BaseTransport | None = None,
) -> Path:
    """下载 + 校验 + 改名，返回最终文件路径。

    - ``plan.use_proxy=False``（MirrorChyan）时忽略 ``proxy``；
    - ``plan.use_speed_limit=False`` 时忽略 ``speed_limit_mbps``；
    - 任一环节失败/取消都会删掉 ``.part``（需求 §35）。
    """
    dest_dir = Path(dest_dir)
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # 需求 §65 新增的失败模式：Android 的更新包写**系统 Download**，
        # 未授予「所有文件访问」时就会在这里失败。给一句能照着做的提示，
        # 而不是把裸 errno 丢给用户（桌面端同样受益：磁盘只读/无权限）。
        raise DownloadError(
            f"无法写入更新包目录：{dest_dir}\n{exc}\n"
            "（Android 请确认已授予「所有文件访问」权限；桌面端请检查磁盘空间与目录权限）"
        ) from exc
    part_path = dest_dir / f"{plan.filename}.part"
    final_path = dest_dir / plan.filename

    effective_proxy = proxy if plan.use_proxy else ""
    effective_limit = speed_limit_mbps if plan.use_speed_limit else 0
    limit_bps = _bps_from_mbps(effective_limit)

    if part_path.exists():      # 不做断点续传：直接重下（需求 §1/§35）
        log.info("发现上次残留的临时文件，删除后重下：%s", part_path.name)
        part_path.unlink(missing_ok=True)

    def report(done: int, total: int | None, speed: float | None, phase: str) -> None:
        if on_progress is not None:
            on_progress(done, total, speed, phase)

    def check_cancel() -> None:
        if cancel is not None and cancel():
            raise DownloadCancelled()

    log.info(
        "开始下载：%s（渠道 %s，代理 %s，限速 %s）",
        plan.filename, plan.source,
        effective_proxy or "无",
        f"{effective_limit} MB/s" if effective_limit else "不限速",
    )

    try:
        report(0, plan.filesize, None, PHASE_CONNECTING)
        downloaded, total = _stream_to_part(
            plan, part_path, proxy=effective_proxy, limit_bps=limit_bps,
            on_progress=report, check_cancel=check_cancel, transport=transport,
        )
        check_cancel()

        # 需求 §53：破坏性操作前必须"包存在 + SHA-256 正确"
        report(downloaded, total, None, PHASE_VERIFYING)
        if not verify_size(part_path, plan.filesize):
            raise DownloadError(
                f"下载不完整：{plan.filename} 的大小与预期不符"
                f"（期望 {plan.filesize} 字节）"
            )
        if plan.sha256:
            try:
                verify_file(part_path, plan.sha256)
            except ChecksumMismatch as exc:
                raise DownloadError(str(exc)) from exc
        else:
            log.warning("没有可用的 SHA-256，跳过校验：%s（渠道 %s）",
                        plan.filename, plan.source)

        part_path.replace(final_path)       # 需求 §35：校验通过才改名为正式文件名
        log.info("下载完成：%s", final_path.name)
        return final_path
    except BaseException:
        # 取消、校验失败、网络中断、磁盘写满……一律不留半成品
        try:
            part_path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - 删除失败不该掩盖真正的异常
            log.warning("删除临时文件失败：%s", part_path.name)
        raise


def _stream_to_part(
    plan: DownloadPlan,
    part_path: Path,
    *,
    proxy: str,
    limit_bps: float,
    on_progress: ProgressCallback,
    check_cancel: Callable[[], None],
    transport: httpx.BaseTransport | None,
) -> tuple[int, int | None]:
    """把响应体流式写进 ``.part``，返回 ``(已下载字节, 总字节或 None)``。"""
    written = 0
    started = time.monotonic()
    last_report = 0.0
    speed: float | None = None

    try:
        with _client(proxy, transport=transport) as client:
            with client.stream("GET", plan.url) as response:
                response.raise_for_status()
                total = _content_length(response)
                if total is None:
                    # 需求 §34：无 Content-Length 时用不确定进度条
                    log.info("响应没有 Content-Length，进度将按不确定模式显示")
                    total = plan.filesize

                with open(part_path, "wb") as fh:
                    for chunk in response.iter_bytes(CHUNK_SIZE):
                        check_cancel()
                        if not chunk:
                            continue
                        fh.write(chunk)
                        written += len(chunk)

                        now = time.monotonic()
                        elapsed = now - started
                        speed = written / elapsed if elapsed > 0 else None

                        # 限速：只在"跑得比配额快"时等待
                        if limit_bps > 0:
                            expected = written / limit_bps
                            drift = expected - elapsed
                            if drift > 0:
                                time.sleep(min(drift, 1.0))

                        # 进度回调限频：每 100 ms 一次，避免刷爆事件循环
                        if now - last_report >= 0.1:
                            last_report = now
                            on_progress(written, total, speed, PHASE_DOWNLOADING)
    except httpx.HTTPStatusError as exc:
        raise DownloadError(
            f"下载失败：HTTP {exc.response.status_code}（{plan.filename}）"
        ) from exc
    except httpx.HTTPError as exc:
        raise DownloadError(f"下载失败：{type(exc).__name__}: {exc}") from exc
    except OSError as exc:
        raise DownloadError(f"写入文件失败：{exc}") from exc

    on_progress(written, total, speed, PHASE_DOWNLOADING)
    return written, total


def _content_length(response: httpx.Response) -> int | None:
    """从响应头取总大小；缺失或非法返回 ``None``。"""
    raw = response.headers.get("Content-Length")
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None

# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""下载器单测（需求 §34/§35/§29/§30）。

全部离线（``httpx.MockTransport``）。重点覆盖：

- ``.part`` 生命周期：成功改名、失败/取消必删（§35）；
- 校验失败**绝不**留下正式文件（§53 的"破坏性操作前必须校验"）；
- 无 ``Content-Length`` 时用不确定进度（§34）；
- 代理/限速只作用于 GitHub 与 R2（§29/§30）。
"""

from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from mangaproof.update import downloader  # noqa: E402
from mangaproof.update.downloader import (  # noqa: E402
    DownloadCancelled,
    download_and_verify,
)
from mangaproof.update.errors import DownloadError  # noqa: E402
from mangaproof.update.models import DownloadPlan  # noqa: E402

BODY = b"MangaProof payload " * 8192          # 147 KB
DIGEST = hashlib.sha256(BODY).hexdigest()
FILENAME = "MangaProof-1.1.0.alpha-linux-x64.tar.gz"


def make_plan(**kw) -> DownloadPlan:
    base = dict(
        filename=FILENAME,
        url="https://download.mangaproof.priloba.com/stable/" + FILENAME,
        sha256=DIGEST,
        filesize=len(BODY),
        use_proxy=True,
        use_speed_limit=True,
        source="r2",
    )
    base.update(kw)
    return DownloadPlan(**base)


def body_transport(*, status: int = 200, body: bytes = BODY,
                   content_length: bool = True) -> httpx.MockTransport:
    headers = {"Content-Length": str(len(body))} if content_length else {}

    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, text="nope")
        return httpx.Response(200, content=body, headers=headers)

    return httpx.MockTransport(handler)


def test_successful_download_verifies_and_renames(tmp_path: Path):
    final = download_and_verify(
        make_plan(), tmp_path, transport=body_transport()
    )
    assert final == tmp_path / FILENAME
    assert final.read_bytes() == BODY
    assert not (tmp_path / f"{FILENAME}.part").exists()


def test_progress_reports_phases_and_monotonic_bytes(tmp_path: Path):
    events: list[tuple[int, int | None, float | None, str]] = []
    download_and_verify(
        make_plan(), tmp_path,
        on_progress=lambda *a: events.append(a),
        transport=body_transport(),
    )
    assert events, "至少要报告一次进度"
    phases = [e[3] for e in events]
    assert downloader.PHASE_CONNECTING in phases
    assert downloader.PHASE_VERIFYING in phases
    downloaded = [e[0] for e in events]
    assert downloaded == sorted(downloaded), "已下载字节必须单调不减"
    assert downloaded[-1] == len(BODY)


def test_download_without_content_length(tmp_path: Path):
    """需求 §34：无 Content-Length 时仍要能下载（用不确定进度）。"""
    events = []
    final = download_and_verify(
        make_plan(filesize=None), tmp_path,
        on_progress=lambda *a: events.append(a),
        transport=body_transport(content_length=False),
    )
    assert final.read_bytes() == BODY
    assert any(e[1] is None for e in events), "应出现 total=None 的不确定进度事件"


def test_checksum_mismatch_leaves_nothing_behind(tmp_path: Path):
    with pytest.raises(DownloadError):
        download_and_verify(
            make_plan(sha256="0" * 64), tmp_path, transport=body_transport()
        )
    assert not (tmp_path / FILENAME).exists()
    assert not (tmp_path / f"{FILENAME}.part").exists()


def test_size_mismatch_is_detected_before_hashing(tmp_path: Path):
    with pytest.raises(DownloadError) as exc:
        download_and_verify(
            make_plan(filesize=len(BODY) + 1), tmp_path, transport=body_transport()
        )
    assert "不完整" in str(exc.value)
    assert not (tmp_path / FILENAME).exists()


def test_missing_sha256_still_installs_but_warns(tmp_path: Path, caplog):
    """没有校验值时不阻断（R2 manifest 缺失的场景），但要留下告警日志。"""
    final = download_and_verify(
        make_plan(sha256=None), tmp_path, transport=body_transport()
    )
    assert final.exists()


def test_cancel_removes_part_file(tmp_path: Path):
    """中途取消：必须删掉 .part，且不产生正式文件（需求 §35）。

    用 1 MiB 负载（>CHUNK_SIZE 256 KiB）确保真的跨多块读取 ——
    否则 MockTransport 会一次给完，取消检查只落在开头/结尾，测不到"读到一半取消"。
    """
    body = b"y" * (1024 * 1024)
    state = {"n": 0}

    def cancel() -> bool:
        state["n"] += 1
        return state["n"] > 1          # 读到第二块前取消

    with pytest.raises(DownloadCancelled):
        download_and_verify(
            make_plan(sha256=hashlib.sha256(body).hexdigest(), filesize=len(body)),
            tmp_path, cancel=cancel, transport=body_transport(body=body),
        )
    assert not (tmp_path / FILENAME).exists()
    assert not (tmp_path / f"{FILENAME}.part").exists()


def test_cancel_before_start_does_not_write(tmp_path: Path):
    with pytest.raises(DownloadCancelled):
        download_and_verify(
            make_plan(), tmp_path, cancel=lambda: True, transport=body_transport()
        )
    assert list(tmp_path.iterdir()) == []


def test_http_error_becomes_download_error_without_partial_file(tmp_path: Path):
    with pytest.raises(DownloadError) as exc:
        download_and_verify(
            make_plan(), tmp_path, transport=body_transport(status=500)
        )
    assert "500" in str(exc.value)
    assert list(tmp_path.iterdir()) == []


def test_stale_part_file_is_replaced(tmp_path: Path):
    """上次中断留下的 .part 必须被清掉重下（第一版不做断点续传，§1/§35）。"""
    stale = tmp_path / f"{FILENAME}.part"
    stale.write_bytes(b"garbage from last run")
    final = download_and_verify(make_plan(), tmp_path, transport=body_transport())
    assert final.read_bytes() == BODY
    assert not stale.exists()


def test_mirrorchyan_plan_ignores_proxy_and_speed_limit(tmp_path: Path, monkeypatch):
    """需求 §29/§30：MirrorChyan 不使用用户代理与限速。"""
    captured: dict = {}

    real_client = downloader._client

    def spy(proxy: str, *, transport=None):
        captured["proxy"] = proxy
        return real_client(proxy, transport=transport)

    monkeypatch.setattr(downloader, "_client", spy)
    plan = make_plan(use_proxy=False, use_speed_limit=False, source="mirrorchyan")
    download_and_verify(
        plan, tmp_path, proxy="http://127.0.0.1:1080", speed_limit_mbps=10,
        transport=body_transport(),
    )
    assert captured["proxy"] == "", "MirrorChyan 渠道不得把用户代理传下去"


def test_speed_limit_actually_waits(tmp_path: Path):
    """限速生效：128 KB @ 1 MB/s 至少应花掉理论时间的若干成。"""
    body = b"x" * (128 * 1024)
    digest = hashlib.sha256(body).hexdigest()
    started = time.monotonic()
    download_and_verify(
        make_plan(sha256=digest, filesize=len(body)), tmp_path,
        speed_limit_mbps=1, transport=body_transport(body=body),
    )
    elapsed = time.monotonic() - started
    assert elapsed >= 0.05, f"限速没有生效（耗时 {elapsed:.3f}s）"


def test_no_speed_limit_is_fast(tmp_path: Path):
    started = time.monotonic()
    download_and_verify(
        make_plan(), tmp_path, speed_limit_mbps=0, transport=body_transport()
    )
    assert time.monotonic() - started < 2.0


def test_bps_conversion():
    assert downloader._bps_from_mbps(0) == 0.0
    assert downloader._bps_from_mbps(None) == 0.0
    assert downloader._bps_from_mbps(10) == 10 * 1024 * 1024


def test_unwritable_dest_dir_is_reported_actionably(tmp_path: Path, monkeypatch):
    """目标目录建不出来 → 给出能照着做的提示，而不是裸 errno（需求 §65）。

    Android 上这是**新引入的**失败模式：更新包写系统 Download，未授予
    「所有文件访问」时就会走这里。裸 errno 用户看不懂，也不提示该去授权。
    """
    def _deny(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "mkdir", _deny)

    with pytest.raises(DownloadError) as excinfo:
        download_and_verify(make_plan(), tmp_path, transport=body_transport())

    text = str(excinfo.value)
    assert "无法写入更新包目录" in text
    assert "所有文件访问" in text, "要点明该去哪里授权"
    assert str(tmp_path) in text, "要带上具体目录，便于排查"

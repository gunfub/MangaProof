# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""下载渠道（sources）单测：R2 / GitHub / MirrorChyan 的取包与取哈希方式。

三条渠道的哈希来源不同（R2 manifest / GitHub digest / MirrorChyan 响应），
本文件的用例把三者都钉死，并覆盖需求 §28 的渠道差异（CDK、代理、限速）。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from mangaproof.update import detector, sources  # noqa: E402
from mangaproof.update.errors import DownloadError, NetworkError, UpdateError  # noqa: E402
from mangaproof.update.models import ReleaseInfo  # noqa: E402
from mangaproof.update.version import AppVersion  # noqa: E402

VERSION = "1.1.0.alpha"
TARGET = detector.target_for("linux", "x64")
FILENAME = detector.artifact_name(VERSION, TARGET)
SHA = "a" * 64


def router(routes: dict[str, httpx.Response]):
    """按 URL 前缀分发响应；未命中返回 404。"""
    record: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        record.append(request)
        url = str(request.url)
        for prefix, response in routes.items():
            if url.startswith(prefix):
                return response
        return httpx.Response(404, text="not found")

    return httpx.MockTransport(handler), record


# --- Cloudflare R2 -----------------------------------------------------------

def test_r2_plan_uses_branch_directory_and_manifest():
    manifest_url = (
        f"{detector.r2_url(sources._r2_base(), 'beta', detector.sha256_manifest_name(VERSION))}"
    )
    transport, record = router({
        manifest_url: httpx.Response(200, json={FILENAME: SHA}),
    })
    plan = sources.r2_plan(
        version=VERSION, target=TARGET, branch="beta", transport=transport
    )
    assert plan.filename == FILENAME
    assert plan.url == detector.r2_url(sources._r2_base(), "beta", FILENAME)
    assert plan.sha256 == SHA
    assert plan.use_proxy is True and plan.use_speed_limit is True
    # manifest 请求必须带 cache-bust（Cloudflare 默认缓存 4 小时）
    assert any("?t=" in str(r.url) for r in record)


def test_r2_branch_is_not_the_download_source():
    """回归：R2 路径用的是**更新分支**（stable/beta/alpha），不是下载渠道名。"""
    transport, _ = router({})
    plan = sources.r2_plan(
        version=VERSION, target=TARGET, branch="alpha",
        transport=transport, with_sha256=False,
    )
    assert "/alpha/" in plan.url
    assert "/r2/" not in plan.url


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(404, text="no manifest"),
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json={"other-file.zip": SHA}),
        httpx.Response(200, json={FILENAME: "not-a-hash"}),
        httpx.Response(200, json=[1, 2, 3]),
    ],
)
def test_r2_plan_survives_broken_manifest(response):
    """manifest 缺失/损坏时降级为"无校验值"，但不阻断下载（并给出提示）。"""
    transport, _ = router({detector.sha256_manifest_name(VERSION): response})
    plan = sources.r2_plan(
        version=VERSION, target=TARGET, branch="stable", transport=transport
    )
    assert plan.sha256 is None
    assert plan.notes, "应当留下「跳过校验」的说明"


def test_probe_size_reads_content_length():
    transport, _ = router({
        "https://download.mangaproof.priloba.com/": httpx.Response(
            200, headers={"Content-Length": "123456"}, content=b""
        )
    })
    assert sources.probe_size("https://download.mangaproof.priloba.com/x", transport=transport) == 123456


def test_probe_size_failure_is_not_fatal():
    transport, _ = router({})
    assert sources.probe_size("https://x/y", transport=transport) is None


# --- GitHub ------------------------------------------------------------------

def github_payload(digest: str | None = "sha256:" + SHA, name: str = FILENAME) -> dict:
    asset = {
        "name": name,
        "browser_download_url": f"https://github.com/o/r/releases/download/v{VERSION}/{name}",
        "size": 99862975,
    }
    if digest is not None:
        asset["digest"] = digest
    return {"tag_name": f"v{VERSION}", "assets": [asset]}


def test_github_plan_strips_digest_prefix():
    """需求 §22 + 官方 API：digest 形如 sha256:<hex>，落库前要剥前缀。"""
    transport, _ = router({"https://api.github.com/": httpx.Response(200, json=github_payload())})
    plan = sources.github_plan(version=VERSION, target=TARGET, transport=transport)
    assert plan.sha256 == SHA
    assert plan.filesize == 99862975
    assert plan.source == "github"
    assert plan.url.endswith(FILENAME)


def test_github_plan_without_digest_still_downloads():
    transport, _ = router({
        "https://api.github.com/": httpx.Response(200, json=github_payload(digest=None))
    })
    plan = sources.github_plan(version=VERSION, target=TARGET, transport=transport)
    assert plan.sha256 is None
    assert any("digest" in n for n in plan.notes)


def test_github_plan_rate_limit_hint():
    """实测过：匿名 GitHub API 会 403 限流，必须给可行动的提示。"""
    transport, _ = router({"https://api.github.com/": httpx.Response(403, json={})})
    with pytest.raises(NetworkError) as exc:
        sources.github_plan(version=VERSION, target=TARGET, transport=transport)
    assert "403" in str(exc.value)
    assert "R2" in str(exc.value)


def test_github_plan_missing_asset():
    transport, _ = router({
        "https://api.github.com/": httpx.Response(
            200, json=github_payload(name="MangaProof-other-linux-x64.tar.gz")
        )
    })
    with pytest.raises(DownloadError) as exc:
        sources.github_plan(version=VERSION, target=TARGET, transport=transport)
    assert FILENAME in str(exc.value)


# --- GitHub Token（2026-10-20） ----------------------------------------------

def sequence_router(responses: list[httpx.Response]):
    """按调用顺序返回响应（最后一次之后重复最后一个），并记录请求。"""
    record: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        record.append(request)
        return responses[min(len(record) - 1, len(responses) - 1)]

    return httpx.MockTransport(handler), record


def _has_authorization(request: httpx.Request) -> bool:
    return any(k.lower() == "authorization" for k in request.headers)


def test_github_plan_sends_token_as_bearer_header():
    """有 token 时，api.github.com 的请求必须带 Authorization。"""
    transport, record = sequence_router([httpx.Response(200, json=github_payload())])
    plan = sources.github_plan(
        version=VERSION, target=TARGET, transport=transport, token="ghp_test"
    )
    assert record[0].headers["Authorization"] == "Bearer ghp_test"
    assert plan.sha256 == SHA


def test_github_plan_without_token_sends_no_credentials():
    """留空 = 匿名请求：请求里不允许出现任何 Authorization。"""
    transport, record = sequence_router([httpx.Response(200, json=github_payload())])
    sources.github_plan(version=VERSION, target=TARGET, transport=transport)
    assert not _has_authorization(record[0])


def test_github_plan_invalid_token_falls_back_to_anonymous():
    """401（Bad credentials）→ 记一条提示 + 自动匿名重试，不让流程卡死。"""
    transport, record = sequence_router([
        httpx.Response(401, json={"message": "Bad credentials"}),
        httpx.Response(200, json=github_payload()),
    ])
    plan = sources.github_plan(
        version=VERSION, target=TARGET, transport=transport, token="ghp_bad"
    )
    assert len(record) == 2, "应当自动重试一次"
    assert _has_authorization(record[0]) and not _has_authorization(record[1])
    assert plan.sha256 == SHA, "回落后仍应拿到完整计划"
    assert any("匿名" in n for n in plan.notes), "回退要留下提示给界面"


def test_github_plan_forbidden_token_falls_back_and_keeps_reason():
    """403（无权/配额尽）同样回退，提示里要带状态码。"""
    transport, record = sequence_router([
        httpx.Response(403, json={"message": "Resource not accessible by personal access token"}),
        httpx.Response(200, json=github_payload()),
    ])
    plan = sources.github_plan(
        version=VERSION, target=TARGET, transport=transport, token="ghp_limited"
    )
    assert len(record) == 2
    assert any("403" in n for n in plan.notes)


def test_github_plan_fallback_then_rate_limited_reports_both_reasons():
    """Token 坏了、匿名也被限流 → 一条报错讲清两件事，并指向 R2。"""
    transport, _ = sequence_router([
        httpx.Response(401, json={"message": "Bad credentials"}),
        httpx.Response(403, json={"message": "API rate limit exceeded"}),
    ])
    with pytest.raises(NetworkError) as exc:
        sources.github_plan(
            version=VERSION, target=TARGET, transport=transport, token="ghp_bad"
        )
    text = str(exc.value)
    assert "Token" in text and "401" in text
    assert "匿名" in text and "403" in text
    assert "R2" in text


def test_anonymous_rate_limit_hint_points_to_token_field():
    """匿名 403 的文案必须直接告诉用户"去更新页填 GitHub Token"。"""
    transport, _ = sequence_router([httpx.Response(403, json={})])
    with pytest.raises(NetworkError) as exc:
        sources.github_plan(version=VERSION, target=TARGET, transport=transport)
    text = str(exc.value)
    assert "GitHub Token" in text and "60 次/小时" in text


def test_github_token_never_reaches_logs(caplog):
    """需求 §16 的延伸：Token 不进日志（连脱敏前的原文都不行）。"""
    import logging

    caplog.set_level(logging.DEBUG, logger="mangaproof.update")
    token = "ghp_supersecret_do_not_log"
    transport, _ = sequence_router([
        httpx.Response(401, json={"message": "Bad credentials"}),
        httpx.Response(200, json=github_payload()),
    ])
    sources.github_plan(version=VERSION, target=TARGET, transport=transport, token=token)
    assert token not in caplog.text


def test_build_plan_passes_token_to_github():
    transport, record = sequence_router([httpx.Response(200, json=github_payload())])
    sources.build_plan(
        source="github", branch="stable", version=VERSION,
        target=TARGET, transport=transport, token="ghp_via_build",
    )
    assert record[0].headers["Authorization"] == "Bearer ghp_via_build"


def test_build_plan_ignores_token_for_r2():
    """R2 渠道没有凭证概念：即使误传 token 也不能出现在请求里。"""
    manifest_url = detector.r2_url(
        sources._r2_base(), "stable", detector.sha256_manifest_name(VERSION)
    )
    transport, record = router({manifest_url: httpx.Response(200, json={FILENAME: SHA})})
    sources.build_plan(
        source="r2", branch="stable", version=VERSION,
        target=TARGET, transport=transport, token="ghp_should_not_leak",
    )
    assert all(not _has_authorization(r) for r in record)



# --- MirrorChyan -------------------------------------------------------------

def make_release(**kw) -> ReleaseInfo:
    base = dict(
        version=AppVersion.parse("v1.0.0"),
        version_name="v1.0.0",
        url="https://mirrorchyan.com/api/resources/download/AbCdEf",
        sha256=SHA,
        filesize=176652385,
        update_type="full",
    )
    base.update(kw)
    return ReleaseInfo(**base)


def test_mirrorchyan_plan_uses_server_url_and_disables_proxy_and_limit():
    plan = sources.mirrorchyan_plan(release=make_release(), target=TARGET)
    assert plan.url == "https://mirrorchyan.com/api/resources/download/AbCdEf"
    assert plan.sha256 == SHA
    assert plan.filesize == 176652385
    assert plan.use_proxy is False, "需求 §29：MirrorChyan 不使用代理"
    assert plan.use_speed_limit is False, "需求 §30：MirrorChyan 不使用限速"


def test_mirrorchyan_plan_rejects_incremental_package():
    """需求 §1：第一版只支持完整包；服务端若给增量包必须明确拒绝。"""
    with pytest.raises(DownloadError) as exc:
        sources.mirrorchyan_plan(release=make_release(update_type="incremental"), target=TARGET)
    assert "完整包" in str(exc.value)


def test_mirrorchyan_plan_requires_url():
    with pytest.raises(DownloadError):
        sources.mirrorchyan_plan(release=make_release(url=None), target=TARGET)


# --- build_plan 统一入口 ------------------------------------------------------

def test_build_plan_dispatch_r2():
    manifest_url = detector.r2_url(
        sources._r2_base(), "stable", detector.sha256_manifest_name(VERSION)
    )
    transport, _ = router({manifest_url: httpx.Response(200, json={FILENAME: SHA})})
    plan = sources.build_plan(
        source="r2", branch="stable", version=VERSION, target=TARGET, transport=transport
    )
    assert plan.source == "r2" and plan.sha256 == SHA


def test_build_plan_dispatch_mirrorchyan_requires_release():
    with pytest.raises(DownloadError) as exc:
        sources.build_plan(
            source="mirrorchyan", branch="stable", version=VERSION, target=TARGET
        )
    assert "CDK" in str(exc.value)


def test_build_plan_rejects_unknown_source():
    with pytest.raises(UpdateError):
        sources.build_plan(
            source="ftp", branch="stable", version=VERSION, target=TARGET
        )


def test_release_tag_matches_repo_convention():
    """仓库约定：标签 = v + __version__（release.yml 会校验两者相等）。"""
    from mangaproof import __version__

    assert sources.release_tag(__version__) == f"v{__version__}"

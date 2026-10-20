# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""下载渠道：把"目标版本 + 渠道设置"变成一个可执行的 :class:`DownloadPlan`。

需求 §28 的渠道矩阵（本模块是它的唯一实现处）：

============  ==========  ========  ============  ======  ======
项目            GitHub      R2        MirrorChyan   代理     限速
============  ==========  ========  ============  ======  ======
检查更新         MirrorChyan（一律，且不带 CDK）
实际下载         ✅          ✅         ✅            —       —
CDK            ❌          ❌         ✅            —       —
代理            ✅          ✅         ❌            —       —
限速            ✅          ✅         ❌            —       —
============  ==========  ========  ============  ======  ======

三条渠道拿到 SHA-256 的方式各不相同（都归一到裸 hex）：

- **MirrorChyan**：带 CDK 的响应直接给 ``sha256``（下载前即可拿到）；
- **R2**：``{channel}/MangaProof-<version>-sha256.json`` 里按文件名查（需求 §24/§26）；
- **GitHub**：Release asset 的 ``digest``（形如 ``sha256:<hex>``，需剥前缀）。

**发现新版本页面的"文件/大小"**（需求 §33）在 R2/GitHub 渠道由一次 HEAD 补齐；
MirrorChyan 渠道不预取（那需要 CDK，而 CDK 只能用在"立即更新"阶段）。

**2026-10-20 追加：GitHub Token 的边界**

GitHub 渠道真正会被 IP 限制卡住的是 ``api.github.com`` 那一次资产元数据请求
（匿名配额 60 次/小时/IP，用尽即 403）；拿到 ``browser_download_url`` 之后的
字节下载会 302 到 ``release-assets.githubusercontent.com`` 的**签名短链**，
本来就不需要任何凭证。因此 token 的适用边界按**请求目标**划，而不是按阶段划：

- 带 -> ``api.github.com``（检查阶段的大小探测、下载阶段的资产解析，同一函数）；
- 不带 -> MirrorChyan、Cloudflare R2、``github.com`` 与 ``release-assets`` 的字节下载。

token 无效（401）或无权限/配额尽（403）时**自动回落匿名请求一次**，并在
``DownloadPlan.notes`` 里留一条提示给更新页展示；绝不因为 token 坏了就卡死流程。
"""

from __future__ import annotations

import logging
import time

import httpx

from mangaproof.update import detector
from mangaproof.update.checksum import normalize_sha256
from mangaproof.update.errors import (
    DownloadError,
    GitHubTokenError,
    NetworkError,
    UpdateError,
)
from mangaproof.update.models import DownloadPlan, ReleaseInfo

log = logging.getLogger("mangaproof.update.sources")

GITHUB_API = "https://api.github.com"
GITHUB_OWNER = "gunfub"
GITHUB_REPO = "MangaProof"

#: 轻量请求（HEAD / manifest / release API）的超时
_PROBE_TIMEOUT = httpx.Timeout(connect=10.0, read=20.0, write=10.0, pool=10.0)


def _probe_client(proxy: str, *, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        timeout=_PROBE_TIMEOUT,
        follow_redirects=True,
        trust_env=False,
        proxy=(proxy or None),
        headers={"User-Agent": "MangaProof", "Accept": "*/*"},
        transport=transport,
    )


def release_tag(version: str) -> str:
    """版本 → GitHub Release 标签（仓库约定：标签 = ``v`` + ``__version__``）。"""
    return f"v{version}"


# --- 渠道：Cloudflare R2 -----------------------------------------------------

def r2_plan(
    *,
    version: str,
    target: detector.Target,
    branch: str,
    proxy: str = "",
    transport: httpx.BaseTransport | None = None,
    with_sha256: bool = True,
) -> DownloadPlan:
    """R2 渠道的下载计划（需求 §23/§24/§26）。

    注意 ``branch`` 是**更新分支**（alpha/beta/stable，决定 R2 目录名），
    不是"下载渠道"（r2/github/mirrorchyan）。两者是不同维度，别混。

    ``with_sha256=False`` 时跳过 manifest 请求（用于只想拿文件名/大小的场景）。
    """
    filename = detector.artifact_name(version, target)
    url = detector.r2_url(_r2_base(), branch, filename)
    sha256 = None
    notes: list[str] = []
    if with_sha256:
        sha256 = _r2_manifest_sha256(
            version=version, branch=branch, filename=filename,
            proxy=proxy, transport=transport,
        )
        if sha256 is None:
            notes.append("R2 manifest 中没有该文件的 SHA-256，将跳过校验")
    return DownloadPlan(
        filename=filename, url=url, sha256=sha256, filesize=None,
        use_proxy=True, use_speed_limit=True, source="r2", notes=notes,
    )


def _r2_base() -> str:
    from mangaproof.config.settings import R2_PUBLIC_BASE

    return R2_PUBLIC_BASE


def _r2_manifest_sha256(
    *,
    version: str,
    branch: str,
    filename: str,
    proxy: str,
    transport: httpx.BaseTransport | None,
) -> str | None:
    """读取 R2 上的 manifest 并取出目标文件的 SHA-256。

    manifest 文件名按版本动态生成（需求 §24：不得硬编码）。
    请求带 cache-bust 查询串：Cloudflare 对对象默认 ``max-age=14400``，
    同版本重发时可能读到 4 小时内的旧副本（见调研报告 §3.2）。
    """
    name = detector.sha256_manifest_name(version)
    url = detector.r2_url(_r2_base(), branch, name)
    bust = f"{url}?t={int(time.time())}"
    try:
        with _probe_client(proxy, transport=transport) as client:
            response = client.get(bust)
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("读取 R2 manifest 失败（%s）：%s", name, exc)
        return None

    if not isinstance(payload, dict):
        log.warning("R2 manifest 结构异常：%s", name)
        return None
    raw = payload.get(filename)
    if not raw:
        log.warning("R2 manifest 中没有 %s", filename)
        return None
    try:
        return normalize_sha256(str(raw))
    except ValueError as exc:
        log.warning("R2 manifest 中的 SHA-256 非法（%s）：%s", filename, exc)
        return None


def probe_size(
    url: str, *, proxy: str = "", transport: httpx.BaseTransport | None = None
) -> int | None:
    """用 HEAD 取目标大小（需求 §33 的"大小"在 R2/GitHub 渠道要提前显示）。

    取不到就返回 ``None`` —— UI 显示"—"，**不得因此阻断更新流程**。
    """
    try:
        with _probe_client(proxy, transport=transport) as client:
            response = client.head(url)
            response.raise_for_status()
            raw = response.headers.get("Content-Length")
            return int(raw) if raw and raw.isdigit() and int(raw) > 0 else None
    except (httpx.HTTPError, ValueError) as exc:
        log.info("HEAD 取大小失败（可忽略）：%s", exc)
        return None


#: 代理测试打的目标：拿到**任意** HTTP 响应即证明代理链路通（404 也算通）
PROXY_TEST_URL = "https://github.com/"


def probe_proxy(
    proxy: str, *, timeout: float = 10.0, transport: httpx.BaseTransport | None = None
) -> tuple[bool, str]:
    """测试代理是否可用（需求 §29：必须明确显示成功/失败及错误原因）。

    判定标准是"能通过该代理拿到 HTTP 响应"——**不要求 200**：
    打的是公网站点，403/404 同样说明代理链路是通的。

    :returns: ``(是否成功, 说明文本)``
    """
    if not proxy.strip():
        return False, "请先填写代理地址（例如 http://127.0.0.1:7890 或 socks5://127.0.0.1:1080）"
    try:
        with httpx.Client(
            timeout=httpx.Timeout(
                connect=timeout, read=timeout, write=timeout, pool=timeout
            ),
            follow_redirects=True,
            trust_env=False,
            proxy=proxy.strip(),
            headers={"User-Agent": "MangaProof"},
            transport=transport,
        ) as client:
            response = client.head(PROXY_TEST_URL)
        return True, f"代理可用（{PROXY_TEST_URL} → HTTP {response.status_code}）"
    except httpx.HTTPError as exc:
        return False, f"代理不可用：{type(exc).__name__}: {exc}"
    except Exception as exc:  # pragma: no cover - 代理串非法等
        return False, f"代理不可用：{exc}"


# --- 渠道：GitHub Release ----------------------------------------------------

#: 匿名调用 api.github.com 的配额是 **60 次/小时/IP**（官方限速文档），用尽即 403；
#: 带上用户的 Token 后是 5,000 次/小时。所以这条提示要直接指向更新页的输入框。
ANON_RATE_LIMIT_HINT = (
    "匿名调用 GitHub API 被限流（HTTP 403）：本机 IP 的匿名配额"
    "（60 次/小时）已用尽。可在更新页填写 GitHub Token 后重试，"
    "或把更新渠道改为 Cloudflare R2。"
)

#: Token 被拒后自动回落匿名时写进 ``DownloadPlan.notes`` 的提示（更新页会展示）。
TOKEN_FALLBACK_NOTE = (
    "{reason}，已自动改用匿名请求；若仍失败，请更新 Token 或改用 Cloudflare R2 渠道。"
)


def github_plan(
    *,
    version: str,
    target: detector.Target,
    proxy: str = "",
    transport: httpx.BaseTransport | None = None,
    with_sha256: bool = True,
    token: str = "",
) -> DownloadPlan:
    """GitHub 渠道的下载计划（需求 §22）。

    优先走 Release API：不仅能拿到资产，还能直接读到 ``digest``（SHA-256），
    不必本地重算。匿名调用可能被限流（实测 403），此时给出明确提示。

    ``token``（2026-10-20）：只加在 api.github.com 的请求上，**绝不**加到字节
    下载上（下载 URL 是签名短链，不需要凭证）。Token 被拒时自动回落匿名请求
    一次，并把原因记进 ``notes`` —— 更新页会把它显示在提示行里。
    """
    filename = detector.artifact_name(version, target)
    tag = release_tag(version)
    notes: list[str] = []

    if with_sha256:
        asset = _github_asset_with_fallback(
            tag, filename, token=token, proxy=proxy, transport=transport, notes=notes
        )
        url = str(asset.get("browser_download_url") or "")
        sha256 = _digest_to_sha256(asset.get("digest"), filename, notes)
        if not url:
            raise DownloadError(f"GitHub 资产 {filename} 缺少下载地址")
        return DownloadPlan(
            filename=filename, url=url, sha256=sha256,
            filesize=(int(asset["size"]) if str(asset.get("size", "")).isdigit() else None),
            use_proxy=True, use_speed_limit=True, source="github", notes=notes,
        )

    url = (
        f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/download/"
        f"{tag}/{filename}"
    )
    return DownloadPlan(
        filename=filename, url=url, use_proxy=True, use_speed_limit=True,
        source="github", notes=notes,
    )


def _github_asset_with_fallback(
    tag: str,
    filename: str,
    *,
    token: str,
    proxy: str,
    transport: httpx.BaseTransport | None,
    notes: list[str],
) -> dict:
    """按文件名查资产；Token 被拒时**回落匿名请求一次**。

    回退只发生在"带 token 的请求被拒"（401/403）这一种情况：
    404（资产不存在）、5xx、网络错误都照旧直接抛，绝不拿"换个身份再试"
    去掩盖真正的故障。回退后仍失败时，把两条原因合并报出去 —— 用户才能
    判断是"token 坏了"还是"IP 也被限流了"。
    """
    token = (token or "").strip()
    fallback_reason = ""
    if token:
        try:
            return _github_asset(
                tag, filename, proxy=proxy, transport=transport, token=token
            )
        except GitHubTokenError as exc:
            fallback_reason = exc.reason()
            # 日志只说"被拒 + 状态码"，绝不打 token 本身（需求 §16）
            log.warning("%s：回退为匿名请求", fallback_reason)
            notes.append(TOKEN_FALLBACK_NOTE.format(reason=fallback_reason))

    try:
        return _github_asset(tag, filename, proxy=proxy, transport=transport, token="")
    except NetworkError as exc:
        if not fallback_reason:
            raise
        raise NetworkError(
            "查询 GitHub Release",
            f"{fallback_reason}；改用匿名请求后仍失败：{exc.detail}",
        ) from exc


def _github_asset(
    tag: str,
    filename: str,
    *,
    proxy: str,
    transport: httpx.BaseTransport | None,
    token: str = "",
) -> dict:
    """按文件名在 Release 资产里查一个 asset（返回原始 dict）。

    ``token`` 非空时以 ``Authorization: Bearer`` 发送 —— 只发往 api.github.com。
    """
    api = f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/tags/{tag}"
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with _probe_client(proxy, transport=transport) as client:
            response = client.get(api, headers=headers)
            _raise_for_github_status(response, token=bool(token))
            payload = response.json()
    except httpx.HTTPError as exc:
        # 注意：这里不能把 request 对象塞进日志 —— httpx 的 repr 会带请求头，
        # 而请求头里可能有 Authorization。
        raise NetworkError("查询 GitHub Release", f"{type(exc).__name__}: {exc}") from exc
    except ValueError as exc:
        raise NetworkError("查询 GitHub Release", f"响应不是合法 JSON：{exc}") from exc

    for asset in payload.get("assets", []) or []:
        if str(asset.get("name")) == filename:
            return asset
    # 查不到资产与"token 好不好"无关（token 被拒在上面就分流了），照旧直接报错。
    raise DownloadError(
        f"GitHub Release {tag} 中找不到资产 {filename}\n"
        "可能是该版本尚未发布完成，或当前网络无法访问 GitHub API。"
    )


def _raise_for_github_status(response: httpx.Response, *, token: bool) -> None:
    """按状态码分流：带 token 被拒 → GitHubTokenError；匿名 403 → 限流提示。

    401 只会在"带了 token"时出现（匿名请求没有可失效的凭证）；
    403 既可能是 token 无权/配额尽，也可能是匿名 IP 配额尽 —— 靠 ``token`` 区分。
    """
    status = response.status_code
    if token and status in (401, 403):
        raise GitHubTokenError(status, _response_message(response))
    if status == 403:
        raise NetworkError("查询 GitHub Release", ANON_RATE_LIMIT_HINT)
    response.raise_for_status()


def _response_message(response: httpx.Response) -> str:
    """取服务端 ``message`` 字段（只用于日志/排查，展示层不用它）。

    响应体不可解析、或超大时一律返回空串；文本仍过一次脱敏，
    防止某些代理把请求头回显进错误体。
    """
    try:
        body = response.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    message = str(body.get("message") or "")
    return message[:200]


def _digest_to_sha256(digest: object, filename: str, notes: list[str]) -> str | None:
    """GitHub asset 的 ``digest`` → 裸 sha256；缺失则记一条提示（需求 §27 的客户端侧）。"""
    if not digest:
        notes.append("GitHub 未提供 digest，将跳过校验")
        log.warning("GitHub 资产 %s 没有 digest", filename)
        return None
    try:
        return normalize_sha256(str(digest))
    except ValueError:
        notes.append("GitHub 提供的 digest 格式异常，将跳过校验")
        log.warning("GitHub 资产 %s 的 digest 非法：%r", filename, digest)
        return None


# --- 渠道：MirrorChyan -------------------------------------------------------

def mirrorchyan_plan(
    *,
    release: ReleaseInfo,
    target: detector.Target,
) -> DownloadPlan:
    """把"带 CDK 的响应"变成下载计划（需求 §19/§21）。

    ``url`` 必须是服务端下发的短效地址：客户端**不得**自行拼接最终下载 URL。
    该渠道不使用代理与限速（需求 §29/§30）。
    """
    if not release.url:
        raise DownloadError("MirrorChyan 未返回下载地址（url 为空）")
    sha256 = None
    notes: list[str] = []
    if release.sha256:
        try:
            sha256 = normalize_sha256(release.sha256)
        except ValueError:
            notes.append("MirrorChyan 返回的 sha256 格式异常，将跳过校验")
    else:
        notes.append("MirrorChyan 未返回 sha256，将跳过校验")
    if release.update_type and release.update_type != "full":
        # 需求 §1：第一版只支持完整包；服务端若给出增量包应明确拒绝
        raise DownloadError(
            f"MirrorChyan 返回的是 {release.update_type} 更新包，"
            "当前版本只支持完整包更新"
        )
    return DownloadPlan(
        filename=_mirrorchyan_filename(release, target),
        url=release.url,
        sha256=sha256,
        filesize=release.filesize,
        use_proxy=False,        # 需求 §29
        use_speed_limit=False,  # 需求 §30
        source="mirrorchyan",
        notes=notes,
    )


def _mirrorchyan_filename(release: ReleaseInfo, target: detector.Target) -> str:
    """给 MirrorChyan 渠道起一个**本地**文件名（服务端 url 里没有文件名）。

    只用于落盘命名；下载地址仍用服务端下发的 ``url``。
    """
    return detector.artifact_name(str(release.version), target)


def build_plan(
    *,
    source: str,
    branch: str,
    version: str,
    target: detector.Target,
    release: ReleaseInfo | None = None,
    proxy: str = "",
    transport: httpx.BaseTransport | None = None,
    token: str = "",
) -> DownloadPlan:
    """构造下载计划（三渠道统一入口）。

    :param source: **下载渠道** —— ``r2`` / ``github`` / ``mirrorchyan``（设置项）
    :param branch: **更新分支** —— ``alpha`` / ``beta`` / ``stable``（决定 R2 目录）
    :param token: GitHub Token（只对 ``github`` 渠道有效，其余渠道忽略）
    """
    if source == "r2":
        return r2_plan(
            version=version, target=target, branch=branch,
            proxy=proxy, transport=transport,
        )
    if source == "github":
        return github_plan(
            version=version, target=target, proxy=proxy,
            transport=transport, token=token,
        )
    if source == "mirrorchyan":
        if release is None or not release.url:
            raise DownloadError(
                "MirrorChyan 渠道需要先用 CDK 获取下载信息（点击「立即更新」后自动进行）"
            )
        return mirrorchyan_plan(release=release, target=target)
    raise UpdateError(f"未知的下载渠道：{source}")


__all__ = [
    "ANON_RATE_LIMIT_HINT",
    "TOKEN_FALLBACK_NOTE",
    "build_plan",
    "github_plan",
    "mirrorchyan_plan",
    "r2_plan",
    "probe_size",
    "probe_proxy",
    "PROXY_TEST_URL",
    "release_tag",
    "GITHUB_OWNER",
    "GITHUB_REPO",
]

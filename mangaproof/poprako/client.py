# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""Poprako HTTP 客户端（需求 §5、§9；设计文档 §3、§6）。

**本模块不导入 Qt**：可以在 ``httpx.MockTransport`` 上离线单测，
也能被任何非 UI 路径复用。

约定（全部来自已核实的 swagger / 服务端文档）：

- 鉴权：``Authorization: Bearer <token>``；登录接口自身不带该头；
- 信封：成功 ``{code: 0, data: …}``；失败 ``{code: n}``（**没有 message 字段**），
  所以异常只带状态码与业务 code，中文文案由 :func:`mangaproof.poprako.errors.user_message`
  在展示层拼；
- 分页：``offset`` + ``limit``，本客户端固定 ``limit=100``；
- 重试：只对传输层错误（连不上 / 读超时）重试 1 次。导入是"整体替换"语义，
  重试安全；4xx 一律不重试。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional
from urllib.parse import quote

import httpx

from mangaproof import __version__
from mangaproof.poprako import API_BASE_URL
from mangaproof.poprako.errors import (
    PoprakoAuthError,
    PoprakoError,
    PoprakoForbiddenError,
    PoprakoHTTPError,
    PoprakoNetworkError,
    PoprakoValidationError,
)
from mangaproof.poprako.models import (
    AuthSession,
    Chapter,
    Comic,
    ImportResult,
    Membership,
    PageArtwork,
    RemoteIssue,
    User,
    Workset,
)

log = logging.getLogger("mangaproof.poprako.client")

#: 单页条数。swagger 的 ``ListLimit200`` 暗示上限 200，这里取一半留余量。
PAGE_LIMIT = 100

#: 分页安全上限：超过就说明服务端行为异常，直接失败而不是死循环。
MAX_PAGES = 20

#: 传输层错误的重试间隔（秒）
RETRY_DELAY = 0.8

#: 默认超时：(连接, 读)
DEFAULT_TIMEOUT = (10.0, 30.0)

_UNSET = object()

#: 代理允许的协议（httpx 只认这三个；socks5 由 httpx[socks] 提供）
PROXY_SCHEMES = ("http", "https", "socks5")

#: 代理测试目标：**谷歌**。任何 HTTP 响应都算链路通（被墙/被劫持会超时或异常），
#: 因此"能拿到状态码"就说明代理确实在转发请求（用户决策 2026-10-20）。
PROXY_TEST_URL = "https://www.google.com/generate_204"

#: 代理地址长度上限（防止把整段 JSON 粘进来）
_PROXY_MAX_LEN = 300


def validate_proxy(text: str) -> tuple[str, str]:
    """校验代理地址，返回 ``(规范化后的值, 错误说明)``。

    空值合法（= 直连，与更新模块同一口径，2026-10-20 决策）。
    """
    value = (text or "").strip()
    if not value:
        return "", ""
    if len(value) > _PROXY_MAX_LEN:
        return "", "代理地址过长"
    if any(ch.isspace() for ch in value):
        return "", "代理地址里不能有空格"
    if "://" not in value:
        return "", "代理地址需要带协议，例如 http://127.0.0.1:7890"
    try:
        url = httpx.URL(value)
    except (httpx.InvalidURL, ValueError):
        return "", "代理地址格式不对，例如 http://127.0.0.1:7890"
    if url.scheme not in PROXY_SCHEMES:
        return "", "只支持 http / https / socks5 代理"
    if not url.host:
        return "", "代理地址缺少主机名"
    return value, ""


def mask_proxy(text: str) -> str:
    """日志用的代理遮罩：``http://user:pass@host:port`` → ``http://***@host:port``。"""
    value = (text or "").strip()
    if not value:
        return ""
    try:
        url = httpx.URL(value)
    except (httpx.InvalidURL, ValueError):
        return "[REDACTED]" if "@" in value else value
    if url.userinfo:
        port = f":{url.port}" if url.port else ""
        return f"{url.scheme}://***@{url.host}{port}"
    return value


def _build_http_client(
    timeout: tuple[float, float],
    headers: dict[str, str],
    transport: Optional[httpx.BaseTransport],
    proxy: str,
) -> httpx.Client:
    """构造 httpx 客户端。

    **一律 ``trust_env=False``**（用户决策 2026-10-20：与更新模块一致）：

    - ``proxy`` 为空 → 直连；
    - 非空 → 只走这个代理；
    - 顺带避开 ``NO_PROXY`` 里裸 IPv6 让 httpx 在构造阶段抛 ``InvalidURL`` 的坑
      （那曾导致"菜单点了没反应"）。
    """
    return httpx.Client(
        timeout=httpx.Timeout(timeout[1], connect=timeout[0]),
        headers=headers,
        transport=transport,
        trust_env=False,
        proxy=(proxy or None),
    )


def probe_connection(
    proxy: str,
    *,
    timeout: float = 10.0,
    transport: Optional[httpx.BaseTransport] = None,
) -> tuple[bool, str]:
    """用候选代理访问谷歌，**不读写任何设置**（需求：测试只做临时连接）。

    :returns: ``(是否连通, 说明文本)``
    """
    value, error = validate_proxy(proxy)
    if error:
        return False, error
    if not value:
        return False, "留空表示直连，不需要测试代理"

    try:
        with httpx.Client(
            timeout=httpx.Timeout(connect=timeout, read=timeout, write=timeout, pool=timeout),
            follow_redirects=False,
            trust_env=False,
            proxy=value,
            headers={"User-Agent": f"MangaProof/{__version__}"},
            transport=transport,
        ) as client:
            response = client.get(PROXY_TEST_URL)
        return True, f"代理可用（{PROXY_TEST_URL} → HTTP {response.status_code}）"
    except httpx.HTTPError as exc:
        return False, f"代理不可用：{type(exc).__name__}: {exc}"
    except Exception as exc:  # pragma: no cover —— 代理串非法等
        return False, f"代理不可用：{exc}"


class PoprakoClient:
    """Poprako API 的薄封装。一个实例 = 一个令牌 + 一个 base_url。"""

    def __init__(
        self,
        base_url: str = API_BASE_URL,
        token: str = "",
        *,
        proxy: str = "",
        timeout: tuple[float, float] = DEFAULT_TIMEOUT,
        max_retries: int = 1,
        retry_delay: float = RETRY_DELAY,
        transport: Optional[httpx.BaseTransport] = None,
        user_agent: Optional[str] = None,
    ):
        normalized = (base_url or "").strip() or API_BASE_URL
        self._base_url = normalized.rstrip("/")
        self._token = (token or "").strip()
        proxy_value, proxy_error = validate_proxy(proxy)
        if proxy_error:
            log.warning("忽略非法的代理地址（%s）：%s", proxy_error, mask_proxy(proxy))
        self._proxy = proxy_value
        self._max_retries = max(0, int(max_retries))
        self._retry_delay = max(0.0, float(retry_delay))
        headers = {
            "Accept": "application/json",
            "User-Agent": user_agent or f"MangaProof/{__version__}",
        }
        # 留空 = 直连；非空 = 只走该代理（与更新模块同一口径，用户决策 2026-10-20）
        self._http = _build_http_client(timeout, headers, transport, self._proxy)

    # -- 生命周期 ----------------------------------------------------------

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "PoprakoClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- 状态 --------------------------------------------------------------

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def proxy(self) -> str:
        return self._proxy

    @property
    def token(self) -> str:
        return self._token

    @token.setter
    def token(self, value: str) -> None:
        self._token = (value or "").strip()

    @property
    def has_token(self) -> bool:
        return bool(self._token)

    # -- 鉴权 --------------------------------------------------------------

    def login(self, qid: str, password: str) -> AuthSession:
        """``POST /auth/login``。成功后把令牌记在实例上，后续请求自动带。"""
        data = self._request(
            "POST",
            "auth/login",
            json_body={"qid": qid, "password": password},
            auth=False,
        )
        session = AuthSession.from_api(data)
        if not session.token:
            raise PoprakoError("登录响应缺少令牌")
        self._token = session.token
        log.info("Poprako 登录成功（user_id=%s）", session.user_id)
        return session

    def me(self) -> User:
        """``GET /users/me``：校验令牌并取昵称/qid。"""
        return User.from_api(self._request("GET", "users/me"))

    def logout(self) -> None:
        """``POST /auth/logout``：服务端只清 Cookie；Bearer 客户端丢弃本地令牌即可。

        这里仍发一次（失败由调用方吞掉），只为保持与网页端一致的会话语义。
        """
        self._request("POST", "auth/logout", json_body={}, auth=False)

    # -- 层级：团队 → 项目集 → 作品 → 章节 -----------------------------------

    def my_teams(self) -> list[Membership]:
        """``GET /members/me?incl=team``：当前用户所属团队（带团队名与角色）。"""
        rows = self._paged("members/me", {"incl": ["team"]})
        return [Membership.from_api(row) for row in rows]

    def worksets(self, team_id: str) -> list[Workset]:
        rows = self._paged(f"teams/{quote(team_id, safe='')}/worksets", {})
        return [Workset.from_api(row) for row in rows]

    def comics(self, workset_id: str) -> list[Comic]:
        """只列未归档作品（需求 §6.3）。

        注意这个端点的 data 是**对象**而不是数组：
        ``{comics, pinned_chapters, pinned_chapter_assignments}``
        （swagger 的 ``ListComicInfosVal``，poprako-web 也按 ``data.comics`` 解码）。
        """
        rows = self._paged(
            f"worksets/{quote(workset_id, safe='')}/comics",
            {"status": "active"},
            key="comics",
        )
        return [Comic.from_api(row) for row in rows if not row.get("is_archived")]

    def chapters(self, comic_id: str) -> list[Chapter]:
        rows = self._paged(f"comics/{quote(comic_id, safe='')}/chapters", {})
        return [Chapter.from_api(row) for row in rows]

    def chapter(self, chapter_id: str) -> Chapter:
        return Chapter.from_api(self._request("GET", f"chapters/{quote(chapter_id, safe='')}"))

    # -- 页面与问题 --------------------------------------------------------

    def page_artworks(self, chapter_id: str) -> list[PageArtwork]:
        data = self._request("GET", f"chapters/{quote(chapter_id, safe='')}/page-artworks")
        rows = data if isinstance(data, list) else []
        return [PageArtwork.from_api(row) for row in rows if isinstance(row, dict)]

    def issues(self, chapter_id: str) -> list[RemoteIssue]:
        """读远端现有问题。

        **只用于**「统计将被清空的规模」与「保留未映射页」两件事，
        不写入任务、不参与本地统计（需求 §9.2 的单向上传边界）。
        """
        data = self._request("GET", f"chapters/{quote(chapter_id, safe='')}/issues")
        rows = data if isinstance(data, list) else []
        return [RemoteIssue.from_api(row) for row in rows if isinstance(row, dict)]

    def import_issues(self, chapter_id: str, payload: dict[str, Any]) -> ImportResult:
        """``POST /chapters/{id}/issues/import``：整体替换该章问题。"""
        data = self._request(
            "POST",
            f"chapters/{quote(chapter_id, safe='')}/issues/import",
            json_body=payload,
        )
        return ImportResult.from_api(data)

    # -- 内部：请求 / 分页 / 解析 -------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self._base_url}/{path.lstrip('/')}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = _UNSET,
        params: Optional[dict[str, Any]] = None,
        auth: bool = True,
    ) -> Any:
        url = self._url(path)
        headers: dict[str, str] = {}
        content: Optional[bytes] = None
        if json_body is not _UNSET:
            content = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if auth and self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        attempt = 0
        started = time.monotonic()
        while True:
            try:
                response = self._http.request(
                    method, url, content=content, params=params, headers=headers
                )
                break
            except httpx.TransportError as exc:
                if attempt < self._max_retries:
                    attempt += 1
                    log.info(
                        "Poprako %s %s 传输失败（%s），重试 %d/%d",
                        method, path, type(exc).__name__, attempt, self._max_retries,
                    )
                    if self._retry_delay:
                        time.sleep(self._retry_delay)
                    continue
                log.warning(
                    "Poprako %s %s 传输失败（%s），已重试 %d 次",
                    method, path, type(exc).__name__, self._max_retries,
                )
                raise PoprakoNetworkError() from exc

        elapsed_ms = int((time.monotonic() - started) * 1000)
        log.info("Poprako %s %s → HTTP %d (%d ms)", method, path, response.status_code, elapsed_ms)
        return self._decode(response, method, path)

    def _decode(self, response: httpx.Response, method: str, path: str) -> Any:
        if response.status_code == 204:
            return None

        try:
            payload = response.json()
        except ValueError:
            payload = None

        code: Optional[int] = None
        if isinstance(payload, dict) and isinstance(payload.get("code"), int):
            code = payload["code"]

        if response.status_code >= 400 or (code is not None and code != 0):
            self._raise_http(response.status_code, code, method, path)

        if isinstance(payload, dict):
            return payload.get("data")
        return payload

    @staticmethod
    def _raise_http(status: int, code: Optional[int], method: str, path: str) -> None:
        # 日志只记方法/路径/状态/业务码：令牌与请求体绝不出现（需求 §12）
        log.warning("Poprako %s %s → HTTP %d（code %s）", method, path, status, code)
        if status == 401:
            raise PoprakoAuthError(status, code)
        if status == 403:
            raise PoprakoForbiddenError(status, code)
        if status == 422:
            raise PoprakoValidationError(status, code)
        message = f"HTTP {status}" + (f"（code {code}）" if code is not None else "")
        raise PoprakoHTTPError(message, status=status, code=code)

    def _paged(
        self, path: str, params: dict[str, Any], *, key: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """拉取全部分页。

        ``key`` 用于"列表被包在对象里"的端点（``/worksets/{id}/comics`` 返回
        ``{comics, pinned_chapters, pinned_chapter_assignments}``）；其余端点仍是
        裸数组。**形态不符时抛错**，而不是静默返回空列表——空列表和"解析失败"
        在界面上必须能区分开（否则就是"作品下拉一片空白、也不知道为什么"）。
        """
        items: list[dict[str, Any]] = []
        offset = 0
        for _ in range(MAX_PAGES):
            query = dict(params)
            query["offset"] = offset
            query["limit"] = PAGE_LIMIT
            data = self._request("GET", path, params=query)
            chunk = self._list_of(data, key, path)
            items.extend(chunk)
            if len(chunk) < PAGE_LIMIT:
                return items
            offset += PAGE_LIMIT
        raise PoprakoError(f"分页超过 {MAX_PAGES} 页上限，已中止")

    @staticmethod
    def _list_of(data: Any, key: Optional[str], path: str) -> list[dict[str, Any]]:
        """从响应 data 里取出列表；形态不符抛 :class:`PoprakoError`。"""
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        if key and isinstance(data, dict) and isinstance(data.get(key), list):
            return [row for row in data[key] if isinstance(row, dict)]
        if data is None:
            # {code:0, data:null} 当作空列表（服务端偶发，不明显是错误）
            return []

        detail = f"data.{key}" if key else "data"
        top = sorted(data)[:8] if isinstance(data, dict) else type(data).__name__
        log.warning("Poprako %s 返回结构不符（期望 %s 为列表）；顶层：%s", path, detail, top)
        raise PoprakoError(
            f"接口返回结构不符：{path} 的 {detail} 不是列表（顶层键：{top}）"
        )

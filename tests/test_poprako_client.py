# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""Poprako HTTP 客户端单测（需求 §5、§9；设计文档 §13）。

全部离线：用 ``httpx.MockTransport`` 喂响应，不发任何真实请求。覆盖：

- 登录请求的 snake_case 与"不带 Authorization"；
- 后续请求的 Bearer 头与查询参数编码（``incl`` 重复键）；
- 信封解包、4xx/业务码映射、异常类型与中文文案；
- 分页（``limit=100`` + ``offset`` 递增）；
- 传输层错误只重试 1 次；
- **异常文本永不含令牌**（需求 §12.4）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable

import httpx
import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from mangaproof.poprako.client import PAGE_LIMIT, PoprakoClient  # noqa: E402
from mangaproof.poprako.errors import (  # noqa: E402
    PoprakoAuthError,
    PoprakoForbiddenError,
    PoprakoHTTPError,
    PoprakoNetworkError,
    PoprakoValidationError,
    user_message,
)
from mangaproof.poprako.models import (  # noqa: E402
    Chapter,
    PageArtwork,
    is_published,
    roles_text,
    stage_phases,
)

BASE = "https://api.example.test/api/v1"

Handler = Callable[[httpx.Request], httpx.Response]


def make_client(handler: Handler, **kwargs) -> PoprakoClient:
    kwargs.setdefault("retry_delay", 0.0)
    return PoprakoClient(BASE, transport=httpx.MockTransport(handler), **kwargs)


def envelope(data) -> httpx.Response:
    return httpx.Response(200, json={"code": 0, "data": data})


# ---------------------------------------------------------------- 登录 / 鉴权


def test_login_sends_snake_case_without_authorization():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["raw_path"] = request.url.raw_path.split(b"?")[0].decode("ascii")
        seen["body"] = json.loads(request.content.decode("utf-8"))
        seen["authorization"] = request.headers.get("Authorization")
        return envelope({"token": "tok-1", "user_id": "u_1"})

    client = make_client(handler, token="stale-token")
    session = client.login("123456", "p@ss")

    assert seen["method"] == "POST"
    assert seen["raw_path"] == "/api/v1/auth/login"
    assert seen["body"] == {"qid": "123456", "password": "p@ss"}
    assert seen["authorization"] is None, "登录请求不能带旧令牌"
    assert session.token == "tok-1" and session.user_id == "u_1"
    assert client.token == "tok-1"


def test_authenticated_request_carries_bearer_header():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("Authorization")
        return envelope({
            "id": "u_1", "qid": "123456", "nickname": "秋叶声生", "is_sadmin": False,
        })

    client = make_client(handler, token="tok-1")
    user = client.me()

    assert seen["authorization"] == "Bearer tok-1"
    assert user.nickname == "秋叶声生"
    assert user.qid == "123456"


def test_team_list_encodes_repeated_incl_and_pagination_params():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["incl"] = request.url.params.get_list("incl")
        seen["offset"] = request.url.params.get("offset")
        seen["limit"] = request.url.params.get("limit")
        return envelope([{
            "team_id": "t_1", "roles": 32, "team": {"id": "t_1", "name": "木叶翻译组"},
        }])

    client = make_client(handler, token="tok-1")
    teams = client.my_teams()

    assert seen == {"incl": ["team"], "offset": "0", "limit": str(PAGE_LIMIT)}
    assert teams[0].team_name == "木叶翻译组"
    assert teams[0].is_reviewer is True


# ---------------------------------------------------------------- 错误映射


@pytest.mark.parametrize(
    "status, expected",
    [
        (401, PoprakoAuthError),
        (403, PoprakoForbiddenError),
        (422, PoprakoValidationError),
        (500, PoprakoHTTPError),
        (404, PoprakoHTTPError),
    ],
)
def test_http_status_maps_to_typed_errors(status: int, expected: type):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"code": 1001})

    client = make_client(handler, token="tok-1")
    with pytest.raises(expected) as excinfo:
        client.chapter("ch_1")
    assert excinfo.value.status == status
    assert excinfo.value.code == 1001


def test_success_status_with_nonzero_code_is_an_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 7})

    client = make_client(handler, token="tok-1")
    with pytest.raises(PoprakoHTTPError) as excinfo:
        client.chapter("ch_1")
    assert excinfo.value.code == 7


def test_error_text_never_contains_token():
    token = "SECRET-TOKEN-VALUE"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"code": 9})

    client = make_client(handler, token=token)
    with pytest.raises(PoprakoHTTPError) as excinfo:
        client.import_issues("ch_1", {"pages": []})

    rendered = f"{excinfo.value!s} {excinfo.value!r} {user_message(excinfo.value)}"
    assert token not in rendered


@pytest.mark.parametrize(
    "error, fragment",
    [
        (PoprakoNetworkError(), "无法连接"),
        (PoprakoAuthError(401, None), "重新登录"),
        (PoprakoForbiddenError(403, None), "监制"),
        (PoprakoValidationError(422, 1001), "HTTP 422"),
        (PoprakoHTTPError("HTTP 404", status=404, code=None), "已不存在"),
        (PoprakoHTTPError("HTTP 429", status=429, code=None), "过于频繁"),
        (PoprakoHTTPError("HTTP 503", status=503, code=None), "重试是安全的"),
    ],
)
def test_user_message_covers_failure_matrix(error: Exception, fragment: str):
    assert fragment in user_message(error)


# ---------------------------------------------------------------- 分页


def test_pagination_walks_until_short_page():
    offsets: list[int] = []
    full_page = [{"id": f"ch_{i}"} for i in range(PAGE_LIMIT)]
    tail = [{"id": "tail-1"}, {"id": "tail-2"}]

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params.get("offset", "0"))
        offsets.append(offset)
        return envelope(full_page if offset == 0 else tail)

    client = make_client(handler, token="tok-1")
    chapters = client.chapters("c_1")

    assert offsets == [0, PAGE_LIMIT]
    assert len(chapters) == PAGE_LIMIT + 2
    assert chapters[-1].id == "tail-2"


# ---------------------------------------------------------------- 层级与导入


def test_comics_filters_archived_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("status") == "active"
        return envelope([
            {"id": "c_1", "title": "某漫画", "is_archived": False},
            {"id": "c_2", "title": "旧坑", "is_archived": True},
        ])

    client = make_client(handler, token="tok-1")
    comics = client.comics("w_1")
    assert [c.id for c in comics] == ["c_1"]


def test_import_issues_posts_payload_and_returns_counts():
    seen: dict = {}
    payload = {"pages": [{"page_artwork_id": "pa_1", "issues": []}]}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["raw_path"] = request.url.raw_path.split(b"?")[0].decode("ascii")
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return envelope({"imported_page_count": 1, "imported_issue_count": 0})

    client = make_client(handler, token="tok-1")
    result = client.import_issues("ch 1", payload)

    assert seen["raw_path"] == "/api/v1/chapters/ch%201/issues/import", "路径参数必须 URL 编码"
    assert seen["body"] == payload
    assert result.imported_page_count == 1
    assert result.imported_issue_count == 0


def test_page_artworks_and_issues_parse_lists():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/page-artworks"):
            return envelope([{"id": "pa_1", "index": 0, "raw_ident": "001.psd",
                              "image_uploaded": True, "ext": "png"}])
        return envelope([{"id": "i_1", "page_artwork_id": "pa_1", "index": 0,
                          "variant": "文字位置错误", "layer_name": None,
                          "rect": None, "note": "向左"}])

    client = make_client(handler, token="tok-1")
    pages = client.page_artworks("ch_1")
    issues = client.issues("ch_1")

    assert pages[0].raw_ident == "001.psd"
    assert pages[0].display_name == "第 1 页 · 001.psd"
    assert issues[0].rect is None and issues[0].note == "向左"


# ---------------------------------------------------------------- 重试


def test_transport_error_is_retried_once_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom", request=request)
        return envelope({"id": "ch_1", "index": 0, "stages": 0})

    client = make_client(handler, token="tok-1", max_retries=1)
    chapter = client.chapter("ch_1")
    assert calls["n"] == 2
    assert chapter.id == "ch_1"


def test_transport_error_exhausted_raises_network_error():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("slow", request=request)

    client = make_client(handler, token="tok-1", max_retries=1)
    with pytest.raises(PoprakoNetworkError):
        client.chapter("ch_1")
    assert calls["n"] == 2


# ---------------------------------------------------------------- 模型辅助


def test_published_flag_and_stage_phases():
    # 发布阶段（bits 10–11）= 2 → 已发布；校对（bits 4–5）= 1 → 进行中
    stages = (0b10 << 10) | (0b01 << 4)
    assert is_published(stages) is True

    phases = stage_phases(stages)
    assert [name for name, _ in phases] == ["供图", "翻译", "校对", "排版 / 修图", "监制", "发布"]
    assert dict(phases)["校对"] == "active"
    assert dict(phases)["发布"] == "completed"

    assert is_published(0) is False


def test_roles_text_and_tolerant_parsing():
    assert roles_text(32 | 4) == "校对 / 监制"
    assert roles_text(0) == "—"

    page = PageArtwork.from_api({"id": "pa_1"})
    assert page.index == 0 and page.raw_ident is None and page.image_uploaded is False

    chapter = Chapter.from_api({"id": "ch_1", "index": 12, "subtitle": "初次见面"})
    assert chapter.label == "第 13 话 初次见面"
    assert Chapter.from_api({"id": "ch_2", "index": 0}).label == "第 1 话"


def test_domain_layer_does_not_import_qt():
    """域层不得导入 PySide6（设计文档 §2.2）：否则离线单测与复用都会被拖进 Qt。"""
    import subprocess

    code = (
        "import sys;"
        "import mangaproof.poprako.client, mangaproof.poprako.credentials,"
        "       mangaproof.poprako.models, mangaproof.poprako.errors;"
        "assert 'PySide6' not in sys.modules, '域层不得导入 Qt';"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr

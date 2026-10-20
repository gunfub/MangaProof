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

from mangaproof.poprako.client import (  # noqa: E402
    PAGE_LIMIT,
    PROXY_TEST_URL,
    PoprakoClient,
    create_client,
)
from mangaproof.poprako.errors import (  # noqa: E402
    PoprakoAuthError,
    PoprakoError,
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
        (PoprakoForbiddenError(403, None), "监修"),
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


def test_comics_reads_documented_object_shape():
    """回归：``/worksets/{id}/comics`` 的 data 是**对象**，不是数组。

    swagger 的 ``ListComicInfosVal`` = ``{comics, pinned_chapters,
    pinned_chapter_assignments}``；按数组解析会永远得到空列表（曾经的 bug：
    作品与章节都加载不出来）。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("status") == "active"
        return envelope({
            "comics": [
                {"id": "c_1", "title": "某漫画", "is_archived": False},
                {"id": "c_2", "title": "旧坑", "is_archived": True},
            ],
            "pinned_chapters": [None, None],
            "pinned_chapter_assignments": [[], []],
        })

    client = make_client(handler, token="tok-1")
    comics = client.comics("w_1")
    assert [c.id for c in comics] == ["c_1"]


def test_comics_still_accepts_bare_array():
    """服务端若回退成裸数组也能吃（防御）."""

    def handler(request: httpx.Request) -> httpx.Response:
        return envelope([{"id": "c_1", "title": "某漫画"}])

    client = make_client(handler, token="tok-1")
    assert [c.id for c in client.comics("w_1")] == ["c_1"]


def test_paged_object_shape_paginates_over_comics():
    offsets: list[str] = []
    first = [{"id": f"c_{i}"} for i in range(PAGE_LIMIT)]
    second = [{"id": "c_tail"}]

    def handler(request: httpx.Request) -> httpx.Response:
        offset = request.url.params.get("offset", "0")
        offsets.append(offset)
        comics = first if offset == "0" else second
        return envelope({
            "comics": comics,
            "pinned_chapters": [],
            "pinned_chapter_assignments": [],
        })

    client = make_client(handler, token="tok-1")
    comic_ids = [c.id for c in client.comics("w_1")]
    assert offsets == ["0", str(PAGE_LIMIT)]
    assert len(comic_ids) == PAGE_LIMIT + 1
    assert comic_ids[-1] == "c_tail"


def test_unexpected_list_shape_raises_readable_error():
    """结构不符必须报错，而不是静默返回空列表（用户要求）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return envelope({"items": [{"id": "x"}]})

    client = make_client(handler, token="tok-1")
    with pytest.raises(PoprakoError) as excinfo:
        client.worksets("t_1")
    message = str(excinfo.value)
    assert "结构不符" in message
    assert "items" in message


def test_null_data_is_treated_as_empty_list():
    def handler(request: httpx.Request) -> httpx.Response:
        return envelope(None)

    client = make_client(handler, token="tok-1")
    assert client.chapters("c_1") == []


#: 列表端点 → 文档形态。任何一个端点的 data 形态变了，这里会先炸（契约表）。
_LIST_CONTRACTS = [
    ("my_teams", lambda c: c.my_teams(),
     [{"team_id": "t_1", "roles": 32, "team": {"name": "T"}}]),
    ("worksets", lambda c: c.worksets("t_1"),
     [{"id": "w_1", "name": "W"}]),
    ("comics", lambda c: c.comics("w_1"),
     {"comics": [{"id": "c_1", "title": "C"}],
      "pinned_chapters": [], "pinned_chapter_assignments": []}),
    ("chapters", lambda c: c.chapters("c_1"),
     [{"id": "ch_1", "index": 0}]),
    ("page_artworks", lambda c: c.page_artworks("ch_1"),
     [{"id": "pa_1", "index": 0}]),
    ("issues", lambda c: c.issues("ch_1"),
     [{"id": "i_1", "page_artwork_id": "pa_1", "variant": "v", "note": "n"}]),
]


@pytest.mark.parametrize("name, call, data", _LIST_CONTRACTS,
                         ids=[case[0] for case in _LIST_CONTRACTS])
def test_list_endpoints_match_documented_shapes(name, call, data):
    def handler(request: httpx.Request) -> httpx.Response:
        return envelope(data)

    client = make_client(handler, token="tok-1")
    rows = call(client)
    assert len(rows) == 1, f"{name} 的文档形态解析失败"


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


# ---------------------------------------------------------------- 章节分工（2026-10-21）


def test_chapter_assignments_lists_roles():
    """``GET /assignments?chapter_id=…``：拿到每个人的角色位，能认出自己的监修。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return envelope([
            {"id": "as_1", "chapter_id": "ch_1", "user_id": "u_1", "roles": 32},
            {"id": "as_2", "chapter_id": "ch_1", "user_id": "u_2", "roles": 4},
        ])

    client = make_client(handler, token="tok-1")
    rows = client.chapter_assignments("ch_1")

    assert seen["path"].endswith("/assignments")
    assert seen["params"]["chapter_id"] == "ch_1"
    assert [row.user_id for row in rows] == ["u_1", "u_2"]
    assert rows[0].is_reviewer is True
    assert rows[1].is_reviewer is False


def test_join_chapter_posts_snake_case_body():
    """自助加入分工：body 必须是 ``{chapter_id, roles}``（服务端只认 snake_case）。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["method"] = request.method
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"code": 0, "data": {
            "id": "as_9", "chapter_id": "ch_1", "user_id": "u_1", "roles": 32,
        }})

    client = make_client(handler, token="tok-1")
    assignment = client.join_chapter("ch_1", 32)

    assert captured["method"] == "POST"
    assert captured["path"].endswith("/assignments/join")
    assert captured["body"] == {"chapter_id": "ch_1", "roles": 32}
    assert assignment.id == "as_9"
    assert assignment.is_reviewer is True


def test_join_chapter_403_is_forbidden_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"code": 1})

    client = make_client(handler, token="tok-1")
    with pytest.raises(PoprakoForbiddenError):
        client.join_chapter("ch_1", 32)


def test_assignment_from_api_tolerates_missing_fields():
    from mangaproof.poprako.models import Assignment

    empty = Assignment.from_api({})
    assert (empty.id, empty.chapter_id, empty.user_id, empty.roles) == ("", "", "", 0)
    assert empty.is_reviewer is False


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


def test_default_retries_are_two():
    """2026-10-20 调参：传输层失败默认重试 2 次（共 3 次尝试），退避线性。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectTimeout("slow", request=request)

    client = make_client(handler, token="tok-1")      # 用默认 max_retries，retry_delay=0
    with pytest.raises(PoprakoNetworkError):
        client.chapter("ch_1")
    assert calls["n"] == 3


def test_network_error_knows_whether_it_used_a_proxy():
    """异常要能说清走的是代理还是直连（决定提示文案）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    direct = make_client(handler, token="tok-1")
    with pytest.raises(PoprakoNetworkError) as direct_exc:
        direct.chapter("ch_1")
    assert direct_exc.value.via_proxy is False

    proxied = make_client(handler, token="tok-1", proxy="http://127.0.0.1:7897")
    with pytest.raises(PoprakoNetworkError) as proxy_exc:
        proxied.chapter("ch_1")
    assert proxy_exc.value.via_proxy is True
    assert "当前为直连" not in user_message(proxy_exc.value)


def test_create_client_honours_the_two_argument_contract():
    """UI 注入用的工厂契约 ``(base_url, proxy)``：代理必须走关键字参数接上。"""
    client = create_client("https://api.example.test/api/v1", "http://127.0.0.1:7897")
    try:
        assert client.base_url == "https://api.example.test/api/v1"
        assert client.proxy == "http://127.0.0.1:7897"
        assert client.token == "", "第二个位置参数不是 token"
    finally:
        client.close()

    plain = create_client("https://api.example.test/api/v1", "")
    try:
        assert plain.proxy == ""
    finally:
        plain.close()


# ---------------------------------------------------------------- 模型辅助


def test_published_flag_and_stage_phases():
    # 发布阶段（bits 10–11）= 2 → 已发布；校对（bits 4–5）= 1 → 进行中
    stages = (0b10 << 10) | (0b01 << 4)
    assert is_published(stages) is True

    phases = stage_phases(stages)
    assert [name for name, _ in phases] == ["供图", "翻译", "校对", "排版 / 修图", "监修", "发布"]
    assert dict(phases)["校对"] == "active"
    assert dict(phases)["发布"] == "completed"

    assert is_published(0) is False


def test_roles_text_and_tolerant_parsing():
    assert roles_text(32 | 4) == "校对 / 监修"
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


# ---------------------------------------------------------------- 代理

def test_client_without_proxy_is_direct_and_ignores_env(monkeypatch):
    """留空 = 直连：一律 trust_env=False（与更新模块同口径，2026-10-20 决策）。"""
    from mangaproof.poprako import client as client_module

    recorded: dict = {}

    class _FakeHttpxClient:
        def __init__(self, **kwargs):
            recorded.update(kwargs)

        def close(self):
            return None

    monkeypatch.setattr(client_module.httpx, "Client", _FakeHttpxClient)
    client_module.PoprakoClient(BASE)

    assert recorded["trust_env"] is False
    assert recorded["proxy"] is None


def test_client_uses_explicit_proxy(monkeypatch):
    from mangaproof.poprako import client as client_module

    recorded: dict = {}

    class _FakeHttpxClient:
        def __init__(self, **kwargs):
            recorded.update(kwargs)

        def close(self):
            return None

    monkeypatch.setattr(client_module.httpx, "Client", _FakeHttpxClient)
    client = client_module.PoprakoClient(BASE, proxy="http://127.0.0.1:7897")

    assert recorded["trust_env"] is False
    assert recorded["proxy"] == "http://127.0.0.1:7897"
    assert client.proxy == "http://127.0.0.1:7897"


def test_client_ignores_invalid_proxy(monkeypatch):
    from mangaproof.poprako import client as client_module

    recorded: dict = {}

    class _FakeHttpxClient:
        def __init__(self, **kwargs):
            recorded.update(kwargs)

        def close(self):
            return None

    monkeypatch.setattr(client_module.httpx, "Client", _FakeHttpxClient)
    client = client_module.PoprakoClient(BASE, proxy="ftp://nope:1")

    assert client.proxy == ""
    assert recorded["proxy"] is None


def test_client_constructs_when_no_proxy_env_is_malformed(monkeypatch):
    """回归：``NO_PROXY`` 含裸 IPv6 会让 httpx 在 trust_env=True 时构造失败。

    我们已固定 ``trust_env=False``，所以这种环境变量再坏也不影响客户端构造
    （曾导致"菜单点了没反应"）。
    """
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1,::1,[::1]")
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1,::1,[::1]")

    client = PoprakoClient(BASE)
    try:
        assert isinstance(client._http, httpx.Client)
    finally:
        client.close()


@pytest.mark.parametrize(
    "raw, expected, error_fragment",
    [
        ("", "", None),
        ("   ", "", None),
        ("127.0.0.1:7897", "", "协议"),
        ("http://127.0.0.1:7897", "http://127.0.0.1:7897", None),
        ("  socks5://127.0.0.1:1080  ", "socks5://127.0.0.1:1080", None),
        ("https://proxy.example:443", "https://proxy.example:443", None),
        ("http://user:pass@host:1", "http://user:pass@host:1", None),
        ("ftp://host:1", "", "只支持"),
        ("http://h ost:1", "", "空格"),
        ("http://" + "a" * 400, "", "过长"),
    ],
)
def test_validate_proxy(raw: str, expected: str, error_fragment: str | None):
    from mangaproof.poprako.client import validate_proxy

    value, error = validate_proxy(raw)
    assert value == expected
    if error_fragment is None:
        assert error == ""
    else:
        assert error_fragment in error


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("", ""),
        ("http://127.0.0.1:7897", "http://127.0.0.1:7897"),
        ("http://user:secret@127.0.0.1:7897", "http://***@127.0.0.1:7897"),
        ("socks5://u:p@host:1080", "socks5://***@host:1080"),
    ],
)
def test_mask_proxy_hides_credentials(raw: str, expected: str):
    from mangaproof.poprako.client import mask_proxy

    assert mask_proxy(raw) == expected


class _FakeProbeClient:
    """替身探测客户端：记录 kwargs，返回预设响应或抛预设异常。"""

    response: httpx.Response | None = None
    error: Exception | None = None
    last_kwargs: dict = {}

    def __init__(self, **kwargs):
        type(self).last_kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def get(self, url: str):
        type(self).last_url = url
        if type(self).error is not None:
            raise type(self).error
        return type(self).response


@pytest.fixture
def fake_probe(monkeypatch):
    from mangaproof.poprako import client as client_module

    _FakeProbeClient.response = httpx.Response(204)
    _FakeProbeClient.error = None
    monkeypatch.setattr(client_module.httpx, "Client", _FakeProbeClient)
    return _FakeProbeClient


def test_probe_connection_success_reports_status_and_latency(fake_probe):
    from mangaproof.poprako.client import probe_connection

    ok, message, elapsed_ms = probe_connection("http://127.0.0.1:7897", timeout=1.0)

    assert ok is True
    assert "HTTP 204" in message
    # 2026-10-20：探针改打 Poprako 自己的站点，不再打谷歌
    assert "poprako.com" in message
    assert "google" not in message
    assert "ms" in message and "代理可用" in message
    assert elapsed_ms >= 0
    assert fake_probe.last_url == PROXY_TEST_URL
    assert fake_probe.last_kwargs["proxy"] == "http://127.0.0.1:7897"
    assert fake_probe.last_kwargs["trust_env"] is False


def test_probe_connection_allows_direct(fake_probe):
    """留空 = 测直连：用来回答"不设代理到底通不通"。"""
    from mangaproof.poprako.client import probe_connection

    ok, message, _elapsed_ms = probe_connection("", timeout=1.0)

    assert ok is True
    assert message.startswith("直连可用")
    assert "poprako.com" in message
    assert fake_probe.last_kwargs["proxy"] is None


def test_probe_connection_treats_any_http_response_as_reachable(fake_probe):
    from mangaproof.poprako.client import probe_connection

    fake_probe.response = httpx.Response(403)
    ok, message, _elapsed_ms = probe_connection("socks5://127.0.0.1:1080", timeout=1.0)

    assert ok is True
    assert "HTTP 403" in message


def test_probe_connection_reports_network_error(fake_probe):
    from mangaproof.poprako.client import probe_connection

    fake_probe.error = httpx.ConnectTimeout("timed out")
    ok, message, elapsed_ms = probe_connection("http://127.0.0.1:7897", timeout=1.0)

    assert ok is False
    assert "ConnectTimeout" in message
    assert "s）" in message, "失败也要说清等了多久"
    assert elapsed_ms >= 0


def test_probe_connection_rejects_invalid_proxy():
    from mangaproof.poprako.client import probe_connection

    ok, message, elapsed_ms = probe_connection("127.0.0.1:7897")
    assert ok is False and "协议" in message
    assert elapsed_ms == 0, "地址都不合法，没有发请求，不该有耗时"

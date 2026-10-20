# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""Poprako 数据结构（本平台专属，不与其它平台共用）。

来源：``poprako-server/docs/swagger.json``（OpenAPI 3.1）与
``docs/issue-upload-api.md`` / ``docs/chapter-issues.md``。

解析原则：**逐字段容错**——缺字段、类型不符一律回落默认值，
绝不因为平台多返回/少返回一个字段就把整页炸掉（用户看到的应该是
"某项显示为空"，而不是一个 traceback）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

__all__ = [
    "AuthSession",
    "User",
    "Membership",
    "Assignment",
    "Workset",
    "Comic",
    "Chapter",
    "PageArtwork",
    "Rect",
    "RemoteIssue",
    "ImportResult",
    "ROLE_RAW_PROVIDER",
    "ROLE_TRANSLATOR",
    "ROLE_PROOFREADER",
    "ROLE_TYPESETTER",
    "ROLE_REDRAWER",
    "ROLE_REVIEWER",
    "ROLE_PUBLISHER",
    "ROLE_ADMIN",
    "STAGE_NAMES",
    "PHASE_LABELS",
    "stage_phases",
    "is_published",
    "roles_text",
]

# ---------------------------------------------------------------------------
# 角色位（RoleMask，见 swagger 的 RoleMask 描述）
# ---------------------------------------------------------------------------

ROLE_RAW_PROVIDER = 1
ROLE_TRANSLATOR = 2
ROLE_PROOFREADER = 4
ROLE_TYPESETTER = 8
ROLE_REDRAWER = 16
ROLE_REVIEWER = 32
ROLE_PUBLISHER = 64
ROLE_ADMIN = 128

_ROLE_LABELS: tuple[tuple[int, str], ...] = (
    (ROLE_RAW_PROVIDER, "供图"),
    (ROLE_TRANSLATOR, "翻译"),
    (ROLE_PROOFREADER, "校对"),
    (ROLE_TYPESETTER, "排版"),
    (ROLE_REDRAWER, "修图"),
    (ROLE_REVIEWER, "监修"),
    (ROLE_PUBLISHER, "发布"),
    (ROLE_ADMIN, "管理员"),
)

# ---------------------------------------------------------------------------
# 流程阶段（StageMask：每阶段占 2 bit，从低位起）
# ---------------------------------------------------------------------------

#: 六个阶段的显示名（顺序即位序）
STAGE_NAMES: tuple[str, ...] = ("供图", "翻译", "校对", "排版 / 修图", "监修", "发布")

_PHASE_TOKENS: tuple[str, ...] = ("pending", "active", "completed")

#: 阶段相位 → 中文
PHASE_LABELS: dict[str, str] = {
    "pending": "未开始",
    "active": "进行中",
    "completed": "已完成",
    "unknown": "未知",
}


def stage_phases(stages: int) -> list[tuple[str, str]]:
    """把 ``StageMask`` 拆成 ``[(阶段名, pending/active/completed), …]``（6 项）。"""
    value = _as_int(stages)
    out: list[tuple[str, str]] = []
    for index, name in enumerate(STAGE_NAMES):
        bits = (value >> (2 * index)) & 0b11
        out.append((name, _PHASE_TOKENS[bits] if bits < len(_PHASE_TOKENS) else "unknown"))
    return out


def is_published(stages: int) -> bool:
    """发布阶段 completed（bits 10–11 == 2）即视为已发布；已发布章节拒收导入。"""
    return ((_as_int(stages) >> 10) & 0b11) == 2


def roles_text(roles: int) -> str:
    """角色位 → ``"监修 / 校对"``；没有任何角色时返回 ``"—"``。"""
    value = _as_int(roles)
    names = [label for bit, label in _ROLE_LABELS if value & bit]
    return " / ".join(names) if names else "—"


# ---------------------------------------------------------------------------
# 容错解析小工具
# ---------------------------------------------------------------------------

def _as_str(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _as_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    return value if isinstance(value, bool) else default


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


# ---------------------------------------------------------------------------
# 结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class AuthSession:
    """登录结果：``data.{token,user_id}``。"""

    token: str
    user_id: str

    @classmethod
    def from_api(cls, raw: Any) -> "AuthSession":
        data = _mapping(raw)
        return cls(token=_as_str(data.get("token")), user_id=_as_str(data.get("user_id")))


@dataclass(frozen=True, slots=True)
class User:
    """``GET /users/me``。"""

    id: str
    qid: str
    nickname: str
    is_sadmin: bool

    @classmethod
    def from_api(cls, raw: Any) -> "User":
        data = _mapping(raw)
        return cls(
            id=_as_str(data.get("id")),
            qid=_as_str(data.get("qid")),
            nickname=_as_str(data.get("nickname")),
            is_sadmin=_as_bool(data.get("is_sadmin")),
        )


@dataclass(frozen=True, slots=True)
class Membership:
    """``GET /members/me?incl=team``：当前用户与某个团队的关系。"""

    team_id: str
    team_name: str
    roles: int

    @property
    def is_reviewer(self) -> bool:
        """导入要求该章节的 REVIEWER 指派；这里只是提示性预检，服务端为准。"""
        return bool(self.roles & ROLE_REVIEWER)

    @property
    def display_name(self) -> str:
        return self.team_name or self.team_id

    @classmethod
    def from_api(cls, raw: Any) -> "Membership":
        data = _mapping(raw)
        team = _mapping(data.get("team"))
        return cls(
            team_id=_as_str(data.get("team_id")) or _as_str(team.get("id")),
            team_name=_as_str(team.get("name")),
            roles=_as_int(data.get("roles")),
        )


@dataclass(frozen=True, slots=True)
class Assignment:
    """``GET /assignments?chapter_id=…`` / ``POST /assignments/join``：章节分工。

    ``roles`` 是**章节级**的工作角色位（不含 ADMIN 的展示语义），与
    :class:`Membership` 的团队角色是两套东西：团队里是「监修」，不代表这一章
    已经分到了监修 —— 导入权限看的是这一行的 ``REVIEWER`` 位。
    """

    id: str
    chapter_id: str
    user_id: str
    roles: int

    @property
    def is_reviewer(self) -> bool:
        """这一章里是否有「监修」分工（上传问题需要它，仅管理员身份不够）。"""
        return bool(self.roles & ROLE_REVIEWER)

    @classmethod
    def from_api(cls, raw: Any) -> "Assignment":
        data = _mapping(raw)
        return cls(
            id=_as_str(data.get("id")),
            chapter_id=_as_str(data.get("chapter_id")),
            user_id=_as_str(data.get("user_id")),
            roles=_as_int(data.get("roles")),
        )


@dataclass(frozen=True, slots=True)
class Workset:
    """``GET /teams/{team_id}/worksets``。"""

    id: str
    name: str
    comic_count: int

    @classmethod
    def from_api(cls, raw: Any) -> "Workset":
        data = _mapping(raw)
        return cls(
            id=_as_str(data.get("id")),
            name=_as_str(data.get("name")) or _as_str(data.get("id")),
            comic_count=_as_int(data.get("comic_count")),
        )


@dataclass(frozen=True, slots=True)
class Comic:
    """``GET /worksets/{id}/comics?status=active``。"""

    id: str
    title: str
    author: str
    chapter_count: int
    is_archived: bool

    @classmethod
    def from_api(cls, raw: Any) -> "Comic":
        data = _mapping(raw)
        return cls(
            id=_as_str(data.get("id")),
            title=_as_str(data.get("title")) or _as_str(data.get("id")),
            author=_as_str(data.get("author")),
            chapter_count=_as_int(data.get("chapter_count")),
            is_archived=_as_bool(data.get("is_archived")),
        )


@dataclass(frozen=True, slots=True)
class Chapter:
    """``GET /chapters/{id}`` / ``GET /comics/{id}/chapters``。"""

    id: str
    comic_id: str
    index: int
    subtitle: str
    page_count: int
    stages: int

    @property
    def is_published(self) -> bool:
        return is_published(self.stages)

    @property
    def label(self) -> str:
        """下拉框里显示的一行：``第 13 话 初次见面``。"""
        head = f"第 {self.index + 1} 话"
        return f"{head} {self.subtitle}".strip() if self.subtitle else head

    @classmethod
    def from_api(cls, raw: Any) -> "Chapter":
        data = _mapping(raw)
        return cls(
            id=_as_str(data.get("id")),
            comic_id=_as_str(data.get("comic_id")),
            index=_as_int(data.get("index")),
            subtitle=_as_str(data.get("subtitle")),
            page_count=_as_int(data.get("page_count")),
            stages=_as_int(data.get("stages")),
        )


@dataclass(frozen=True, slots=True)
class PageArtwork:
    """复合图页（``GET /chapters/{id}/page-artworks``）。"""

    id: str
    index: int
    raw_ident: Optional[str]
    image_uploaded: bool
    ext: Optional[str]

    @property
    def display_name(self) -> str:
        """下拉框里显示的一行：``第 1 页 · 001.psd``。"""
        ident = self.raw_ident or "（未命名）"
        return f"第 {self.index + 1} 页 · {ident}"

    @classmethod
    def from_api(cls, raw: Any) -> "PageArtwork":
        data = _mapping(raw)
        ident = data.get("raw_ident")
        ext = data.get("ext")
        return cls(
            id=_as_str(data.get("id")),
            index=_as_int(data.get("index")),
            raw_ident=ident if isinstance(ident, str) else None,
            image_uploaded=_as_bool(data.get("image_uploaded")),
            ext=ext if isinstance(ext, str) else None,
        )


@dataclass(frozen=True, slots=True)
class Rect:
    """归一化到整张复合图的矩形（左上原点，``[0,1]``，宽高为正）。"""

    x: float
    y: float
    width: float
    height: float

    def to_api(self) -> dict[str, float]:
        return {
            "x_coord": self.x,
            "y_coord": self.y,
            "width": self.width,
            "height": self.height,
        }

    @classmethod
    def from_api(cls, raw: Any) -> Optional["Rect"]:
        data = _mapping(raw)
        if not data:
            return None
        try:
            return cls(
                x=float(data.get("x_coord", 0.0)),
                y=float(data.get("y_coord", 0.0)),
                width=float(data.get("width", 0.0)),
                height=float(data.get("height", 0.0)),
            )
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True, slots=True)
class RemoteIssue:
    """``GET /chapters/{id}/issues`` 的一行。

    **只在上传前后的一次性流程里存在**：不写入任务文件、不参与本地统计
    （需求 §9.2「单向上传的边界」）。
    """

    id: str
    page_artwork_id: str
    index: int
    variant: str
    layer_name: Optional[str]
    rect: Optional[Rect]
    note: str

    @classmethod
    def from_api(cls, raw: Any) -> "RemoteIssue":
        data = _mapping(raw)
        layer_name = data.get("layer_name")
        return cls(
            id=_as_str(data.get("id")),
            page_artwork_id=_as_str(data.get("page_artwork_id")),
            index=_as_int(data.get("index")),
            variant=_as_str(data.get("variant")),
            layer_name=layer_name if isinstance(layer_name, str) else None,
            rect=Rect.from_api(data.get("rect")),
            note=_as_str(data.get("note")),
        )


@dataclass(frozen=True, slots=True)
class ImportResult:
    """``POST /chapters/{id}/issues/import`` 的返回计数。"""

    imported_page_count: int
    imported_issue_count: int

    @classmethod
    def from_api(cls, raw: Any) -> "ImportResult":
        data = _mapping(raw)
        return cls(
            imported_page_count=_as_int(data.get("imported_page_count")),
            imported_issue_count=_as_int(data.get("imported_issue_count")),
        )

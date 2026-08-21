"""问题模型（需求 §33、§63）。

一个失败图层可以拥有多个 Issue，每个 Issue 独立拥有：
类型、批注、红框（PSD World Coordinates）、问题编号。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Tuple


@dataclass
class Issue:
    """单个问题。"""

    file: str                                  # 所属 PSD（相对路径）
    layer_id: str                              # 所属图层 id
    layer_name: str                            # 所属图层名（供返修单显示）
    type: str                                  # 问题类型（预制或自定义）
    comment: str = ""                          # 自定义批注
    rect: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)  # x, y, w, h（世界坐标）
    issue_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    issue_no: int = 0                          # 任务内全局问题编号（1 起）

    def to_dict(self) -> dict:
        return {
            "issue_id": self.issue_id,
            "issue_no": self.issue_no,
            "file": self.file,
            "layer_id": self.layer_id,
            "layer_name": self.layer_name,
            "type": self.type,
            "comment": self.comment,
            "rect": {
                "x": self.rect[0],
                "y": self.rect[1],
                "w": self.rect[2],
                "h": self.rect[3],
            },
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "Issue":
        r = raw.get("rect") or {}
        try:
            rect = (
                float(r.get("x", 0)),
                float(r.get("y", 0)),
                float(r.get("w", 0)),
                float(r.get("h", 0)),
            )
        except (TypeError, ValueError):
            rect = (0.0, 0.0, 0.0, 0.0)
        return cls(
            issue_id=str(raw.get("issue_id") or uuid.uuid4().hex),
            issue_no=int(raw.get("issue_no") or 0),
            file=str(raw.get("file", "")),
            layer_id=str(raw.get("layer_id", "")),
            layer_name=str(raw.get("layer_name", "")),
            type=str(raw.get("type") or "其他"),
            comment=str(raw.get("comment") or ""),
            rect=rect,
        )

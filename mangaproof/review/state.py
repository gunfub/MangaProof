# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""任务状态模型（需求 §63、§64）。

所有监制信息（通过/未通过、问题、红框、批注、进度）都属于
MangaProof 自己的数据，绝不写入 PSD（需求 §2.4）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from mangaproof.review.issue import Issue

UNREVIEWED = "unreviewed"
PASSED = "passed"
FAILED = "failed"
PARTIAL = "partial"   # 仅文件级：部分图层已监制（图层级不会出现）

LAYER_STATUSES = (UNREVIEWED, PASSED, FAILED)

STATUS_LABELS = {
    UNREVIEWED: "未监制",
    PASSED: "已通过",
    FAILED: "未通过",
    PARTIAL: "部分监制",
}

STATUS_ICONS = {
    UNREVIEWED: "○",
    PASSED: "✓",
    FAILED: "✗",
    PARTIAL: "●",
}


@dataclass
class FileRecord:
    """任务内单个 PSD 的身份记录（需求 §7.3、§64）。"""

    relative_path: str
    file_name: str
    size: int
    mtime: float = 0.0
    sample_sha256: Optional[str] = None   # 仅抽样文件记录完整 Hash

    def to_dict(self) -> dict:
        d = {
            "relative_path": self.relative_path,
            "file_name": self.file_name,
            "size": self.size,
            "mtime": self.mtime,
        }
        if self.sample_sha256:
            d["sample_sha256"] = self.sample_sha256
        return d

    @classmethod
    def from_dict(cls, raw: dict) -> "FileRecord":
        return cls(
            relative_path=str(raw.get("relative_path", "")),
            file_name=str(raw.get("file_name", "")),
            size=int(raw.get("size", 0)),
            mtime=float(raw.get("mtime", 0.0)),
            sample_sha256=raw.get("sample_sha256"),
        )


@dataclass
class TaskState:
    """任务状态（需求 §64）。"""

    schema_version: int = 1
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    task_name: str = ""
    task_type: str = "single"            # "single" | "folder"
    source: str = ""                     # 原始路径（单 PSD 或文件夹）
    current_file: str = ""               # 相对路径
    current_layer: str = ""              # 图层 id
    files: List[FileRecord] = field(default_factory=list)
    reviews: Dict[str, str] = field(default_factory=dict)  # "<file>|<layer_id>" -> status
    issues: List[Issue] = field(default_factory=list)
    #: 各平台自己的同步绑定（1.2.0）：``{"poprako": {...}}``。
    #: 平台只读写自己那个子键；旧版本读到新文件忽略未知键，新版本读到旧文件为空。
    integrations: Dict[str, dict] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    # -- 状态操作（需求 §14） ---------------------------------------------

    @staticmethod
    def _review_key(file_rel: str, layer_id: str) -> str:
        return f"{file_rel}|{layer_id}"

    def status_of(self, file_rel: str, layer_id: str) -> str:
        return self.reviews.get(self._review_key(file_rel, layer_id), UNREVIEWED)

    def set_status(self, file_rel: str, layer_id: str, status: str) -> None:
        if status not in LAYER_STATUSES:
            raise ValueError(f"非法图层状态：{status}")
        key = self._review_key(file_rel, layer_id)
        if status == UNREVIEWED:
            self.reviews.pop(key, None)
            self.drop_issues_for_layer(file_rel, layer_id)
        else:
            self.reviews[key] = status

    # -- 问题 --------------------------------------------------------------

    def add_issue(
        self,
        file_rel: str,
        layer_id: str,
        layer_name: str,
        issue_type: str,
        comment: str,
        rect,
    ) -> Issue:
        next_no = max((i.issue_no for i in self.issues), default=0) + 1
        issue = Issue(
            file=file_rel,
            layer_id=layer_id,
            layer_name=layer_name,
            type=issue_type,
            comment=comment,
            rect=tuple(float(v) for v in rect),
            issue_no=next_no,
        )
        self.issues.append(issue)
        return issue

    def remove_issue(self, issue_id: str) -> None:
        self.issues = [i for i in self.issues if i.issue_id != issue_id]

    def drop_issues_for_layer(self, file_rel: str, layer_id: str) -> None:
        self.issues = [
            i for i in self.issues
            if not (i.file == file_rel and i.layer_id == layer_id)
        ]

    def issues_for(self, file_rel: str, layer_id: str) -> List[Issue]:
        return [i for i in self.issues if i.file == file_rel and i.layer_id == layer_id]

    def issues_for_file(self, file_rel: str) -> List[Issue]:
        return [i for i in self.issues if i.file == file_rel]

    # -- 统计（需求 §41、§42） --------------------------------------------

    def count_file(self, file_rel: str, layer_ids) -> dict:
        passed = failed = unreviewed = 0
        for lid in layer_ids:
            st = self.status_of(file_rel, lid)
            if st == PASSED:
                passed += 1
            elif st == FAILED:
                failed += 1
            else:
                unreviewed += 1
        return {
            "total": len(layer_ids),
            "reviewed": passed + failed,
            "passed": passed,
            "failed": failed,
            "unreviewed": unreviewed,
            "issues": len(self.issues_for_file(file_rel)),
        }

    def count_all(self, layer_ids_by_file: Dict[str, List[str]]) -> dict:
        """总体统计。layer_ids_by_file: {file_rel: 当前可监制图层 id 列表}

        只统计「当前真实存在且可见」的图层：PSD 里被隐藏、或已删除/改名
        的图层，其历史监制记录不再计入任何一项（否则会出现 passed 比
        total 还大的怪数；隐藏图层同样不该出现在进度里）。
        """
        passed = failed = 0
        total = 0
        for rel, layer_ids in layer_ids_by_file.items():
            total += len(layer_ids)
            for lid in layer_ids:
                st = self.status_of(rel, lid)
                if st == PASSED:
                    passed += 1
                elif st == FAILED:
                    failed += 1
        reviewed = passed + failed
        return {
            "files": len(layer_ids_by_file),
            "total": total,
            "reviewed": reviewed,
            "passed": passed,
            "failed": failed,
            "unreviewed": total - reviewed,
            "issues": len(self.issues),
        }

    def file_status(self, file_rel: str, layer_ids) -> str:
        """PSD 级状态：PASSED / FAILED / PARTIAL / UNREVIEWED。

        返回值为 state 模块的状态常量（不是图层级 LAYER_STATUSES 的子集：
        额外可能返回 PARTIAL），调用方必须按下表映射图标/颜色，
        不要自行硬编码字符串。
        """
        st = self.count_file(file_rel, layer_ids)
        if st["total"] == 0:
            return UNREVIEWED
        if st["failed"] > 0 and st["unreviewed"] == 0:
            return FAILED
        if st["unreviewed"] == 0:
            return PASSED
        if st["reviewed"] > 0:
            return PARTIAL
        return UNREVIEWED

    # -- 序列化（需求 §8、§10） -------------------------------------------

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "task_name": self.task_name,
            "task_type": self.task_type,
            "source": self.source,
            "current_file": self.current_file,
            "current_layer": self.current_layer,
            "files": [f.to_dict() for f in self.files],
            "reviews": dict(self.reviews),
            "issues": [i.to_dict() for i in self.issues],
            "integrations": {
                str(name): dict(value)
                for name, value in self.integrations.items()
                if isinstance(value, dict)
            },
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "TaskState":
        task = cls()
        task.schema_version = int(raw.get("schema_version", 1))
        task.task_id = str(raw.get("task_id") or task.task_id)
        task.task_name = str(raw.get("task_name") or "")
        task.task_type = str(raw.get("task_type") or "single")
        task.source = str(raw.get("source") or "")
        task.current_file = str(raw.get("current_file") or "")
        task.current_layer = str(raw.get("current_layer") or "")
        task.files = [FileRecord.from_dict(f) for f in (raw.get("files") or [])]
        reviews = raw.get("reviews") or {}
        task.reviews = {
            str(k): str(v) for k, v in reviews.items() if v in LAYER_STATUSES
        }
        task.issues = [Issue.from_dict(m) for m in (raw.get("issues") or [])]
        integrations = raw.get("integrations") or {}
        if isinstance(integrations, dict):
            task.integrations = {
                str(name): dict(value)
                for name, value in integrations.items()
                if isinstance(value, dict)
            }
        task.created_at = str(raw.get("created_at") or "")
        task.updated_at = str(raw.get("updated_at") or "")
        return task

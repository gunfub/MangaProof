# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""Poprako 的"上次选择"（独立 JSON 文件，**不写进 settings.json**）。

需求（2026-10-20）：团队 / 项目集 / 作品三级的"上次选过的那个"要在下拉里标出来，
但**不自动选中**（逐级懒加载，用户必须自己选）。

为什么单独存文件（与 ``recent.json`` 同一个理由）：

- 它是**使用痕迹**而不是程序设置——重置设置、settings.json 被整份重建或损坏，
  都不该顺手把"上次选到哪"清掉；
- settings.json 由设置对话框整体重写，混进去容易被整份覆盖；
- 写入时机完全不同：用户每选一级就写一次，与设置变更互不干扰。

文件格式（``version`` 供后续迁移）::

    {
      "version": 1,
      "team_id": "t_1", "team_name": "木叶翻译组",
      "workset_id": "w_1", "workset_name": "2026 春季",
      "comic_id": "co_1", "comic_title": "某漫画"
    }

任何读取失败都当作"没有记录"（返回空对象），绝不让它挡住同步窗口打开。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from mangaproof.config import paths

log = logging.getLogger("mangaproof.poprako.last_choice")

__all__ = ["LAST_CHOICE_FILE_NAME", "LAST_CHOICE_VERSION", "LastChoice", "LastChoiceStore"]

LAST_CHOICE_FILE_NAME = "poprako_last.json"
LAST_CHOICE_VERSION = 1

#: 单个字段的长度上限（防止手改坏的文件把下拉撑爆）
_MAX_TEXT = 200


@dataclass(frozen=True, slots=True)
class LastChoice:
    """上次使用的团队 / 项目集 / 作品（只用于在下拉里标注，不自动选中）。"""

    team_id: str = ""
    team_name: str = ""
    workset_id: str = ""
    workset_name: str = ""
    comic_id: str = ""
    comic_title: str = ""

    def is_empty(self) -> bool:
        return not any(
            (self.team_id, self.workset_id, self.comic_id)
        )


def _as_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:_MAX_TEXT]


class LastChoiceStore:
    """``poprako_last.json`` 的读写封装（默认落在程序目录）。"""

    def __init__(self, path: Optional[Path] = None):
        self._path = Path(path) if path is not None else (
            paths.get_app_dir() / LAST_CHOICE_FILE_NAME
        )

    @property
    def file_path(self) -> Path:
        return self._path

    def load(self) -> LastChoice:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return LastChoice()
        except (OSError, ValueError) as exc:
            log.info("读取 %s 失败，按无记录处理：%s", self._path.name, exc)
            return LastChoice()
        if not isinstance(raw, dict):
            return LastChoice()
        return LastChoice(
            team_id=_as_text(raw.get("team_id")),
            team_name=_as_text(raw.get("team_name")),
            workset_id=_as_text(raw.get("workset_id")),
            workset_name=_as_text(raw.get("workset_name")),
            comic_id=_as_text(raw.get("comic_id")),
            comic_title=_as_text(raw.get("comic_title")),
        )

    def save(self, choice: LastChoice) -> None:
        payload = {
            "version": LAST_CHOICE_VERSION,
            "team_id": choice.team_id,
            "team_name": choice.team_name,
            "workset_id": choice.workset_id,
            "workset_name": choice.workset_name,
            "comic_id": choice.comic_id,
            "comic_title": choice.comic_title,
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(self._path.name + ".tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            tmp.replace(self._path)
        except OSError as exc:
            # 记不住"上次"不影响功能，绝不因此报错或回滚用户选择
            log.info("写入 %s 失败（忽略）：%s", self._path.name, exc)

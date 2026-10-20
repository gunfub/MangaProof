# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""Poprako「上次选择」存储单测（``poprako/last_choice.py``）。

要点：**独立文件**（不写 settings.json）、坏文件不致命、原子写。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from mangaproof.poprako.last_choice import (  # noqa: E402
    LAST_CHOICE_FILE_NAME,
    LAST_CHOICE_VERSION,
    LastChoice,
    LastChoiceStore,
)


def test_default_file_name_matches_recent_json_convention():
    assert LAST_CHOICE_FILE_NAME.endswith(".json")
    assert LAST_CHOICE_FILE_NAME != "settings.json"


def test_missing_file_returns_empty_choice(tmp_path: Path):
    store = LastChoiceStore(tmp_path / "poprako_last.json")
    choice = store.load()
    assert choice.is_empty()
    assert choice.team_id == ""


def test_roundtrip_keeps_every_field(tmp_path: Path):
    path = tmp_path / "poprako_last.json"
    store = LastChoiceStore(path)
    saved = LastChoice(
        team_id="t_1", team_name="木叶翻译组",
        workset_id="w_1", workset_name="2026 春季",
        comic_id="co_1", comic_title="某漫画",
    )
    store.save(saved)

    loaded = store.load()
    assert loaded == saved
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == LAST_CHOICE_VERSION
    assert raw["comic_title"] == "某漫画"
    assert not (tmp_path / "poprako_last.json.tmp").exists(), "临时文件必须被 replace 掉"


def test_corrupt_file_is_treated_as_empty(tmp_path: Path):
    path = tmp_path / "poprako_last.json"
    path.write_text("{not json", encoding="utf-8")
    assert LastChoiceStore(path).load().is_empty()


def test_non_object_payload_is_treated_as_empty(tmp_path: Path):
    path = tmp_path / "poprako_last.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert LastChoiceStore(path).load().is_empty()


def test_non_string_values_are_ignored(tmp_path: Path):
    path = tmp_path / "poprako_last.json"
    path.write_text(
        json.dumps({"team_id": 123, "workset_id": " w_1 ", "comic_id": None}),
        encoding="utf-8",
    )
    choice = LastChoiceStore(path).load()
    assert choice.team_id == ""
    assert choice.workset_id == "w_1"
    assert choice.comic_id == ""


def test_long_values_are_truncated(tmp_path: Path):
    path = tmp_path / "poprako_last.json"
    path.write_text(json.dumps({"team_name": "x" * 5000}), encoding="utf-8")
    assert len(LastChoiceStore(path).load().team_name) <= 200


def test_write_failure_does_not_raise(tmp_path: Path):
    """目录不可写时只记日志——"记不住上次"绝不能挡住同步流程。"""
    blocked = tmp_path / "blocked"
    blocked.write_text("not a dir", encoding="utf-8")
    store = LastChoiceStore(blocked / "poprako_last.json")
    store.save(LastChoice(team_id="t_1"))      # 不应抛异常
    assert store.load().is_empty()

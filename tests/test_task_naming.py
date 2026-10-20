# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""任务显示名规则单测（``utils/task_naming.py``）。

这套规则同时被返修单默认名与"按名称匹配作品"使用，所以既要测三条基本规则，
也要测**与返修单侧完全一致**（后者已有 ``test_default_report_name_output_folder`` 守着）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from mangaproof.utils.task_naming import task_display_name  # noqa: E402


def test_folder_task_uses_folder_name():
    assert task_display_name("folder", Path("/x/Chapter01")) == "Chapter01"


def test_folder_task_in_output_dir_uses_parent_name():
    assert task_display_name("folder", Path("/x/Chapter01/output")) == "Chapter01"


def test_output_dir_check_is_case_insensitive():
    assert task_display_name("folder", Path("/x/Chapter01/OUTPUT")) == "Chapter01"


def test_single_task_uses_psd_stem():
    assert task_display_name("single", Path("/x/Chapter01"), "001.psd") == "001"


def test_single_task_in_output_dir_uses_parent_name():
    assert task_display_name("single", Path("/x/Chapter01/output"), "001.psd") == "Chapter01"


def test_single_task_without_file_name_falls_back_to_report():
    assert task_display_name("single", Path("/x/Chapter01")) == "report"


def test_output_dir_without_parent_name_keeps_self():
    # 极端路径：/output 的父目录名是空（根），不能用空字符串当名字
    assert task_display_name("folder", Path("/output")) == "output"


def test_matches_report_default_name():
    """与 ``report.generator.default_report_name`` 结果必须逐字一致。"""
    from mangaproof.report.generator import default_report_name

    cases = [
        ("folder", Path("/x/Chapter01"), ""),
        ("folder", Path("/x/Chapter01/output"), ""),
        ("single", Path("/x/Chapter01"), "001.psd"),
        ("single", Path("/x/Chapter01/output"), "007.psd"),
    ]
    for task_type, base, psd_name in cases:
        assert task_display_name(task_type, base, psd_name) == default_report_name(
            task_type, base, psd_name
        ), (task_type, base, psd_name)

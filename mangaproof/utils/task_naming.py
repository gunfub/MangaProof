# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""任务显示名（漫画 / 章节名）的纯函数。

为什么单独成模块（而不是留在 ``report/generator.py`` 里）：

- 名字规则被**两个**互不相关的地方需要：返修单的默认文件名、监制数据同步窗口里
  "按名称匹配作品"；后者不该为了一个字符串把 ``reportlab`` 拉进对话框路径；
- 规则本身（``output`` 目录还原成上一级）属于"任务命名"，不属于"PDF 生成"。

零依赖（只用标准库），且在 ``report/generator.py::default_report_name`` 里被委托调用，
保证两处行为永远一致（返修单侧已有回归用例守着）。
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["task_display_name"]

#: 自动嵌字脚本的固定输出目录名（不区分大小写）
OUTPUT_DIR_NAME = "output"


def task_display_name(task_type: str, base_dir: Path, psd_file_name: str = "") -> str:
    """返回任务对应的"作品 / 章节"名。

    - 单 PSD（``task_type == "single"``）：所在目录叫 ``output`` 时取上一级目录名，
      否则取 PSD 文件名（去扩展名）；
    - 文件夹：目录名是 ``output`` 时取上一级目录名，否则取目录名。

    这是既有返修单命名规则的**唯一实现**，调用方不要再各写一份。
    """
    base = Path(base_dir)
    if base.name.lower() == OUTPUT_DIR_NAME and base.parent.name:
        return base.parent.name
    if task_type == "single":
        return Path(psd_file_name or "report").stem
    return base.name

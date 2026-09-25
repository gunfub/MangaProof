#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""把仓库根 LICENSE 的全文生成为 `mangaproof/gpl_text.py`（程序内离线可查看）。

为什么内置而不是运行时读文件
----------------------------
第三方许可页（`mangaproof/third_party.py`）本来就是把全文作为常量放在代码里；
本软件自身的许可采用同一套做法，于是：

- 任何产物形态（onedir / .app / APK）都无需关心"许可文件有没有被打进去"；
- 运行时不依赖工作目录、`sys._MEIPASS` 或程序目录下有没有那个文件。

`LICENSE` 仍是**唯一数据源**：本文件从它生成，`tests/test_app_license.py`
守卫两者逐字一致——改了 LICENSE 却忘记重新生成时测试会直接失败。

用法：
    uv run python scripts/build_app_license_text.py          # 写入 mangaproof/gpl_text.py
    uv run python scripts/build_app_license_text.py --check   # 只校验是否与 LICENSE 一致
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "LICENSE"
OUTPUT = ROOT / "mangaproof" / "gpl_text.py"

HEADER = '''# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""GNU General Public License v3.0 全文（**自动生成，请勿手工编辑**）。

由 `scripts/build_app_license_text.py` 从仓库根 `LICENSE` 生成；
改了 LICENSE 请重新生成，`tests/test_app_license.py` 会守卫两者一致。

程序内「关于 → 许可证…」直接展示本常量，因此不需要在运行时读文件，
Android 等产物也不必专门把 LICENSE 打进包。
"""

GPL3_LICENSE_TEXT = """\\
'''


def render() -> str:
    text = SOURCE.read_text(encoding="utf-8")
    if '"""' in text or text.endswith("\\"):
        raise SystemExit("LICENSE 含有三引号或行尾反斜杠，需改用其它嵌入方式")
    if not text.endswith("\n"):
        text += "\n"
    return HEADER + text + '"""\n'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 mangaproof/gpl_text.py")
    parser.add_argument(
        "--check",
        action="store_true",
        help="只校验文件是否与 LICENSE 一致（不一致时退出码 1，供测试使用）",
    )
    args = parser.parse_args(argv)

    content = render()
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != content:
            print(
                f"{OUTPUT.name} 与 LICENSE 不一致，"
                "请运行：uv run python scripts/build_app_license_text.py",
                file=sys.stderr,
            )
            return 1
        print(f"{OUTPUT.name} 已是最新")
        return 0

    OUTPUT.write_text(content, encoding="utf-8")
    print(f"已写入 {OUTPUT.relative_to(ROOT)}（{len(content.splitlines())} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

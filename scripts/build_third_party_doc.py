#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""把「关于 → 第三方许可」的数据导出为仓库根目录的 THIRD_PARTY_LICENSES.md。

为什么需要这个脚本
------------------
程序内的许可页要先下载并运行程序才能看到。仓库里放一份**同源导出**的文件后，
只浏览代码的人、合规审查、下游打包者不必安装任何东西就能直接查阅全部许可。

单一数据源
----------
数据来自 `mangaproof/third_party.py` 的 `build_third_party_items()`——程序内
「关于 → 第三方许可」用的是同一个函数，两边不会各写一份、各自过期。

用法：
    uv run python scripts/build_third_party_doc.py           # 写入 THIRD_PARTY_LICENSES.md
    uv run python scripts/build_third_party_doc.py --check    # 只校验文件是否与数据一致

版本号说明：条目版本优先取本机已安装包的元数据，未安装时回退到源码里记录的
版本（见 third_party._resolve_version）。因此在本项目虚拟环境里生成，得到的就是
uv.lock 锁定的版本。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mangaproof.third_party import ThirdPartyItem, build_third_party_items  # noqa: E402

OUTPUT = ROOT / "THIRD_PARTY_LICENSES.md"

HEADER = """# 第三方组件与许可

> 本文件由 [`scripts/build_third_party_doc.py`](scripts/build_third_party_doc.py) 依据
> [`mangaproof/third_party.py`](mangaproof/third_party.py) **自动生成，请勿手工编辑**——
> 需要改动请修改源文件后重新生成。
> 程序内 **关于 → 第三方许可** 展示的是同一份数据。

MangaProof 使用了下列第三方组件。每个条目给出组件名、版本、许可证标识（SPDX）、
版权声明、主页与许可证全文（过长的 GPL / LGPL 给要点摘要与官方全文链接）。

## 范围说明

- **随应用分发**（Python 运行时、psd-tools、NumPy、PySide6 / Qt、shiboken6、reportlab、
  Pillow、attrs、typing-extensions、charset-normalizer、MiSans 与 Noto 字体等）：
  这些组件的许可声明会随安装包一并提供给用户；
- **仅构建 / 打包期使用**（名称里标了「仅 … 打包用」或「… 依赖」的条目前缀，如
  PyInstaller、python-for-android、buildozer、Cython、Nuitka、Android SDK / NDK）：
  它们不随应用分发，列出是为了让构建链路的许可同样透明；
- **未列入**：只在开发 / 测试环境使用、不进入任何产物的工具（uv、pytest、性能分析工具等）；
- 部分组件自身还会引入第三方原生库或字体（例如 Qt 内的 libpng / harfbuzz / ICU、
  Pillow 内的 libjpeg-turbo / zlib、p4a 引入的 OpenSSL / libffi），其许可随该组件一并分发，
  详见各组件官方许可页；
- 版本号在本机装有该包时取自包元数据，否则回退到仓库记录的版本。

## 组件总览

| # | 组件 | 版本 | 许可证（SPDX） | 主页 |
| --- | --- | --- | --- | --- |
"""


def _cell(text: str) -> str:
    """表格单元格：竖线与换行都要转义，否则表格会错位。"""
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def _fence(text: str) -> str:
    """按内容里最长的反引号串决定围栏长度，避免正文提前闭合围栏。"""
    longest = 0
    current = 0
    for ch in text:
        current = current + 1 if ch == "`" else 0
        longest = max(longest, current)
    return "`" * max(3, longest + 1)


def render(items: list[ThirdPartyItem]) -> str:
    out: list[str] = [HEADER]
    for index, item in enumerate(items, 1):
        out.append(
            f"| {index} | {_cell(item.name)} | {_cell(item.version)} | "
            f"{_cell(item.spdx)} | <{item.homepage}> |\n"
        )

    out.append("\n## 许可全文与声明\n")
    for index, item in enumerate(items, 1):
        fence = _fence(item.license_text)
        out.append(
            f"\n### {index}. {item.name}\n\n"
            f"- **版本**：{item.version}\n"
            f"- **许可证（SPDX）**：{item.spdx}\n"
            f"- **版权**：{item.copyright}\n"
            f"- **主页**：<{item.homepage}>\n\n"
            f"{fence}text\n{item.license_text}\n{fence}\n"
        )
    return "".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 THIRD_PARTY_LICENSES.md")
    parser.add_argument(
        "--check",
        action="store_true",
        help="只校验文件是否与当前数据一致（不一致时退出码 1，供测试使用）",
    )
    args = parser.parse_args(argv)

    content = render(build_third_party_items())

    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != content:
            print(
                f"{OUTPUT.name} 与 third_party.py 不一致，"
                "请运行：uv run python scripts/build_third_party_doc.py",
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

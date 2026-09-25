# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""应用统一字体加载（MiSans）。

- 直接运行（python main.py）：查找 程序目录/font/MiSans-Medium.ttf；
- PyInstaller 打包产物：数据文件位于冻结资源目录（sys._MEIPASS，
  PyInstaller 6.x 的 onedir 布局为 _internal/），自动回退查找；
- 源码/开发态兜底：包目录的上一级（pytest、python -m 等 __main__ 不在
  项目根的场景），保证测试与开发环境也能拿到同一份字体文件；
- 注册进 Qt 字体数据库并设为应用默认字体，主题样式表同步使用；
- 返修单 PDF 生成（report/generator.py）复用 find_font_path() 取得同一
  字体文件，使 PDF 与界面字体一致。

MiSans 字体（https://hyperos.mi.com/font/download）版权归小米所有，
依据《MiSans 字体知识产权许可协议》使用：
- 本软件在「关于」对话框与 README 中注明使用 MiSans 字体；
- 不对字体做任何改编或二次开发；
- 字体文件仅随本软件整体分发，不单独提供下载。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger("mangaproof.fonts")

FONT_FILENAME = "MiSans-Medium.ttf"
#: 字形回退字体（**仅 Android 挂进家族链**）：
#: MiSans 缺 5 个界面在用符号的字形（✗ U+2717、▣ U+25A3、✎ U+270E、🗑 U+1F5D1、
#: ⚠ U+26A0），而 Android 版 Qt 的平台字体回退几乎是空的
#: （QAndroidPlatformFontDatabase::fallbacksForFamily() 只加 emoji / 按系统语言的
#: CJK / QT_ANDROID_FONTS 名单，**不会**把 /system/fonts 当逐字回退），于是缺字形
#: 直接显示成空白；桌面三平台靠系统回退（DirectWrite/CoreText/fontconfig）本来就能
#: 补，所以桌面不挂这条链，既有观感保持不变。
#:
#: 为什么自带而不是用系统字体：Noto Sans Symbols 2 正是 Android 自己在
#: `fonts.xml` 里给 `und-Zsym` 家族用的那支（NotoSansSymbols-Regular-Subsetted2.ttf），
#: 但**部分 OEM ROM 会换掉/裁掉它**，且 Qt 本来也不会调用系统回退——自带一份才能
#: 保证所有机型一致。OFL-1.1，随包分发（许可全文见「关于 → 第三方许可」）。
#: 详见 docs/Android端界面适配_缩放与菜单栏.md 第 8 节。
FALLBACK_FONT_FILENAME = "NotoSansSymbols2-Regular.ttf"


def _candidates(filename: str) -> list[Path]:
    """字体文件候选路径（程序目录 → 冻结资源目录 → 源码目录）。"""
    from mangaproof.config import paths

    candidates = [paths.get_app_dir() / "font" / filename]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "font" / filename)
    # 源码/开发态兜底：包目录（mangaproof/）的上一级即项目根
    candidates.append(Path(__file__).resolve().parent.parent / "font" / filename)
    return candidates


def font_candidates() -> list[Path]:
    """MiSans 字体文件的候选路径。"""
    return _candidates(FONT_FILENAME)


def fallback_font_candidates() -> list[Path]:
    """回退字体（符号图标）的候选路径。"""
    return _candidates(FALLBACK_FONT_FILENAME)


def find_font_path() -> Path | None:
    """返回第一个存在的 MiSans 字体文件；未找到返回 None。

    供 Qt 字体加载与 PDF 生成共用，避免两处各自判断字体位置。
    """
    for path in font_candidates():
        if path.exists():
            return path
    return None


def load_app_fonts(app, candidates: list[Path] | None = None) -> str | None:
    """注册 MiSans 并设为应用默认字体。

    返回实际使用的字体族名（用于主题样式表）；失败返回 None
    （回退系统默认字体，不阻塞启动）。
    """
    from PySide6.QtGui import QFont, QFontDatabase

    paths = candidates if candidates is not None else font_candidates()
    for path in paths:
        if not path.exists():
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id < 0:
            log.warning("字体注册失败：%s", path)
            continue
        families = QFontDatabase.applicationFontFamilies(font_id)
        if not families:
            continue
        family = families[0]
        default = app.font()
        size = default.pointSize() if default.pointSize() > 0 else 10
        font = QFont(family)
        font.setPointSize(size)
        app.setFont(font)
        log.info("已加载应用字体：%s（%s）", family, path)
        return family

    log.warning("未找到 MiSans 字体（%s），使用系统默认字体", [str(p) for p in paths])
    return None


def load_symbol_fallback_families(
    candidates: list[Path] | None = None, *, force: bool = False
) -> list[str]:
    """注册符号回退字体，返回要挂进字体家族链的家族名。

    **仅 Android 生效**（`force=True` 供测试用）：桌面三平台的系统字体回退本来
    就能补 MiSans 缺的字形，把回退字体挂进桌面家族链会改变既有观感，所以桌面
    直接返回空列表。

    Qt 侧机制（源码 qfontdatabase.cpp）：`QFont.setFamilies([A, B, C])` 会把 B、C
    作为 `fallBackFamilies` 交给 `QFontEngineMulti`，逐字回退时按链顺序查找；
    平台回退表（Android 上近乎为空）只是追加在链后。所以把字体挂进家族链即可在
    Android 上补上 MiSans 缺失的字形。
    """
    from PySide6.QtGui import QFontDatabase

    from mangaproof.utils.platform import is_android_strict

    if not force and not is_android_strict():
        return []

    paths = candidates if candidates is not None else fallback_font_candidates()
    for path in paths:
        if not path.exists():
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id < 0:
            log.warning("回退字体注册失败：%s", path)
            continue
        families = [
            f for f in QFontDatabase.applicationFontFamilies(font_id) if f
        ]
        if not families:
            continue
        log.info("已加载符号回退字体：%s（%s）", families[0], path)
        return families

    log.warning("未找到符号回退字体（%s），缺失字形在 Android 上可能显示为空白",
                [str(p) for p in paths])
    return []

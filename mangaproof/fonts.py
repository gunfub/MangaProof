"""应用统一字体加载（MiSans）。

- 直接运行（python main.py）：查找 程序目录/font/MiSans-Medium.ttf；
- PyInstaller 打包产物：数据文件位于冻结资源目录（sys._MEIPASS，
  PyInstaller 6.x 的 onedir 布局为 _internal/），自动回退查找；
- 注册进 Qt 字体数据库并设为应用默认字体，主题样式表同步使用。

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


def font_candidates() -> list[Path]:
    """字体文件候选路径（直接运行 + 打包两种布局）。"""
    from mangaproof.config import paths

    candidates = [paths.get_app_dir() / "font" / FONT_FILENAME]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "font" / FONT_FILENAME)
    return candidates


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

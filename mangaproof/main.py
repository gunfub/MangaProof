"""MangaProof 程序入口。

启动顺序：日志 → QApplication → 暗色主题 → 设置 → 主窗口。
程序目录判定统一走 config.paths.get_app_dir()（需求 §56、§57）。
"""

from __future__ import annotations

import sys
from pathlib import Path

from mangaproof import APP_NAME, __version__


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)

    # 程序目录（兼容 python main.py / PyInstaller onedir）
    from mangaproof.config import paths

    app_dir = paths.get_app_dir()

    from mangaproof.utils.logging_setup import get_logger, setup_logging

    setup_logging(app_dir)
    log = get_logger("main")
    log.info("%s v%s 启动，程序目录：%s", APP_NAME, __version__, app_dir)

    from PySide6.QtWidgets import QApplication

    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("MangaProof")

    from mangaproof.config.settings import SettingsManager
    from mangaproof.ui.dark_titlebar import install_dark_titlebar
    from mangaproof.ui.main_window import MainWindow
    from mangaproof.ui.theme import apply_dark_theme

    apply_dark_theme(app)
    install_dark_titlebar(app)
    settings_manager = SettingsManager()

    window = MainWindow(settings_manager)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

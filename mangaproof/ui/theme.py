"""暗色主题（需求 §28）。

专业图像工作环境：深灰主背景、高可读性文字、适度亮度层级、
不使用大面积纯黑；选中状态明确；通过/失败颜色具有语义。
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

# 语义色（需求 §28）
COLOR_BG_MAIN = "#2b2d30"       # 深灰主背景
COLOR_BG_PANEL = "#313438"
COLOR_BG_WIDGET = "#3a3d42"
COLOR_BG_HOVER = "#45494f"
COLOR_BG_SELECTED = "#2d5f8a"
COLOR_BORDER = "#4a4e54"
COLOR_TEXT = "#e4e6eb"
COLOR_TEXT_DIM = "#a0a4ab"
COLOR_ACCENT = "#4a90d9"
COLOR_PASS = "#4caf50"          # 通过：绿色
COLOR_FAIL = "#e53935"          # 失败：红色
COLOR_WARN = "#f5a623"          # 警告：黄/橙
COLOR_UNREVIEWED = "#8a8f98"    # 未监制：灰色
COLOR_CHECKER_A = "#2b2b2b"
COLOR_CHECKER_B = "#333333"

# 默认字体族优先级（主题样式表用）；加载 MiSans 后会插到最前
DEFAULT_FONT_FAMILIES = (
    "Segoe UI", "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", "sans-serif",
)

_FONT_PLACEHOLDER = "__FONT_FAMILIES__"

_STYLESHEET = f"""
* {{
    font-family: {_FONT_PLACEHOLDER};
    font-size: 13px;
}}
QMainWindow, QDialog {{ background-color: {COLOR_BG_MAIN}; color: {COLOR_TEXT}; }}
QWidget {{ color: {COLOR_TEXT}; }}
QMenuBar {{ background-color: {COLOR_BG_MAIN}; border-bottom: 1px solid {COLOR_BORDER}; }}
QMenuBar::item {{ background: transparent; padding: 4px 10px; }}
QMenuBar::item:selected {{ background: {COLOR_BG_HOVER}; }}
QMenu {{ background-color: {COLOR_BG_PANEL}; border: 1px solid {COLOR_BORDER}; }}
QMenu::item {{ padding: 5px 24px 5px 12px; }}
QMenu::item:selected {{ background: {COLOR_BG_SELECTED}; }}
QToolBar {{ background: {COLOR_BG_MAIN}; border-bottom: 1px solid {COLOR_BORDER}; spacing: 4px; padding: 3px; }}
QDockWidget {{ titlebar-close-icon: none; titlebar-normal-icon: none; }}
QDockWidget::title {{ background: {COLOR_BG_PANEL}; padding: 5px 8px; border-bottom: 1px solid {COLOR_BORDER}; font-weight: bold; }}
QStatusBar {{ background: {COLOR_BG_MAIN}; border-top: 1px solid {COLOR_BORDER}; color: {COLOR_TEXT_DIM}; }}
QStatusBar QLabel {{ color: {COLOR_TEXT_DIM}; }}
QListWidget, QListWidget#layerList {{
    background-color: {COLOR_BG_PANEL};
    border: none;
    outline: none;
    padding: 2px;
}}
QListWidget::item {{ padding: 5px 6px; border-radius: 3px; }}
QListWidget::item:selected {{ background: {COLOR_BG_SELECTED}; color: white; }}
QListWidget::item:hover {{ background: {COLOR_BG_HOVER}; }}
QPushButton, QToolButton {{
    background-color: {COLOR_BG_WIDGET};
    border: 1px solid {COLOR_BORDER};
    border-radius: 4px;
    padding: 5px 10px;
    color: {COLOR_TEXT};
}}
QPushButton:hover, QToolButton:hover {{ background-color: {COLOR_BG_HOVER}; }}
QPushButton:pressed, QToolButton:pressed {{ background-color: {COLOR_BG_SELECTED}; }}
QPushButton:checked, QToolButton:checked {{
    background-color: {COLOR_BG_SELECTED};
    border-color: {COLOR_ACCENT};
}}
QPushButton:disabled, QToolButton:disabled {{ color: {COLOR_TEXT_DIM}; }}
QComboBox {{
    background-color: {COLOR_BG_WIDGET};
    border: 1px solid {COLOR_BORDER};
    border-radius: 4px;
    padding: 4px 8px;
}}
QComboBox:disabled {{
    background-color: {COLOR_BG_MAIN};
    border-color: {COLOR_BORDER};
    color: {COLOR_TEXT_DIM};
}}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{
    background-color: {COLOR_BG_PANEL};
    border: 1px solid {COLOR_BORDER};
    selection-background-color: {COLOR_BG_SELECTED};
}}
QLineEdit, QTextEdit, QPlainTextEdit {{
    background-color: {COLOR_BG_WIDGET};
    border: 1px solid {COLOR_BORDER};
    border-radius: 4px;
    padding: 4px 6px;
    selection-background-color: {COLOR_BG_SELECTED};
}}
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {{
    background-color: {COLOR_BG_MAIN};
    color: {COLOR_TEXT_DIM};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{ border-color: {COLOR_ACCENT}; }}
QGroupBox {{
    border: 1px solid {COLOR_BORDER};
    border-radius: 4px;
    margin-top: 12px;
    padding-top: 6px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
    color: {COLOR_TEXT_DIM};
}}
QProgressBar {{
    background-color: {COLOR_BG_WIDGET};
    border: 1px solid {COLOR_BORDER};
    border-radius: 4px;
    text-align: center;
    color: {COLOR_TEXT};
}}
QProgressBar::chunk {{ background-color: {COLOR_ACCENT}; border-radius: 3px; }}
QTableWidget {{
    background-color: {COLOR_BG_PANEL};
    gridline-color: {COLOR_BORDER};
    border: 1px solid {COLOR_BORDER};
}}
QHeaderView::section {{
    background-color: {COLOR_BG_WIDGET};
    border: none;
    border-right: 1px solid {COLOR_BORDER};
    border-bottom: 1px solid {COLOR_BORDER};
    padding: 4px;
}}
QCheckBox {{ spacing: 6px; }}
QCheckBox:disabled {{ color: {COLOR_TEXT_DIM}; }}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {COLOR_BORDER};
    border-radius: 3px;
    background: {COLOR_BG_WIDGET};
}}
QCheckBox::indicator:disabled {{ background: {COLOR_BG_MAIN}; }}
QCheckBox::indicator:checked {{ background-color: {COLOR_ACCENT}; }}
QScrollBar:vertical {{ background: {COLOR_BG_MAIN}; width: 12px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {COLOR_BORDER}; border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {COLOR_TEXT_DIM}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: {COLOR_BG_MAIN}; height: 12px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {COLOR_BORDER}; border-radius: 5px; min-width: 24px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QMessageBox {{ background-color: {COLOR_BG_MAIN}; }}
QLabel#hintLabel {{ color: {COLOR_WARN}; }}
QToolTip {{ background-color: {COLOR_BG_PANEL}; color: {COLOR_TEXT}; border: 1px solid {COLOR_BORDER}; }}
"""


def apply_dark_theme(app: QApplication, primary_family: str | None = None) -> None:
    """应用暗色主题。

    primary_family：应用统一字体族名（如 MiSans），会置于样式表
    字体族列表首位；None 则使用默认字体族列表。
    """
    families = DEFAULT_FONT_FAMILIES
    if primary_family:
        families = (primary_family,) + tuple(
            f for f in DEFAULT_FONT_FAMILIES if f != primary_family
        )
    family_list = ", ".join(f'"{f}"' for f in families)

    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(COLOR_BG_MAIN))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(COLOR_TEXT))
    palette.setColor(QPalette.ColorRole.Base, QColor(COLOR_BG_WIDGET))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(COLOR_BG_PANEL))
    palette.setColor(QPalette.ColorRole.Text, QColor(COLOR_TEXT))
    palette.setColor(QPalette.ColorRole.Button, QColor(COLOR_BG_WIDGET))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(COLOR_TEXT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(COLOR_BG_SELECTED))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(COLOR_BG_PANEL))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(COLOR_TEXT))
    palette.setColor(QPalette.ColorRole.Link, QColor(COLOR_ACCENT))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(COLOR_TEXT_DIM))
    app.setPalette(palette)
    app.setStyleSheet(_STYLESHEET.replace(_FONT_PLACEHOLDER, family_list))

# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""统计面板芯片墙：每行换行上限 + 不得反向撑宽左栏（需求 §41、§43）。

背景：芯片墙每行原写死 12 个。``QGridLayout`` 的最小宽度是各列之和，12 个芯片
= 12×22 + 11×2 = 286px，比左栏的自然宽度还宽（左栏宽度由上面的 PSD 文件列表
决定：``QListWidget`` 默认 sizeHint 256 + 面板边距 12 = 268px）。于是芯片墙反过来
把左 dock 撑到 306px、画布被挤掉 38px——观感就是"界面莫名被撑宽两个格子"。
现在上限是 ``CHIP_COLUMNS``（10 个 = 238px，放得下）。

本文件钉两件事：

- 第 11 个芯片必须落到下一行（用 ``QGridLayout.getItemPosition`` 判定，
  不依赖像素与间距——间距只是最小值，多余宽度会被布局摊到列间）；
- 真实 ``MainWindow`` 里塞 11/12/24/40 个图层，左 dock 与画布宽度不得变化，
  且面板最小宽度不得反超左边文件列表的自然宽度（取运行时值，不硬编码 268）。

运行：QT_QPA_PLATFORM=offscreen uv run python -m pytest tests/test_statistics_panel.py -v
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from mangaproof.config.settings import SettingsManager  # noqa: E402
from mangaproof.review.state import UNREVIEWED  # noqa: E402
from mangaproof.ui.main_window import MainWindow  # noqa: E402
from mangaproof.ui.statistics_panel import CHIP_COLUMNS, StatisticsPanel  # noqa: E402

app = QApplication.instance() or QApplication([])


def _feed(panel: StatisticsPanel, n: int, psd: str = "001.psd") -> None:
    """给面板塞 n 个图层（全部未监制）。"""
    panel.set_current_psd(
        psd, [f"图层 {i}" for i in range(n)], [UNREVIEWED] * n, [0] * n
    )


def _positions(panel: StatisticsPanel) -> list[tuple[int, int]]:
    """每个芯片在网格里的 (行, 列)。"""
    layout = panel.chip_layout
    return [layout.getItemPosition(layout.indexOf(chip))[:2] for chip in panel._chips]


def _settle(rounds: int = 3) -> None:
    """跑几轮事件循环，让 QMainWindow 把布局走完。

    offscreen 下 ``show()`` 之后还会再走一轮布局（例如右栏按字体度量定宽后，
    中央区要让出宽度），只 pump 一次就取基线会读到中间态，把"窗口自己收敛"
    误判成"芯片把画布挤窄"。
    """
    for _ in range(rounds):
        app.processEvents()


def test_chip_wrap_caps_at_ten_columns():
    panel = StatisticsPanel()
    _feed(panel, CHIP_COLUMNS + 2)

    positions = _positions(panel)
    assert len(positions) == CHIP_COLUMNS + 2
    # 前 10 个一行排满；第 11 个必须换行——12 列时代第 11、12 个还挤在第一行
    assert positions[0] == (0, 0)
    assert positions[CHIP_COLUMNS - 1] == (0, CHIP_COLUMNS - 1)
    assert positions[CHIP_COLUMNS] == (1, 0)
    assert positions[CHIP_COLUMNS + 1] == (1, 1)

    # 多行也不越界：40 个图层 = 4 行 × 10 列
    _feed(panel, 40, psd="002.psd")
    positions = _positions(panel)
    assert len(positions) == 40
    assert positions == [(i // CHIP_COLUMNS, i % CHIP_COLUMNS) for i in range(40)]


def test_chips_never_widen_left_dock(tmp_path):
    window = MainWindow(SettingsManager(tmp_path / "settings.json"))
    window.resize(1440, 900)
    window.show()
    _settle()
    try:
        # 未打开任务时的左栏宽度 = 自然宽度（由文件列表的 sizeHint 决定）
        base_dock = window.left_dock.width()
        base_viewer = window.viewer.width()
        assert base_dock > 0 and base_viewer > 0

        for n in (CHIP_COLUMNS + 1, CHIP_COLUMNS + 2, 24, 40):
            _feed(window.stats_panel, n, psd=f"{n:03d}.psd")
            _settle()
            assert window.left_dock.width() == base_dock, f"{n} 图层把左栏撑宽了"
            assert window.viewer.width() == base_viewer, f"{n} 图层把画布挤窄了"
            # 芯片墙的最小宽度必须留在左栏自然宽度之内（否则 QGridLayout 会反撑 dock）
            assert (
                window.stats_panel.minimumSizeHint().width()
                <= window.file_panel.sizeHint().width()
            ), f"{n} 图层：芯片墙最小宽度超过左栏自然宽度"
    finally:
        window.close()

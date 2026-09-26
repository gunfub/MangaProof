# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""自动显示比例：档位表 + bg / bg 拷贝 的独立比例（需求 §20、§20.2）。

三件事必须钉住：

1. **档位表**：`DISPLAY_RATIOS` 含 10% 与 100%，两处下拉（工具栏 / 设置页）用的是
   同一份，且设置页对"手工写进设置文件的非档位合法值"仍会补一项显示；
2. **解析规则**（`camera.zoom.resolve_display_ratio`，纯函数）：
   - 功能关闭 → 一律全局比例（默认行为与旧版完全一致）；
   - `bg` 优先（`bg_layer_id` 已含"没有 bg 名 → 最底部有内容图层"的既有兜底）；
   - `bg 拷贝`（`bg 拷贝` / `bg copy`，大小写不敏感）**没有就不套用、也不兜底**；
   - 其余图层 → 全局比例；
3. **接线**：`MainWindow._effective_display_ratio()` 把设置与文档的图层 id 喂给上面
   那条规则；功能关闭时**一次图层查询都不做**（`bg_layer_id()` 在没有 bg 名的文件上
   会触发内容探测）。

另含设置文件的读写往返（三个新字段）与设置页控件状态（关闭时置灰、禁滚轮）。

运行：QT_QPA_PLATFORM=offscreen uv run python -m pytest tests/test_display_ratio.py -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from mangaproof.camera.zoom import resolve_display_ratio  # noqa: E402
from mangaproof.config.settings import (  # noqa: E402
    DEFAULT_BG_COPY_DISPLAY_RATIO,
    DEFAULT_BG_DISPLAY_RATIO,
    DEFAULT_BG_RATIO_ENABLED,
    DEFAULT_DISPLAY_RATIO,
    DISPLAY_RATIOS,
    Settings,
    SettingsManager,
)
from mangaproof.psd.document import BG_COPY_LAYER_NAMES, PSDDocument  # noqa: E402

DATA_DIR = Path(__file__).parent / "data" / "chapter01"
FIXTURE = DATA_DIR / "001.psd"

app = QApplication.instance() or QApplication([])


# --------------------------------------------------------------------------- #
# ① 档位表
# --------------------------------------------------------------------------- #


def test_display_ratios_cover_ten_and_hundred_percent():
    assert DISPLAY_RATIOS[0] == 0.1, "2026-09-25：新增 10% 档"
    assert DISPLAY_RATIOS[-1] == 1.0, "2026-09-25：新增 100% 档（最长边铺满视口）"
    assert DISPLAY_RATIOS == sorted(DISPLAY_RATIOS), "档位必须升序"
    assert len(set(DISPLAY_RATIOS)) == len(DISPLAY_RATIOS), "档位不许重复"
    assert DEFAULT_DISPLAY_RATIO in DISPLAY_RATIOS, "默认值必须在档位表里"


def test_both_combos_use_the_same_preset_list(tmp_path):
    """工具栏与设置页必须共享同一份档位（历史上就是这么共用的）。"""
    from mangaproof.ui.main_window import MainWindow
    from mangaproof.ui.settings_dialog import SettingsDialog

    sm = SettingsManager(tmp_path / "settings.json")
    window = MainWindow(sm)
    dialog = SettingsDialog(sm.settings)
    try:
        toolbar_items = [
            window.ratio_combo.itemData(i) for i in range(window.ratio_combo.count())
        ]
        dialog_items = [
            dialog.ratio_combo.itemData(i) for i in range(dialog.ratio_combo.count())
        ]
        assert toolbar_items == list(DISPLAY_RATIOS)
        assert dialog_items == list(DISPLAY_RATIOS)
        assert 0.1 in toolbar_items and 1.0 in toolbar_items
    finally:
        dialog.deleteLater()
        window.close()


def test_dialog_keeps_an_off_list_ratio_visible(tmp_path):
    """设置文件里手工写的非档位合法值（如 150%）不能被显示成别的档位。"""
    from mangaproof.ui.settings_dialog import SettingsDialog

    settings = Settings()
    settings.layer_display_ratio = 1.5
    dialog = SettingsDialog(settings)
    try:
        assert dialog.ratio_combo.currentData() == 1.5
        assert dialog.ratio_combo.itemText(dialog.ratio_combo.currentIndex()) == "150%"
    finally:
        dialog.deleteLater()


# --------------------------------------------------------------------------- #
# ② 解析规则（纯函数）
# --------------------------------------------------------------------------- #


RULES = dict(global_ratio=0.6, bg_ratio=1.0, bg_copy_ratio=0.8)


def test_disabled_feature_always_uses_the_global_ratio():
    for layer in ("bg-id", "copy-id", "text-id", None):
        assert resolve_display_ratio(
            **RULES, per_layer_enabled=False, layer_id=layer,
            bg_layer_id="bg-id", bg_copy_layer_id="copy-id",
        ) == 0.6


def test_enabled_feature_maps_bg_and_copy_and_falls_back_to_global():
    kwargs = dict(per_layer_enabled=True, bg_layer_id="bg-id", bg_copy_layer_id="copy-id")
    assert resolve_display_ratio(**RULES, layer_id="bg-id", **kwargs) == 1.0
    assert resolve_display_ratio(**RULES, layer_id="copy-id", **kwargs) == 0.8
    assert resolve_display_ratio(**RULES, layer_id="text-id", **kwargs) == 0.6
    assert resolve_display_ratio(**RULES, layer_id=None, **kwargs) == 0.6


def test_missing_bg_copy_layer_is_not_fallback():
    """「bg 拷贝」没有就没有：不给它兜底，也不会把别的图层当成它。"""
    assert resolve_display_ratio(
        **RULES, per_layer_enabled=True, layer_id="copy-id",
        bg_layer_id="bg-id", bg_copy_layer_id=None,
    ) == 0.6
    # 没有 bg 拷贝、也没有 bg（极端文件）→ 仍然全局
    assert resolve_display_ratio(
        **RULES, per_layer_enabled=True, layer_id="any",
        bg_layer_id=None, bg_copy_layer_id=None,
    ) == 0.6


def test_bg_wins_when_the_fallback_layer_is_also_named_bg_copy():
    """没有 bg 名时兜底可能选中「bg 拷贝」那一层：它按 bg 的独立比例显示。"""
    assert resolve_display_ratio(
        **RULES, per_layer_enabled=True, layer_id="same",
        bg_layer_id="same", bg_copy_layer_id="same",
    ) == 1.0


# --------------------------------------------------------------------------- #
# ③ bg 拷贝 的图层名匹配
# --------------------------------------------------------------------------- #


def test_bg_copy_names_accept_chinese_and_english_spellings():
    assert set(BG_COPY_LAYER_NAMES) == {"bg 拷贝", "bg copy"}


@pytest.mark.parametrize(
    "name, expected",
    [
        ("bg 拷贝", True),
        ("bg copy", True),
        ("BG COPY", True),        # 大小写不敏感（英文写法）
        ("Bg Copy", True),
        ("bg copy 2", False),     # 多次复制不认
        ("bg 拷贝 2", False),
        ("background", False),
        ("bg", False),            # bg 本身走 bg_layer_id，不是拷贝层
    ],
)
def test_bg_copy_matching_uses_name_only(name: str, expected: bool):
    """只按名字匹配（不做内容探测）：用鸭子类型的 self 直接调那个方法。"""
    fake_doc = SimpleNamespace(layers=[SimpleNamespace(id="x", name=name)])
    found = PSDDocument.bg_copy_layer_id(fake_doc)
    assert (found == "x") is expected


def test_bg_copy_matching_returns_none_without_that_layer():
    fake_doc = SimpleNamespace(layers=[SimpleNamespace(id="a", name="bg"),
                                       SimpleNamespace(id="b", name="文字层")])
    assert PSDDocument.bg_copy_layer_id(fake_doc) is None


@pytest.mark.skipif(not FIXTURE.is_file(), reason="缺少 chapter01 样本 PSD")
def test_real_psd_exposes_both_layers():
    """真实样本里 bg 与 bg 拷贝 都是不同图层，且都能被认出来。"""
    doc = PSDDocument(FIXTURE)
    names = {info.id: info.name for info in doc.layers}

    bg_id = doc.bg_layer_id()
    copy_id = doc.bg_copy_layer_id()
    assert names[bg_id] == "bg"
    assert names[copy_id] == "bg 拷贝"
    assert bg_id != copy_id


# --------------------------------------------------------------------------- #
# ④ 设置文件往返与设置页控件
# --------------------------------------------------------------------------- #


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_bg_ratio_defaults_are_off_and_hundred_percent(tmp_path):
    manager = SettingsManager(tmp_path / "settings.json")
    s = manager.settings
    assert s.bg_ratio_enabled is DEFAULT_BG_RATIO_ENABLED is False
    assert s.bg_display_ratio == DEFAULT_BG_DISPLAY_RATIO == 1.0
    assert s.bg_copy_display_ratio == DEFAULT_BG_COPY_DISPLAY_RATIO == 1.0


def test_bg_ratio_roundtrip_and_validation(tmp_path):
    path = tmp_path / "settings.json"
    _write(path, {
        "bg_ratio_enabled": True,
        "bg_display_ratio": 0.9,
        "bg_copy_display_ratio": 1.5,      # 档位表之外但合法 → 尊重
    })
    s = SettingsManager(path).settings
    assert s.bg_ratio_enabled is True
    assert s.bg_display_ratio == 0.9
    assert s.bg_copy_display_ratio == 1.5

    # 存回去：三个字段都要落盘
    SettingsManager(path).save()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["bg_ratio_enabled"] is True
    assert saved["bg_display_ratio"] == 0.9
    assert saved["bg_copy_display_ratio"] == 1.5

    # 非法值 → 各自回默认，不影响其它字段
    _write(path, {"bg_ratio_enabled": "yes", "bg_display_ratio": "abc",
                  "bg_copy_display_ratio": 99})
    s2 = SettingsManager(path).settings
    assert s2.bg_ratio_enabled is True          # bool("yes")
    assert s2.bg_display_ratio == DEFAULT_BG_DISPLAY_RATIO
    assert s2.bg_copy_display_ratio == DEFAULT_BG_COPY_DISPLAY_RATIO


def test_dialog_greys_out_the_per_layer_controls(tmp_path):
    from mangaproof.ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog(Settings())
    try:
        assert not dialog.bg_ratio_combo.isEnabled(), "默认关：bg 下拉必须置灰"
        assert not dialog.bg_copy_ratio_combo.isEnabled(), "默认关：拷贝下拉必须置灰"

        dialog.bg_ratio_check.setChecked(True)
        assert dialog.bg_ratio_combo.isEnabled() and dialog.bg_copy_ratio_combo.isEnabled()

        dialog.bg_ratio_combo.setCurrentIndex(dialog.bg_ratio_combo.findData(0.9))
        dialog.bg_copy_ratio_combo.setCurrentIndex(
            dialog.bg_copy_ratio_combo.findData(1.0)
        )
        out = Settings()
        dialog.apply_to(out)
        assert (out.bg_ratio_enabled, out.bg_display_ratio, out.bg_copy_display_ratio) \
            == (True, 0.9, 1.0)

        dialog._reset_defaults()
        assert dialog.bg_ratio_check.isChecked() is False
        assert dialog.bg_ratio_combo.currentData() == DEFAULT_BG_DISPLAY_RATIO
        assert dialog.bg_copy_ratio_combo.currentData() == DEFAULT_BG_COPY_DISPLAY_RATIO
        assert not dialog.bg_ratio_combo.isEnabled(), "复位后要重新置灰"
    finally:
        dialog.deleteLater()


def test_dialog_combos_ignore_the_wheel():
    """置灰之外还要"禁用滚轮改值"：两个下拉都必须是 NoWheelComboBox。"""
    from mangaproof.ui.settings_dialog import NoWheelComboBox, SettingsDialog

    dialog = SettingsDialog(Settings())
    try:
        assert isinstance(dialog.bg_ratio_combo, NoWheelComboBox)
        assert isinstance(dialog.bg_copy_ratio_combo, NoWheelComboBox)
    finally:
        dialog.deleteLater()


# --------------------------------------------------------------------------- #
# ⑤ 主窗口接线
# --------------------------------------------------------------------------- #


class _FakeDoc:
    """只实现解析需要的三个东西（图层表 + 两个 id 查询）。"""

    def __init__(self) -> None:
        self.layers = [SimpleNamespace(id="bg-id", name="bg"),
                       SimpleNamespace(id="copy-id", name="bg 拷贝"),
                       SimpleNamespace(id="text-id", name="文字层")]
        self.bg_queries = 0
        self.copy_queries = 0

    def bg_layer_id(self):
        self.bg_queries += 1
        return "bg-id"          # 与 layers 里的 id 对齐（真实文档返回的是 id）

    def bg_copy_layer_id(self):
        self.copy_queries += 1
        return "copy-id"


def _window_with(tmp_path: Path, doc: _FakeDoc):
    """造一个带"当前文档"的主窗口：`current_doc` 是只读属性，按它内部那张表注入。"""
    from mangaproof.ui.main_window import MainWindow

    sm = SettingsManager(tmp_path / "settings.json")
    window = MainWindow(sm)
    window._current_file = "fake.psd"
    window._docs["fake.psd"] = doc
    return window


def test_window_uses_per_layer_ratios_when_enabled(tmp_path):
    doc = _FakeDoc()
    window = _window_with(tmp_path, doc)
    try:
        window.settings.layer_display_ratio = 0.6
        window.settings.bg_ratio_enabled = True
        window.settings.bg_display_ratio = 1.0
        window.settings.bg_copy_display_ratio = 0.8

        by_id = {info.id: info for info in doc.layers}
        assert window._effective_display_ratio(by_id["bg-id"]) == 1.0
        assert window._effective_display_ratio(by_id["copy-id"]) == 0.8
        assert window._effective_display_ratio(by_id["text-id"]) == 0.6
    finally:
        window.close()
        app.processEvents()


def test_window_does_not_query_layers_while_the_feature_is_off(tmp_path):
    """默认关：一次图层查询都不做（bg_layer_id 可能触发内容探测）。"""
    doc = _FakeDoc()
    window = _window_with(tmp_path, doc)
    try:
        window.settings.bg_ratio_enabled = False
        window.settings.layer_display_ratio = 0.4

        for info in doc.layers:
            assert window._effective_display_ratio(info) == 0.4
        assert doc.bg_queries == 0 and doc.copy_queries == 0
    finally:
        window.close()
        app.processEvents()


def test_window_toolbar_still_only_changes_the_global_ratio(tmp_path):
    """工具栏不提供独立数值：改下拉只动全局比例，两个独立值保持不变。"""
    doc = _FakeDoc()
    window = _window_with(tmp_path, doc)
    try:
        window.settings.bg_ratio_enabled = True
        window.settings.bg_display_ratio = 1.0
        window.settings.bg_copy_display_ratio = 0.8

        window.settings.layer_display_ratio = 0.2
        window.ratio_combo.setCurrentIndex(window.ratio_combo.findData(0.2))
        app.processEvents()

        assert window.settings.layer_display_ratio == 0.2
        assert window.settings.bg_display_ratio == 1.0
        assert window.settings.bg_copy_display_ratio == 0.8
    finally:
        window.close()
        app.processEvents()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

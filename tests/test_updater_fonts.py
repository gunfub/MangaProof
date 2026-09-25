# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""安装器内置字体单测（调研报告 §11.2）。

Tk 没法像 Qt 那样"加载字体文件"，只能按平台把字体注册进本进程；这一层最容易
悄悄退化，所以用测试钉住四件事：

1. **家族名候选必须与真实字体文件一致** —— 直接解析 ``MiSans-Medium.ttf`` 的 name
   表（stdlib ``struct``，不引入依赖）：nameID16=``MiSans``、nameID1=``MiSans Medium``。
   Windows 的 GDI 只认前者之外的 legacy 名，漏了任何一个都会"注册成功但 Tk 看不到"；
2. 文件定位：冻结态 ``sys._MEIPASS/font/`` 优先，源码态回落到仓库 ``font/``；
3. 平台后端分派与**失败降级**：缺文件、后端抛异常、未知平台都不能影响安装器；
4. ``pick_family`` 先注册再在 Tk 家族表里核对，命中候选名才返回 —— 注册成功但
   Tk 看不到（例如没有 Xft 的 Tk）时返回 ``None``，由 UI 回退系统字体链。

不启动 GUI、不依赖真实 ctypes（后端可注入）、不改任何系统字体配置。
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

from updater import fonts

ROOT = Path(__file__).parent.parent
FONT_FILE = ROOT / "font" / fonts.FONT_FILENAME


def _name_table(path: Path) -> dict[tuple[int, int], str]:
    """读 TTF 的 name 表：``{(platform_id, name_id): 文本}``（只解 UTF-16BE 记录）。"""
    data = path.read_bytes()
    num_tables = struct.unpack(">H", data[4:6])[0]
    tables: dict[bytes, tuple[int, int]] = {}
    offset = 12
    for _ in range(num_tables):
        tag, _checksum, table_offset, length = struct.unpack(">4sIII", data[offset:offset + 16])
        tables[tag] = (table_offset, length)
        offset += 16
    name_offset, _name_length = tables[b"name"]
    _fmt, count, string_offset = struct.unpack(">HHH", data[name_offset:name_offset + 6])
    found: dict[tuple[int, int], str] = {}
    for index in range(count):
        base = name_offset + 6 + index * 12
        platform, encoding, language, name_id, length, text_offset = struct.unpack(
            ">HHHHHH", data[base:base + 12]
        )
        raw = data[name_offset + string_offset + text_offset:
                   name_offset + string_offset + text_offset + length]
        if platform == 3 and encoding == 1 and language == 0x409:  # Windows/Unicode/英文
            found[(platform, name_id)] = raw.decode("utf-16-be")
    return found


def test_family_candidates_match_the_real_font_name_table():
    """候选家族名必须覆盖 GDI（nameID1）与 CoreText/fontconfig（nameID16）两套口径。"""
    assert FONT_FILE.is_file(), f"随包字体缺失：{FONT_FILE}"
    names = _name_table(FONT_FILE)

    legacy = names[(3, 1)]      # Windows GDI 用的家族名
    typographic = names[(3, 16)]  # 排版家族名（CoreText / fontconfig / Qt）
    assert legacy == "MiSans Medium", legacy
    assert typographic == "MiSans", typographic

    assert fonts.FAMILY_CANDIDATES == (typographic, legacy), (
        "候选家族名与字体文件的 name 表不一致：会「注册成功但 Tk 找不到字体」"
    )
    # 顺序即优先级：先试排版家族（Linux/macOS），再试 GDI 口径
    assert fonts.FAMILY_CANDIDATES[0] == typographic


def test_font_candidates_prefers_the_frozen_bundle(tmp_path: Path, monkeypatch):
    """打包后字体在 sys._MEIPASS/font/ 下（spec 的 datas 决定），必须优先命中。"""
    bundle = tmp_path / "_MEI123" / "font"
    bundle.mkdir(parents=True)
    (bundle / fonts.FONT_FILENAME).write_bytes(b"fake")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "_MEI123"), raising=False)

    candidates = fonts.font_candidates()

    assert candidates[0] == bundle / fonts.FONT_FILENAME
    assert fonts.find_font_path() == bundle / fonts.FONT_FILENAME


def test_font_candidates_falls_back_to_the_source_tree(monkeypatch):
    """源码 / 开发态：仓库根的 font/ 就是那一份（与主程序同源）。"""
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)

    candidates = fonts.font_candidates()

    assert candidates[0] == ROOT / "font" / fonts.FONT_FILENAME
    assert fonts.find_font_path() == FONT_FILE


def test_register_bundled_uses_the_platform_backend(monkeypatch, tmp_path: Path):
    """按平台分派后端，并把成功注册的文件报回来。"""
    font = tmp_path / fonts.FONT_FILENAME
    font.write_bytes(b"fake")
    called: list[Path] = []

    def backend(path: Path) -> bool:
        called.append(path)
        return True

    done = fonts.register_bundled(candidates=[font], platform="linux", backend=backend)

    assert done == [font] and called == [font]


def test_register_bundled_swallows_backend_errors(tmp_path: Path, caplog):
    """后端抛异常（缺 libfontconfig / API 不存在）→ 只记日志，绝不抛给调用方。"""
    font = tmp_path / fonts.FONT_FILENAME
    font.write_bytes(b"fake")

    def boom(path: Path) -> bool:
        raise OSError("libfontconfig.so.1: cannot open shared object file")

    with caplog.at_level("WARNING", logger="mangoproof.updater.fonts"):
        done = fonts.register_bundled(candidates=[font], platform="linux", backend=boom)

    assert done == []
    assert any("回退系统字体" in record.getMessage() for record in caplog.records)


def test_register_bundled_without_files_is_a_noop(tmp_path: Path):
    assert fonts.register_bundled(candidates=[tmp_path / "missing.ttf"], platform="linux") == []


def test_unknown_platform_never_registers(tmp_path: Path):
    font = tmp_path / fonts.FONT_FILENAME
    font.write_bytes(b"fake")

    assert fonts.register_bundled(candidates=[font], platform="android") == []
    assert fonts.backend_for("android")(font) is False


def test_pick_family_verifies_against_the_tk_family_list(monkeypatch):
    """注册成功还不够：必须在 Tk 家族表里真的看到候选名才算可用。"""
    monkeypatch.setattr(fonts, "_REGISTERED", False)
    monkeypatch.setattr(fonts, "register_bundled", lambda **_kw: [])

    # GDI 口径（Windows）
    assert fonts.pick_family(None, families=("MiSans Medium", "Microsoft YaHei UI")) \
        == "MiSans Medium"
    # 排版家族口径（Linux / macOS）
    assert fonts.pick_family(None, families=["MiSans", "Noto Sans CJK SC"]) == "MiSans"
    # 大小写不敏感（不同平台/版本的写法差异）
    assert fonts.pick_family(None, families=["misans"]) == "misans"
    # 注册成功但 Tk 看不到（例如 Tk 没编 Xft）→ None，由 UI 回退系统字体
    assert fonts.pick_family(None, families=("Noto Sans CJK SC",)) is None


def test_pick_family_registers_once(monkeypatch, tmp_path: Path):
    """注册只做一次（一个进程一份字体），重复调用不再碰 ctypes。"""
    font = tmp_path / fonts.FONT_FILENAME
    font.write_bytes(b"fake")
    calls: list[Path] = []

    def backend(path: Path) -> bool:
        calls.append(path)
        return True

    monkeypatch.setattr(fonts, "_REGISTERED", False)
    for _ in range(3):
        fonts.pick_family(
            None, families=("MiSans",), candidates=[font], platform="linux", backend=backend
        )

    assert calls == [font]


def test_pick_family_without_tk_returns_none(monkeypatch):
    """还没有 Tk（无图形会话）→ 直接 None，不注册、不抛异常。"""
    monkeypatch.setattr(fonts, "_REGISTERED", False)
    registered: list[object] = []
    monkeypatch.setattr(fonts, "register_bundled", lambda **_kw: registered.append(True))

    assert fonts.pick_family(None) is None
    assert registered == []


def test_reset_cache_makes_registration_possible_again(monkeypatch, tmp_path: Path):
    font = tmp_path / fonts.FONT_FILENAME
    font.write_bytes(b"fake")
    monkeypatch.setattr(fonts, "_REGISTERED", True)
    fonts.register_bundled(candidates=[font], platform="linux", backend=lambda _p: True)
    fonts._reset_cache()

    assert fonts._REGISTERED is False


def test_platform_backend_names(monkeypatch):
    """三个桌面平台各有后端；未知平台落到兜底（返回 False 而不是抛异常）。"""
    assert fonts.backend_for("windows") is fonts._register_windows
    assert fonts.backend_for("linux") is fonts._register_linux
    assert fonts.backend_for("macos") is fonts._register_macos
    assert fonts.backend_for("plan9") is fonts._register_unsupported


@pytest.mark.skipif(sys.platform != "linux", reason="只有 Linux 用 fontconfig 后端")
def test_linux_backend_registers_the_bundled_font_for_this_process():
    """真机验证 Linux 后端：注册后 fontconfig 必须能把 MiSans 解析到这一份文件。

    走的是与 Tk/Xft 完全相同的 fontconfig 默认配置（进程内，不动系统字体目录）。
    """
    import ctypes

    if not FONT_FILE.is_file():
        pytest.skip(f"随包字体不在源码树里：{FONT_FILE}")
    try:
        fontconfig = ctypes.CDLL("libfontconfig.so.1")
    except OSError as exc:  # pragma: no cover - 极简环境才没有 fontconfig
        pytest.skip(f"没有 libfontconfig：{exc}")

    assert fonts.register_bundled(platform="linux") == [FONT_FILE], (
        "随包字体应该注册成功（本机 fontconfig 可用）"
    )

    fontconfig.FcInit()
    fontconfig.FcConfigGetCurrent.restype = ctypes.c_void_p
    fontconfig.FcNameParse.argtypes = [ctypes.c_char_p]
    fontconfig.FcNameParse.restype = ctypes.c_void_p
    fontconfig.FcFontMatch.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    fontconfig.FcFontMatch.restype = ctypes.c_void_p
    fontconfig.FcPatternGetString.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(ctypes.c_char_p),
    ]
    config = fontconfig.FcConfigGetCurrent()
    pattern = fontconfig.FcNameParse(b":family=MiSans")
    # 结果指针不能传 NULL：fontconfig 的 FcFontMatch 会直接 assert 掉（实测）
    matched = fontconfig.FcFontMatch(config, ctypes.c_void_p(pattern), ctypes.byref(ctypes.c_int(0)))
    assert matched, "fontconfig 没有匹配到任何字体（预期是刚注册的 MiSans）"

    value = ctypes.c_char_p()
    assert fontconfig.FcPatternGetString(
        ctypes.c_void_p(matched), b"file", 0, ctypes.byref(value)
    ) == 0
    assert Path(value.value.decode()).resolve() == FONT_FILE.resolve(), (
        "MiSans 没有解析到随包的那一份字体文件"
    )

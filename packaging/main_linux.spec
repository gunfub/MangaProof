# -*- mode: python ; coding: utf-8 -*-
"""MangaProof - Linux PyInstaller 打包配置（onedir）。

控制台策略：Linux 无独立控制台窗口，console=False（windowed）：
文件管理器双击启动无终端输出；从终端运行则输出保留在终端。
图标：PyInstaller 不支持向 ELF 嵌入图标，运行时窗口图标来自
随包分发的 ico/ico.png；桌面集成见 packaging/linux/mangaproof.desktop。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

SPEC_DIR = Path(SPECPATH)
ROOT = SPEC_DIR.parent

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "ico"), "ico"),
    ],
    hiddenimports=collect_submodules("psd_tools"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MangaProof",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                       # windowed：图形启动无终端
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="MangaProof",
)

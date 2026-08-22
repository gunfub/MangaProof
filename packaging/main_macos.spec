# -*- mode: python ; coding: utf-8 -*-
"""MangaProof - macOS PyInstaller 打包配置（.app bundle）。

控制台策略：macOS 无独立控制台窗口，console=False（windowed）：
Finder 双击启动无终端输出；从终端运行则输出保留在终端。
图标：ico/ico.icns（App Bundle 图标，含 11 个尺寸块）；
ico/ico.png 随包分发（运行时窗口图标）。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

SPEC_DIR = Path(SPECPATH)
ROOT = SPEC_DIR.parent

# 随包数据：ico/、font/、以及已构建的原生加速扩展（ctypes 按路径加载）
_datas = [
    (str(ROOT / "ico"), "ico"),
    (str(ROOT / "font"), "font"),
] + [
    (str(p), "mangaproof")
    for p in (ROOT / "mangaproof").glob("_psd_fast.so")
] + [
    (str(p), "mangaproof")
    for p in (ROOT / "mangaproof").glob("_psd_fast.pyd")
]

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=_datas,
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
    icon=str(ROOT / "ico" / "ico.icns"),
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

app = BUNDLE(
    coll,
    name="MangaProof.app",
    icon=str(ROOT / "ico" / "ico.icns"),
    bundle_identifier="com.mangaproof.app",
    info_plist={
        "NSHighResolutionCapable": True,
        "CFBundleShortVersionString": "1.0.0",
        "NSHumanReadableCopyright": "MangaProof",
        "LSMinimumSystemVersion": "11.0",
    },
)

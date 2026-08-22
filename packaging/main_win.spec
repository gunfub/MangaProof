# -*- mode: python ; coding: utf-8 -*-
"""MangaProof - Windows PyInstaller 打包配置（onedir）。

控制台策略（与 mangaproof/console.py 配合）：
- console=True：保留控制台子系统，运行时通过 Win32 ShowWindow 隐藏/恢复；
- 设置「打包产物隐藏控制台窗口」默认开启 → 启动即隐藏；
- 关闭该设置后控制台恢复显示（直接运行 py 时始终显示，不受影响）。
图标：ico/ico.ico（exe 图标）；ico/ico.png 随包分发（运行时窗口图标）。
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
        # 整个 ico/ 目录 → 程序目录/ico/（运行时窗口图标从 ico/ico.png 加载）
        (str(ROOT / "ico"), "ico"),
        # MiSans 字体 → 程序目录/font/（运行时统一字体加载）
        (str(ROOT / "font"), "font"),
    ],
    hiddenimports=collect_submodules("psd_tools"),  # psd-tools 大量惰性导入
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
    console=True,                        # 保留控制台子系统（运行时按设置隐藏/恢复）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "ico" / "ico.ico"),
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

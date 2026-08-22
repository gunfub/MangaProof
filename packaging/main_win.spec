# -*- mode: python ; coding: utf-8 -*-
"""MangaProof - Windows PyInstaller 打包配置（onedir）。

控制台策略（与 mangaproof/console.py 配合）：
- console=True：保留控制台子系统，运行时通过 Win32 FreeConsole/AllocConsole
  关闭/恢复；设置「打包产物关闭控制台窗口」默认开启 → 启动即消失；
- 直接运行 py 时始终显示，不受开关影响。
图标：ico/ico.ico（exe 图标）；ico/ico.png 随包分发（运行时窗口图标）。
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
    console=True,                        # 保留控制台子系统（运行时按设置关闭/恢复）
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

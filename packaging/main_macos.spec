# -*- mode: python ; coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""MangaProof - macOS PyInstaller 打包配置（.app bundle）。

控制台策略：macOS 无独立控制台窗口，console=False（windowed）：
Finder 双击启动无终端输出；从终端运行则输出保留在终端。
图标：ico/ico.icns（App Bundle 图标，含 11 个尺寸块）；
ico/ico.png 随包分发（运行时窗口图标）。

`ico.icns` 由 `scripts/make_icns.py` 从 `ico/ico.png` 生成，并已适配 macOS 图标网格：
1024 画布 / 824×824 实体居中 / 四边各 100px 透明环 / 圆角 184px（= 22.3% of 824）。
实测 Apple 自家 Xcode、iMovie 与 macOS 26 原生应用的图标实体框都是 (100,100,924,924)，
圆角按 184px 拟合 IoU ≈ 0.998 —— 直接用满幅源图切块会让 Dock 里的图标大出约 24%。
生成时会同时落盘 `ico/ico-macos-1024.png`（那张网格主图，便于评审与回归）。
回归测试见 tests/test_icon_assets.py（含"入库产物必须与脚本输出逐字节一致"）。
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

from mangaproof.update.platform import PLATFORM_MODULES

SPEC_DIR = Path(SPECPATH)
ROOT = SPEC_DIR.parent

# 随包数据：ico/、font/、以及已构建的原生加速扩展（ctypes 按路径加载）；
# 另收许可文本——GPLv3 §6 要求分发目标码时随附本许可副本与第三方许可清单，
# 运行时由「关于 → 许可证…」读取（见 mangaproof/app_license.py）。
_datas = [
    (str(ROOT / "ico"), "ico"),
    (str(ROOT / "font"), "font"),
    (str(ROOT / "LICENSE"), "licenses"),
    (str(ROOT / "THIRD_PARTY_LICENSES.md"), "licenses"),
] + [
    (str(p), "mangaproof")
    for p in (ROOT / "mangaproof").glob("_psd_fast.so")
] + [
    (str(p), "mangaproof")
    for p in (ROOT / "mangaproof").glob("_psd_fast.pyd")
]

# ---------------------------------------------------------------------------
# 更新安装器：与主程序可执行文件**同级同目录**（需求 1.2.1；调研报告 §11.3）
# ---------------------------------------------------------------------------
# 关键点（读 PyInstaller 源码得出，反直觉）：
#   COLLECT.assemble() 里只有 typecode == "EXECUTABLE"/"PKG" 的条目才落在 dist 根，
#   其余（含 BINARY）一律进 contents_directory（即 _internal/）；macOS 上 BINARY
#   还会被 BUNDLE 归到 Contents/Frameworks。所以**不能**走 binaries=(…, ".")。
#   用裸 TOC 三元组 + "EXECUTABLE" 才能得到：
#     · Windows/Linux → dist/MangaProof/MangaProof-update-installer[.exe]
#     · macOS         → MangaProof.app/Contents/MacOS/MangaProof-update-installer
#   该分支还会自动 chmod 0o755（Linux/macOS 的执行位因此不用手工补）。
#
# 安装器先于主程序构建（CI 见 build.yml 的 "Build update installer"），
# 缺失即**构建失败** —— 绝不允许做出一个"能更新但没安装器"的发布包。
_installer_name = (
    "MangaProof-update-installer.exe" if sys.platform == "win32"
    else "MangaProof-update-installer"
)
_installer_candidates = []
if os.environ.get("MANGAPROOF_INSTALLER"):
    _installer_candidates.append(Path(os.environ["MANGAPROOF_INSTALLER"]))
_installer_candidates += [
    ROOT / "dist-installer" / _installer_name,
    ROOT / "packaging" / "installer-dist" / _installer_name,
]
_installer = next((p for p in _installer_candidates if p.is_file()), None)
if _installer is None:
    raise SystemExit(
        "缺少更新安装器：%s\n"
        "请先构建：uv run pyinstaller --noconfirm --clean packaging/installer.spec "
        "--distpath dist-installer\n"
        "（CI 由 build.yml 上一步构建；也可用环境变量 MANGAPROOF_INSTALLER 指定路径）"
        % _installer_name
    )

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=_datas,
    hiddenimports=[
        # psd-tools 大量惰性导入
        *collect_submodules("psd_tools"),
        # 平台模块由 update/platform/__init__.py 里拼接字符串运行时导入，
        # 静态分析看不见 —— 不显式声明就会打出"点安装报 No module named
        # 'mangaproof.update.platform.windows'"的残废包（需求 §6/§7）
        *(f"mangaproof.update.platform.{name}" for name in PLATFORM_MODULES),
    ],
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
    # 更新安装器：dest 名 = 文件名 → 落在 dist 根（macOS 则是 Contents/MacOS）
    [(_installer_name, str(_installer), "EXECUTABLE")],
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
        "NSHumanReadableCopyright": "Copyright (C) 2026 gunfub. Licensed under GPL-3.0-only.",
        "LSMinimumSystemVersion": "11.0",
        # 系统原生面板（访达式文件选择器）的界面语言跟随 app 的本地化，
        # 而不是跟随系统语言。PyInstaller 产物没有任何 .lproj，不显式声明的话
        # macOS 按"只支持开发区域语言"处理，中文系统上给出的是英文面板。
        # 只声明简体中文；非中文系统由 CFBundleDevelopmentRegion 兜底为英文。
        "CFBundleLocalizations": ["zh-Hans", "zh-CN"],
        "CFBundleDevelopmentRegion": "en",
        # Qt 官方 macOS 模板同样带这一项：允许从系统 framework 取本地化资源
        "CFBundleAllowMixedLocalizations": True,
    },
)

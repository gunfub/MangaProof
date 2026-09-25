# -*- mode: python ; coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""MangaProof 更新安装器 —— PyInstaller **onefile** 打包配置（需求 §43/§44）。

**由 CI 按平台构建**（``.github/workflows/build.yml`` 的 "Build update installer"
步骤，Windows/Linux/macOS 各构建一次）::

    uv run pyinstaller --noconfirm --clean packaging/installer.spec \
        --distpath dist-installer --workpath build-installer

CI 随后把 ``dist-installer/MangaProof-update-installer[.exe]`` 通过
``MANGAPROOF_INSTALLER`` 传给 ``packaging/main_*.spec``，由主程序 spec 用
裸 TOC ``("EXECUTABLE")`` 放到发布包根目录 —— 安装器与主程序可执行文件
**同级同目录**（调研报告 §11.3），因此新旧版本天然同版本、§61 的清理清单闭合。

为什么是 onefile（而不是 onedir）：

- 安装器必须能从临时目录运行（需求 §40：不得直接从旧程序目录运行），onefile
  只有一个文件，主程序复制/移动它的成本最低；
- 安装器必须完全独立于主程序运行环境（需求 §43）——onefile 自带解释器与
  标准库，不依赖主程序目录里的 ``_internal/``。

``console=False``：GUI 是 Tkinter（需求 §43）。注意 windowed onefile 下
``sys.stdout``/``sys.stderr`` 可能是 ``None``，安装器已在 ``updater/main.py``
最早期把日志重定向到 ``installer-<token>.log``，这里不需要额外处理。

**绝对不要** ``excludes=["tkinter"]``：安装器的 GUI 就是 tkinter；缺 tkinter 时
安装器会优雅降级为控制台进度（``--cli`` 路径），但那是**运行期**的兜底，
不是构建期可以砍掉它的理由。CI 在构建前会显式检查解释器里有 tkinter。
"""

import sys
from pathlib import Path

SPEC_DIR = Path(SPECPATH)
ROOT = SPEC_DIR.parent

ENTRY = ROOT / "updater" / "main.py"
if not ENTRY.is_file():  # 构建期强校验：入口缺失直接失败，不产出"半个安装器"
    raise SystemExit(f"缺少安装器入口：{ENTRY}")

# 图标：仅 Windows 需要（PyInstaller 不支持向 ELF/Mach-O 嵌入窗口图标）
_icon = ROOT / "ico" / "ico.ico"
_icon_arg = str(_icon) if (sys.platform == "win32" and _icon.is_file()) else None

# 安装器只用到 mangaproof 里几个**零依赖**模块（config.user_data /
# utils.hashing / update.checksum / update.platform_dirs /
# utils.logging_setup）。显式声明 hiddenimports，避免静态分析漏掉
# （它们经由 updater.backup / updater.verify / updater.installer 间接导入）。
# 这几个模块都只 import 标准库，不会把 Qt/psd-tools 拖进 onefile。
_hiddenimports = [
    "mangaproof.config.user_data",
    "mangaproof.utils.hashing",
    "mangaproof.update.checksum",
    "mangaproof.update.platform_dirs",
    "mangaproof.utils.logging_setup",
]

# 明确排除主程序的重型依赖：安装器必须"完全独立，不依赖主程序运行环境"（§43），
# 误打包进去只会让 onefile 体积膨胀、启动变慢。tkinter **不在**此列。
_excludes = [
    "PySide6",
    "psd_tools",
    "numpy",
    "PIL",
    "reportlab",
    "httpx",
    "httpcore",
    "keyring",
    "pytest",
]

# 随包许可文本（GPLv3 §6）：安装器与主程序一起分发，因此也要随附许可副本与
# 第三方许可清单（与三份 main_*.spec 同一约定，tests/test_app_license.py 守卫）。
# 另带内置字体 MiSans：安装器 GUI 与主程序用同一份字体（调研报告 §11.2），
# 运行时由 updater/fonts.py 按平台注册**进本进程**（不写系统字体目录）。
_datas = [
    (str(ROOT / "LICENSE"), "licenses"),
    (str(ROOT / "THIRD_PARTY_LICENSES.md"), "licenses"),
    (str(ROOT / "font" / "MiSans-Medium.ttf"), "font"),
]

a = Analysis(
    [str(ENTRY)],
    pathex=[str(ROOT)],
    binaries=[],
    datas=_datas,
    hiddenimports=_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# onefile：EXE 直接收 binaries/datas，**不写 COLLECT**、exclude_binaries=False。
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="MangaProof-update-installer",   # Windows 由 PyInstaller 自动加 .exe
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                            # 明确关闭（需求：upx=False）
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                        # GUI（tkinter）；日志见 updater/main.py
    disable_windowed_traceback=False,
    argv_emulation=False,                 # macOS：不要 argv_emulation（会抢参数）
    target_arch=None,
    codesign_identity=None,               # macOS 签名/公证由发布侧负责（调研报告 §5.3 R3）
    entitlements_file=None,
    icon=_icon_arg,
    exclude_binaries=False,
)

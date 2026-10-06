#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""MangaProof → Android(APK) 构建包装脚本。

为什么需要它
------------
`pyside6-android-deploy` 有两个硬性行为（源码级事实，官方文档未写）：

1. `deploy_lib/android/buildozer.py` 把 `requirements` **写死**为
   `python3,shiboken6,PySide6` —— 没有任何配置项能追加应用侧依赖；
2. `deploy_util.py` 的 `cleanup()` 在**每次运行开头**就删除
   `<project>/buildozer.spec` 与 `<project>/deployment/`，因此
   「预置 spec」或「先 --init 再改」都会被清掉。

所以只能在**同一次运行内**、`BuildozerConfig` 写完 spec 之后、buildozer
启动之前，劫持该类做二次注入（本脚本做法），或直接 `sed` 打补丁安装目录。

另外：工具的 `main()` 会 `except Exception: print(traceback)` **吞掉异常**
（退出码仍为 0），所以本脚本在最后**必须自己断言 APK 存在**。

用法（在仓库根目录执行；CI 见 .github/workflows/android.yml）
-------------------------------------------------------------
    python scripts/android/build_android.py \
        --pyside-wheel  /path/pyside6-6.11.2-...-android_aarch64.whl \
        --shiboken-wheel /path/shiboken6-6.11.2-...-android_aarch64.whl \
        --ndk-path "$ANDROID_HOME/ndk/27.2.12479018" --sdk-path "$ANDROID_HOME" \
        --mode debug --arch aarch64 --apk-out "$GITHUB_ENV"
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECIPES_DIR = REPO_ROOT / "packaging" / "android" / "recipes"
#: p4a hook：往清单注入「不参与辅助功能」的 provider（见 packaging/android/p4a_hook.py）
P4A_HOOK_PATH = REPO_ROOT / "packaging" / "android" / "p4a_hook.py"
SPEC_PATH = REPO_ROOT / "pysidedeploy.spec"

# 打进 APK 的资源：font/ 的 ttf 与 ico/ 的 png 必须在列
SOURCE_INCLUDE_EXTS = "py,png,jpg,ttf,json,qml,js"
# 注意：这里**只放 ASCII 目录名**。PySide6 的 deploy_lib 写 buildozer.spec 用的是
# open(path, "w")（不带 encoding，依赖进程 locale），一旦 locale 不是 UTF-8，
# 写入非 ASCII（比如仓库里那个中文资料目录）就会 UnicodeEncodeError，而这个异常
# 会被工具的 try/except 吞掉、只留下 traceback。中文目录名本身已在 .gitignore 里，
# CI 检出根本不包含它，因此这里不需要（也不该）列它。
SOURCE_EXCLUDE_DIRS = ",".join([
    ".git", ".github", ".venv", ".uv-cache", ".pytest_cache", ".benchmarks",
    "deployment", "docs", "scripts", "tests", "packaging", "logs",
    "local_samples", "README.assets",
])

# 依赖名（uv.lock 中的名字）→ p4a requirements token
REQUIREMENT_TOKENS = {
    "numpy": "numpy",
    "pillow": "Pillow",
    "reportlab": "reportlab",
    "psd-tools": "psd-tools",
    "attrs": "attrs",
    "typing-extensions": "typing-extensions",
    "charset-normalizer": "charset-normalizer",
    # 自更新系统（需求 §8 统一 httpx）：token 必须与 packaging/android/recipes/
    # 下的**目录名**一致（p4a 的 Recipe.name 取自目录名，大小写不敏感匹配）。
    # keyring 及其子树**不在**此表：它们在 analyze_lock_deps.ANDROID_SKIP 里，
    # 根本不会出现在下面的闭包中（需求 §14：Android 不打包 keyring）。
    "httpx": "httpx",
    "httpcore": "httpcore",
    "h11": "h11",
    "anyio": "anyio",
    "certifi": "certifi",
    "idna": "idna",
    "socksio": "socksio",
}
QT_PACKAGES = {"pyside6", "pyside6-essentials", "pyside6-addons", "shiboken6"}

# 自适应图标资源（file:res 相对路径）。p4a 处理 --add-resource 的文件模式时会
# 先 ensure_dir(目录) 再复制，所以 mipmap-anydpi-v26/ 会被创建出来 —— 这正是
# 绕开 p4a"写 XML 却不建目录"那个 bug 的关键。
#   · mipmap/（无密度限定）= 兜底，保证 @mipmap/icon_foreground 在任何密度都能解析
#   · mipmap-xxxhdpi/ = 432×432 主图，高密度设备直接用原图（避免被当作 mdpi 放大）
RESOURCE_ENTRIES = ",".join([
    "ico/Android-foreground.png:mipmap/icon_foreground.png",
    "ico/Android-background.png:mipmap/icon_background.png",
    "ico/Android-foreground.png:mipmap-xxxhdpi/icon_foreground.png",
    "ico/Android-background.png:mipmap-xxxhdpi/icon_background.png",
    "ico/android/res/mipmap-anydpi-v26/icon.xml:mipmap-anydpi-v26/icon.xml",
    # 首屏（启动底色 + Logo）：首次启动要解包 Python 发行包，期间 Qt 还没画出第一帧，
    # 靠 Android 框架的 windowBackground 顶上（p4a 的 presplash 在 Qt bootstrap 下不生效）。
    # Logo 复用应用图标前景层（带透明边距）；drawable-nodpi = 不做密度缩放、按原像素居中。
    "ico/Android-foreground.png:drawable-nodpi/mangaproof_logo.png",
    "packaging/android/res/drawable/mangaproof_splash.xml:drawable/mangaproof_splash.xml",
    "packaging/android/res/values/colors.xml:values/colors.xml",
    "packaging/android/res/values/themes.xml:values/themes.xml",
    # 交给系统安装器的 APK 的可共享目录（需求 §65）。清单里那个 FileProvider 的
    # meta-data 指向 @xml/qtprovider_paths，缺了它构建期就会报资源找不到。
    "packaging/android/res/xml/qtprovider_paths.xml:xml/qtprovider_paths.xml",
])

#: Gradle 依赖（需求 §65）：`androidx.core.content.FileProvider` 所在的构件。
#:
#: 为什么**必须**显式依赖：清单里声明了这个 provider，Android 会在进程启动时
#: 立刻实例化它（先于任何 Activity）。类不在 APK 内 → ClassNotFoundException →
#: **启动即崩**；而这件事**构建期不会报错**（清单合并器不检查 provider 类是否存在），
#: 只会在真机上炸。2026-10-06 实测 v1.1.14.alpha 的 APK：6 个 dex 里
#: `Landroidx/` 一次都没出现（Qt/p4a 都不带 androidx）。
#:
#: 版本选择：1.13.1 的 minCompileSdk 是 34，本项目 compileSdk 35（见 ANDROID_API），
#: minSdk 30 ≥ 其要求，因此兼容；再高的版本要求 compileSdk 35，也能用，但没有必要。
ANDROIDX_CORE_DEP = "androidx.core:core:1.13.1"


def log(msg: str) -> None:
    print(f"[mangaproof-android] {msg}", flush=True)


def _force_utf8_locale() -> None:
    """把进程 locale 切到 UTF-8。

    部署工具在若干地方用 `open(path, "w")` 写文件（不带 encoding），行为取决于
    进程 locale；CI runner 的 locale 若不是 UTF-8，写入非 ASCII 内容会抛
    UnicodeEncodeError（且被工具的 try/except 吞掉）。这里在进程内兜一层，不依赖
    CI 环境变量（workflow 里也设了 PYTHONUTF8=1，属双保险）。
    """
    import locale  # noqa: PLC0415

    for candidate in ("C.UTF-8", "en_US.UTF-8", ""):
        try:
            locale.setlocale(locale.LC_ALL, candidate)
        except locale.Error:
            continue
        encoding = (locale.getpreferredencoding(False) or "").lower()
        if encoding.replace("-", "").startswith("utf8"):
            log(f"locale = {candidate or '(系统默认)'} / encoding = {encoding}")
            return
    log("⚠️ 无法将 locale 切到 UTF-8，非 ASCII 内容写盘可能失败")


def _prepare_pyside_scripts() -> None:
    """把 PySide6 的 scripts 目录放进 sys.path。

    `PySide6/scripts/android_deploy.py` 用的是**顶层**导入（`from deploy_lib import ...`），
    官方 CLI 是靠"脚本就在该目录里"才成立的；我们要在自己的进程内 monkeypatch，
    所以必须显式把该目录加进 sys.path。
    """
    import PySide6  # noqa: PLC0415

    scripts_dir = Path(PySide6.__file__).resolve().parent / "scripts"
    if not scripts_dir.is_dir():
        raise SystemExit(f"[build] 找不到 PySide6 scripts 目录：{scripts_dir}")
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


def _check_host_requirements() -> None:
    """工具在 CLI 入口会校验 requirements-android.txt；进程内调用要自己查一遍。"""
    missing = []
    for name in ("jinja2", "pkginfo", "tqdm", "packaging"):
        try:
            __import__(name)
        except ImportError:
            missing.append(name)
    if missing:
        raise SystemExit(
            "[build] 宿主环境缺少 Android 部署工具的依赖："
            f"{', '.join(missing)}\n"
            "        请安装：pip install pyside6 jinja2 pkginfo tqdm packaging==24.1"
        )


# ---------------------------------------------------------------------------
# 版本号
# ---------------------------------------------------------------------------

def project_version() -> str:
    init = (REPO_ROOT / "mangaproof" / "__init__.py").read_text(encoding="utf-8")
    for line in init.splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("[build] 无法从 mangaproof/__init__.py 解析 __version__")


def numeric_version(version: str) -> int:
    """major*10000 + minor*100 + patch（Android versionCode 必须为递增整数）。"""
    parts = (version.split(".") + ["0", "0", "0"])[:3]
    try:
        major, minor, patch = (int(p) for p in parts)
    except ValueError:
        major, minor, patch = 1, 0, 0
    return major * 10000 + minor * 100 + patch


# ---------------------------------------------------------------------------
# 运行时依赖闭包（uv.lock）
# ---------------------------------------------------------------------------

def lock_requirements() -> list[str]:
    """用 scripts/android/analyze_lock_deps.py 的 JSON 输出得到要打包的依赖。"""
    tools_dir = Path(__file__).resolve().parent
    sys.path.insert(0, str(tools_dir))
    import analyze_lock_deps  # noqa: PLC0415 （同目录脚本）

    result = analyze_lock_deps.analyze(REPO_ROOT / "uv.lock")
    tokens: list[str] = []
    unknown: list[str] = []
    for row in result["packages"]:
        name = row["name"]
        if name in QT_PACKAGES:
            continue                      # Qt 由工具自己写进 requirements
        token = REQUIREMENT_TOKENS.get(name)
        if token is None:
            unknown.append(name)
        else:
            tokens.append(token)
    if unknown:
        raise SystemExit(f"[build] 以下依赖没有 Android 归宿（请补 recipe 并更新脚本）：{unknown}")
    log(f"运行时依赖（来自 uv.lock）：{', '.join(tokens)}")
    return tokens


# ---------------------------------------------------------------------------
# pysidedeploy.spec / buildozer.spec
# ---------------------------------------------------------------------------

def write_pysidedeploy_spec(*, mode: str, arch: str) -> None:
    """预置 pysidedeploy.spec（工具存在该文件时不会重建），用于指定 mode/arch。"""
    spec = f"""[app]

# Title of your application
title = MangaProof

# Project root directory. Default: The parent directory of input_file
project_dir = {REPO_ROOT}

# Source file entry point path. Default: main.py
input_file = main.py

# Directory where the executable output is generated
exec_directory =

# Path to the project file relative to project_dir
project_file =

# Application icon（p4a 会把它放到 res/mipmap/icon.png；自适应图标由本脚本注入 buildozer.spec）
icon = ico/Android-fallback.png

[python]

python_path =

packages = Nuitka==4.1.1
android_packages = buildozer==1.5.0,cython==0.29.33

[qt]

modules =

[android]

wheel_pyside =
wheel_shiboken =
plugins =

[nuitka]

mode = onefile
extra_args = --quiet --noinclude-qt-translations

[buildozer]

mode = {mode}

recipe_dir =
jars_dir =
ndk_path =
sdk_path =
local_libs =
arch = {arch}
"""
    SPEC_PATH.write_text(spec, encoding="utf-8")
    log(f"已写入 {SPEC_PATH.relative_to(REPO_ROOT)}（mode={mode}, arch={arch}）")


def patch_buildozer_config(*, requirements: list[str], icons: dict[str, str],
                           version: str, api: int, min_api: int, ndk_api: int,
                           recipes_dir: Path | None) -> None:
    """劫持 BuildozerConfig：在官方写入之后做二次注入。"""
    _prepare_pyside_scripts()
    from deploy_lib.android import buildozer as bz_mod  # noqa: PLC0415

    original = bz_mod.BuildozerConfig

    class MangaProofBuildozerConfig(original):  # type: ignore[misc, valid-type]
        def __init__(self, spec_file, pysidedeploy_config):  # noqa: D401
            super().__init__(spec_file, pysidedeploy_config)
            cfg = pysidedeploy_config

            def put(section: str, key: str, value: str) -> None:
                """直接写 parser：default.spec 里被注释掉的键也能新增/覆盖。"""
                if not self.parser.has_section(section):
                    self.parser.add_section(section)
                self.parser.set(section, key, str(value))

            # 1) requirements（工具的硬编码行 + 应用运行时依赖）
            current = [t for t in (self.get_value("app", "requirements") or "").split(",") if t]
            merged = list(dict.fromkeys(current + requirements))
            put("app", "requirements", ",".join(merged))
            log(f"buildozer.spec requirements = {','.join(merged)}")

            # 2) 本地 p4a recipe：拷进工具刚建好的 deployment/recipes/
            if recipes_dir and recipes_dir.is_dir() and cfg.recipe_dir:
                shutil.copytree(recipes_dir, cfg.recipe_dir, dirs_exist_ok=True)
                log(f"已注入本地 recipe：{sorted(p.name for p in recipes_dir.iterdir())}")

            # 3) 打包范围（字体 ttf 必须包含；仓库垃圾必须排除）
            put("app", "source.include_exts", SOURCE_INCLUDE_EXTS)
            put("app", "source.exclude_dirs", SOURCE_EXCLUDE_DIRS)

            # 4) Android 平台参数
            put("app", "android.api", str(api))
            put("app", "android.minapi", str(min_api))
            put("app", "android.ndk_api", str(ndk_api))
            put("app", "android.accept_sdk_license", "True")
            put("app", "android.release_artifact", "apk")     # 本轮只出 APK（release 默认是 aab）
            put("app", "android.debug_artifact", "apk")
            put("app", "fullscreen", "1")                      # buildozer 默认 0
            put("app", "orientation", "landscape")             # buildozer 默认 portrait
            put("app", "android.manifest.orientation", "sensorLandscape")  # 允许左右横屏翻转
            # 应用 ID = package.domain + "." + package.name（buildozer 1.5.0
            # targets/android.py:1004-1007 的拼接规则）→ com.priloba.mangaproof。
            # 不设 domain 时 PySide6 部署工具会写 org.<Title>（org.MangaProof），
            # 所以这里必须显式覆盖。
            put("app", "package.domain", "com.priloba")
            put("app", "package.name", "mangaproof")           # 包名小写惯例
            put("app", "version", version)
            put("app", "android.numeric_version", str(numeric_version(version)))
            put("app", "icon.filename", icons["fallback"])
            # 首屏主题：fullscreen=1 时 p4a 会把该名字拼成 `<apptheme>.Fullscreen`
            # （见 packaging/android/res/values/themes.xml 的说明），所以两个变体都已定义。
            # ⚠️ 真正决定首屏的是 **Activity** 主题（p4a 模板硬编码 @style/KivySupportCutout，
            #    会覆盖 Application 主题）→ windowBackground 由 p4a_hook 注入到那个主题上。
            put("app", "android.apptheme", "@style/MangaProofSplash")

            # ⚠️ 刻意**不设** icon.adaptive_foreground/background.filename：
            #    p4a 的 bootstraps/common/build/build.py 生成自适应图标时会直接
            #      open('res/mipmap-anydpi-v26/icon.xml', "w")
            #    却从不创建该目录（SDL 模板里有、Qt 模板里没有，git 又不跟踪空目录）
            #    → 构建最后打包阶段必然 FileNotFoundError（run 34914938082 即如此）。
            #    自适应图标改由自带资源投放（见 RESOURCE_ENTRIES）。
            put("app", "android.add_resources", RESOURCE_ENTRIES)

            # 5) 权限叠加
            #    · MANAGE_EXTERNAL_STORAGE：全文件访问，Android 11+ 直接路径读写的关键；
            #    · REQUEST_INSTALL_PACKAGES：Android 8+ 调起系统安装器所必需（需求 §65）。
            #      缺它则 Intent 被系统拒绝；用户侧还要授予"安装未知应用"。
            perms = [p for p in (self.get_value("app", "android.permissions") or "").split(",") if p]
            perms.append("android.permission.MANAGE_EXTERNAL_STORAGE")
            perms.append("android.permission.REQUEST_INSTALL_PACKAGES")
            put("app", "android.permissions", ",".join(dict.fromkeys(perms)))

            # 5b) Gradle 依赖：androidx.core（**必填，缺了会崩**）
            #     需求 §65 的安装流程要在清单里声明 `androidx.core.content.FileProvider`
            #     （Qt 枚举清单里的该 provider 来把 file:// 换成 content://）。
            #     ⚠️ 清单里写了 provider，Android 会在**进程启动时**
            #     （ActivityThread.installContentProviders）就把它实例化 —— 类不在包内
            #     就是 ClassNotFoundException **启动即崩**，而且**构建期完全不报错**
            #     （清单合并器不校验 provider 类是否存在）。
            #     2026-10-06 实测：v1.1.14.alpha 的 APK 里 **一个 androidx 类都没有**
            #     （6 个 dex 全无 `Landroidx/`），所以这一步不是"预防"而是必需。
            #     buildozer 的 `android.gradle_dependencies` → p4a `--depend`
            #     → 模板里 `implementation '<dep>'`；模板已配 google()/mavenCentral()。
            gradle_deps = [
                d for d in (self.get_value("app", "android.gradle_dependencies") or "").split(",")
                if d
            ]
            gradle_deps.append(ANDROIDX_CORE_DEP)
            put("app", "android.gradle_dependencies",
                ",".join(dict.fromkeys(gradle_deps)))

            # 6) p4a 参数：刘海/挖孔区域可绘制（buildozer 无对应键）
            extra_args = (self.get_value("app", "p4a.extra_args") or "").strip()
            put("app", "p4a.extra_args", f"{extra_args} --display-cutout shortEdges".strip())

            # 7) p4a hook：注入「不参与辅助功能」的 provider。
            #    某些系统（实测 HyperOS）的读屏会在启动时并发查询 Qt 画布，撞上 Qt 的
            #    无障碍桥（BlockingQueuedConnection 回主线程）与死锁保护逻辑 → 死锁/崩溃。
            #    解法是用 Qt 官方开关 QT_ANDROID_DISABLE_ACCESSIBILITY=1（不触碰死锁保护器），
            #    但该变量必须由 Java 在**任何 Activity 之前**写入进程环境 → 用 ContentProvider。
            if P4A_HOOK_PATH.is_file():
                put("app", "p4a.hook", str(P4A_HOOK_PATH))
            else:
                raise FileNotFoundError(f"p4a hook 缺失：{P4A_HOOK_PATH}")

            self.update_config()

    bz_mod.BuildozerConfig = MangaProofBuildozerConfig


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="构建 MangaProof Android APK")
    parser.add_argument("--pyside-wheel", type=Path, required=True)
    parser.add_argument("--shiboken-wheel", type=Path, required=True)
    parser.add_argument("--ndk-path", type=Path)
    parser.add_argument("--sdk-path", type=Path)
    parser.add_argument("--name", default="MangaProof")
    parser.add_argument("--mode", choices=["debug", "release"], default="debug")
    parser.add_argument("--arch", choices=["aarch64", "x86_64"], default="aarch64")
    parser.add_argument("--api", type=int, default=35, help="targetSdk")
    parser.add_argument("--min-api", type=int, default=30, help="minSdk（全文件访问权限下限）")
    parser.add_argument("--ndk-api", type=int, default=30)
    parser.add_argument("--extra-requirements", default="",
                        help="额外 p4a requirements（逗号分隔，一般不需要）")
    parser.add_argument("--no-lock-deps", action="store_true",
                        help="不从 uv.lock 注入运行时依赖（仅调试用）")
    parser.add_argument("--recipes-dir", type=Path, default=DEFAULT_RECIPES_DIR)
    parser.add_argument("--keep-deployment-files", action="store_true")
    parser.add_argument("--apk-out", type=Path, help="把产物路径写入该文件（供 CI 读取）")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    os.chdir(REPO_ROOT)                                    # 工具要求 cwd 下有 main.py
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    _force_utf8_locale()
    _check_host_requirements()

    for label, path in (("pyside wheel", args.pyside_wheel), ("shiboken wheel", args.shiboken_wheel)):
        if not path.is_file():
            raise SystemExit(f"[build] {label} 不存在：{path}")

    version = project_version()
    requirements = [] if args.no_lock_deps else lock_requirements()
    if args.extra_requirements:
        requirements += [t.strip() for t in args.extra_requirements.split(",") if t.strip()]

    icons = {
        "fallback": "ico/Android-fallback.png",
        "foreground": "ico/Android-foreground.png",
        "background": "ico/Android-background.png",
        "adaptive_xml": "ico/android/res/mipmap-anydpi-v26/icon.xml",
    }
    for key, rel in icons.items():
        if not (REPO_ROOT / rel).is_file():
            raise SystemExit(f"[build] 图标缺失（{key}）：{rel}")

    write_pysidedeploy_spec(mode=args.mode, arch=args.arch)
    patch_buildozer_config(
        requirements=requirements, icons=icons, version=version,
        api=args.api, min_api=args.min_api, ndk_api=args.ndk_api,
        recipes_dir=args.recipes_dir if args.recipes_dir.is_dir() else None,
    )

    import android_deploy  # noqa: PLC0415  （顶层导入，与官方 CLI 一致；见 _prepare_pyside_scripts）

    log(f"调用 pyside6-android-deploy：mode={args.mode} arch={args.arch} "
        f"api={args.api} minapi={args.min_api}")
    android_deploy.main(
        name=args.name,
        pyside_wheel=args.pyside_wheel.resolve(),
        shiboken_wheel=args.shiboken_wheel.resolve(),
        ndk_path=args.ndk_path.resolve() if args.ndk_path else None,
        sdk_path=args.sdk_path.resolve() if args.sdk_path else None,
        config_file=SPEC_PATH,
        init=False,
        loglevel=logging.INFO if args.verbose else logging.WARNING,
        dry_run=False,
        # 只把应用代码当作"项目源码"来扫描 PySide 模块；构建脚本/文档/测试不参与，
        # 既避免 "Found 'import PySide6' in file 0" 之类的噪音，也更快。
        extra_ignore_dirs="scripts,packaging,docs,tests",
        keep_deployment_files=args.keep_deployment_files,
        force=True,                                        # 不在 venv 里也不交互提问
    )

    # 工具会吞掉异常 → 必须自己判成功
    apks = sorted(REPO_ROOT.glob("*.apk"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not apks:
        log("❌ 未找到 APK：工具内部异常被吞掉了，请查看上方日志（含 traceback）")
        return 1
    apk = apks[0]
    log(f"✅ 产物：{apk.name}（{apk.stat().st_size / 1048576:.1f} MiB）")
    if args.apk_out:
        args.apk_out.write_text(str(apk), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

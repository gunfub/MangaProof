# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""python-for-android hook：为 MangaProof 注入「进程环境预置」用的 ContentProvider。

这个 provider（`A11yEnvProvider`）在任何 Activity 之前、同一进程内写入两个环境
变量；两件事都必须在 Qt 启动前完成，理由分别如下。

为什么需要这个 hook
------------------
某些系统（实测 HyperOS）的读屏/辅助功能会在应用启动、Qt 主线程正在创建窗口时
并发查询界面；Qt 的 Android 无障碍桥每次查询都要 `BlockingQueuedConnection`
回到主线程（`androidjniaccessibility.cpp` 的 `runInObjectContext()`），于是撞上
Qt 的 `AndroidDeadlockProtector` → 死锁/崩溃。

解法用 Qt 的**官方开关**（不触碰死锁保护器）：在进程环境里设置
`QT_ANDROID_DISABLE_ACCESSIBILITY=1`，Qt 的
`QtAccessibilityDelegate.onAccessibilityStateChanged()` 会直接 return，
不再创建覆盖在 Qt 布局上的无障碍 View，系统根本不来查（源码见
qtbase:src/android/jar/src/org/qtproject/qt/android/QtAccessibilityDelegate.java:94）。

**`MANGAPROOF_SW_DP=<最小宽度 dp>`——界面缩放默认值按机型分档**

界面缩放要在 QApplication 之前写进 `QT_SCALE_FACTOR`（Qt 只在启动时读一次），
那一刻 PySide6 拿不到屏幕信息（没有 QJniObject，QScreen 也还不存在），所以由
Java 侧用 `DisplayMetrics` 算好 `min(宽,高) / density` 写进环境变量；Python 侧
据此把默认缩放分成"手机 0.55 / 折叠屏内屏与平板 0.75"（见
docs/Android端界面适配_缩放与菜单栏.md §2.1）。

而这个环境变量必须**在任何 Activity 之前**写入进程（该监听的注册与首次触发都在
QtLayout/Activity 初始化时）。Android 的生命周期保证 ContentProvider 早于所有
Activity（`ActivityThread.handleBindApplication()` 里 `installContentProviders()`
先于 Activity 创建），因此本 hook 做两件事：

1. 把 `packaging/android/java/.../A11yEnvProvider.java` 放进 dist 的 Gradle 源码集
   （`src/main/java/...`）——不用 `android.add_src`，避免与 p4a 的复制逻辑重复；
2. 在生成的 `AndroidManifest.xml` 的 `<application>` 内注入该 provider 声明。

hook 被调用的时机：p4a `toolchain.py` 在 `with current_directory(dist.dist_dir)` 里
依次调用 `before_apk_build` → 渲染清单/打包 → `after_apk_build` → `before_apk_assemble`
→ Gradle 组装。因此本模块：before_apk_build 只拷 Java（清单还没生成），
after_apk_build / before_apk_assemble 注入 provider 并**断言**成功。

本 hook 现在做三件事：① 注入 A11yEnvProvider；② 注入 FileProvider（把已下载的
APK 交给系统安装器，需求 §65）；③ 给 Activity 主题挂首屏背景
（底色 + Logo，解决首次启动解包 Python 期间的白屏/黑屏）。
此前还做过两件清单改造（入口 Activity 替换、extractNativeLibs 注入），均已移除，
原因见下文 `【已移除的两个清单改造】` 注释块。
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

# packaging/android/p4a_hook.py → parents[2] = 仓库根
REPO_ROOT = Path(__file__).resolve().parents[2]
#: Java 源码树根（`com/…` 结构，整体镜像进 Gradle 源码集）
JAVA_SRC_ROOT = REPO_ROOT / "packaging" / "android" / "java"
JAVA_REL = Path("com/mangaproof/a11y/A11yEnvProvider.java")
JAVA_SRC = JAVA_SRC_ROOT / JAVA_REL

PROVIDER_CLASS = "com.mangaproof.a11y.A11yEnvProvider"
# 授权名必须**全局唯一**（同一设备同时装着旧包 org.MangaProof.mangaproof 与本包
# com.priloba.mangaproof 时，若授权名相同会 INSTALL_FAILED_CONFLICTING_PROVIDER），
# 所以跟随应用 ID 一起改。
PROVIDER_AUTHORITY = "com.priloba.mangaproof.a11y.env"

#: p4a 的 Qt 模板给 Activity 硬编码的主题名（首屏背景要挂到它上面）
STYLE_NAME = "KivySupportCutout"

_PROVIDER_XML = (
    "\n        <!-- MangaProof: 在 Activity 之前把 QT_ANDROID_DISABLE_ACCESSIBILITY=1"
    " 与 MANGAPROOF_SW_DP（最小宽度 dp，供界面缩放按机型分档）写入进程环境"
    "（见 packaging/android/p4a_hook.py） -->\n"
    f'        <provider android:name="{PROVIDER_CLASS}"\n'
    f'                  android:authorities="{PROVIDER_AUTHORITY}"\n'
    '                  android:exported="false" />\n    '
)

#: Android 交给系统安装器用的 FileProvider（需求 §65）。
#:
#: **必须是 `androidx.core.content.FileProvider` 这个类本身，不能自建子类**：
#: Qt 的 Android 平台层是这样挑 authority 的（qandroidplatformservices.cpp，
#: getFileProviderAuthorities()）——
#:
#:     providerName.replace(".", "/").contains("androidx/core/content/FileProvider")
#:
#: 即按**清单里声明的类名**做子串匹配。子类名（如 com.mangaproof.*）不含该子串，
#: 会被 Qt 直接忽略，表现为 openUrl() 返回 false、日志里一句
#: "No file provider found in the AndroidManifest.xml."。
FILE_PROVIDER_CLASS = "androidx.core.content.FileProvider"

#: 与 PROVIDER_AUTHORITY 同理：授权名跟随应用 ID，避免与同设备的旧包冲突。
#: 后缀沿用 Qt 自己的命名习惯（Qt 自带模板用的是 `${applicationId}.qtprovider`），
#: 便于日后对照。
FILE_PROVIDER_AUTHORITY = "com.priloba.mangaproof.qtprovider"

_FILE_PROVIDER_XML = (
    "\n        <!-- MangaProof: 把已下载的 APK 交给系统安装器（需求 §65）。"
    "主程序调 QDesktopServices.openUrl() 时，Qt 会枚举本清单里的"
    " androidx FileProvider，用它把 file:// 换成可共享的 content:// URI"
    "（并带 FLAG_GRANT_READ_URI_PERMISSION）。可共享目录见"
    " res/xml/qtprovider_paths.xml（只开放系统 Download）。 -->\n"
    f'        <provider android:name="{FILE_PROVIDER_CLASS}"\n'
    f'                  android:authorities="{FILE_PROVIDER_AUTHORITY}"\n'
    '                  android:exported="false"\n'
    '                  android:grantUriPermissions="true">\n'
    '            <meta-data android:name="android.support.FILE_PROVIDER_PATHS"\n'
    '                       android:resource="@xml/qtprovider_paths" />\n'
    '        </provider>\n    '
)

#: before_apk_assemble 时必须已经注入成功
_state = {
    "java_copied": False,
    "manifest_patched": False,
    "splash_patched": False,
}


def _log(message: str) -> None:
    print(f"[mangaproof-hook] {message}", flush=True)


def _install_java(dist_dir: Path) -> None:
    """把 `packaging/android/java/**` 整棵树镜像进 Gradle 源码集。

    逐个文件拷贝（而不是整个目录 tree）便于校验"一个都不能少"：漏掉文件会表现为
    "清单/代码指向的类不存在"——构建期才炸，代价高。
    """
    if not JAVA_SRC_ROOT.is_dir():
        raise RuntimeError(f"[mangaproof-hook] 找不到 Java 源目录：{JAVA_SRC_ROOT}")
    sources = sorted(JAVA_SRC_ROOT.rglob("*.java"))
    if not sources:
        raise RuntimeError(f"[mangaproof-hook] {JAVA_SRC_ROOT} 下没有任何 .java")
    if not JAVA_SRC.is_file():
        raise RuntimeError(f"[mangaproof-hook] 缺少必需文件：{JAVA_SRC}")

    dest_root = dist_dir / "src" / "main" / "java"
    copied: list[str] = []
    for src in sources:
        rel = src.relative_to(JAVA_SRC_ROOT)
        dest = dest_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        if not dest.is_file():
            raise RuntimeError(f"[mangaproof-hook] Java 源拷贝失败：{dest}")
        copied.append(rel.as_posix())
    if not _state["java_copied"]:
        _log(f"已放入 {len(copied)} 个 Java 源：{copied}")
        _state["java_copied"] = True


# 【已移除的两个清单改造】—— 不要再加回来，除非有新的真机证据
# ---------------------------------------------------------------------------
# 1) `android:extractNativeLibs="true"`：为修"release 包安装后 so 不被解压"而注入。
#    实测**无效**：注入后 aapt2 读最终 APK 确实是 =true，但真机仍不解压
#    （该属性压不过 AGP 的 `packagingOptions { jniLibs { useLegacyPackaging } }`）。
#    现改为 Android 构建固定用 **debug** 模式（真机验证 so 会解压），见 android.yml。
#
# 2) 入口 Activity 换成自建的 `PickerActivity`：为接管 SAF 选择器的 onActivityResult。
#    那套"自建 Java 选择器 + 共享文件协议"因**不稳定**已整体放弃；现在 Android 端
#    直接用 Qt 控件版文件对话框（`QFileDialog` + `DontUseNativeDialog`，见
#    mangaproof/storage/picker.py），既不碰 Activity，也不需要换入口。
#
# 本 hook 现在做三件事：注入 A11yEnvProvider（无障碍开关 + 机型 dp）、注入
# FileProvider（系统安装器，需求 §65）、挂首屏背景。


def _patch_splash_background(dist_dir: Path) -> bool:
    """把首屏背景（底色 + Logo）挂到 **Activity 真正使用的主题** 上。

    背景（为什么不能只设 `android.apptheme`）
    -----------------------------------------
    p4a 的 Qt 清单模板里两处主题是分开的：

        <application android:theme="{{args.android_apptheme}}…">
            <activity android:theme="@style/KivySupportCutout">      ← 硬编码

    Activity 主题会**覆盖** Application 主题，所以只把 apptheme 指到我们的首屏主题
    **不生效**（首次启动解包 Python 期间仍会白屏/黑屏）。真正有效的是给 Activity 用的
    `@style/KivySupportCutout` 补一条 `android:windowBackground` ——
    `windowBackground` 由 system_server 在窗口创建时绘制，与 Qt/Java 无关，
    因此 Qt bootstrap 下同样有效（p4a 自带的 presplash 在 Qt bootstrap 下不生效）。

    实现：在 dist 的资源目录里就地改写 styles.xml 中 `KivySupportCutout` 的
    `<style>` 块，插入 `android:windowBackground`。资源文件位置/名字随 p4a 版本
    可能变化，所以这里**遍历所有候选 styles.xml**，命中即改；一个都没命中时返回
    False 并打印醒目警告（不阻断构建——首屏只是观感问题，不该因此让整条流水线失败），
    由 CI 侧的 aapt2 校验兜底确认。
    """
    candidates: list[Path] = []
    for pattern in ("values/styles.xml", "values/*styles*.xml", "values/*.xml"):
        candidates.extend(sorted(dist_dir.glob(f"src/main/res/{pattern}")))
    # 去重且保持顺序
    seen: set[Path] = set()
    files = [p for p in candidates if not (p in seen or seen.add(p))]

    marker = 'android:windowBackground'
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if STYLE_NAME not in text:
            continue
        if marker in text:
            _log(f"{path.name} 已含 {marker}，跳过")
            return True
        # 只改 KivySupportCutout 那个 style 块
        match = re.search(
            r'(<style\s+name="' + re.escape(STYLE_NAME) + r'"[^>]*>)(.*?)(</style>)',
            text,
            flags=re.S,
        )
        if match is None:
            continue
        injected = (
            match.group(1)
            + match.group(2).rstrip()
            + f"\n        <item name=\"{marker}\">@drawable/mangaproof_splash</item>\n    "
            + match.group(3)
        )
        text = text[:match.start()] + injected + text[match.end():]
        path.write_text(text, encoding="utf-8")
        _log(f"已在 {path.relative_to(dist_dir)} 的 {STYLE_NAME} 注入 {marker}")
        return True

    _log(
        "⚠️ 未找到可改写的 styles.xml（含 " + STYLE_NAME + "）—— 首屏背景不会生效。\n"
        f"        候选文件：{[str(p.relative_to(dist_dir)) for p in files] or '（一个都没有）'}\n"
        "        请核对 p4a 版本是否改了 Activity 主题名/资源布局"
    )
    return False


def _inject_into_application(text: str, *, marker: str, snippet: str, label: str) -> str:
    """把一个片段注入 `</application>` 之前；已存在则原样返回。

    `</application>` 必须唯一 —— 出现多次就说明清单结构不是我们预期的样子，
    此时宁可直接失败，也不要"改错地方"（这份清单是构建期生成的，症状会推迟到
    真机才暴露，代价很高）。
    """
    if marker in text:
        _log(f"清单中已包含 {label}，跳过注入")
        return text
    count = text.count("</application>")
    if count != 1:
        raise RuntimeError(
            f"[mangaproof-hook] 清单里 </application> 出现 {count} 次，无法安全注入 {label}"
        )
    text = text.replace("</application>", snippet + "</application>", 1)
    if marker not in text:
        raise RuntimeError(f"[mangaproof-hook] {label} 注入后校验失败")
    return text


def _patch_manifest(dist_dir: Path, *, required: bool) -> None:
    """在 <application> 内注入两个 provider 声明，并挂上首屏背景。"""
    manifest = dist_dir / "src" / "main" / "AndroidManifest.xml"
    if not manifest.is_file():
        if required:
            raise RuntimeError(f"[mangaproof-hook] 清单不存在，无法注入 provider：{manifest}")
        _log("清单尚未生成，跳过注入（before_apk_build 阶段属正常）")
        return

    text = manifest.read_text(encoding="utf-8")
    original = text

    # 1) provider（无障碍开关 + 机型 dp）
    text = _inject_into_application(
        text, marker=PROVIDER_CLASS, snippet=_PROVIDER_XML, label="A11yEnvProvider"
    )
    # 2) FileProvider（把 APK 交给系统安装器，需求 §65）
    text = _inject_into_application(
        text,
        marker=FILE_PROVIDER_CLASS,
        snippet=_FILE_PROVIDER_XML,
        label="FileProvider（系统安装器）",
    )

    if text != original:
        manifest.write_text(text, encoding="utf-8")
    _state["manifest_patched"] = True
    _log("清单已注入 A11yEnvProvider 与 FileProvider")

    # 3) 首屏背景（底色 + Logo）→ 挂到 Activity 实际使用的主题上
    _state["splash_patched"] = _patch_splash_background(dist_dir)


def _apply(*, require_manifest: bool) -> None:
    dist_dir = Path.cwd()          # p4a 在 dist 目录内调用 hook
    _install_java(dist_dir)
    _patch_manifest(dist_dir, required=require_manifest)


# --- p4a hook 入口（函数名即阶段名，p4a 会 getattr 后调用） ------------------

def before_apk_build(toolchain=None) -> None:      # noqa: ARG001
    _apply(require_manifest=False)


def after_apk_build(toolchain=None) -> None:       # noqa: ARG001
    _apply(require_manifest=True)


def before_apk_assemble(toolchain=None) -> None:   # noqa: ARG001
    _apply(require_manifest=True)
    if not _state["manifest_patched"]:
        raise RuntimeError("[mangaproof-hook] 清单 provider 注入未完成，拒绝继续组装 APK")
    if not _state["java_copied"]:
        raise RuntimeError("[mangaproof-hook] Java 源未安装，拒绝继续组装 APK")
    if not _state["splash_patched"]:
        # 首屏只是观感问题：这里不阻断构建，交给 CI 的 aapt2 校验去发现（见 android.yml）
        print("[mangaproof-hook] ⚠️ 首屏背景未注入成功（不阻断构建，请检查上方警告）", flush=True)

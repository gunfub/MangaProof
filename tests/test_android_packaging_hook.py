# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""p4a hook 的本地验证：Java 源安装 + 清单注入 + 入口 Activity 替换。

真机构建跑在 CI（NDK 交叉编译），但 hook 本身是纯 Python，可以、也应该在本地
把"幂等 / 硬失败 / 注入结果"全部测掉——这几处一旦出错就是 60 分钟构建后才暴露。

对应实现：`packaging/android/p4a_hook.py`。
"""

from __future__ import annotations

import importlib.util
import shutil
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: p4a Qt 模板给 Activity 用的主题（结构照抄线上资源，注释见 p4a_hook.STYLE_NAME）
STYLES_XML = """<?xml version="1.0" encoding="utf-8"?>
<resources>
    <style name="KivySupportCutout" parent="@android:style/Theme.NoTitleBar.Fullscreen">
        <item name="android:windowLayoutInDisplayCutoutMode">shortEdges</item>
    </style>
    <style name="KivyOther" parent="@android:style/Theme.NoTitleBar"></style>
</resources>
"""
PACKAGING_DIR = REPO_ROOT / "packaging" / "android"
HOOK_PATH = PACKAGING_DIR / "p4a_hook.py"


@pytest.fixture()
def hook(monkeypatch):
    """按文件路径加载 hook（`packaging/` 不是 Python 包，p4a 也是按路径加载它）。"""
    spec = importlib.util.spec_from_file_location("mangaproof_p4a_hook", HOOK_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_state",
                        {"java_copied": False, "manifest_patched": False,
                         "splash_patched": False})
    return module


@pytest.fixture()
def fake_dist(tmp_path):
    """构造一个只含 AndroidManifest.xml 的假 dist 目录。"""
    dist = tmp_path / "dist"
    (dist / "src" / "main").mkdir(parents=True)
    manifest = dist / "src" / "main" / "AndroidManifest.xml"
    manifest.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android">\n'
        '    <application android:name="org.qtproject.qt.android.bindings.QtApplication"\n'
        '                 android:label="@string/app_name">\n'
        '        <activity android:name="org.qtproject.qt.android.bindings.QtActivity"\n'
        '                  android:exported="true">\n'
        '            <intent-filter>\n'
        '                <action android:name="android.intent.action.MAIN" />\n'
        '            </intent-filter>\n'
        '        </activity>\n'
        '    </application>\n'
        '</manifest>\n',
        encoding="utf-8",
    )
    (dist / "src" / "main" / "res" / "values").mkdir(parents=True)
    (dist / "src" / "main" / "res" / "values" / "styles.xml").write_text(
        STYLES_XML, encoding="utf-8")
    return dist


def _run(hook, dist, monkeypatch, *, require_manifest=True):
    monkeypatch.chdir(dist)
    hook._apply(require_manifest=require_manifest)
    return dist / "src" / "main" / "AndroidManifest.xml"


# --------------------------------------------------------------------- Java 源

def test_installs_all_java_sources(hook, fake_dist, monkeypatch):
    _run(hook, fake_dist, monkeypatch)
    java_root = PACKAGING_DIR / "java"
    expected = sorted(p.relative_to(java_root).as_posix() for p in java_root.rglob("*.java"))
    assert expected, "仓库里应有 Java 源"
    installed = sorted(p.relative_to(fake_dist / "src" / "main" / "java").as_posix()
                       for p in (fake_dist / "src" / "main" / "java").rglob("*.java"))
    assert installed == expected
    # provider 必须在（清单注入指向的就是它）
    assert "com/mangaproof/a11y/A11yEnvProvider.java" in installed


def test_installed_java_matches_source_bytewise(hook, fake_dist, monkeypatch):
    _run(hook, fake_dist, monkeypatch)
    src = PACKAGING_DIR / "java" / "com" / "mangaproof" / "a11y" / "A11yEnvProvider.java"
    dest = fake_dist / "src" / "main" / "java" / "com" / "mangaproof" / "a11y" / "A11yEnvProvider.java"
    assert dest.read_bytes() == src.read_bytes()


# --------------------------------------------------------------------- 清单

def test_injects_provider_only(hook, fake_dist, monkeypatch):
    """hook 现在只注入 provider；入口 Activity 保持 p4a 渲染的原样（不再改写）。"""
    manifest = _run(hook, fake_dist, monkeypatch)
    text = manifest.read_text(encoding="utf-8")
    assert "com.mangaproof.a11y.A11yEnvProvider" in text
    assert 'android:authorities="com.priloba.mangaproof.a11y.env"' in text
    assert 'android:exported="false"' in text
    # 入口 Activity 不动（原样保留 p4a 渲染结果）
    assert 'android:name="org.qtproject.qt.android.bindings.QtActivity"' in text
    # 其它属性没被动坏
    assert 'android:name="org.qtproject.qt.android.bindings.QtApplication"' in text
    assert 'android:exported="true"' in text
    assert text.count("<activity") == 1 and text.count("</activity>") == 1


def test_hook_is_idempotent(hook, fake_dist, monkeypatch):
    first = _run(hook, fake_dist, monkeypatch).read_text(encoding="utf-8")
    second = _run(hook, fake_dist, monkeypatch).read_text(encoding="utf-8")
    assert first == second, "重复执行不应改变清单（否则每次 before_apk_* 都会重复注入）"
    assert second.count("A11yEnvProvider") == first.count("A11yEnvProvider")


def test_missing_manifest_is_hard_failure_when_required(hook, tmp_path, monkeypatch):
    (tmp_path / "dist" / "src" / "main").mkdir(parents=True)
    monkeypatch.chdir(tmp_path / "dist")
    with pytest.raises(RuntimeError, match="清单不存在"):
        hook._apply(require_manifest=True)


def test_missing_manifest_is_tolerated_early(hook, tmp_path, monkeypatch):
    """before_apk_build 阶段清单还没生成：只拷 Java，不报错。"""
    dist = tmp_path / "dist"
    (dist / "src" / "main").mkdir(parents=True)
    monkeypatch.chdir(dist)
    hook._apply(require_manifest=False)
    assert (dist / "src" / "main" / "java" / "com" / "mangaproof" / "a11y"
            / "A11yEnvProvider.java").is_file()


# --------------------------------------------------------------------- 阶段断言

def test_before_apk_assemble_asserts_patch_done(hook, tmp_path, monkeypatch):
    """组装前必须确认注入完成（否则会打出"点了没反应"的包）。"""
    dist = tmp_path / "dist"
    (dist / "src" / "main").mkdir(parents=True)
    monkeypatch.chdir(dist)
    # 清单缺失 → _apply 直接抛错，不会静默通过
    with pytest.raises(RuntimeError):
        hook.before_apk_assemble()


def test_before_apk_assemble_passes_after_patch(hook, fake_dist, monkeypatch):
    manifest = _run(hook, fake_dist, monkeypatch)
    monkeypatch.chdir(fake_dist)
    hook.before_apk_assemble()          # 不应抛错
    assert "com.mangaproof.a11y.A11yEnvProvider" in manifest.read_text(encoding="utf-8")


# ------------------------------------------------------- 首屏背景（启动底色 + Logo）

def test_splash_background_injected_into_activity_theme(hook, fake_dist, monkeypatch):
    """必须注入到 **Activity 实际使用的主题** 上。

    p4a 模板里 Application 主题是 android:apptheme、Activity 主题硬编码
    @style/KivySupportCutout；Activity 主题会覆盖 Application 主题，所以只设
    apptheme 不生效 —— 这条测试钉住"改的是 KivySupportCutout"。
    """
    _run(hook, fake_dist, monkeypatch)
    styles = fake_dist / "src" / "main" / "res" / "values" / "styles.xml"
    text = styles.read_text(encoding="utf-8")
    block = re.search(r'<style\s+name="KivySupportCutout".*?</style>', text, re.S).group(0)
    assert "android:windowBackground" in block
    assert "@drawable/mangaproof_splash" in block
    # 其它主题不许被动
    other = re.search(r'<style\s+name="KivyOther".*?</style>', text, re.S).group(0)
    assert "windowBackground" not in other
    # 原有内容保留（挖孔模式不能被覆盖掉）
    assert "windowLayoutInDisplayCutoutMode" in block
    ET.fromstring(text)          # XML 仍可解析


def test_splash_injection_is_idempotent(hook, fake_dist, monkeypatch):
    styles = fake_dist / "src" / "main" / "res" / "values" / "styles.xml"
    _run(hook, fake_dist, monkeypatch)
    first = styles.read_text(encoding="utf-8")
    _run(hook, fake_dist, monkeypatch)
    assert styles.read_text(encoding="utf-8") == first
    assert first.count("android:windowBackground") == 1


def test_splash_missing_styles_warns_but_does_not_fail(hook, tmp_path, monkeypatch):
    """找不到可改写的 styles.xml：只警告、不抛错（首屏是观感问题，不该阻断出包）。"""
    dist = tmp_path / "dist"
    (dist / "src" / "main").mkdir(parents=True)
    (dist / "src" / "main" / "AndroidManifest.xml").write_text(
        "<?xml version='1.0'?><manifest><application></application></manifest>", encoding="utf-8")
    monkeypatch.chdir(dist)
    assert hook._patch_splash_background(dist) is False


def test_splash_resources_exist_and_are_wired():
    """资源本体与构建参数都要在（防止只改了一半）。"""
    res = PACKAGING_DIR / "res"
    splash = (res / "drawable" / "mangaproof_splash.xml").read_text(encoding="utf-8")
    colors = (res / "values" / "colors.xml").read_text(encoding="utf-8")
    themes = (res / "values" / "themes.xml").read_text(encoding="utf-8")
    assert "@color/mangaproof_splash_bg" in splash
    assert "@drawable/mangaproof_logo" in splash
    assert "#202227" in colors                      # 需求方指定的底色
    # fullscreen 时 p4a 会拼 .Fullscreen，两个变体都要有
    assert 'name="MangaProofSplash"' in themes
    assert 'name="MangaProofSplash.Fullscreen"' in themes

    build_script = (REPO_ROOT / "scripts" / "android" / "build_android.py").read_text(encoding="utf-8")
    assert "drawable-nodpi/mangaproof_logo.png" in build_script     # logo 投放
    assert "drawable/mangaproof_splash.xml" in build_script         # drawable 投放
    assert "values/themes.xml" in build_script                      # 主题投放
    assert 'put("app", "android.apptheme", "@style/MangaProofSplash")' in build_script


def test_injects_file_provider_for_the_system_installer(hook, fake_dist, monkeypatch):
    """需求 §65：注入 androidx FileProvider，供 QDesktopServices.openUrl 把 APK 交出去。

    类名**必须**是 `androidx.core.content.FileProvider` 本身 —— Qt 的
    getFileProviderAuthorities() 是按清单里的类名做子串匹配的，自建子类会被忽略
    （症状：openUrl 返回 false，日志 "No file provider found"）。
    """
    text = _run(hook, fake_dist, monkeypatch).read_text(encoding="utf-8")
    assert 'android:name="androidx.core.content.FileProvider"' in text
    assert 'android:authorities="com.priloba.mangaproof.qtprovider"' in text
    assert 'android:grantUriPermissions="true"' in text, "不给授权则系统安装器读不到 APK"
    assert 'android:resource="@xml/qtprovider_paths"' in text
    # 与既有的 A11yEnvProvider 并存，两个都在
    assert "com.mangaproof.a11y.A11yEnvProvider" in text
    assert text.count("<provider") == 2
    # 仍然只有一份 application / activity
    assert text.count("<application") == 1 and text.count("</application>") == 1
    assert text.count("<activity") == 1 and text.count("</activity>") == 1


def test_file_provider_injection_is_idempotent(hook, fake_dist, monkeypatch):
    """before_apk_build / after_apk_build / before_apk_assemble 会各跑一次，不许叠加。"""
    first = _run(hook, fake_dist, monkeypatch).read_text(encoding="utf-8")
    second = _run(hook, fake_dist, monkeypatch).read_text(encoding="utf-8")
    assert first == second
    assert second.count("androidx.core.content.FileProvider") == 1


def test_injection_refuses_ambiguous_manifest(hook, tmp_path, monkeypatch):
    """</application> 出现多次 → 直接失败，绝不"改错地方"。"""
    dist = tmp_path / "dist"
    (dist / "src" / "main").mkdir(parents=True)
    (dist / "src" / "main" / "AndroidManifest.xml").write_text(
        "<manifest></application></application></manifest>", encoding="utf-8"
    )
    monkeypatch.chdir(dist)
    with pytest.raises(RuntimeError, match="无法安全注入"):
        hook._apply(require_manifest=True)


def test_file_provider_resource_exists_and_is_wired():
    """资源本体、投放参数、清单引用三者必须对齐（只改一半会在构建期才炸）。"""
    paths_xml = (PACKAGING_DIR / "res" / "xml" / "qtprovider_paths.xml").read_text(encoding="utf-8")
    root = ET.fromstring(paths_xml)
    entries = [(child.tag, child.get("name"), child.get("path")) for child in root]
    assert entries == [("external-path", "download", "Download/")], (
        "只开放系统 Download；不要照抄 Qt 模板的 path=\"/\"（会放开整个外部存储）"
    )

    build_script = (REPO_ROOT / "scripts" / "android" / "build_android.py").read_text(encoding="utf-8")
    assert "packaging/android/res/xml/qtprovider_paths.xml:xml/qtprovider_paths.xml" in build_script

    hook_src = HOOK_PATH.read_text(encoding="utf-8")
    assert '@xml/qtprovider_paths' in hook_src


def test_request_install_packages_permission_is_declared():
    """Android 8+ 调起系统安装器必须有 REQUEST_INSTALL_PACKAGES（需求 §65）。"""
    build_script = (REPO_ROOT / "scripts" / "android" / "build_android.py").read_text(encoding="utf-8")
    assert "android.permission.REQUEST_INSTALL_PACKAGES" in build_script
    assert "android.permission.MANAGE_EXTERNAL_STORAGE" in build_script


# --------------------------------------------------- Android 资源文件的合法性（防再犯）

def _illegal_double_hyphens(raw: bytes) -> list[int]:
    """返回 XML 注释**内部**非法的连续连字符位置（排除 <!-- 与 --> 自身）。

    XML 规范禁止注释内容出现连续两个连字符。这条规则在**字节层**生效，所以中文里的
    "——"（U+2014 的 UTF-8 编码含 0x2D 0x2D 字节对）同样非法 —— 本项目就因为
    XML 注释里写了中文破折号，导致 aapt2 编译失败、Gradle 秒挂（CI 实测）。
    """
    out: list[int] = []
    i, n = 0, len(raw)
    while i < n:
        if raw[i:i + 4] == b"<!--":
            j = raw.find(b"-->", i + 4)
            if j < 0:
                out.append(i)
                break
            body = raw[i + 4:j]
            k = body.find(b"--")
            if k >= 0:
                out.append(i + 4 + k)
            i = j + 3
        else:
            i += 1
    return out


def test_android_res_xml_has_no_illegal_double_hyphen():
    """所有投放的 Android 资源 XML 都必须能在字节层通过 XML 注释规则。

    这是"构建期才炸、报错信息还指向别处"的典型：aapt2 只报 `not well-formed`，
    Gradle 秒挂，而根因是注释里一个中文破折号。所以放在单测里，改资源立刻能发现。
    """
    res_dir = PACKAGING_DIR / "res"
    files = sorted(res_dir.rglob("*.xml"))
    assert files, "packaging/android/res 下应有资源 XML"
    offenders = {str(f.relative_to(res_dir)): _illegal_double_hyphens(f.read_bytes())
                 for f in files}
    offenders = {k: v for k, v in offenders.items() if v}
    assert not offenders, f"这些资源 XML 的注释里有非法连续连字符：{offenders}"


def test_android_res_xml_is_well_formed():
    """结构上必须是合法 XML（ElementTree 口径）。"""
    res_dir = PACKAGING_DIR / "res"
    for f in sorted(res_dir.rglob("*.xml")):
        ET.fromstring(f.read_bytes())          # 解析失败即测试失败


def test_splash_resources_survive_aapt2_compile():
    """有 aapt2 时真编一遍（这才是构建期真正会炸的地方）；没有就跳过。"""
    aapt2 = None
    for candidate in (
        Path.home() / "Android/Sdk/build-tools",
        Path("/usr/lib/android-sdk/build-tools"),
    ):
        if candidate.is_dir():
            for bt in sorted(candidate.iterdir(), reverse=True):
                if (bt / "aapt2").is_file():
                    aapt2 = bt / "aapt2"
                    break
        if aapt2:
            break
    if aapt2 is None:
        pytest.skip("本机没有 aapt2，跳过（CI 由构建步骤覆盖）")

    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        (work / "res" / "values").mkdir(parents=True)
        (work / "res" / "drawable").mkdir(parents=True)
        (work / "res" / "drawable-nodpi").mkdir(parents=True)
        for src, dst in (
            (PACKAGING_DIR / "res" / "values" / "colors.xml", work / "res" / "values" / "colors.xml"),
            (PACKAGING_DIR / "res" / "values" / "themes.xml", work / "res" / "values" / "themes.xml"),
            (PACKAGING_DIR / "res" / "drawable" / "mangaproof_splash.xml",
             work / "res" / "drawable" / "mangaproof_splash.xml"),
        ):
            dst.write_bytes(src.read_bytes())
        # Logo 用仓库里真实的那张图
        shutil.copyfile(REPO_ROOT / "ico" / "Android-foreground.png",
                        work / "res" / "drawable-nodpi" / "mangaproof_logo.png")
        proc = subprocess.run(
            [str(aapt2), "compile", "--dir", str(work / "res"), "-o", str(work / "out.zip")],
            capture_output=True, text=True, check=False,
        )
        assert proc.returncode == 0, f"aapt2 compile 失败：\n{proc.stdout}\n{proc.stderr}"

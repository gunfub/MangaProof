# MangaProof Android 打包研究：PySide6 官方工具链 × GitHub Actions 云端构建

> **文档性质**：只读调研报告（本次未修改任何代码与配置文件，仅新增本文档）
> **姊妹文档**：`docs/Android端适配设计_原生文件读写_横屏全屏_分层图标_内存策略.md`（第二轮/第三轮调研：仅 APK、全文件访问权限 + 真实路径直读（SAF 仅用于选目录）、强制横屏+全面屏全屏、自适应图标、内存强制激进）——本文中与之相关的结论以该文档为准。
> **日期**：2026-09-15
> **结论一句话**：技术路线成立，且**不需要任何本地工具链**——官方 `pyside6-android-deploy`（buildozer + python-for-android + Qt bootstrap）可以在 GitHub Actions 的 Ubuntu runner 上出 APK/AAB；但本项目要真正"能装、能开、能用"，还有 **5 条硬约束**和一组应用侧改造项必须先解决。
> **证据等级**：本文所有关键论断标注来源。`【源码】`= 本机 `.venv` 内 PySide6 6.11.2 自带工具源码（第一手，与将要使用的版本完全一致）；`【官方文档】`= Qt 官方文档 / pyside-setup `v6.11.2` tag 的 rst 原文；`【实测】`= 本次实际发起的 HTTP/wheel/ELF 核验；`【推断】`= 尚未验证的工程判断。

---

## 1. 调研范围与方法

### 1.1 要回答的问题

1. PySide6 **官方**的打包工具是什么？桌面与 Android 分别是什么？各自能力边界在哪？
2. 官方 Android 路线**具体怎么工作**，前置条件、产物、可配置项是什么？
3. 怎么做到**完全不使用本地工具链**，全部在 GitHub Actions 云端编译？
4. 本项目（Python 3.12 + PySide6 6.11.2 的 Qt Widgets 桌面应用，含 numpy / psd-tools / reportlab / Pillow 依赖与一个自研 C 扩展）走这条路线的**差距、风险与验收方法**是什么？
5. 仓库已备好的签名密钥（`KEYSTORE_BASE64` / `KEYSTORE_PASSWORD` / `KEY_ALIAS` / `KEY_PASSWORD`）如何接入？

### 1.2 本次实际做过的核证动作

| # | 动作 | 结果 |
|---|------|------|
| 1 | 通读本机 `.venv` 内 PySide6 6.11.2 的 `PySide6/scripts/android_deploy.py`、`deploy_lib/**`（与将来 CI 使用的版本一致） | 拿到工具真实行为，纠正了官方文档中的若干过时描述 |
| 2 | 抓取 pyside-setup `v6.11.2` tag 的官方文档 `deployment-pyside6-android-deploy.rst` 原文 | 确认 6.11.2 的官方前置条件表述 |
| 3 | `curl` 校验 `download.qt.io` 上 Android wheel 的存在性与体积 | **6.11.2 的 aarch64 / x86_64 Android wheel 均存在**（PySide6 83,924,266 B；shiboken6 273,559 B） |
| 4 | 解包 PySide6 Android wheel 检查内部结构 | 147 个 `Qt/lib` `.so`、11 个 `.jar`、24 个 `*-android-dependencies.xml`、`*.abi3.so` |
| 5 | `readelf -lW` 检查关键 `.so` 的 `LOAD` 段 `p_align` | Qt 库与 PySide6 abi3 模块 = `0x4000`（16 KB，**合规**）；shiboken6 wheel 内两个 `.so` = `0x1000`（4 KB，**需关注**） |
| 6 | 抓取 python-for-android（p4a）`develop` 分支的 recipe 清单、`toolchain.py`、`recipe.py`、`Dockerfile`、`qt` bootstrap | 确认依赖可行性、签名 CLI、系统依赖清单、单 ABI 限制 |
| 7 | 抓取 buildozer 1.5.0 / 1.6.0 源码与默认 `buildozer.spec` | 确认默认 API/NDK/打包范围、p4a 获取方式（git clone + branch） |
| 8 | 抓取 Qt 官方 "Qt for Android" 支持矩阵 | **Android 9 (API 28) ~ 16 (API 36)**；**NDK r27c (27.2.12479018)**，与工具源码常量一致 |
| 9 | 静态分析本仓库代码（平台分支、Qt API、原生扩展回退路径、依赖元数据） | 见 §4 |
| 10 | 查询 GitHub Actions 各 action 当前主版本 | 见 §5.3（checkout v7、setup-python v7、setup-java v6、setup-android v4、cache v6、upload-artifact v7、download-artifact v8、setup-uv v10） |
| 11 | `curl` 校验 NDK 与 SDK 侧资源 | NDK 官方包 `android-ndk-r27c-linux.zip` **HTTP 200，663,987,688 B（约 634 MiB）**；`ndk;27.2.12479018` 确在 Google SDK 仓库清单中（可被 `sdkmanager` 安装） |

---

## 2. 结论速览（TL;DR）

### 2.1 可行性判定

**可行，但不是"配置一下就好"**。官方工具链的确存在且被 Qt 官方维护（`pyside6-android-deploy` 与 PySide6 同仓发布、文档在册，但成熟度明显低于桌面 `pyside6-deploy`），云端构建完全可行；但本项目要把 Android 包做到"可用"，需要三块工作叠加：

- **A. 构建链路**（本文重点）：CI 上跑通 `pyside6-android-deploy`，出签名 APK/AAB。
- **B. 依赖打包**：`numpy / Pillow / reportlab` 走 p4a recipe，`attrs / typing-extensions / charset-normalizer / psd-tools` 需自建 4 个本地 recipe。
- **C. 应用改造**（独立主线，工作量最大）：Qt Widgets 的桌面交互模型（菜单栏/快捷键/滚轮/右键/停靠面板）与 Android 的触屏 + 存储权限模型（SAF）不兼容，需要专门适配。

### 2.2 五条硬约束（决定方案形态）

| # | 硬约束 | 依据 | 影响 |
|---|--------|------|------|
| 1 | **宿主 Python 必须 ≤ 3.11**，否则工具直接 `RuntimeError` | 【源码】`android_deploy.py:209-211`（报错文案自称原因是 "a restriction in buildozer"）；工具固定安装 `buildozer==1.5.0` + `cython==0.29.33`【源码】`deploy_lib/default.spec:30`，属较旧的工具链【推断：具体技术原因未逐行确认】 | CI 的 Android 构建必须建 **Python 3.11** 的独立 venv；与项目 `requires-python >=3.12` 不冲突（宿主只跑构建工具，宿主不会 import 应用代码——模块探测是纯 AST 扫描【源码】`deploy_lib/dependency_util.py:142-190`） |
| 2 | **Android wheel 不在 PyPI**，只在 `download.qt.io`；且 wheel 的 abi3 模块虽为稳定 ABI，但 wheel tag 是 `cp311` | 【实测】HTTP 200；文件名 `pyside6-6.11.2-6.11.2-cp311-cp311-android_aarch64.whl` | CI 需 `curl` 下载；设备端 Python 由 p4a 编译（p4a `develop` 当前为 **CPython 3.14.2**【源码】p4a `recipes/python3/__init__.py:57`），"cp311 wheel 跑在 3.14 上"必须真机实测确认（§6-R2） |
| 3 | **工具的 `requirements` 是硬编码的**，且**每次运行都会先删除 `buildozer.spec` 与 `deployment/` 目录** | 【源码】`deploy_lib/android/buildozer.py:27` 写死 `python3,shiboken6,PySide6`；`deploy_util.py:22-46` 的 `cleanup()` 删除 `<project>/buildozer.spec` 与 `<project>/deployment`；`android_deploy.py:99` 在每次运行开头**无条件**调用 | **无法用"预置 buildozer.spec"或"两段式 --init 再改"的办法加依赖**；必须用包装脚本在内存中劫持 `BuildozerConfig`（§5.4），或 `sed` 打进 CI 里那份 PySide6 包 |
| 4 | **Qt Widgets 的桌面交互在 Android 上不可用/不适用**（键鼠模型），但**文件访问已定方案**：申请 `MANAGE_EXTERNAL_STORAGE`（全文件访问）→ 用**真实路径直读**；SAF 仅用于选目录（Qt 原生选择器）后映射为路径 | 【官方文档】Qt for Android 支持矩阵仅到框架层；【源码】本应用重度使用 `QMenuBar/QStatusBar/QDockWidget/QShortcut/滚轮/右键`（§4.2）；**第二轮核证**：qtbase `qandroidplatformfiledialoghelper.cpp`（选目录=SAF）+ Android 官方"direct file path access" | 打包侧零成本；改造集中在 `storage/**` 薄层（选目录+URI→路径映射+权限检测）与触屏交互（P3）；详见姊妹文档 §2 |
| 5 | **16 KB page size（Android 15+ / Play 要求）** 需要逐库核验 | 【实测】PySide6 wheel 内 Qt 库/PySide6 模块为 `0x4000`（合规），shiboken6 wheel 内为 `0x1000`（4 KB） | 需要在 CI 加一条 `readelf`/`zipalign -c -P 16` 校验；若 Play 拒绝，缓解手段见 §6-R1 |

### 2.3 推荐路线（与不推荐路线）

| 路线 | 结论 | 理由 |
|------|------|------|
| **官方 `pyside6-android-deploy` + GitHub Actions（推荐）** | ✅ 采用 | Qt 官方维护、与 PySide6 版本同步、CI 全云端、无需本地 NDK/SDK |
| Kivy/Buildozer 直用（绕过 Qt 工具） | ❌ | 需要自行编写 PySide6/shiboken6 的 p4a recipe（Qt 工具正是干这个），维护成本高且容易踩版本坑 |
| BeeWare Briefcase / Toga | ❌ | Briefcase 的 Android 后端基于 Chaquopy，Toga 使用原生控件，**不支持 PySide6/Qt Widgets**【推断：基于 Briefcase/Toga 的既有定位，未逐条核证】 |
| KivyMD / 重写 UI 为 QML | ⚠️ 备选 | QML 在 Android 上是 Qt 的一等公民，触屏体验天然更好；但等于重写界面，成本远高于本次目标 |
| 本地起 NDK/SDK 构建 | ❌（本次明确排除） | 与"不使用本地工具链"的要求冲突 |

---

## 3. 官方打包工具研究（第一手）

### 3.1 两个官方工具的关系

PySide6 6.x 自带两个部署入口（都在 `PySide6` 包内，随 `pip install pyside6` 一起装好）：

| 工具 | 底层 | 目标平台 | 配置文件 | 能否出 Android 包 |
|------|------|----------|----------|-------------------|
| `pyside6-deploy` | **Nuitka** | Windows / Linux / macOS | `pysidedeploy.spec` | ❌ 不能 |
| `pyside6-android-deploy` | **buildozer + python-for-android（p4a）+ Qt bootstrap** | Android（arm64-v8a / x86_64；armv7a、x86 需自行交叉编译 Qt） | 同一个 `pysidedeploy.spec` | ✅ 唯一官方途径 |

两者共用 `pysidedeploy.spec`，字段含义见 §3.5。

### 3.2 `pyside6-android-deploy` 工作原理（逐步 + 源码位置）

```
pyside6-android-deploy --name MangaProof --wheel-pyside <A.whl> --wheel-shiboken <B.whl> \
                       --ndk-path <NDK r27c> --sdk-path <SDK>
        │
        ├─ 0. 前置校验：cwd 下必须有 main.py                      【android_deploy.py:71-76】
        │      （本项目根目录已有 main.py ✅）
        ├─ 1. 宿主 Python 检查：sys.version_info >= (3,12) → 直接失败【:209-211】
        ├─ 2. 构建 PythonExecutable：若非 venv 且未加 --force 会交互提问【python_helper.py:26-38】
        ├─ 3. 读/建 pysidedeploy.spec（首次由 deploy_lib/default.spec 生成）【:83-93】
        ├─ 4. AndroidConfig 解析：
        │      · arch 从 wheel 文件名推断（aarch64/x86_64/armv7a/i686）【android_config.py:281-289】
        │      · Qt 模块 = AST 扫描项目 .py 里的 `from PySide6.QtXxx import ...`【dependency_util.py:142-190】
        │      · 依赖的额外 Qt 模块 = 用 NDK 的 llvm-readobj 读 wheel 内 .so 的 NEEDED【android_config.py:291-337】
        │      · 权限/jars/插件 = 解析 wheel 内 *-android-dependencies.xml【:79-121, 339-355】
        ├─ 5. ⚠️ cleanup()：删除 <project>/deployment/ 与 <project>/buildozer.spec【android_deploy.py:99 → deploy_util.py:22-46】
        ├─ 6. 安装宿主侧构建工具：pip install buildozer==1.5.0, cython==0.29.33【default.spec:30 + python_helper.py:106-141】
        ├─ 7. 解包 wheel 的 jar → <project>/deployment/jar/PySide6/jar/（含必需的 Qt6AndroidBindings.jar）【android_config.py:269-279】
        ├─ 8. 生成 p4a 本地 recipe：<project>/deployment/recipes/{PySide6,shiboken6}/__init__.py
        │      （Jinja2 模板：把 wheel 解压进 site-packages + 把 Qt 库/插件 copy 到 libs）【android_helper.py:34-64 + recipes/*.tmpl.py】
        ├─ 9. buildozer init → 生成 <project>/buildozer.spec，然后**覆写**关键字段【buildozer.py:15-121】
        │      title / package.name = <name>；package.domain = org.<name>
        │      requirements = python3,shiboken6,PySide6          ← 写死，无法通过配置追加
        │      p4a.bootstrap = qt；p4a.branch = develop；p4a.local_recipes = <project>/deployment/recipes
        │      android.archs = <wheel 架构>；android.add_jars = <jar 列表>
        │      p4a.extra_args = --qt-libs=<模块> --load-local-libs=<库> --init-classes=<类>
        │      buildozer.bin_dir = <exec_directory>；icon.filename = <app.icon>
        ├─10. 执行 `python -m buildozer android <mode>`：buildozer 会 git clone kivy/python-for-android
        │      （branch=develop）到 <项目>/.buildozer/android/platform/python-for-android，
        │      p4a 的构建/下载目录 = <项目>/.buildozer/android/platform/build-<arch>，然后调用 p4a 编译
        │      （p4a：hostpython3 → python3(3.14.2) → 各 recipe → Qt libs 拷贝 → Gradle 打包）
        ├─11. 产物：mode=debug → APK（buildozer `android.debug_artifact` 默认 apk）；
        │      mode=release → AAB（buildozer `android.release_artifact` 默认 aab【源码】buildozer 1.5.0 target.py:105/142）；
        │      release 是否签名取决于 4 个环境变量 P4A_RELEASE_{KEYSTORE,KEYALIAS,KEYSTORE_PASSWD,KEYALIAS_PASSWD}【源码】同文件 :925-940
        └─12. 收尾：把 .buildozer 移入 deployment/，随后 cleanup() 删除 deployment/（除非 --keep-deployment-files）
```

**⚠️ 第 5 步和第 12 步是本方案最大的工程陷阱**（官方文档完全没写）：
`cleanup()` 每次运行开头都会删掉 `buildozer.spec` 和 `deployment/`，所以**任何"预置 spec"或"分两次运行"的取巧办法都会被打回**（`--init` 时 `return` 也会触发 `finally` 里的 cleanup，除非同时给 `--keep-deployment-files`）。要用包装脚本在**同一次运行内**、`BuildozerConfig` 写完 spec 之后、buildozer 启动之前注入自定义内容（§5.4）。

**⚠️ 第 10/11 步的退出码不可信**：`android_deploy.main()` 用 `except Exception: print(traceback)` 吞掉所有异常、不 `raise`、不设非零退出码【android_deploy.py:142-146】。**CI 必须用"产物是否存在"作为成功判据**，不能只看 step 退出码。

### 3.3 前置条件与版本矩阵（Qt 6.11 系）

| 项目 | 要求 | 依据 |
|------|------|------|
| 宿主 OS | **仅 Linux / macOS**（Windows 不支持） | 【官方文档】6.11.2 rst "only works with a Unix (Linux or macOS) host"；【源码】`android_helper.py` 的 `get_llvm_readobj` 硬编码 `{sys.platform}-x86_64` |
| 宿主 Python | **3.10 ~ 3.11**（≥3.12 直接报错） | 【源码】`android_deploy.py:209-211`；官方 6.11.2 文档未写此限制（文档滞后） |
| 宿主还需 | `jinja2 / pkginfo / tqdm / packaging==24.1`（`PySide6/scripts/requirements-android.txt`）+ `unzip`（`extract_zip` 依赖）+ `git` | 【源码】`requirements-android.txt`、`android_utilities.py:88-103` |
| 自动安装 | `buildozer==1.5.0`、`cython==0.29.33`（装进宿主 venv） | 【源码】`default.spec:30` |
| Android NDK | **r27c = 27.2.12479018**（Qt 6.11 官方支持矩阵），工具默认自动下载 r27c | 【实测】Qt for Android 文档 "Clang 17.0.2 (NDK r27c or 27.2.12479018)"；【源码】`android_utilities.py:20-21`（注意 `android_deploy.py:191` 帮助文本还写着 r26b，是过时文案；`develop` 分支文档已改口为 r28c——**以你实际安装的 PySide6 版本内的常量/文档为准**） |
| Android SDK | `platform-tools`、`build-tools`、`platforms;android-<API>`；Qt 6.11 可跑 Android 9~16；**targetSdk 建议 35**（36 会失去平板横屏锁定，见姊妹文档 §3.1） | 【实测】Qt for Android 支持矩阵 |
| JDK | **17+**（Gradle 要求） | 【官方文档】6.11.2 rst |
| 目标 ABI | arm64-v8a、x86_64（官方 wheel 齐备）；armeabi-v7a、x86 需自行交叉编译 Qt | 【官方文档】rst warning |
| 目标 Python | 由 p4a 编译（`develop` 当前 **CPython 3.14.2**） | 【源码】p4a `recipes/python3/__init__.py:57`；【官方文档】pyside-setup `dev` rst "currently CPython 3.14" |
| 入口文件 | 必须叫 `main.py`，且从项目目录运行 | 【源码】`android_deploy.py:71-76`（本项目已满足） |
| 单 APK 多 ABI | ❌ 不支持（q4 bootstrap 明确拒绝 >1 arch） | 【源码】p4a `bootstraps/qt/__init__.py:28-30` |

### 3.4 Android wheel：位置、命名、内容（实测）

**不在 PyPI**（PyPI 的 `pyside6` 只有桌面 wheel，tag 为 `cp310-abi3-*`）。Android wheel 只在 Qt 下载站：

```
https://download.qt.io/official_releases/QtForPython/pyside6/pyside6-6.11.2-6.11.2-cp311-cp311-android_aarch64.whl
https://download.qt.io/official_releases/QtForPython/pyside6/pyside6-6.11.2-6.11.2-cp311-cp311-android_x86_64.whl
https://download.qt.io/official_releases/QtForPython/shiboken6/shiboken6-6.11.2-6.11.2-cp311-cp311-android_aarch64.whl
```

| 项 | 实测值（6.11.2 / aarch64 / 2026-09-15） |
|----|------------------------------------------|
| PySide6 wheel | HTTP 200（经 302 跳转），**83,924,266 B（约 80 MiB）** |
| shiboken6 wheel | HTTP 200，**273,559 B** |
| PySide6 wheel 条目数 | 3,320 |
| `PySide6/Qt/lib/*.so` | **147** 个（含 `libQt6Core_arm64-v8a.so`、`libQt6Gui_…`、`libQt6Widgets_…` 等） |
| `*.abi3.so` | `PySide6/QtCore.abi3.so`、`QtWidgets.abi3.so`、`libpyside6.abi3.so` … |
| `.jar` | 11 个，含工具**强制要求存在**的 `PySide6/jar/Qt6AndroidBindings.jar`【源码】`buildozer.py:112-119` |
| 依赖描述 | 24 个 `Qt6<Module>_arm64-v8a-android-dependencies.xml`（权限/插件/本地库由此推导） |
| ELF `LOAD` 对齐 | Qt 库与 PySide6 abi3 模块 = `0x4000`（16 KB ✅）；**shiboken6 wheel 内 2 个 `.so` = `0x1000`（4 KB ⚠️）** |

历史版本同样可下载（aarch64 从 6.8.0 到 6.11.2 连续存在），因此**版本可回退**，这对排障很重要。

### 3.5 配置文件：`pysidedeploy.spec` 与 `buildozer.spec`

**`pysidedeploy.spec`**（工具自己的配置，首次自动生成；与本项目 PyInstaller 用的 `packaging/*.spec` 无关）

| 段 | 键 | 说明 |
|----|----|------|
| `app` | `title` / `project_dir` / `input_file` / `exec_directory` / `icon` / `project_file` | `input_file` 必须指向 `main.py`；`icon` 用于 `icon.filename` |
| `python` | `python_path` / `android_packages` | `android_packages` 默认 `buildozer==1.5.0,cython==0.29.33`；venv 建议放在**项目目录之外**（否则会被打进包）【官方文档】 |
| `qt` | `modules` / `plugins` | `modules` 自动探测，可手工追加（不带 `Qt` 前缀）；`plugins` 对 Android 无效 |
| `android` | `wheel_pyside` / `wheel_shiboken` / `plugins` | wheel 路径；插件名来自 wheel 内 `plugins/` 目录名 |
| `buildozer` | `mode` / `arch` / `recipe_dir` / `jars_dir` / `local_libs` / `ndk_path` / `sdk_path` | `mode=debug` 出 APK、`mode=release` 出 AAB（官方文档）；`arch` 由 wheel 文件名决定 |

**`buildozer.spec`**（工具生成并覆写的部分 + 我们需要补的部分）

| 键 | 工具写入值 | 我们需要追加/修正 |
|----|-----------|-------------------|
| `title` / `package.name` / `package.domain` | `<name>` / `<name>` / `org.<name>` | 已定：`package.name` 小写、`package.domain` 显式设为 `com.priloba` → 应用 ID **`com.priloba.mangaproof`**（buildozer 按 `domain + "." + name` 拼接，见 `targets/android.py:1004-1007`；不显式设 domain 会得到 `org.MangaProof.mangaproof`） |
| `requirements` | **`python3,shiboken6,PySide6`（写死）** | 必须注入 `numpy,Pillow,reportlab,attrs,typing-extensions,charset-normalizer,psd-tools`（+ 本地 recipe 名） |
| `source.dir` / `source.include_exts` | `.` / `py,png,jpg,kv,atlas`（buildozer 1.5.0 默认）+ 工具追加 `qml,js` | 需加 **`ttf`**（MiSans 字体 `font/MiSans-Medium.ttf`）、`json`；否则字体不会进包 |
| `source.exclude_dirs` | 默认注释掉 | **必须**排除 `.git,.venv,.uv-cache,deployment,tests,local_samples,logs,.pytest_cache,.benchmarks,packaging,README.assets,美术素材原文件` 等，否则包体会失控 |
| `p4a.bootstrap` / `p4a.branch` / `p4a.local_recipes` | `qt` / `develop` / `<project>/deployment/recipes` | 保留；把自建 recipe 一起放进 `<project>/deployment/recipes/` |
| `p4a.extra_args` | `--qt-libs=… --load-local-libs=… --init-classes=…` | 如需 p4a 直接签名，再追加 `--keystore/--signkey/--keystorepw/--signkeypw`（§5.5） |
| `android.archs` | `<wheel 架构>` | 一次一个 ABI |
| `android.add_jars` | `<deployment/jar/PySide6/jar/*.jar>` | 保留 |
| `android.api` / `minapi` / `ndk_api` | 未设置 → buildozer 默认 `api=31, minapi=21, ndk_api=21` | 建议 `android.api=35`（= targetSdk；**只侧载不上 Play 时建议 35**：targetSdk ≥36 会让系统在 ≥600dp 平板上忽略方向限制，横屏锁不住，详见姊妹文档 §3.1）、`minapi=30` / `ndk_api=30`（**minSdk 抬到 Android 11**：MANAGE_EXTERNAL_STORAGE 从 API 30 才有，见姊妹文档 §2.0；Qt 6.11 官方下限本是 28） |
| `android.accept_sdk_license` | 未设置 → `False` | **必须设 `True`**，否则 CI 里 sdkmanager 卡在 license 交互 |
| `android.permissions` | 由 wheel 的 XML 推导 | 视改造方案追加存储类权限（§4.2） |
| `android.release_artifact` / `android.debug_artifact` | release 段默认 **`aab`**、debug 段默认 `apk` | 若 P0 阶段只想要"release 模式但出 APK"，显式写 `android.release_artifact = apk` |

### 3.6 p4a / buildozer 侧关键事实

- **p4a 版本不固定**：buildozer 1.5.0 会 `git clone kivy/python-for-android`，分支来自 `p4a.branch=develop`【源码】`buildozer/targets/android.py:636+`。`develop` 是移动靶，**建议首轮成功后在包装脚本里写入 `p4a.commit=<sha>` 或 `p4a.branch` 换成 tag，保证可复现**。
- **本应用真正要打包的依赖闭包（由 `uv.lock` 解析得出，见 §3.7）**：共 **11 个包**（4 直接 + 7 传递）——

| 包 | lock 版本 | 关系 | 纯 Python | Android 打包动作 |
|----|-----------|------|-----------|------------------|
| numpy | 2.5.2 | 直接 | ❌ | p4a 官方 recipe（建议自建钉 2.5.2） |
| psd-tools | 1.18.0 | 直接 | ✅ | ★ 自建本地 recipe |
| pyside6 / pyside6-essentials / pyside6-addons / shiboken6 | 6.11.2 | 直接+传递 | ❌ | 官方 Android wheel（工具自动生成 recipe） |
| reportlab | 5.0.1 | 直接 | ✅ | p4a 官方 recipe（建议自建钉 5.0.1） |
| pillow | 12.3.0 | 传递 | ❌ | p4a 官方 recipe（建议自建钉 12.3.0） |
| attrs | 26.1.0 | 传递 | ✅ | ★ 自建本地 recipe |
| charset-normalizer | 3.5.1 | 传递 | ✅ | ★ 自建本地 recipe |
| typing-extensions | 4.16.0 | 传递 | ✅ | ★ 自建本地 recipe |

  即：**自建 recipe 只要 4 个纯 Python 包**（attrs / charset-normalizer / psd-tools / typing-extensions）+ 3 个可选钉版本的官方 recipe。
  **好消息**：psd-tools 的 `[composite]` extra（会拉 **aggdraw + scipy**）**不在闭包里**——应用只用 `node.topil()/node.composite()` 这些内置渲染，从不走 `psd_tools.composite` 的矢量/字形路径（本机 venv 也没装 aggdraw/scipy 且功能正常），因此**不需要 scipy recipe**（省掉一个巨大的编译项）。

- **p4a 官方 recipe 覆盖情况**（`develop` 分支实测，共 170 个 recipe；与上面闭包相关的部分）：

| 包 | 官方 recipe | 版本/说明 |
|----|------------|-----------|
| numpy | ✅ | `v2.3.0`，`url=git+https://github.com/numpy/numpy`（源码构建）——低于闭包的 2.5.2 |
| Pillow | ✅ | `11.3.0`，`depends=['png','jpeg','freetype']`，带 `setup.py.patch`——低于闭包的 12.3.0 |
| reportlab | ❌ **已失效** | p4a 内置 recipe 指向 hg 修订 `fe660f227cac`（`hg.reportlab.com`），该地址实测 **HTTP 403**（run 34914649124 因此失败）→ 已用本地 recipe 覆盖为 PyPI **5.0.1** sdist（见 §5.10 ③） |
| setuptools / cython / cffi / libffi / openssl / sqlite3 / freetype / harfbuzz / libwebp / png / jpeg / lxml / pycryptodome / scipy / pyjnius / android | ✅ | 供 recipe 内部或可选功能使用 |
| **attrs / typing-extensions / charset-normalizer / psd-tools / reportlab** | ❌ | 需自建本地 recipe（闭包内的 4 个纯 Python 包 + 因上游失效而被迫覆盖的 reportlab） |

- **p4a recipe 基类**（自建 recipe 的正确姿势）【源码】`pythonforandroid/recipe.py`：
  - `PythonRecipe`：`pip install . --compile --target <site-packages>`；
  - `PyProjectRecipe`（现代 `pyproject.toml` 包首选，如 attrs/typing-extensions/charset-normalizer/psd-tools）：支持 `python -m build` 源码构建，也支持"先在 PyPI 找 android 平台预编译 wheel"的路径；
  - `CythonRecipe` / `CompiledComponentsPythonRecipe`：带 C/Cython 组件的包。
- **p4a 命令行自带 release 签名**（`--release` 时经环境变量传给 Gradle）【源码】`pythonforandroid/toolchain.py:516-535, 982-998`：
  `--keystore`(→`P4A_RELEASE_KEYSTORE`)、`--signkey`(→`P4A_RELEASE_KEYALIAS`)、`--keystorepw`(→`P4A_RELEASE_KEYSTORE_PASSWD`)、`--signkeypw`(→`P4A_RELEASE_KEYALIAS_PASSWD`)；`aab` 走 Gradle `bundleRelease`，APK release 走 `assembleRelease`，产物后缀可能是 `release-unsigned`。
- **p4a 官方系统依赖**（Dockerfile 实测清单，可直接作为 CI apt 清单）：`ant autoconf automake autopoint ccache cmake g++ gcc git lbzip2 libffi-dev libltdl-dev libtool libssl-dev make openjdk-17-jdk patch pkg-config python3 python3-dev python3-pip python3-venv sudo unzip wget zip`。

### 3.7 依赖闭包怎么来：解析 `uv.lock`（不吃 `pyproject.toml` 的亏）

**为什么要这样**：`pyproject.toml` 只写了 4 个直接依赖，而 Android 侧要为**整棵传递依赖树**准备 recipe（`psd-tools` 的 attrs/typing-extensions、`reportlab` 的 charset-normalizer、`psd-tools`/`reportlab` 共同依赖的 Pillow/numpy……）。`uv.lock` 里有完整的解析结果与依赖边，是唯一准确的来源。

**关键规则（也是防"依赖组污染"的机制）**：
1. **只从根包（`mangaproof`，`source = { editable = "." }`）的 `dependencies` 出发遍历**——这是 uv 写进 lock 的**运行时**依赖（对应 `[project].dependencies`）；`dev-dependencies = { dev = [...], speedup = [...] }` 是**依赖组**，从它们出发的包**永远不可达**；
2. 带 `extra` 的依赖边，只有在该 extra 被显式请求时才跟随（本项目不请求任何 extra → `psd-tools[composite]` 的 aggdraw/scipy 自动排除）；
3. **硬校验**：`运行时闭包 ∩ 组专属包 == ∅`，否则报错。注意区分"组专属"与"两边都要"——例如 **`typing-extensions` 既是 psd-tools 的运行时依赖、又被 dev 组间接引入**，它**必须打包**（脚本用"组可达集 − 运行时闭包"来判定，不会误删）。

> 由于规则 1，**不管本机装了哪个依赖组（包括为性能调优装的 `speedup`），闭包结果都一样**；这不是"相信当前 uv.lock"，而是"只信 lock 里根项目的运行时依赖边"。

**脚本（建议实施阶段落库为 `scripts/android/analyze_lock_deps.py`）**

```python
#!/usr/bin/env python3
"""解析 uv.lock，输出 Android 打包需要的『运行时依赖闭包』。

规则：
- 只从根包（source.editable/virtual == "."）的 dependencies 出发（= [project].dependencies）；
- dev / speedup 等依赖组只用于『排除校验』，绝不作为遍历入口；
- 未请求的 extra 不跟随（psd-tools[composite] 之类不会混进来）；
- 断言：闭包 ∩ 组专属包 == ∅，且所有直接依赖都在闭包内。
"""
from __future__ import annotations
import argparse, json, sys, tomllib
from collections import deque
from pathlib import Path

P4A_OFFICIAL = {"numpy", "pillow", "reportlab"}                       # p4a 自带 recipe（版本偏旧）
LOCAL_RECIPE = {"attrs", "typing-extensions", "charset-normalizer", "psd-tools"}
QT_WHEEL = {"pyside6", "pyside6-essentials", "pyside6-addons", "shiboken6"}
NATIVE = QT_WHEEL | {"numpy", "pillow"}


def find_root(packages):
    for p in packages:
        src = p.get("source", {})
        if src.get("editable") == "." or src.get("virtual") == ".":
            return p
    raise SystemExit("uv.lock 中找不到根项目（editable/virtual == '.'）")


def reachable(pkgs, starts, *, skip_extras=True):
    seen, q = set(), deque(starts)
    while q:
        n = q.popleft()
        if n in seen:
            continue
        seen.add(n)
        for d in pkgs.get(n, {}).get("dependencies", []):
            if skip_extras and (d.get("extra") or (d.get("marker") or "").find("extra ==") >= 0):
                continue
            q.append(d["name"])
    return seen


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lock", default="uv.lock", type=Path)
    ap.add_argument("--json", action="store_true", help="输出 JSON（供 CI 校验 recipe 覆盖）")
    args = ap.parse_args()

    lock = tomllib.loads(args.lock.read_text(encoding="utf-8"))
    pkgs = {p["name"]: p for p in lock["package"]}
    root = find_root(lock["package"])

    direct = [d["name"] for d in root.get("dependencies", [])]
    runtime = reachable(pkgs, direct) | {root["name"]}

    group_reach, group_of = set(), {}
    for gname, deps in (root.get("dev-dependencies") or {}).items():
        for n in reachable(pkgs, [d["name"] for d in deps]):
            group_reach.add(n)
            group_of.setdefault(n, gname)
    group_only = group_reach - runtime
    overlap = sorted(group_reach & runtime)

    assert not (runtime & group_only), f"闭包混入组专属包：{sorted(runtime & group_only)}"
    assert set(direct) <= runtime, "直接依赖缺失"

    def action(name: str) -> str:
        if name in QT_WHEEL:
            return "qt-wheel"
        if name in P4A_OFFICIAL:
            return "p4a-official"
        if name in LOCAL_RECIPE:
            return "local-recipe"
        return "unknown"

    rows = [{
        "name": n,
        "version": pkgs[n].get("version"),
        "relation": "direct" if n in direct else "transitive",
        "pure_python": n not in NATIVE,
        "android_action": action(n),
    } for n in sorted(runtime) if n != root["name"]]

    if args.json:
        print(json.dumps({"root": root["name"], "packages": rows,
                          "excluded_group_only": sorted(group_only),
                          "group_overlap_must_package": overlap},
                         ensure_ascii=False, indent=2))
        return 0

    print(f"根项目 {root['name']} {root.get('version')}："
          f"运行时闭包 {len(rows)} 个（直接 {len(direct)}）")
    print(f"组专属包 {len(group_only)} 个已排除；组/运行时重叠（必须打包）：{overlap}\n")
    for r in rows:
        flag = "★" if r["android_action"] == "local-recipe" else " "
        print(f"{flag} {r['name']:22s}{str(r['version']):11s}{r['relation']:11s}"
              f"{'纯Py' if r['pure_python'] else '原生':4s}{r['android_action']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

**本次实际运行结果（2026-09-15，当前 `uv.lock`）**

- 根项目 `mangaproof 1.0.0`；**运行时闭包 11 个包**（直接 4）；依赖组可达 23 个；
- **22 个"组专属包"被正确排除**，与闭包零交集：
  - `[dev]` altgraph、colorama、iniconfig、macholib、packaging、pefile、pluggy、pygments、pyinstaller、pyinstaller-hooks-contrib、pytest、pywin32-ctypes、setuptools
  - `[speedup]` line-profiler、memory-profiler、psutil、py-cpuinfo2、py-spy、pyinstrument、pytest-benchmark、snakeviz、tornado
- **组/运行时重叠：`typing-extensions`** → 必须打包（它是 psd-tools 的运行时依赖）；
- 因此 Android 侧要准备的就是 §3.6 那张 11 行表：**4 个自建纯 Python recipe + 3 个可选钉版本的官方 recipe + Qt wheel**。

> 用途延伸：实施阶段把这个脚本接进 CI（`--json`），在构建前 **assert 每个包的 `android_action != "unknown"`**，一旦有人加了新依赖就会立刻失败并提示补 recipe——比"构建到一半才炸"友好得多。

---

## 4. 本项目适配性差距分析

### 4.1 现状（仓库事实）

| 维度 | 现状 | 证据 |
|------|------|------|
| 语言版本 | `requires-python = ">=3.12"`；venv 为 3.12.3 | `pyproject.toml:6` |
| 依赖 | `numpy>=2.5.2`、`psd-tools>=1.18.0`、`pyside6>=6.11.2`、`reportlab>=5.0.1`（venv 实装 Pillow 12.3.0） | `pyproject.toml:7-12`；dist-info |
| 入口 | 仓库根有 `main.py`（工具的硬要求 ✅） | `main.py` |
| 代码规模 | `mangaproof/` 50 个 py 文件 / 1.2 MB | `find` |
| 平台分支 | 仅 `console.py`、`ui/dark_titlebar.py`、`ui/settings_dialog.py` 有 win32 分支；非 Windows 走 no-op | grep 结果 |
| 原生扩展 | `mangaproof/_psd_fast.c`（纯 C，ctypes 加载），`psd_accel.py` 在加载失败时**自动回退纯 Python** | `psd_accel.py:26-40`、`README` |
| 第三方原生加速 | psd-tools 的 Cython `_rle` **缺失时自动回退** `rle.py` | `.venv/.../psd_tools/compression/__init__.py:81-84` |
| Qt API | `QMenuBar/QStatusBar/QDockWidget/QFileDialog/QShortcut/QProgressDialog/QMessageBox`、滚轮缩放、原生缩放手势 | grep 统计 |
| 交互 | 纯键鼠：`Up/Down/Left/Right/Enter//=Space/Esc/Ctrl+S…`，右键/滚轮 | `settings.json` 键位表 |
| 资源 | `font/MiSans-Medium.ttf`(8.1 MB)、`ico/ico.png`(1024×1024 RGBA ✅ 可直接做 Android 图标) | `ls`/PIL |
| 数据落盘 | `settings.json`/`recent.json`/`logs/` 写在"程序目录"（`get_app_dir()`） | `config/paths.py` |
| 内存策略 | LRU 256 / 512 / 768 MB（三档），另有背景图池 | `README` 性能章节 |
| 现有 CI | 桌面 5 组合 PyInstaller + 冒烟 + tag 发布 | `.github/workflows/build.yml` |

### 4.2 逐项差距与处置建议

| # | 差距 | 影响 | 建议处置 | 阶段 |
|---|------|------|----------|------|
| G1 | 宿主 3.12 vs 工具要求 ≤3.11 | 构建直接失败 | CI 单独建 Python 3.11 venv（放项目目录之外）；**本地不装任何东西** | P0 |
| G2 | 依赖需 p4a recipe，且 4 个包无官方 recipe | 运行期 `ImportError` | 自建本地 recipe：`attrs`、`typing-extensions`、`charset-normalizer`、`psd-tools`（`PyProjectRecipe`），并入 `<project>/deployment/recipes/` | P1 |
| G3 | 依赖版本漂移：numpy 2.5.2→recipe 2.3.0；Pillow 12.3.0→11.3.0；reportlab 5.0.1→hg 旧修订 | 行为差异/隐性 bug | 自建 recipe 钉版本（numpy 2.5.2 / Pillow 12.3.0 / reportlab 5.0.1 sdist），或在 Android 侧放宽 `pyproject` 约束并实测 | P1/P2 |
| G4 | 自研 C 扩展 `_psd_fast.so` 无 Android 版本 | 仅性能下降（有回退） | P0/P1 直接禁用（回退纯 Python）；P3 可选：写 p4a recipe 用 NDK 编译 arm64 版（务必加 `-Wl,-z,max-page-size=16384`） | P3 |
| G5 | `QMenuBar` 在 Android 无原生菜单栏 | 文件/设置/关于菜单不可达 | 改为 `QToolBar` + 抽屉/`QPushButton` 菜单，或 Android 专用布局分支 | P3 |
| G6 | `QShortcut` 依赖物理键盘；滚轮/右键/悬停交互 | 触屏无法操作 | 触屏手势（捏合缩放、长按菜单、点击标注）、按钮化常用操作；可保留键盘支持（外接键盘/Chromebook 可用） | P3 |
| G7 | `QDockWidget` 停靠面板在窄屏不可用 | 布局崩坏 | 改为 Tab/抽屉/堆叠页面布局（`QStackedWidget` 或 `QTabWidget`） | P3 |
| G8 | `QFileDialog.getOpenFileName/getExistingDirectory` 无法访问 Android 共享存储 | **核心流程（选漫画文件夹）不可用** | **已定方案**：改用 `getExistingDirectoryUrl()`（系统原生 SAF 选择器）→ 纯 Python 把 tree URI 映射为真实路径（`primary:`/`<UUID>:`/`raw:`，详见姊妹文档 §2.8）→ 之后全部走真实路径直读；配 `MANAGE_EXTERNAL_STORAGE` 权限 + 未授权弹窗（§2.7）。**不需要** JNI/`QJniObject`（PySide6 也没有） | P1 |
| G9 | 数据落盘在"程序目录" | p4a 会把应用解包到 `/data/data/<pkg>/files/...`（可写）→ `get_app_dir()` 语义仍然成立 | 保持现状；确认首次启动解包耗时与磁盘占用；如需更规范可用 `QStandardPaths.AppDataLocation` | P2 |
| G10 | 内存策略 256–768 MB LRU + 大 PSD | 中低端机 OOM/被系统杀 | Android 侧**固定**"激进（256 MB）"，已实现（读取强制 + 写回配置 + 设置页灰显，见适配设计文档 §5）；压测 500 MB 级 PSD，数值是否再下调由真机数据决定 | P2/P3 |
| G11 | 字体/图标资源不在默认打包扩展名内 | 字体丢失 → 回退系统字体（MiSans 许可要求"随软件整体分发"，不宜缺失） | `source.include_exts` 加 `ttf`（`png` 已在默认列表） | P0/P1 |
| G12 | 打包范围未收敛 | 包体会把 `.git`、`.venv`、`tests` 等一起收进去 | `source.exclude_dirs` 显式排除（§3.5 表） | P0/P1 |
| G13 | 无 Android 版 UI 尺寸/DPI 设计 | 手机端体验差 | 明确目标形态：**平板 / Chromebook / 折叠屏优先**，手机端作为"轻量查看" | 产品决策 |
| G14 | 现有 CI 的 tag 发布只覆盖桌面产物 | Release 里没有 Android 包 | 新 workflow 独立产出 APK/AAB，并在同一 tag 下附加 | P1 |

### 4.3 依赖可行性矩阵（版本取自 `uv.lock` 的运行时闭包，见 §3.7）

| 包（lock 版本） | 纯 Python? | Android 方案 | 结论 |
|-----------------|-----------|--------------|------|
| numpy 2.5.2 | ❌（C） | p4a 官方 recipe（自带 2.3.0）或自建 recipe 钉 2.5.2 | 可行（源码编译，耗时最长项之一） |
| Pillow 12.3.0 | ❌（C） | p4a 官方 recipe（自带 11.3.0，`png/jpeg/freetype`）或自建钉 12.3.0 | 可行 |
| reportlab 5.0.1 | ✅（可选 C 加速） | p4a 官方 recipe（旧 hg 修订）或自建 recipe 用 PyPI 5.0.1 sdist | 可行，建议自建钉版本 |
| attrs 26.1.0 | ✅ | 自建 `PyProjectRecipe` | 可行 |
| typing-extensions 4.16.0 | ✅ | 自建 `PyProjectRecipe`（注意：它同时出现在 dev 组，但**是运行时依赖，必须打包**） | 可行 |
| charset-normalizer 3.5.1 | ✅（纯 Python 回退） | 自建 `PyProjectRecipe` | 可行 |
| psd-tools 1.18.0 | ✅（Cython `_rle` 可选，缺失自动回退） | 自建 `PyProjectRecipe` + 依赖上面的包 | 可行（RLE 解码会慢） |
| PySide6 / Essentials / Addons / shiboken6 6.11.2 | ❌ | 官方 Android wheel + 工具自动生成 recipe | 可行 |
| ~~aggdraw / scipy~~（psd-tools `[composite]` extra） | ❌ | **不在闭包内，无需处理** | ✅ 省掉两个大件 |
| `_psd_fast`（自研） | ❌ | 禁用（回退）或自建 NDK recipe | 可行（P3 可选） |

---

## 5. GitHub Actions 云端构建方案（不使用本地工具链）

### 5.1 设计原则

1. **本地零依赖**：不在本机安装 NDK/SDK/buildozer/任何新包；所有构建、签名、验证都在 CI 完成。
2. **构建环境与项目环境隔离**：Android 构建用 Python 3.11 的临时 venv（放 `$RUNNER_TEMP`，**必须在项目目录之外**）；项目自身的 `uv` 环境（3.12）完全不参与 Android 构建。
3. **可复现**：wheel 版本、NDK 版本、Android API、p4a commit 全部显式钉住。
4. **成功判据以产物为准**：因为工具会吞异常，必须检查 APK/AAB 文件存在 + 用 `apksigner verify` 校验。
5. **与现有 `build.yml` 解耦**：独立 workflow `android.yml`。现状（已落地）：**push 到 master 自动构建**（`release` + 签名）与 `workflow_dispatch` 手动构建，且用 matrix **同时构建 aarch64 与 x86_64 两个 ABI**（互不阻塞）。

### 5.2 Job 图

```
android-apk (ubuntu-24.04)
  ├─ setup: checkout → Python 3.11 → JDK 17 → Android SDK/NDK → 宿主 venv(pyside6 6.11.2 + 工具依赖)
  ├─ fetch: 下载 PySide6/shiboken6 Android wheel（带 actions/cache）
  ├─ build: pyside6-android-deploy（经包装脚本注入依赖）→ .apk
  ├─ sign : zipalign → apksigner sign → apksigner verify
  └─ upload: actions/upload-artifact（并校验存在性）
release-android (仅 tag)  → 把 APK/AAB 附加到 GitHub Release
（可选）android-x86_64 矩阵项：给模拟器冒烟用（云端 emulator-runner）
（暂不做）android-aab 矩阵项：将来上 Play 时切 `android.release_artifact = aab`
```

### 5.3 环境准备细节

| 项 | 选择 | 备注 |
|----|------|------|
| Runner | `ubuntu-24.04`（备选 `ubuntu-22.04`） | p4a 官方 Docker 基于 22.04；24.04 若遇 glibc/cmake 兼容问题，回退 22.04【推断】 |
| Action 版本（当前主版本，已核实） | `actions/checkout@v7`、`actions/setup-python@v7`、`actions/setup-java@v6`、`android-actions/setup-android@v4`、`actions/cache@v6`、`actions/upload-artifact@v7`、`actions/download-artifact@v8`、`softprops/action-gh-release@v3`（仓库现有 workflow 用 `gh release create`，同样可行） | 与本仓库 `build.yml` 的用法保持一致 |
| Python | `3.11`（**必须**，工具硬检查） | venv 建在 `$RUNNER_TEMP/android-venv` |
| 宿主 pip 包 | `pyside6==6.11.2`、`jinja2`、`pkginfo`、`tqdm`、`packaging==24.1` | 工具随后自装 `buildozer==1.5.0,cython==0.29.33` |
| JDK | Temurin 17 | p4a/Gradle 要求 |
| Android SDK | `android-actions/setup-android@v4` + `sdkmanager` 安装 `platform-tools`、`platforms;android-35`、`build-tools;35.0.0` | 35 = targetSdk（保平板强制横屏；如需 36 见姊妹文档 §3.1 警告） |
| Android NDK | `sdkmanager --install "ndk;27.2.12479018"`（= r27c，与 Qt 6.11 官方一致；**已核实该包存在于 Google SDK 仓库清单**）**或** 让工具自动从 `dl.google.com` 下载（`android-ndk-r27c-linux.zip`，实测 663,987,688 B） | 传 `--ndk-path $ANDROID_HOME/ndk/27.2.12479018 --sdk-path $ANDROID_HOME` 最可控 |
| apt 依赖 | p4a Dockerfile 清单（§3.6 末条）+ `unzip`/`git`/`zip` | 一次装齐，避免中途失败 |
| 磁盘 | 需要数 GB 空闲（NDK ~2 GB + SDK + Gradle + p4a 下载 + 构建中间物） | 必要时清理 runner 预装无用组件（如 `/usr/share/dotnet`、`/usr/local/lib/android/sdk/ndk/*`）【推断，需实测】 |

**缓存键设计建议**

| 缓存 | 路径 | 键 |
|------|------|-----|
| Qt Android wheel | `${{ runner.temp }}/qt-wheels` | `qt-android-wheels-6.11.2-aarch64` |
| buildozer/p4a 源码与构建（主要在**项目内**） | `.buildozer/`（= `android/platform/python-for-android`、`android/platform/build-<arch>`） | `buildozer-${{ runner.os }}-${{ hashFiles('packaging/android/recipes/**') }}` |
| buildozer 全局目录 / pip | `~/.buildozer`、`~/.cache/pip` | 按 runner + 版本号 |
| Gradle | `~/.gradle` | `gradle-${{ runner.os }}-${{ hashFiles('...') }}` |
| NDK/SDK | `$ANDROID_HOME/ndk/27.2.12479018` 等 | 按版本号（也可直接不缓存，用 setup-android 每次装） |

### 5.4 构建步骤（含"注入依赖"这一必需技巧）

因为 §3.2 第 5/9 步的两个陷阱，CI 里不能用"预置 spec"，而要使用**包装脚本**（在 CI 的 venv 中运行；只在内存里改行为，不动 PySide6 安装文件）：

```
scripts/android/build_android.py   # 实施阶段新增，本次不创建
  ├─ import PySide6.scripts.deploy_lib.android.buildozer as bz
  ├─ 备份并替换 bz.BuildozerConfig 为一个子类：
  │     def __init__(self, spec, cfg):
  │         super().__init__(spec, cfg)                       # 工具原来的覆写逻辑全部保留
  │         self.set_value("app", "requirements", 现值的 + ",numpy,Pillow,reportlab,attrs,typing-extensions,charset-normalizer,psd-tools")
  │         self.set_value("app", "source.include_exts", "py,png,jpg,ttf,json,qml,js")
  │         self.set_value("app", "source.exclude_dirs", ".git,.venv,.uv-cache,deployment,tests,local_samples,logs,.pytest_cache,.benchmarks,packaging,README.assets")
  │         self.set_value("app", "android.accept_sdk_license", "True")
  │         self.set_value("app", "android.api", "35"); minapi/ndk_api = 30   # 35 = targetSdk（保平板强制横屏）；30 = 全文件访问权限的下限
  │         self.set_value("app", "package.name", "mangaproof")
  │         # —— 第二轮需求新增（详见姊妹文档 §3.1 / §4.4）——
  │         self.set_value("app", "orientation", "landscape")        # 强制横屏（buildozer 默认是 portrait！）
  │         # 注：需求方最终选定 sensorLandscape（允许左右横屏翻转）→ 改走下面这行
  │         # self.set_value("app", "android.manifest.orientation", "sensorLandscape")
  │         self.set_value("app", "android.permissions", 现值 + ",android.permission.MANAGE_EXTERNAL_STORAGE")
  │         self.set_value("app", "fullscreen", "1")                 # 全屏主题（buildozer 默认是 0！）
  │         self.set_value("app", "android.release_artifact", "apk") # 仅出 APK（release 默认是 aab）
  │         self.set_value("app", "p4a.extra_args", 现值 + " --display-cutout shortEdges")  # 刘海/全面屏
  │         # 自适应图标（美术资源就位后再打开）：
  │         # self.set_value("app", "icon.adaptive_foreground.filename", "ico/android/ic_launcher_foreground.png")
  │         # self.set_value("app", "icon.adaptive_background.filename", "ico/android/ic_launcher_background.png")
  │         self.set_value("app", "p4a.commit", "<固定 sha>")   # 可选：锁 p4a 版本
  │         shutil.copytree("packaging/android/recipes", cfg.recipe_dir, dirs_exist_ok=True)  # 复制自建 recipe 进 deployment/recipes
  │         self.update_config()
  ├─ 调用 android_deploy.main(name="MangaProof", pyside_wheel=..., shiboken_wheel=..., ndk_path=..., sdk_path=..., keep_deployment_files=True, force=True)
  └─ 构建结束后：断言产物存在（工具吞异常），并按需打补丁/重命名
```

> 若担心劫持内部类随版本漂移，**等价兜底**是在 CI 里对 venv 中的 `PySide6/scripts/deploy_lib/android/buildozer.py` 打一行 `sed` 补丁（把写死的 requirements 行替换掉）——两者效果相同，前者可入库、可 review，更推荐。
>
> 补充：宿主 Python ≤3.11 的检查写在 `android_deploy.py` 的 `__main__` 里（CLI 入口），直接 `import` 并调用 `main()` 会绕过这条检查；但 **CI 仍然必须用 3.11**，因为真正跑构建的是 buildozer 1.5.0 + p4a，它们才是"buildozer 限制"的根源。

**构建命令（CI 实际执行的形态）**

```bash
source "$RUNNER_TEMP/android-venv/bin/activate"
pyside6-android-deploy \
  --name MangaProof \
  --wheel-pyside  "$RUNNER_TEMP/qt-wheels/pyside6-6.11.2-6.11.2-cp311-cp311-android_aarch64.whl" \
  --wheel-shiboken "$RUNNER_TEMP/qt-wheels/shiboken6-6.11.2-6.11.2-cp311-cp311-android_aarch64.whl" \
  --ndk-path "$ANDROID_HOME/ndk/27.2.12479018" \
  --sdk-path "$ANDROID_HOME" \
  --force --keep-deployment-files -v
ls -la ./*.apk ./*.aab 2>/dev/null    # ← 真正的成功判据
```

### 5.5 签名（仓库已备好 4 个 secrets）

两条可行路径，**建议默认走 B（事后重签），把 A 作为可选**：

**B. 产物重签（推荐，简单可控）**

```bash
echo "$KEYSTORE_BASE64" | base64 -d > "$RUNNER_TEMP/release.jks"     # 只在 runner 临时目录落地，用完删
BT="$(ls -d "$ANDROID_HOME"/build-tools/* | sort -V | tail -1)"
"$BT/zipalign" -p -f 4 app-unsigned.apk app-aligned.apk              # 先对齐，再签名（顺序不能反）
"$BT/apksigner" sign \
  --ks "$RUNNER_TEMP/release.jks" --ks-key-alias "$KEY_ALIAS" \
  --ks-pass env:KS_PASS --key-pass env:KEY_PASS \
  --out "MangaProof-<version>-android-arm64-v8a.apk" app-aligned.apk
"$BT/apksigner" verify --verbose --print-certs "MangaProof-…apk"     # 校验签名
rm -f "$RUNNER_TEMP/release.jks"
```

**A. 让 p4a/Gradle 直接签（适用于 `mode=release` 的 AAB，因为 Gradle `bundleRelease` 必须有签名配置）**：在调用工具**之前**导出 4 个环境变量即可——buildozer 1.5.0 正是靠它们判断"release 要不要签名"【源码】buildozer 1.5.0 `targets/android.py:925-940`：

```bash
export P4A_RELEASE_KEYSTORE="$RUNNER_TEMP/release.jks"
export P4A_RELEASE_KEYALIAS="$KEY_ALIAS"
export P4A_RELEASE_KEYSTORE_PASSWD="$KS_PASS"
export P4A_RELEASE_KEYALIAS_PASSWD="$KEY_PASS"
# 等价替代：在包装脚本里把这些以 p4a 参数形式追加进 p4a.extra_args
#   --keystore=… --signkey=… --keystorepw=… --signkeypw=…   （p4a toolchain.py:516-535）
```

**AAB（本轮不做，保留开关）**：需求方明确暂不考虑 AAB，本方案默认 `android.release_artifact = apk`。将来若上 Play，再切 `aab` 并用 `jarsigner`（不是 `apksigner`）签名：
`jarsigner -keystore release.jks -storepass "$KS_PASS" -keypass "$KEY_PASS" -sigalg SHA256withRSA -digestalg SHA-256 app.aab "$KEY_ALIAS"`，随后 `jarsigner -verify`。
Play 要求（届时再核对当季政策）：AAB 格式、targetSdk 达标、**16 KB page size 兼容**。

**Secrets 到环境变量的映射（务必用中间变量名）**：
`env: { KS_PASS: ${{ secrets.KEYSTORE_PASSWORD }}, KEY_PASS: ${{ secrets.KEY_PASSWORD }}, KEY_ALIAS: ${{ secrets.KEY_ALIAS }}, KEYSTORE_BASE64: ${{ secrets.KEYSTORE_BASE64 }} }`，因为 `apksigner --ks-pass env:NAME` 需要的是**变量名**。

### 5.6 发布

**现状（已落地）**：

- **push 到 `master` → 自动构建**：两个 ABI（`aarch64` / `x86_64`）并行，`release` 模式，配置了 keystore secrets 就签名；产出的两个 APK 分别作为独立 artifact（`MangaProof-<version>-android-<abi>`）上传，**保留期与桌面一致**（不设 `retention-days`，走仓库默认，即 upload-artifact 的 90 天）；
- `workflow_dispatch` 手动触发：可选 `release`/`debug` 与是否签名（未配 secrets 时只告警、跳过签名，产出 `-unsigned` APK，不让流水线变红）；
- **不做** Release 发布：Android 只上传 artifact（`build.yml` 的 `v*` tag → Release 作业只管桌面三平台产物）；若将来要把 APK 也挂到 Release，注意与桌面 release 作业共用同一个 tag，必须先探测 Release 是否存在再决定 `gh release upload` 还是 `gh release create`，避免两个作业抢占同名 Release；
- 产物命名：`MangaProof-<version>-android-<abi>.apk`（与桌面 `MangaProof-<version>-<platform>` 风格对齐）。

### 5.7 完整 workflow 骨架（可直接演化为 `.github/workflows/android.yml`）

> 注：实际落地的 `android.yml` 在此基础上改为 **push 到 master 自动触发**，并用 matrix
> 同时构建 `aarch64` 与 `x86_64` 两个 ABI（Qt bootstrap 一次只能一个 ABI，因此按 ABI 拆 job）。

> 标注：✅=已核实可用；🔶=需要实测/按当季政策调整；🚧=需要先创建包装脚本与 recipe 文件。

```yaml
name: android

on:
  workflow_dispatch:
  push:
    tags: ["v*"]

permissions:
  contents: read

env:
  PYSIDE_VERSION: "6.11.2"
  ANDROID_NDK: "27.2.12479018"     # NDK r27c，Qt 6.11 官方支持版本 ✅
  ANDROID_API: "35"                # = targetSdk（35 保住平板强制横屏；36 会被系统忽略方向限制）
  ANDROID_MIN_API: "30"            # = minSdk（Android 11；全文件访问权限 MANAGE_EXTERNAL_STORAGE 的下限）
  QT_WHEELS: "https://download.qt.io/official_releases/QtForPython"

jobs:
  android-apk:
    runs-on: ubuntu-24.04          # 🔶 如遇兼容问题回退 ubuntu-22.04
    steps:
      - uses: actions/checkout@v7

      # 1) 工具链：Python 3.11（工具硬要求）✅ + JDK 17 ✅
      - uses: actions/setup-python@v7
        with: { python-version: "3.11" }
      - uses: actions/setup-java@v6
        with: { distribution: temurin, java-version: "17" }

      # 2) Android SDK/NDK ✅
      - uses: android-actions/setup-android@v4
      - name: Install SDK packages
        run: |
          sdkmanager --install "platform-tools" "platforms;android-${ANDROID_API}" \
                                "build-tools;${ANDROID_API}.0.0" "ndk;${ANDROID_NDK}"
          yes | sdkmanager --licenses > /dev/null

      # 3) p4a 需要的系统包（清单来自 p4a 官方 Dockerfile）✅
      - name: Install build dependencies
        run: |
          sudo apt-get update -qq
          sudo apt-get install -y -qq \
            ant autoconf automake autopoint ccache cmake g++ gcc git lbzip2 \
            libffi-dev libltdl-dev libtool libssl-dev make patch pkg-config \
            python3-dev unzip wget zip

      # 4) 宿主 venv（必须在项目目录之外）✅
      - name: Host venv for the deploy tool
        run: |
          python -m venv "$RUNNER_TEMP/android-venv"
          source "$RUNNER_TEMP/android-venv/bin/activate"
          pip install -q --upgrade pip
          pip install -q "pyside6==${PYSIDE_VERSION}" jinja2 pkginfo tqdm "packaging==24.1"
          echo "$RUNNER_TEMP/android-venv/bin" >> "$GITHUB_PATH"

      # 5) Qt Android wheel（实测存在的 URL）✅
      - name: Download Qt Android wheels
        run: |
          mkdir -p "$RUNNER_TEMP/qt-wheels" && cd "$RUNNER_TEMP/qt-wheels"
          curl -fL -o pyside6-android.whl   "$QT_WHEELS/pyside6/pyside6-${PYSIDE_VERSION}-${PYSIDE_VERSION}-cp311-cp311-android_aarch64.whl"
          curl -fL -o shiboken6-android.whl "$QT_WHEELS/shiboken6/shiboken6-${PYSIDE_VERSION}-${PYSIDE_VERSION}-cp311-cp311-android_aarch64.whl"
          ls -la

      # 6) 构建（包装脚本注入依赖 + 调用官方工具）🚧
      - name: Build APK
        run: |
          python scripts/android/build_android.py \
            --pyside-wheel "$RUNNER_TEMP/qt-wheels/pyside6-android.whl" \
            --shiboken-wheel "$RUNNER_TEMP/qt-wheels/shiboken6-android.whl" \
            --ndk-path "$ANDROID_HOME/ndk/${ANDROID_NDK}" --sdk-path "$ANDROID_HOME" \
            --name MangaProof --arch aarch64 --mode debug

      # 7) 产物判据（工具会吞异常，退出码不可信）✅
      - name: Assert APK produced
        run: |
          set -e
          APK=$(ls -1 ./*.apk 2>/dev/null | head -1)
          [ -n "$APK" ] || { echo "::error::未产出 APK（工具异常被吞，请查看上方日志）"; exit 1; }
          echo "APK=$APK" >> "$GITHUB_ENV"

      # 8) 16 KB page size 校验（Play 要求）🔶
      - name: Check 16 KB alignment
        run: |
          BT="$(ls -d "$ANDROID_HOME"/build-tools/* | sort -V | tail -1)"
          "$BT/zipalign" -c -P 16 -v 4 "$APK" || echo "::warning::zipalign 16KB 校验未通过"

      # 9) 签名 ✅（secrets 已就绪）
      - name: Sign APK
        env:
          KEYSTORE_B64: ${{ secrets.KEYSTORE_BASE64 }}
          KS_PASS: ${{ secrets.KEYSTORE_PASSWORD }}
          KEY_PASS: ${{ secrets.KEY_PASSWORD }}
          KEY_ALIAS: ${{ secrets.KEY_ALIAS }}
        run: |
          BT="$(ls -d "$ANDROID_HOME"/build-tools/* | sort -V | tail -1)"
          echo "$KEYSTORE_B64" | base64 -d > "$RUNNER_TEMP/release.jks"
          OUT="MangaProof-android-arm64-v8a.apk"
          "$BT/zipalign" -p -f 4 "$APK" "$RUNNER_TEMP/aligned.apk"
          "$BT/apksigner" sign --ks "$RUNNER_TEMP/release.jks" --ks-key-alias "$KEY_ALIAS" \
            --ks-pass env:KS_PASS --key-pass env:KEY_PASS --out "$OUT" "$RUNNER_TEMP/aligned.apk"
          "$BT/apksigner" verify --verbose --print-certs "$OUT"
          rm -f "$RUNNER_TEMP/release.jks"
          echo "SIGNED_APK=$OUT" >> "$GITHUB_ENV"

      - uses: actions/upload-artifact@v7
        with:
          name: MangaProof-android-arm64-v8a
          path: ${{ env.SIGNED_APK }}
          if-no-files-found: error
```

### 5.8 时长/体积/配额预估（🔶 均为待实测，非官方数据）

| 项 | 预估 | 说明 |
|----|------|------|
| 首次构建（冷缓存） | 40–90 分钟 | 含 NDK 下载（压缩包 634 MB，解压后 >2 GB）、cmdline-tools、目标 CPython 编译、numpy/Pillow 源码编译、Gradle 首次下载 |
| 命中缓存后 | 20–40 分钟 | 主要剩 CPython/依赖重编译（p4a 构建目录缓存可以显著缩短） |
| APK 体积 | 60–120 MB | PySide6 Qt 库（147 个 `.so`，仅实际用到的会被 copy）+ numpy + Pillow；单 ABI 比多 ABI 小一半 |
| CI 配额 | 每次运行占 **2 个 runner**（两个 ABI 并行，各约 30–60 分钟）；push 到 master 自动跑 | 若只想按需构建，可在 `push` 上加 `paths-ignore`（如 `docs/**`）或改回手动触发 |

### 5.9 仓库文件清单（✅ = 已落地 / 🚧 = 待落地）

| 文件 | 状态 | 作用 |
|------|------|------|
| `scripts/android/analyze_lock_deps.py` | ✅ | 解析 `uv.lock` 输出运行时依赖闭包（§3.7）；`--fail-on-unknown` 作为 CI 卡口 |
| `scripts/android/build_android.py` | ✅ | 包装脚本：预置 `pysidedeploy.spec` → 劫持 `BuildozerConfig` 注入 requirements/权限/横屏/图标/打包范围 → 调用官方工具 → **断言 APK 存在**（工具会吞异常） |
| `packaging/android/recipes/{attrs,typing-extensions,charset-normalizer,psd-tools}/__init__.py` | ✅ | 4 个本地 p4a recipe（版本取自 `uv.lock`，URL 已逐个实测 HTTP 200） |
| `.github/workflows/android.yml` | ✅ | Android 构建（**按需求不做冒烟测试**）：push 到 master 自动 + 手动触发；matrix 并行 `aarch64`/`x86_64` 两个 ABI；流程＝依赖覆盖卡口 → 宿主 venv → Qt wheel → 构建 → 断言产物 → 16KB 告警 → 签名 → 上传 artifact |
| `.gitignore` 增补 | ✅ | `pysidedeploy.spec`、`buildozer.spec`、`deployment/`、`*.apk`、`*.aab` |
| `packaging/android/recipes/reportlab/__init__.py` | ✅ | **必须**覆盖：p4a 内置 recipe 的 hg 源已 403（§5.10 ③） |
| `ico/android/res/mipmap-anydpi-v26/icon.xml` | ✅ | 自带自适应图标 XML（绕开 p4a 不建目录的 bug，§5.10 ④） |
| `packaging/android/recipes/{numpy,Pillow}/__init__.py` | 🚧 | 可选：钉版本 recipe（对齐 lock 的 2.5.2 / 12.3.0），P1 再做 |
| `packaging/android/README.md` | 🚧 | Android 构建说明（本地不构建，指向 CI） |
| `mangaproof/storage/**`（应用代码） | 🚧 | 存储薄层：SAF 选目录、`content://`→真实路径映射、全文件访问权限检测、扫描/导出 worker（姊妹文档 §2.3） |

**已落地内容的本地自测（零工具链，均已在本次通过）**：

```bash
# 1) 依赖闭包：应输出 11 个包 + 22 个组专属包被排除 + typing-extensions 属"重叠必须打包"
uv run python scripts/android/analyze_lock_deps.py
uv run python scripts/android/analyze_lock_deps.py --fail-on-unknown   # exit 0

# 2) 包装脚本：版本号解析 / 闭包→requirements / spec 生成 / 猴补丁可安装
uv run python - <<'PY'
import importlib.util, sys
s = importlib.util.spec_from_file_location("b", "scripts/android/build_android.py")
m = importlib.util.module_from_spec(s); sys.modules["b"] = m; s.loader.exec_module(m)
print(m.project_version(), m.numeric_version("1.0.0"), m.lock_requirements())
m.write_pysidedeploy_spec(mode="release", arch="x86_64"); print("spec ok")
PY

# 3) workflow / 脚本语法
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/android.yml'))"
uv run python -m py_compile scripts/android/*.py packaging/android/recipes/*/__init__.py
```

> ⚠️ 包装脚本**本机跑不到底**（宿主依赖 buildozer/p4a/jinja2 等只存在于 CI 的 3.11 venv）；本机缺依赖时会给出明确提示而非 traceback（已验证）。首次 workflow_dispatch 是端到端验证点。

### 5.10 CI 运行记录（2026-09-15，逐次收敛）

**① run 34913456068 —— `Setup Android SDK` 8 秒失败**

| 项 | 内容 |
|----|------|
| 现象 | `android-actions/setup-android@v4` 步骤秒失败，后续步骤全部 skipped；同分支的桌面 `build` 流程正常 |
| **根因** | 该 action 的默认输入是 `packages: 'tools platform-tools'`，而 **Google SDK 仓库里已经不存在独立的 `tools` 包**（实测 repository2-3.xml / repository2-1.xml 共 278 个包，`tools` 匹配数为 **0**；`cmdline-tools` 才是它的替代品）→ `sdkmanager --install tools` 立即报错 |
| 修复 | ① 显式传参 `packages: "platform-tools"` 覆盖默认值；② `sdkmanager --install` 改 `set -euo pipefail` + 稳健解析路径 + 打印 `--list_installed` + 校验 NDK 目录存在 |
| 顺带修掉 | 原「Free disk space」在装完 NDK **之后**执行 `rm -rf $ANDROID_HOME/ndk/*`，会把刚装好的 r27c 删掉 → 提前到 SDK 安装之前 |

**② run 34913864166 —— `Build APK` 17 秒失败（工具吞掉异常）**

| 项 | 内容 |
|----|------|
| 现象 | 前 13 步全绿（SDK/NDK/依赖卡口/宿主 venv/Qt wheel 都 OK），`Build APK` 12~17 秒即失败；工具只把 traceback 打进日志（退出码仍为 0），最后是我的产物断言报错 |
| 日志关键行 | `# sdkmanager path "/usr/local/lib/android/sdk/tools/bin/sdkmanager" does not exist, sdkmanager is notinstalled` → buildozer 退出 1 |
| **根因** | **buildozer 1.5.0 只认旧版布局**：其 `sdkmanager_path` 属性写死 `$ANDROID_HOME/tools/bin/sdkmanager`；而现代 cmdline-tools 装在 `$ANDROID_HOME/cmdline-tools/latest/bin`，且 `tools` 包已被 Google 移除 → 该路径永远不会存在 |
| 修复 | workflow 新增步骤 `Fix cmdline-tools layout for buildozer`：`ln -sfn "$ANDROID_HOME/cmdline-tools/latest" "$ANDROID_HOME/tools"`（目录级软链接；`sdkmanager` 启动脚本会 `cd` 到自身目录上级并用 `pwd -P` 解析真实路径，`lib/` 依然可达），并当场 `sdkmanager --version` 自检 |
| 顺带改进 | ① 包装脚本传 `extra_ignore_dirs="scripts,packaging,docs,tests"`（避免把构建脚本当应用源码扫描，消除 `Found 'import PySide6' in file 0` 噪音）；② 新增失败注解通道与 `Preflight check` 步骤（见 §5.11） |

**③ 已确认正常的部分（来自 run ② 的日志）**

- 依赖闭包注入生效：`requirements = python3,shiboken6,PySide6,attrs,charset-normalizer,numpy,Pillow,psd-tools,reportlab,typing-extensions` ✅
- 本地 recipe 注入生效：`['attrs', 'charset-normalizer', 'psd-tools', 'typing-extensions']` ✅
- Qt 模块自动探测正确：`['Gui', 'Core', 'Widgets']`，并解析出 Qt6Gui/Qt6Widgets 的 .so 依赖 ✅
- buildozer 已 clone p4a（develop）、自动装好 ANT 1.9.4、找到 JDK17 的 javac/keytool、找到 SDK 与 NDK r27c ✅
- 宿主环境 UTF-8 正常（`locale = C.UTF-8 / encoding = utf-8`）——即此前的编码怀疑不成立，已由日志排除 ✅

**③ run 34914649124 —— p4a 编译阶段：Pillow 下载成功、reportlab 下载 403**

| 项 | 内容 |
|----|------|
| 现象 | 已进入 p4a 真正编译：Qt bootstrap 参数正确（`--bootstrap=qt --requirements=… --local-recipes …/deployment/recipes --qt-libs=Widgets,Gui,Core --load-local-libs=plugins_platforms_qtforandroid --display-cutout shortEdges`）；`numpy`（git v2.3.0）与 `Pillow`（GitHub 11.3.0）下载正常，随后在 reportlab 处失败 |
| 日志关键行 | `Downloading reportlab from https://hg.reportlab.com/hg-public/reportlab/archive/fe660f227cac.tar.gz` → `urllib.error.HTTPError: HTTP Error 403: Forbidden`（重试 1/2/4/8s 后放弃） |
| **根因** | p4a 内置的 reportlab recipe 指向 **hg.reportlab.com 上 2017 年的 hg 修订**；实测该地址现在**恒返回 403**（本机 curl 复核：403，非网络抖动） |
| 修复 | 新增本地 recipe `packaging/android/recipes/reportlab/`，改用 **PyPI 5.0.1 sdist**（对齐 `uv.lock`）：`https://files.pythonhosted.org/packages/source/r/reportlab/reportlab-5.0.1.tar.gz`（实测 200） |
| 依据 | ① p4a `Recipe.recipe_dirs()` 把 `--local-recipes` **排在首位**（`recipe.py:701-708`），同名本地 recipe 覆盖内置实现；② reportlab 5.0.1 的 sdist 是标准 `setuptools.build_meta` 构建、**不含需编译的 C 扩展**（加速件是独立可选包 `rl_accel`，本项目 lock 里没有）→ `depends` 只需 `python3 / pillow / charset-normalizer`，连内置 recipe 带的 freetype 都可以省 |
| 顺手排雷 | 其它相关内置 recipe 的源实测可达：`png 1.6.37`（GitHub zip）、`jpeg 2.0.1`（GitHub tar.gz）、`freetype 2.14.1`（savannah）、`libwebp`（googleapis）、`harfbuzz`（freedesktop）✅ |

**⑤ 安装实测 —— 闪退：设备端解释器版本与 Qt wheel 不匹配（R2 命中）**

| 项 | 内容 |
|----|------|
| 现象 | APK 安装成功但启动即闪退；解包可见 `libpython3.14.so` |
| **根因** | Qt 官方 Android wheel 是 **cp311** 构建，其原生模块**硬编码依赖 `libpython3.11.so`**（`readelf -d` 实测：`QtCore.abi3.so` / `libshiboken6.abi3.so` / `libpyside6.abi3.so` / `Shiboken.abi3.so` 四个全部 NEEDED `libpython3.11.so`）；而 p4a develop 的 python3 recipe 是 **3.14.2** → APK 里只有 `libpython3.14.so` → 链接器解析不到 3.11，启动即崩 |
| 排查结论 | ① Qt 下载站**所有** Android wheel（6.10.x~6.11.2）都是 `cp311-cp311`，没有 cp312/313/314，所以设备端只能用 CPython 3.11.x；② 应用代码 grep 确认无 Python 3.12+ 专属写法，跑 3.11 无障碍 |
| 修复 | 新增两个本地 recipe（本地 recipe 优先于内置，`Recipe.recipe_dirs()`）：`packaging/android/recipes/python3` 与 `.../hostpython3`，均 `version = "3.11.5"`（p4a 最后一个正式版 2024.1.21 用的就是 3.11.5；`hostpython3` 有独立硬编码版本，必须一起改） |
| 预验证 | 本地把 develop 为该版本准备的 4 个补丁对 CPython v3.11.5 源码做了 `patch --dry-run`：`pyconfig_detection.patch` / `reproducible-buildinfo.diff` / `cpython-311-ctypes-find-library.patch` / `py3.8.1_fix_cortex_a8.patch` **全部干净应用**（仅 offset/fuzz），避免再白等一次 60 分钟构建 |
| 验收标准 | 新 APK 内出现 `libpython3.11.so`；应用能启动到主窗口 |

**⑥ run 34919720480 —— 本地 recipe 覆盖导致上游补丁丢失**

| 项 | 内容 |
|----|------|
| 现象 | 进入 `Prebuilding recipes` 后立刻失败：`Applying patch fix_ensurepip.patch` → `patch: **** Can't open patch file .../deployment/recipes/hostpython3/fix_ensurepip.patch : No such file or directory` |
| **根因** | 我们的本地覆盖 recipe 只有 `__init__.py`，而 p4a 的 `Recipe.get_recipe_dir()` **优先返回 `--local-recipes` 下的同名目录**（recipe.py:369-378），`apply_patch()` 用 `join(get_recipe_dir(), filename)` 找补丁（recipe.py:289）→ 上游的 `patches/*.patch` 天然找不到 |
| 修复 | 两个覆盖 recipe 都增加 `get_recipe_dir()` 覆写，**指回 p4a 源码树里对应的 recipe 目录**：`Path(pythonforandroid.recipes.<name>.__file__).parent`。这样只覆盖版本号，recipe 自带文件（补丁）仍走上游，无需复制文件、也不会随上游漂移 |
| 依据 | `get_recipe_dir()` 全仓只有 4 处调用点（apply_patch / copy_file / 同类 / IncludedFilesBehaviour），都只用于定位 recipe 自带文件 → 覆写安全 |
| 附带确认 | `hostpython3.download()` 里有**强制版本校验**（python3 与 hostpython3 必须同版本）→ 两个一起钉 3.11.5 是必须的；上游 3.11 需要的 4 个补丁文件均可达（HTTP 200） |

**⑦ 真机复测 —— 部分系统（HyperOS）读屏导致启动死锁/崩溃**

| 项 | 内容 |
|----|------|
| 现象 | 应用已能正常启动，但在开启辅助功能（读屏）的 HyperOS 等系统上，启动阶段（Qt 主线程创建"首次运行自动弹出的设置窗口"）与读屏查询并发 → 死锁/崩溃 |
| 机制（读源码确认） | Qt 用一块 Surface 画整个界面，系统读屏只能整块查询；每次查询都走 `androidjniaccessibility.cpp` 的 `runInObjectContext()` → **`Qt::BlockingQueuedConnection` 阻塞回 Qt 主线程** → 由 `AndroidDeadlockProtector` 保护（超时则打日志并放弃）。主线程此时正忙于创建窗口 → 保护器与其竞争 → 死锁/崩溃 |
| **官方开关** | qtbase `src/android/jar/src/org/qtproject/qt/android/QtAccessibilityDelegate.java:94`：<br>`final String isA11yOff = Os.getenv("QT_ANDROID_DISABLE_ACCESSIBILITY");`<br>命中 `"1"`/`"true"` 就 **直接 return**：不再创建覆盖在 Qt 布局上的无障碍 View、不注册代理 → 系统根本不查询 Qt ✓ **完全不触碰死锁保护器**（按需求方要求） |
| 为什么不用"让查询返回空" | 查询在进 C++ 时**先**做阻塞调用（`runInObjectContext`）**再**查接口，所以"返回空"挡不住那条阻塞路径；要让 C++ 提前返回就得改 Qt 源码或绕过保护器 ✗ |
| 为什么必须在 Java 侧设 | 该变量在"无障碍状态变化"时读取，而监听器在 QtLayout/Activity 初始化时就注册并可能立即触发 → Python 侧 `os.environ` 对"启动时读屏已开启"这一情形来不及 |
| 落地方案 | p4a hook（`packaging/android/p4a_hook.py`，阶段 `before_apk_build` / `after_apk_build` / `before_apk_assemble`，hook 的 cwd 即 dist 目录）：① 把 `A11yEnvProvider.java` 放进 Gradle 源码集 `src/main/java/...`；② 在生成的 `AndroidManifest.xml` 的 `<application>` 内注入 `<provider … exported="false">` |
| 为什么用 ContentProvider | Android 生命周期保证 provider 早于**任何** Activity（`ActivityThread.handleBindApplication()` 里先 `installContentProviders()`）→ 足够早；且 provider 只用 framework API（不依赖 Qt jar 参与编译），也**不必替换** `<application android:name>`（p4a qt 模板硬编码 `…QtApplication`，再注入一个 `android:name` 会变成重复属性、aapt2 直接报错） |
| 本地验证 | 用假 dist 目录实跑 hook：provider 注入成功、Java 落入源码集、重复调用幂等、缺清单时必须硬失败 ✅ |
| 真机验证方式 | `adb logcat \| grep MangaProofA11y` 应出现 `QT_ANDROID_DISABLE_ACCESSIBILITY=1 已设置`；开启读屏后应用可正常启动 |
| 产品决策（需求方确认） | 本应用是**效率工具，永久不适配无障碍**：对系统辅助功能完全不可见是**预期结果**，不是待偿还的技术债。因此不安排任何后续无障碍工作；仅当将来产品定位变化时才需重新评估（那将意味着换用能暴露可访问性信息的 UI 栈，属于重写级别的改动） |

**⑧ 真机复测 —— 退出时闪退（QTBUG-85449 家族）→ 应用侧修复**

| 项 | 内容 |
|----|------|
| 现象 | 前面几处问题（解释器版本、自适应图标、辅助功能）都解决后：应用能正常启动、能正常使用；但**关闭/退出应用时闪退**（界面已消失、进程崩溃，属"退出阶段崩溃"，不影响数据可用性但体验差） |
| **根因** | `Qt for Android` 退出阶段要做全局对象析构 / `exit()` 收尾，这条路径在部分设备与系统版本上必崩 —— 即 **QTBUG-85449 家族**（"Android: crash on exit"）。PySide6 的 Python 收尾还会额外叠一层解释器 finalize，同样会触碰到已开始被销毁的 Qt 对象。**官方对机制的描述**（Qt for Android Environment Variables 页，`QT_ANDROID_NO_EXIT_CALL` 条目）：*"an Android app might not be able to safely clean all threads while calling `exit()` and it might crash. This is because there are C++ threads running and destroying these without joining them terminates an application."* |
| 修复 | 新增 `mangaproof/utils/shutdown.py`：`is_android()`（三重判定：`sys.platform == "android"` / `ANDROID_ROOT` / Qt `QOperatingSystemVersion.OSType.Android`）+ `exit_app(code)`；**Android 上直接 `os._exit(code)`**，跳过 CPython 收尾与 Qt/C++ 析构，由系统回收进程；桌面仍是 `sys.exit(code)`（atexit、析构、缓冲区 flush 全部照常）。入口两处统一改为 `exit_app(main())`：仓库根 `main.py`（APK 真正的入口，`pysidedeploy.spec` 的 `input_file = main.py`）与 `mangaproof/main.py` 的 `__main__` 兜底。**方向与 Qt 官方绕行方式一致**（官方：不调用 `exit()`、交给 Android 系统处理，代价是不跑全局析构） |
| 为什么安全（逐条在代码里核实，不是假设） | ① **数据不丢**：`MainWindow.closeEvent()` 在 `QApplication.exec()` 返回**之前**就已完成 `save_task()` + `_save_settings()`（并先 `wait()` 各 worker），退出逻辑不依赖析构或 atexit；② **日志不丢**：`logging_setup` 用 `RotatingFileHandler`，每条记录即 flush，另有实时 stderr 代理；③ 线程/子进程已在 `closeEvent()` 里请求取消并等待 |
| 为什么不用 `QT_ANDROID_NO_EXIT_CALL=1` | 该变量只作用于 Qt 自己那条收尾（qtbase `androidjnimain.cpp`：main 返回后 `if (!qEnvironmentVariableIsSet("QT_ANDROID_NO_EXIT_CALL")) exit(ret);`），而本进程的退出由 Python 主导 → 对我们的路径**无效**；更关键的是它的语义是"让进程不要退出"，一旦某条原生路径先返回而我们的 `os._exit()` 没跑到，就变成**卡死**（比崩溃更糟）→ 决定**不设**该变量，仅作为文档保留 |
| 本地验证 | 两个分支各跑一次子进程：桌面（`atexit` 打点）→ 退出码 7 且打点**出现**（收尾照常）；置 `ANDROID_ROOT=/system` 走 Android 判定 → 退出码 7 且打点**不出现**（`os._exit` 生效，收尾被跳过）。另用 `QT_QPA_PLATFORM=offscreen` 以**真实入口** `main.py` 起一次应用并在 1.5 s 后关窗：退出码 0、`atexit` 打点出现、`settings.json` / `recent.json` 内容与测试前**逐字节一致**（即关窗落盘不受影响） |
| 预期验收 | 真机：关窗/退出应用不再闪退；退出前后 `settings.json`、`recent.json`、`logs/mangaproof.log` 正常更新 |

**下一轮的风险预告（未发生，先记录）**

- `numpy` 走的是 p4a 内置 recipe（git tag **v2.3.0**）与 `Pillow`（**11.3.0**），二者与 `uv.lock` 的 2.5.2 / 12.3.0 不一致 → 若真机运行期出现 API 差异，再补钉版本 recipe（P1 计划内）。
- `psd-tools` 要用 NDK 交叉编译 Cython 扩展 `_rle`；失败时的兜底已写在 recipe 注释里（`--no-isolation` + hostpython 提供 cython），最差情况可退化为纯 Python 的 `rle.py`（功能不受影响，仅慢）。

**④ run 34914938082 —— 全部依赖编译通过，卡在 p4a 生成自适应图标（p4a 自身 bug）**

| 项 | 内容 |
|----|------|
| 现象 | 已走到打包最后一步：`dists/mangaproof/build.py` 的 `make_package` 抛异常；reportlab 覆盖生效、全部 recipe 编译通过 |
| 日志关键行 | `File ".../dists/mangaproof/build.py", line 433, in make_package` → `with open(join(res_dir, 'mipmap-anydpi-v26/icon.xml'), "w")` → `FileNotFoundError: [Errno 2] No such file or directory: 'src/main/res/mipmap-anydpi-v26/icon.xml'` |
| **根因** | p4a `bootstraps/common/build/build.py:433` 写自适应图标 XML 时**从不创建 `mipmap-anydpi-v26/` 目录**；该目录只存在于 SDL bootstrap 的模板里，**Qt bootstrap 模板里没有**，而 git 不跟踪空目录 → 必然失败（不是我们的配置问题） |
| 修复 | 改用"自带图标资源"：**不设** `icon.adaptive_foreground/background.filename`（p4a 就不会执行那段代码），改由 `android.add_resources` 投放 `mipmap-anydpi-v26/icon.xml` + 两层 PNG（`mipmap/` 兜底 + `mipmap-xxxhdpi/` 主图）。p4a 的文件模式会先 `ensure_dir(dirname(dest))` 再复制（`build.py:414-421`），目录自然被创建 |
| 新增资产 | `ico/android/res/mipmap-anydpi-v26/icon.xml`（自带 `<adaptive-icon>`；将来加 `<monochrome>` 即可支持 Android 13 主题图标，无需改构建脚本） |
| 附带确认 | buildozer → p4a 的命令行完全符合预期：`--permission …MANAGE_EXTERNAL_STORAGE`、`--orientation landscape`、`--manifest-orientation sensorLandscape`、`--numeric-version 10000`、`--icon/--icon-fg/--icon-bg`、`--enable-androidx`、`--local-recipes deployment/recipes` 均正确传入 ✅ |

### 5.11 失败可观测性（为什么必须做）

GitHub 的 **job 日志下载接口要求仓库 admin 权限**（匿名请求 403 `Must have admin rights`），因此外部无法直接读取失败原因。现在 workflow 里加了：

1. `Build APK` 输出 `tee` 到 `$RUNNER_TEMP/build.log`；
2. `Report build failure details` 步骤（`if: failure()`）：把**最后一次 Traceback 之后的 10 行**写成 `::error::` 注解（注解有数量上限，故只取 10 行），并把日志尾部 80 行写进 job summary；
3. **check-run annotations 接口是公开可读的** → 下次失败可以直接从 API 取到根因，无需 admin、无需人工粘贴日志；
4. `Preflight check` 步骤：打印 python/编码/依赖版本并真的 `import android_deploy`、`deploy_lib`，把"导入级"问题提前暴露成一步清晰的失败。

> 教训：第三方 setup action 的**默认输入值**、以及**老工具对 SDK 目录布局的硬编码**，都会随上游变化失效；凡是"写死路径/包名/版本"的环节，都要显式覆盖或补兼容层。已核实可用：`platform-tools` / `platforms;android-35` / `build-tools;35.0.0` / `ndk;27.2.12479018` / `cmdline-tools;latest` 均在 SDK 仓库中 ✅；`actions/cache@v6`、`upload-artifact@v7`、`checkout@v7`、`setup-java@v6` 的 tag 均可解析 ✅。

---

## 6. 风险清单与验证方法

| # | 风险 | 影响 | 验证方法 | 缓解 |
|---|------|------|----------|------|
| R1 | **16 KB page size**：shiboken6 wheel 内 `.so` 为 `p_align=0x1000`（实测） | 可能影响 Android 15+ 设备加载 / Play 审核 | CI 加 `readelf -lW` 扫描 APK 内 `lib/**/*.so`；`zipalign -c -P 16 -v 4`；真机（16 KB 内核）实测启动 | 优先升级到 Qt 官方声明需要 NDK r28c 的更新版本（其 dev 文档已明说 r28c 是为 16 KB）；或向 Qt 反馈 shiboken 对齐问题；必要时用 `-Wl,-z,max-page-size=16384` 重链自建部分 |
| R2 | **设备端 CPython 3.14 加载 cp311 wheel** —— ✅ **已确认发生并修复**（安装后闪退；`readelf -d` 实测 Qt 模块硬依赖 `libpython3.11.so`，而 APK 装的是 `libpython3.14.so`） | 启动即崩 | 复现：安装 APK → 闪退；验证修复：APK 内应出现 `libpython3.11.so`（`unzip -l x.apk \| grep libpython`） | **已修**：新增本地 recipe `packaging/android/recipes/python3` 与 `hostpython3`，把设备端/构建期解释器钉到 **3.11.5**（p4a 2024.1.21 的版本；develop 的补丁已本地 dry-run 验证可干净应用）。注意 Qt 下载站所有 Android wheel 均为 cp311，不存在 cp312+ 可选 |
| R3 | **p4a `develop` 漂移** | 今天能过、明天失败 | 记录首轮成功的 p4a commit sha | 包装脚本写死 `p4a.commit`；同时把 Qt wheel 版本一起钉住 |
| R4 | **依赖版本漂移**（numpy/Pillow/reportlab recipe 版本低于项目要求） | 运行期 API 差异 | 设备端打印 `numpy.__version__`、`PIL.__version__`、`reportlab.Version`；跑一遍真实 PSD 流程 | 自建 recipe 钉 2.5.2 / 12.3.0 / 5.0.1 |
| R5 | **工具吞异常 → 假成功** | CI 绿但无产物 | 产物断言 + `apksigner verify` | 已在 §5.4/§5.5 固化 |
| R6 | **首次构建超时/磁盘不足** | 构建失败 | 观察 runner 日志中 gradle/NDK 下载与磁盘警告 | 清理预装组件、缓存项目内 `.buildozer/` 与 `~/.gradle`、必要时拆成"预热缓存"job |
| R7 | **SDK license 交互阻塞** | 构建卡住 | 日志停留在 license 提示 | `android.accept_sdk_license=True` + `yes \| sdkmanager --licenses` |
| R8 | **应用核心流程不可用**（未授权全文件访问就打不开用户目录） | 装得上但用不了 | 真机/模拟器走一遍"授予所有文件访问 → 选漫画文件夹 → 打开" | P1：权限弹窗 + 拒绝策略（姊妹文档 §2.7/§2.9） |
| R9 | **内存不足**（LRU 256–768 MB） | 中低端机被杀 | 真机跑 500 MB 级 PSD | Android 侧默认更激进的内存档位 |
| R10 | **`source.dir=.` 把仓库垃圾打进包** | 包体巨大/构建慢 | 检查 p4a 日志中的 `private.tar` 文件数与 APK 体积 | `source.exclude_dirs` 白名单化 |
| R11 | 单 ABI 限制（Qt bootstrap 只支持单 arch） | 不能一个 APK 覆盖全部机型 | — | arm64-v8a 为主；x86_64 单独一份给模拟器；多 ABI 需分别构建 |
| R12 | Windows 宿主不支持 | 开发者本机无法复现 Android 构建 | — | 与"不用本地工具链"的要求天然一致：**只在 CI 构建** |

---

## 7. 分阶段实施计划（含验收标准）

| 阶段 | 目标 | 关键动作 | 验收标准 |
|------|------|----------|----------|
| **P0 打包链路（仅 APK）** | 证明云端能出包，并把安卓形态定下来 | `android.yml` + 包装脚本；`android.release_artifact=apk`；**注入横屏全屏清单项**（`orientation=landscape`、`fullscreen=1`、`p4a.extra_args` 追加 `--display-cutout shortEdges`）；**内存强制激进 ✅ 已实现**；依赖先用官方 recipe，**不加**自研扩展 | CI 产出**签名 APK**；`apksigner verify` 通过；真机安装后**横屏全屏**启动到主窗口不崩；设置页内存策略显示"激进"且灰显不可改 |
| **P1 选目录 + 路径直读** | 应用在 Android 上"能跑通一次流程" | 补 4 个本地 recipe；钉 numpy/Pillow/reportlab 版本；新增 `mangaproof/storage/**`（SAF 原生选目录 + URI→真实路径映射 + 权限检测弹窗）；任务文件写回原目录；签名 + 16 KB 校验；tag 发布附加产物 | 真机从"本机存储"选目录 → 直读标注 → `.mangaproof.json` 写回原目录；云盘等不可映射目录给出明确拒绝提示；全程 UI 不卡 |
| **P2 真机核验 + 图标 + 调优** | 可用性与体验达标 | 姊妹文档 §2.10 核验清单（URI 形态/卷 UUID/权限检测/性能/内存）；自适应图标接入（资源已就位）+ CI 图标校验；PDF 导出；启动底色主题 | 内置存储 / SD 卡 / Download 三类目录都能直读；500 MB 级 PSD 连续监制 10 页不 OOM；图标自适应生效 |
| **P3 交互适配** | 触屏可用 | 菜单/工具栏化、Dock 改面板、手势缩放/长按、可选编译 `_psd_fast.so`（16 KB 对齐） | 全程不接键盘鼠标完成一次"打开目录 → 标注 → 生成 PDF" |
| **P4 分发** | 上架/分发 | AAB + Play（或内部分发）、隐私与权限说明、版本号策略 | Play 内部测试轨道安装成功；或企业内部分发渠道验收 |

> 建议先做 **P0→P1**（纯构建链路，不改产品代码），把"能不能出包、装上能不能开"这两个不确定性一次性消掉，再决定 P3 的界面改造规模。

---

## 8. 依赖与环境变更申请（按 `AI-rules/依赖安全与环境管理规则.md`）

**本次调研全程零安装**：没有在本机安装 NDK/SDK/buildozer/p4a 或任何 Python 包，没有修改 `pyproject.toml`/`uv.lock`/`.github/**`，只新增了本文档。

进入实施阶段后，需要你**明确批准**的变更（全部发生在 CI runner 的临时环境内，本机仍保持零安装）：

| 变更 | 位置 | 作用 | 影响范围 | 风险 |
|------|------|------|----------|------|
| apt 安装 p4a 构建依赖（§3.6 清单） | CI runner（每次全新） | 编译 CPython/原生库 | 仅当次 runner，**不落本机** | 低；耗时约 1–2 分钟 |
| `pip install pyside6==6.11.2 jinja2 pkginfo tqdm packaging==24.1` | CI 临时 venv（`$RUNNER_TEMP`） | 提供官方部署工具 | 仅 CI | 低；版本与项目锁定一致 |
| 工具自动安装 `buildozer==1.5.0`、`cython==0.29.33` | 同上 | p4a 前端 | 仅 CI | 低；版本由 Qt 官方钉死 |
| 下载 Android NDK r27c + SDK 组件 | CI runner | 交叉编译 | 仅 CI 磁盘/带宽 | 中：NDK 压缩包 634 MB / 解压 >2 GB，首次构建慢；已给缓存方案 |
| 下载 PySide6/shiboken6 Android wheel（约 84 MB） | CI runner | 目标平台 Qt 运行库 | 仅 CI | 低 |
| 新增 4 个本地 p4a recipe（attrs / typing-extensions / charset-normalizer / psd-tools） | 仓库 `packaging/android/recipes/` | 让依赖进包 | 仓库文件 | 低；纯声明式 |
| 新增 `scripts/android/build_android.py`、`.github/workflows/android.yml` | 仓库 | 构建入口 | 仓库文件 | 中：新增 CI 作业，会消耗 Actions 配额 |
| （可选，P3）`pyproject.toml` 依赖下限调整或 Android 专用约束 | 仓库 | 兼容 recipe 版本 | 项目依赖声明 | 中：需回归桌面构建 |
| （可选，P3）`_psd_fast.c` 的 Android 版编译 recipe | 仓库 + CI | 恢复加速 | 仓库文件 | 低（有纯 Python 回退） |

---

## 9. 附录

### 9.1 关键 URL（本次均实测可访问）

| 用途 | URL |
|------|-----|
| 官方文档（6.11.2 原文） | `https://raw.githubusercontent.com/qtproject/pyside-pyside-setup/v6.11.2/sources/pyside6/doc/deployment/deployment-pyside6-android-deploy.rst` |
| 官方文档（在线） | `https://doc.qt.io/qtforpython-6/deployment/deployment-pyside6-android-deploy.html` |
| Qt for Android 支持矩阵（API/NDK） | `https://doc.qt.io/qt-6/android.html` |
| PySide6 Android wheel（aarch64, 6.11.2） | `https://download.qt.io/official_releases/QtForPython/pyside6/pyside6-6.11.2-6.11.2-cp311-cp311-android_aarch64.whl` |
| shiboken6 Android wheel（aarch64, 6.11.2） | `https://download.qt.io/official_releases/QtForPython/shiboken6/shiboken6-6.11.2-6.11.2-cp311-cp311-android_aarch64.whl` |
| Android NDK r27c | `https://dl.google.com/android/repository/android-ndk-r27c-linux.zip`（实测 HTTP 200，663,987,688 B）；或 `sdkmanager --install "ndk;27.2.12479018"` |
| p4a 仓库（recipe 与源码） | `https://github.com/kivy/python-for-android`（`develop`） |
| buildozer 文档 | `https://buildozer.readthedocs.io/en/latest/specifications.html` |

### 9.2 命令速查（全部为 CI 侧命令，本机不执行）

```bash
# 宿主环境（Python 3.11）
python -m venv "$RUNNER_TEMP/android-venv" && source "$RUNNER_TEMP/android-venv/bin/activate"
pip install "pyside6==6.11.2" jinja2 pkginfo tqdm "packaging==24.1"

# Android SDK/NDK
sdkmanager --install "platform-tools" "platforms;android-35" "build-tools;35.0.0" "ndk;27.2.12479018"
yes | sdkmanager --licenses

# 检查 wheel 的 ELF 对齐（16 KB 合规性）
unzip -p pyside6-android.whl 'PySide6/Qt/lib/libQt6Core_arm64-v8a.so' > /tmp/x.so && readelf -lW /tmp/x.so | awk '/LOAD/{print $NF}'

# 构建 / 签名 / 校验
pyside6-android-deploy --name MangaProof --wheel-pyside … --wheel-shiboken … --ndk-path … --sdk-path … --force -v
"$ANDROID_HOME"/build-tools/*/zipalign -p -f 4 in.apk out.apk
"$ANDROID_HOME"/build-tools/*/apksigner sign --ks release.jks --ks-key-alias "$KEY_ALIAS" --ks-pass env:KS_PASS --key-pass env:KEY_PASS --out signed.apk out.apk
"$ANDROID_HOME"/build-tools/*/apksigner verify --verbose --print-certs signed.apk
"$ANDROID_HOME"/build-tools/*/zipalign -c -P 16 -v 4 signed.apk
```

### 9.3 术语表

| 术语 | 含义 |
|------|------|
| p4a / python-for-android | 把 Python 应用交叉编译成 Android 可运行发行版（含 CPython、依赖 recipe、bootstrap Java 壳） |
| bootstrap（`qt`） | p4a 的应用外壳模板；Qt 专用外壳会链接 PySide6/shiboken6 并复用 Qt Android Java 侧 |
| recipe | p4a 里"如何构建某个依赖"的声明式脚本（下载、编译、安装到 site-packages/libs） |
| `pysidedeploy.spec` | PySide6 官方部署配置（桌面与 Android 共用） |
| `buildozer.spec` | buildozer 的打包配置（由工具生成并覆写） |
| ABI | Android 原生架构（本项目关注 `arm64-v8a`、`x86_64`） |
| SAF | Android Storage Access Framework，访问用户文件的正规途径（`content://` URI + 持久化授权） |
| 16 KB page size | Android 15+ 的内存页大小要求，ELF `LOAD` 段需 `p_align ≥ 0x4000` |

### 9.4 证据索引（源码位置）

| 论断 | 位置 |
|------|------|
| 宿主 Python ≤3.11 硬检查 | `.venv/lib/python3.12/site-packages/PySide6/scripts/android_deploy.py:209-211` |
| 必须存在 `main.py` | 同上 `:71-76` |
| 异常被吞（退出码不可信） | 同上 `:142-148` |
| requirements 写死 + spec 覆写清单 | `PySide6/scripts/deploy_lib/android/buildozer.py:15-121`（写死在 `:27`） |
| 每次运行删除 `buildozer.spec` 与 `deployment/` | `PySide6/scripts/deploy_lib/deploy_util.py:22-46`，调用点 `android_deploy.py:99` 与 `:144-146` |
| NDK r27c 常量 | `PySide6/scripts/deploy_lib/android/android_utilities.py:20-21` |
| 缓存目录 `~/.pyside6_android_deploy`、架构映射 | `PySide6/scripts/deploy_lib/android/__init__.py:7-15` |
| 宿主依赖包与 buildozer/cython 版本 | `PySide6/scripts/requirements-android.txt`、`deploy_lib/default.spec:24-30` |
| Qt 模块 AST 探测（不 import 应用代码） | `PySide6/scripts/deploy_lib/dependency_util.py:142-190` |
| p4a recipe 模板（PySide6/shiboken6） | `PySide6/scripts/deploy_lib/android/recipes/*/__init__.tmpl.py` |
| p4a 签名 CLI 与 env 变量 | p4a `develop` `pythonforandroid/toolchain.py:516-535, 982-998` |
| p4a recipe 基类 | p4a `develop` `pythonforandroid/recipe.py:847-1300` |
| p4a `qt` bootstrap 依赖与单 ABI 限制 | p4a `develop` `pythonforandroid/bootstraps/qt/__init__.py:8-30` |
| p4a 目标 CPython 版本 3.14.2 | p4a `develop` `pythonforandroid/recipes/python3/__init__.py:57` |
| p4a 系统依赖清单 | p4a `develop` `Dockerfile:54-85` |
| buildozer 默认 spec 值 | buildozer `1.5.0` `buildozer/default.spec` |
| buildozer 默认 API/NDK 与本地目录 | buildozer `1.5.0` `buildozer/targets/android.py:12-20, 82-90, 202-211`（`ANDROID_API='31'`、`ANDROID_MINAPI='21'`、NDK 回退常量 `17c`）、`buildozer/__init__.py:850-885`（`.buildozer/` 本地目录、`~/.buildozer` 全局目录） |
| p4a 的获取方式（git clone + `p4a.branch`） | buildozer `1.5.0` `buildozer/targets/android.py:636+` |
| debug/release 的产物类型与签名判定 | buildozer `1.5.0` `buildozer/target.py:105`（debug→`android.debug_artifact`，默认 `apk`）、`:142`（release→`android.release_artifact`，默认 **`aab`**）、`buildozer/targets/android.py:919-940`（`P4A_RELEASE_*` 环境变量决定是否签名） |

---

**下一步（等你指令）**：按 §7 的 P0 范围开工时，我会先动"只读之外"的三类文件（workflow / 包装脚本 / recipe），且**只在 CI 侧引入依赖**；本地仍保持零安装。若你希望缩小范围，也可以先只做"P0-a：不加应用依赖的最小 APK 出包验证"，把 R2（cp311 wheel + CPython 3.14）这个最大不确定性先打掉。

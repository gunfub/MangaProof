# Android 端界面适配：界面缩放 + 找回「文件 / 设置 / 关于」

> 本文记录两项**Android 专有**适配的实现依据与取舍：
> 1. 顶部菜单栏在 Android 上消失的根因与修复（`AA_DontUseNativeMenuBar`）；
> 2. Android 专有界面缩放（`QT_SCALE_FACTOR`，**默认按机型分档：手机 55% / 折叠屏内屏与平板 75%**，设置页 50%–150%）。
>
> 硬约束：**桌面端（Windows / Linux / macOS）的缩放与显示逻辑不受任何影响**。

---

## 1. 为什么「文件 / 设置 / 关于」在 Android 上看不到

Qt 并没有"降级成窗口内菜单栏"，而是**把 QMenuBar 隐藏并交给系统的 options menu**。完整链路（Qt 6.11 源码）：

| # | 事实 | 出处 |
|---|------|------|
| 1 | 平台主题提供"原生菜单栏"时，`QMenuBar` 构造阶段就被 `q->hide()`；且此时 `sizeHint()` 恒为 `(0,0)`，窗口里连空一行都不会留 | `qtbase/src/widgets/widgets/qmenubar.cpp`：`QMenuBarPrivate::init()`、`QMenuBar::minimumSizeHint()` |
| 2 | Android 平台主题**实现了** `createPlatformMenuBar()`（返回 `QAndroidPlatformMenuBar`）→ Qt 认为 Android 有原生菜单栏 | `qtbase/src/plugins/platforms/android/qandroidplatformtheme.cpp` |
| 3 | "原生菜单栏"在 Android 上 = 系统 options menu / ActionBar 溢出菜单；Qt 侧由 `QtAndroidMenu::openOptionsMenu()` → Java `activity.openOptionsMenu()` 打开 | `androidjnimenu.cpp`、`QtMenuInterface.java` |
| 4 | ActionBar 只有在 `onPrepareOptionsMenu()` 回调里才会被显示（`setActionBarVisibility(res && menu.size() > 0)`），而该回调的前提是"菜单已经被要求打开" —— 鸡生蛋问题 | `QtActivityBase.java` |
| 5 | 启动时 Qt 主动隐藏 ActionBar；若 `getActionBar() == null` 则直接放弃 | `QtActivityDelegate.java`：`initMembers()` / `setActionBarVisibility()` |
| 6 | p4a 的 Qt 模板主题让第 5 条必然成立：application 主题 `Theme.NoTitleBar(.Fullscreen)`，activity 主题 `@style/KivySupportCutout`（`windowNoTitle=true`、`windowFullscreen=true`，本项目还传了 `--display-cutout shortEdges`）→ **根本没有 ActionBar** | `bootstraps/qt/build/templates/AndroidManifest.tmpl.xml`、`strings.tmpl.xml` |

结论：窗口内没有菜单栏，系统侧也没有入口 → 用户完全看不到 文件 / 设置 / 关于（工具栏不受影响，因为它是普通控件）。
（顶栏第三项 2026-09-25 由「帮助」改名「关于」，本文其余处的旧称呼按当时语境保留。）

### 修复方式与实现要点

`mangaproof/main.py::configure_android_menu_bar()`：在**任何 QMenuBar 创建之前**设置

```python
QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeMenuBar)
```

- 必须在创建前：该属性在 `QMenuBarPrivate::init()` 里判定（本项目的主窗口在 `MainWindow._build_menus()` 里创建菜单栏）；
- **不要**改用"事后 `menuBar().setNativeMenuBar(False)`"：源码里 `QMenuBarPrivate::getPlatformMenu()` 会把每个顶层 `QMenu` 绑定到 `QAndroidPlatformMenu`，而 `setNativeMenuBar(false)` 只删除菜单栏、不清理子菜单的绑定，存在悬空引用隐患；
- 桌面端**不设置**该属性 → macOS 的系统菜单栏行为保持不变。

---

## 2. Android 上的 DPI 事实（缩放的依据）

| 事实 | 出处 / 实测 |
|------|-------------|
| Qt 在 Android 上：`logicalDpi = pixelDensity × 72`，`logicalBaseDpi = 72` → **设备像素比 = 屏幕密度**（densityDpi/160） | `qandroidplatformscreen.cpp`、`qhighdpiscaling.cpp` |
| 因此 **1 个 Qt 逻辑像素 == 1 个 Android dp**；QSS 里的 `px` 就是 dp，高分屏自动按物理像素渲染（不糊） | 同上 |
| `QScreen.logicalDotsPerInch()` 在 Android 上返回 **72**（不是 96）→ 用 `logicalDotsPerInch()/96` 当缩放系数会把界面缩小 25%，方向相反 | `qhighdpiscaling.cpp::effectiveLogicalDpi` + 实测 |
| 加载 MiSans 后实测（逻辑 px = dp）：完整工具栏 **1396**、窗口最小 **418×559**、菜单栏 26 / 工具栏 39 / 状态栏 23 | 本仓库探针 |

由此得到"整行工具栏不折叠成 »"的条件：`设备dp宽 ÷ 缩放s ≥ 1396 + 余量`

| 设备（横屏） | 逻辑宽度 | 整行不折叠所需缩放 |
|---|---|---|
| 折叠屏内屏（1812×2176@420 → 829×690 dp） | 829 | ≤ 0.55（不可行 → 会走 » 折叠） |
| 模拟器平板（1920×1080@280 → 1097×617 dp） | 1097 | **≤ 0.78** |
| 真 10 寸平板（2560×1600@320 → 1280×800 dp） | 1280 | ≤ 0.91 |

> 默认值按**机型分档**（见下）；顺带解释"模拟器上按钮比电脑上还大"的观感：平板上 1 dp = density 个物理像素（1.75），模拟器按 1:1 显示在显示器上时，同样的 33 dp 按钮就是 58 像素 vs 桌面 100% 缩放下的 33 像素 —— 这是**观看倍率**，不是应用被放大。真机拿在手里时元素其实比桌面更小。

### 2.1 默认缩放按机型分档（手机 55% / 折叠屏内屏与平板 75%）

| 机型 | 判定（最小宽度 dp = min(屏宽,屏高) ÷ 密度） | 默认缩放 | 说明 |
|---|---|---|---|
| **手机** | < 600 dp（主流 360–410 dp） | **0.55** | 逻辑空间太小（~390 dp 宽），不缩更小界面放不下 |
| **折叠屏内屏** | ≥ 600 dp（Fold 类内屏 ≈ 690 dp） | **0.75** | 与平板同档 |
| **平板** | ≥ 600 dp（1280×800 dp 等） | **0.75** | 1097 dp 宽设备上"整行工具栏不折叠"的最大 5% 档（0.80 时 1097/0.8 ≈ 1371 < 1396，会折叠） |
| 机型未知 | 拿不到屏幕信息 | 0.75 | 宁可少缩也不要在未知设备上缩过头 |

**为什么用"最小宽度 dp"而不是只看 dpi**：折叠屏内屏的密度与手机同为 420 上下（前者是把 2176 px 的 7.6 寸屏按 420 dpi 上报），单看 dpi 会把折叠屏误判成手机；而"最小宽度 dp"正是 Android 自己的 `sw600dp` 口径，能把三者分开。

**机型信息怎么在"设置 QT_SCALE_FACTOR 之前"拿到**：Qt 只在启动时读一次该变量，而那一刻 PySide6 拿不到屏幕信息（没有 QJniObject，QScreen 也还不存在）。所以由 Android 侧的 ContentProvider（`packaging/android/java/.../A11yEnvProvider.java`，本来就在任何 Activity 之前运行、且在同一进程内）用 `DisplayMetrics` 算好，写进进程环境变量 **`MANGAPROOF_SW_DP`**；Python 侧 `android_sw_dp()` 读它，读不到时再用 `QScreen` 兜底（运行期），启动前拿不到就按"未知 → 0.75"。

**兜底自愈**：万一 provider 没生效（环境变量缺失），启动时只能按 0.75；`reconcile_android_ui_scale()` 会在 QApplication 就绪后用 `QScreen` 重新判定，若与本次生效值不同就写回 `settings.json`，下次启动即为正确值（并记一条警告日志）。用户手动设过 `ui_scale` 时**永不覆盖**。

---

## 3. 缩放方案：单一机制 `QT_SCALE_FACTOR`

| | 采用：`QT_SCALE_FACTOR`（启动前写入） | 未采用：应用侧改主题 QSS |
|---|---|---|
| 覆盖范围 | **全部**：QSS、代码里的固定尺寸、Qt 自身样式度量（对话框间距、消息框图标、进度条、dock 标题栏） | 只有 QSS 覆盖的部分，对话框细节会"只缩一半" |
| 代码量 | 启动时 3 行（读一次 settings.json + `setdefault`） | 主题重构 + 多处常量 + 运行时重刷 |
| 生效时机 | 启动时（改设置需重启应用） | 可即时预览 |
| 与桌面隔离 | 只在 Android 写环境变量；桌面连键都不写 | 需额外保证"系数 1.0 时逐字节还原" |

两者是同一个缩放的两种表达，**同时使用会双重缩放**（0.85² = 0.72），因此只保留一个。

**语义与副作用**（Qt 高 DPI 模型，全屏窗口）：

```
DPR = 屏幕密度 × 缩放s
逻辑可用空间 = 物理像素 ÷ DPR = dp ÷ s      ← 缩放后"桌面级"空间变大
渲染分辨率   = 逻辑尺寸 × DPR = 物理像素    ← 与 s 无关，不会发虚
```

因放大后的 backing store 始终等于物理像素，界面缩放**不影响画布（PSD）的渲染清晰度**，质检用途不受损。

**实现**（`config/settings.py` + `main.py`）：

- `resolve_ui_scale(app_dir)`：桌面直接返回 1.0（连文件都不读）；Android 读 `settings.json` 的 `ui_scale`，缺键 → **按机型默认**（手机 0.55 / 折叠屏内屏与平板 0.75，见 §2.1），非法/越界 → 同机型默认；
- `apply_startup_ui_scale(app_dir)`：仅在 s ≠ 1.0 时 `os.environ.setdefault("QT_SCALE_FACTOR", ...)`（**绝不覆盖**用户/系统已有的值），并记录"实际生效值"供日志与提示使用；
- `android_sw_dp()` / `android_device_class()`：机型判定（环境变量 `MANGAPROOF_SW_DP` 优先，QScreen 兜底）；
- `reconcile_android_ui_scale(manager)`：provider 未生效时的兜底自愈（写回设置，下次启动生效）；
- `android_ui_scaling()`：界面缩放的**唯一平台判定入口**（设置页是否显示该项、默认值、是否提示重启都走它）。

### 各档位的实际后果（便于设置页说明与验收）

| 缩放 | 字号（13 dp × s） | 按钮高（33 dp × s） | 1097×617 dp 设备 | 1280×800 dp 平板 | 829×690 dp 折叠内屏 | 393×873 dp 手机 |
|---|---|---|---|---|---|---|
| 50% | 6.5 dp ≈ 1.03 mm | 16.5 dp | 工具栏 ✓ | ✓ | ✓ | 布局✓ 工具栏✗ |
| **55%（手机默认）** | 7.2 dp ≈ 1.14 mm | 18.2 dp | ✓ | ✓ | ✓ | 布局✓ 工具栏✗ |
| 70% | 9.1 dp ≈ 1.44 mm | 23.1 dp | ✓ | ✓ | ✗ 折叠 | 布局✓ 工具栏✗ |
| **75%（大屏默认）** | 9.8 dp ≈ 1.55 mm | 24.8 dp | ✓（余量 67 dp） | ✓ | ✗ 折叠 | 布局勉强（524 dp 宽） 工具栏✗ |
| 80% | 10.4 dp ≈ 1.65 mm | 26.4 dp | ✗ 折叠 | ✓ | ✗ | ✗ |
| 100% | 13 dp ≈ 2.06 mm | 33 dp | ✗ | ✓ | ✗ | ✗ |
| ≥110% | — | — | 垂直裁剪（617/1.1 < 最小高 559） | 1.5 时裁剪 | 1.1 时裁剪 | 裁剪 |

> 手机上（393 dp 宽）**整行工具栏一定会折叠**（工具栏逻辑宽 1396 > 393/0.55 ≈ 714），
> 这不是缩放能解决的——手机档要靠后续的抽屉/单栏布局改造，当前以"内容放得下"为准。
> 55% 的选择也是这个目的：393/0.55 ≈ 714 dp 的逻辑宽度里，左右面板 + 画布能排下，
> 而 75% 只有 524 dp、连窗口最小宽（含首次提醒条时 542 dp）都压线。

---

## 4. 桌面端零影响的三重保证

1. **严格平台判定**（本轮新增 `mangaproof/utils/platform.py::is_android_strict()`）：只认编译期平台标识——

   ```cpp
   // qtbase/src/corelib/global/qoperatingsystemversion.h
   static constexpr OSType currentType() {
   #if defined(Q_OS_WIN)      return Windows;
   #elif defined(Q_OS_MACOS)  return MacOS;
   #elif defined(Q_OS_ANDROID) return Android;
   #else                      return Unknown;   // 桌面 Linux
   #endif
   }
   ```
   外加 `sys.platform == "android"`（官方 Android CPython）。**不看任何环境变量**，异常一律按非 Android 处理。

   > 为什么不能复用 `utils/shutdown.py::is_android()`：它把 `ANDROID_ROOT` 环境变量也算作证据（实测：Linux 桌面 `ANDROID_ROOT=/system` → 返回 True）。用于退出方式无妨，用于界面缩放则可能在桌面机上把界面缩到 75%。`tests/test_android_ui_scale.py` 把这一反例钉成了测试。

2. **不写、不改环境变量**：桌面分支 `resolve_ui_scale()` 恒为 1.0 → 不写 `QT_SCALE_FACTOR`；用 `setdefault`，用户自己设的值原样保留。
3. **设置层也不暴露**：「界面缩放」仅在 Android 出现在设置页；桌面端即使手工把 `ui_scale` 写进 `settings.json`，也只被 `Settings` 解析为内存值，不参与任何缩放。

---

## 5. 改动清单

| 文件 | 内容 |
|---|---|
| `mangaproof/utils/platform.py`（新增） | `is_android_strict()`（编译期平台判定）、`qt_os_type_name()`（日志用） |
| `mangaproof/config/settings.py` | 缩放常量（`DEFAULT_UI_SCALE` / `ANDROID_DEFAULT_UI_SCALE` / `UI_SCALE_MIN/MAX/STEP`）、`Settings.ui_scale`、`_from_dict` 解析、`save()` 落盘、`android_ui_scaling()` / `default_ui_scale()` / `ui_scale_choices()` / `clamp_ui_scale()` / `resolve_ui_scale()` / `apply_startup_ui_scale()` / `effective_ui_scale()` |
| `mangaproof/main.py` | QApplication **之前**应用缩放；QApplication **之后**、任何菜单栏之前调用 `configure_android_menu_bar()`；启动日志打印缩放值与平台类型 |
| `mangaproof/ui/settings_dialog.py` | 仅 Android 显示「界面缩放（重启后生效）」下拉（50%–150%，步进 5%，共 21 档）+ 说明 tooltip；`apply_to` / `_reset_defaults` 同步 |
| `mangaproof/ui/main_window.py` | 设置保存后：Android 且缩放值变化 → 弹窗提示"重启应用后完全生效" |
| `tests/test_android_ui_scale.py`（新增） | 桌面守门（含 `ANDROID_ROOT` 注入回归）、Android 路径、档位表、持久化、设置页可见性、菜单栏属性、三个菜单存在性 |
| `mangaproof/ui/nav_pad.py`（新增，见 §5.1） | Android 浮动方向键：十字布局、半透明、NoFocus、透明容器（缝隙不吃画布事件）、`set_enabled_state` / `reposition` |
| `mangaproof/ui/main_window.py`（见 §5.1） | `_setup_nav_pad()`（仅 Android 创建）、`eventFilter`（画布 resize 重定位）、`_refresh_nav_pad()`（置灰）及其三处刷新点 |
| `tests/test_nav_pad.py`（新增，见 §5.1） | 创建条件 / 置灰规则 / 直连动作 / 真导航 / 定位 |

---

## 5.1 Android 专有：画布右下角的浮动方向键（模拟键盘方向键）

**要解决的问题**：桌面端靠键盘方向键完成「上/下一个 PSD、上/下一个图层」；触屏没有
方向键，而画布上的手势已用于**平移/拖框**，不适合再抢来翻页 → Android 上在画布右下角
浮出一组十字键。

**方案（需求方确认：B + ① + 置灰、不做长按）**

| 决策 | 选择 | 理由 |
|------|------|------|
| 位置 | **Viewer 右下角浮动**（半透明十字） | 拇指可达，不占工具栏；工具栏在顶部且手机档本就要横向滚动 |
| 接通方式 | **直接调用** `MainWindow.prev_psd/next_psd/prev_layer/next_layer`（不伪造按键事件） | 没有间接层，不会撞上「快捷键歧义」「文本框守卫」等机制；这四个方法本就是命令式的（内部自判空） |
| 边界 | **置灰**（未打开任务、或已到第一个/最后一个） | 触屏没有"按了没反应"的直觉反馈，置灰直接说明"到头了" |
| 长按连发 | 不做 | 保持简单，真机需要再加 |

**实现**（`mangaproof/ui/nav_pad.py` 新增 + `main_window` 接线）

- `NavPad` 容器**不绘制背景**：只有四个按钮自己画半透明底，所以按钮之间的缝隙里
  事件仍落到画布上，**不影响拖拽平移/拖框**；
- 按钮 `NoFocus`：点击不抢焦点（否则会从画布/批注输入框手里拿走焦点，影响后续快捷键）；
- 字符 `▲▼◀▶`：已实测 **MiSans 自带这些字形**（不依赖回退字体，避免"缺字形变空白"那类问题）；
- 尺寸 44 dp / 间距 4 dp / 距右下角 12 dp，跟随全局 `QT_SCALE_FACTOR` 缩放；
- 只在 `is_android_strict()` 为真时创建；**桌面端 `window.nav_pad is None`**（布局零改动）；
- 置灰刷新挂在三处状态汇合点：`_refresh_enabled_state()`（任务开关/加载完成）、
  `_select_layer_internal()`（图层切换，文件切换也会经它）、`_open_file_internal()` 结尾
  （文件切完、图层落定后 —— 注意 `_switch_file()` 是**异步**的，"刚设置 `_current_file`"
  那一刻状态还没确定，在更早处刷新会算出错误的上/下边界）。

**验证**（`tests/test_nav_pad.py`，9 项）

| 项 | 断言 |
|----|------|
| 桌面零改动 | `window.nav_pad is None` |
| Android 创建 | 4 个按钮、字符正确、`NoFocus`、父级是 viewer |
| 空画布 | 四个全禁用且整体隐藏 |
| 置灰规则 | 打开后 ▲/◀ 置灰；走到末尾后 ▼/▶ 也置灰；关闭任务后全禁用并隐藏 |
| 直连动作 | 回调与四个方法**同源**（不是伪造按键）——注意不能"先建窗口再 patch 实例方法"来断言，回调在创建时就绑定了，事后替换实例属性抓不到（这里踩过） |
| 真导航 | 点 ▼ 真的换文件、点 ▶ 真的换图层 |
| 定位 | 贴右下角且在画布范围内；父控件 resize 时经 eventFilter 重新定位 |

> 无头环境下判断"是否被我们主动隐藏"要用 `isHidden()` 而不是 `isVisible()`
> —— 父窗口没 `show()` 时子控件的 `isVisible()` 恒为 False。

---

## 6. 验收方法

```bash
# 一台设备/模拟器即可覆盖各档几何（改完 wm density 后重启应用再看日志）
adb shell wm size 2560x1600 && adb shell wm density 320   # 真 10 寸平板 ≈1280x800 dp → 75%
adb shell wm size 1812x2176 && adb shell wm density 420   # 折叠内屏 ≈829x690 dp → 75%
adb shell wm size 1920x1080 && adb shell wm density 280   # 1097x617 dp → 75%
adb shell wm size 1080x2400 && adb shell wm density 440   # 手机 ≈393x873 dp → 55%
adb shell wm size reset && adb shell wm density reset
adb exec-out screencap -p > shot.png
```

每档核对 4 件事：

1. 顶部出现 **文件 / 设置 / 关于**，且点开有下拉菜单；
2. 顶部工具栏**整行完整**（无 "»" 折叠；折叠内屏与手机除外，见限制）；
3. 与把设置改成 100% 重启后相比，界面明显更紧凑（对话框一起变小 → 说明覆盖到了 Qt 自身度量）；
4. 启动日志（`adb logcat` 或程序目录 `logs/mangaproof.log`）里出现机型与默认值，例如
   平板/折叠屏：`界面缩放 = 0.75（Qt 平台类型：Android，Android 专有判定：是，最小宽度 690 dp → large）`；
   手机：`界面缩放 = 0.55（… 最小宽度 393 dp → phone）`。

桌面回归：`uv run pytest` 全绿；日志应为 `界面缩放 = 1.00（Qt 平台类型：Unknown/Windows/MacOS，Android 专有判定：否）`，界面与改动前逐像素一致。

---

## 7. 已知限制（有意为之）

- **改缩放需重启应用**：Qt 只在启动时读一次 `QT_SCALE_FACTOR`；
- **折叠内屏整行工具栏会折叠**：需要 ≤55% 才放得下（不可读），故接受 Qt 的 "»" 扩展按钮；
- **≥110% 在平板/折叠内屏/该模拟器上会垂直裁剪**：窗口最小高 559 dp 是硬下限（逻辑尺寸不随缩放变化）；
- 未做：手机档布局（抽屉/单栏）、竖屏与分屏布局改造、dock 误关闭防护与布局持久化、安全区（`safeAreaMargins`）——均另议；
- 方向键不做长按连发（需求方决定，见 §5.1）；按钮位置固定右下角，不可拖动、不记忆位置。

---

## 8. 字体缺字形：Android 没有系统字体回退（已接入自带回退字体）

### 现象与根因

平板上少数"字符图标"显示为空白（桌面端正常）。**不是字体没加载**——`font/MiSans-Medium.ttf` 就位、日志有"已加载应用字体"，中文全都正常；而是两件事叠加：

| # | 事实 | 依据 |
|---|---|---|
| 1 | MiSans 缺这几个字形：`✗` U+2717、`▣` U+25A3、`✎` U+270E、`🗑` U+1F5D1、`⚠` U+26A0（还有日志里用过的 `⑳`） | 直接解析 TTF cmap（format 4/12，29571 码位） |
| 2 | **Android 版 Qt 几乎没有平台字体回退**：`QAndroidPlatformFontDatabase::fallbacksForFamily()` 只追加 emoji 字体、按系统语言追加一个 CJK 字体、以及 `QT_ANDROID_FONTS` 里列的家族名，**不会**把 `/system/fonts` 下的字体当逐字回退 | 【源码】qtbase/src/plugins/platforms/android/qandroidplatformfontdatabase.cpp |
| 3 | 桌面三平台有系统回退（DirectWrite / CoreText / fontconfig），所以桌面一直看不出问题 | 同上对比 |

### Qt 的逐字回退机制（我们利用的那条路）

```cpp
// qtbase/src/gui/text/qfontdatabase.cpp
const QStringList fallBackFamilies = familyList(req);
req.fallBackFamilies = fallBackFamilies;
if (!req.fallBackFamilies.isEmpty())
    req.families = QStringList(req.fallBackFamilies.takeFirst());   // 链首 = 主字体
...
QFontEngineMulti *pfMultiEngine = pfdb->fontEngineMulti(engine, script);
if (!request.fallBackFamilies.isEmpty()) {
    QStringList fallbacks = request.fallBackFamilies;   // ← 家族链的第 2 项起
    fallbacks += fallbacksForFamily(...);               // ← 平台回退（Android 近空）
    pfMultiEngine->setFallbackFamiliesList(fallbacks);
}
```

⇒ **只要把带字形的字体挂进"字体家族链"，Android 上也能逐字回退**，不依赖平台回退表。
家族链由主题的 QSS `* { font-family: ... }` + `QApplication.setFont()` 携带（Qt 6.11 的 QSS 支持多家族，已实测 `QLabel.font().families()` 会带上整条链）。

### 实现（仅 Android 挂链，桌面零变化）

| 文件 | 内容 |
|---|---|
| `font/NotoSansSymbols2-Regular.ttf` | 回退字体（家族名 `Noto Sans Symbols2`，641 KB），随包分发；OFL-1.1 全文内嵌在 `third_party.py`，展示于「关于 → 第三方许可」（不再单独放 .txt） |
| `mangaproof/fonts.py` | `fallback_font_candidates()` / `load_symbol_fallback_families()`：注册回退字体并返回家族名；**`is_android_strict()` 为假时直接返回空**（桌面不挂链） |
| `mangaproof/ui/theme.py` | `apply_dark_theme(app, primary_family, fallback_families=())`：家族链 = 主字体 → 回退字体 → 桌面默认家族；`app.font()` 同步带上同一条链（QSS 覆盖不到的场合也能回退） |
| `mangaproof/main.py` | 组合并打印启动日志 `字体家族链：[...]`（真机核对用） |
| `tests/test_font_fallback.py` | 家族名、Android-only 门控、链顺序、桌面链不变、MiSans 确实缺字形、回退字体确实覆盖 5 个字形、**链渲染与回退字体单独渲染逐像素相同且非空白** |

### 为什么自带这份字体（而不是用设备上的）

`Noto Sans Symbols 2` 正是 Android 自己在 `fonts.xml` 里给 `und-Zsym` 家族用的那支
（`NotoSansSymbols-Regular-Subsetted2.ttf`），但：

1. **部分 OEM ROM 会换掉或裁掉它**（"部分安卓系统用了 OEM 自己的字体"），覆盖范围不可保证；
2. Qt for Android 本来也不会把系统字体当逐字回退（见上文第 2 条根因）。

所以自带一份（OFL-1.1，641 KB）才能保证所有机型表现一致。

### 覆盖情况

**全部 5 个字形都由这份回退字体补上，界面文字/图标一个都没有改：**

| 字符 | 用途 | MiSans | `Noto Sans Symbols2` | 结果 |
|---|---|---|---|---|
| `✗` U+2717 | 未通过（状态/按钮/芯片/提示） | 缺 | **有** | ✅ 正常 |
| `▣` U+25A3 | `▣ 自动框选` | 缺 | **有** | ✅ 正常 |
| `✎` U+270E | `✎ 自定义批注` | 缺 | **有** | ✅ 正常 |
| `🗑` U+1F5D1 | `🗑 删除选中问题` | 缺 | **有** | ✅ 正常 |
| `⚠` U+26A0 | 重要提醒 / 警告文案 | 缺 | **有** | ✅ 正常 |

选型时对比过：Ubuntu 系统里只有这一支同时覆盖这 5 个字形（✗ ⚠ ▣ ✎ 平时回退到
DejaVu Sans，🗑 才落到它）；上游完整版 1.2 MB / 2955 码位，与这份 641 KB / 2655 码位
在**箭头、几何图形、杂项符号、装饰符**四个区块的覆盖完全一致，因此取小的一份。

已知未覆盖（与 MiSans 现状一致，界面未使用）：`⑪`～`⑳`（回退字体也没有；返修单 PDF 早已
按"覆盖到第 N 个"回退成 `(11)` 写法）。

### 以后要加符号/图标的规矩

1. 先确认**家族链里的字体**有该字形（MiSans → 回退字体），或者直接把图标字体挂进链；
2. 不要用 emoji（`🗑`、`🖊` 这类：Android 上既没有逐字回退，颜色 emoji 在 Qt Widgets 里也不可靠）；
3. `tests/test_font_fallback.py` 会把"链里的字体到底覆盖了哪些字形"钉住，改动前先跑它。

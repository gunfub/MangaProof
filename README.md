<div align="center">

# MangaProof

**漫画翻译后期的高速 QA / 监制工作台**

逐层检查嵌字质量 · 键盘驱动标注 · 一键生成返修单

[![Desktop Build](https://github.com/gunfub/MangaProof/actions/workflows/build.yml/badge.svg)](https://github.com/gunfub/MangaProof/actions/workflows/build.yml)
[![Android Build](https://github.com/gunfub/MangaProof/actions/workflows/android.yml/badge.svg)](https://github.com/gunfub/MangaProof/actions/workflows/android.yml)
![platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux%20%7C%20Android-4b5563)
![python](https://img.shields.io/badge/python-3.12%2B-3776ab)
[![license](https://img.shields.io/badge/license-GPL--3.0--only-blue)](./LICENSE)

![主界面总览](./README.assets/screenshot-01.png)

</div>

MangaProof 是面向漫画翻译 / 嵌字 / 修图团队的**独立质量检查工具**：把「逐页逐层检查嵌字质量」这件事，
从 Photoshop 里反复的鼠标操作，变成一条**键盘驱动、可中断、可交接**的流水线——检查、标记、统计、恢复、返修一次闭环。

它不控制 Photoshop，不改动 PSD，也不重新合成画面：Original 画面直接取自 PSD 自带的 merged image，
所有监制数据另存于任务文件中，原始素材始终保持只读。

---

## 目录

- [为什么用它](#为什么用它)
- [核心工作流](#核心工作流)
- [核心能力](#核心能力)
- [界面预览](#界面预览)
- [快速开始](#快速开始)
- [快捷键](#快捷键)
- [设置与数据文件](#设置与数据文件)
- [性能与内存](#性能与内存)
- [平台支持](#平台支持)
- [构建与发布](#构建与发布)
- [项目结构](#项目结构)
- [设计边界](#设计边界)
- [许可与致谢](#许可与致谢)

---

## 为什么用它

嵌字监制的本质是**看几百个图层，挑出有问题的那几十个**。传统做法把所有时间花在"把图层调到能看清"上，
而不是花在"判断对不对"上。MangaProof 把前者全部自动化：

| 传统监制流程 | 使用 MangaProof |
| --- | --- |
| 在 Photoshop 里逐个点开图层、缩放、拖到眼前 | `←` `→` 逐层切换，自动对准**视觉中心**并按设定比例缩放 |
| 反复开关图层可见性，肉眼比对原图与背景 | `Space` **Original ↔ 背景闪切**，字没抠干净、背景透出来一眼可见 |
| 发现问题后记位置、写备注、截图，再汇总成表 | `R` 拖红框 + 快捷键选问题类型 + 批注，坐标锚定 PSD 世界坐标 |
| 进度靠记忆，中断后不知道标到哪 | **打开即恢复**上次位置、状态与标注（先做任务身份校验） |
| 返修意见靠手工整理成文档 | 一键生成**带目录、书签、矢量红框**的返修单 PDF 交给下一环节 |

## 核心工作流

```text
打开文件夹 / 单个 PSD  →  自动恢复上次进度  →  当前图层自动定位到视口中心
      ↓
Space 闪切原图 / 背景  →  Enter 通过  /  「/」未通过
      ↓
R 拖框圈问题 + 快捷键选类型 + 批注  →  自动跳到下一个未监制图层
      ↓
全部完成  →  自动生成 MangaProof 返修单 PDF（可关）  →  Ctrl+W 收工，开下一话
```

## 核心能力

### 1. 键盘驱动的高速监制

整条链路上的每一步都可重绑定，目标是**手不离键盘标完一话**。

- `↑` `↓` 切换 PSD，`←` `→` 切换图层，`Enter` 通过，`/` 未通过，`Space` 自动对比；
- `Enter` 之后跳到**下一个未监制图层**（不按索引死走），标完的不会被重复翻到；
- **纯键盘标注流**：`R` 进红框模式 → `A` 自动框选 → 类型下拉栏自动展开（每项都标着自己的快捷键）
  → 按快捷键选定 → 焦点自动跳到批注框 → 打字 → `Enter` 提交（多行批注 `Shift+Enter`）；
- 默认**标完一个就退出**拖框模式，避免手一滑多画一个框；要连着标就打开「连续标注」开关；
- 界面上每个按钮都显示当前绑定键，设置 →「设置…」→「设置快捷键…」可独立重绑定，改完立即生效；
- **快捷键冲突保护**：同一按键绑给两个动作时 Qt 会判定歧义而"两个都不触发"，
  设置界面实时列出冲突并拒绝保存；真按下冲突键时状态栏直接说明是哪两个动作撞车。

### 2. Original ↔ 背景闪切

嵌字最常见的两类问题——「字没抠干净」「背景被擦坏」——在闪切中无处藏身。

- 默认每秒 4 次（每状态 250ms），五档速度可调：慢 / 较慢 / 正常 / 较快 / 快 = 1 / 2 / 4 / 5 / 8 次/秒；
- **手动挡**：按一下 `Space` 切一次，适合逐帧细看；按 `Esc` 或做其他操作自动回到原图；
- 闪切期间 Camera、缩放、当前图层、红框 Overlay 全部保持不动，只切换显示源，眼睛不需要重新找位置；
- 背景图自动选择：优先严格名为 `bg` 的图层，否则取最底部有像素内容的图层；
- 对比中禁止创建问题（先停止再标注），红框始终可见。

### 3. 图层自动定位

选择图层后自动做两件事，等价于 Photoshop 的"智能定位"，但不需要 Photoshop：

- **视觉中心定位**：按 `alpha > 0` 的像素算实际内容包围盒，把**内容**的视觉中心对准视口中心，
  而不是盲取图层 Bounds 的几何中心；
- **按比例缩放**：20%～90% 可选（默认 60%），最长边占视口对应尺寸的比例；
- **蓝色虚线边界框**标出当前图层的实际内容范围，一眼看清"这一层占了哪块地方"（可关闭，不影响定位行为）；
- 手动 Pan / Zoom 不会被自动恢复，只有切换图层、点「定位当前图层」或调整显示比例时才重新定位。

### 4. 大 PSD 无感切换

50MB、几百图层的 PSD 连续翻页不再卡顿，靠的是两阶段后台预加载 + 原生解码加速。

- **图像预加载**：先铺开各文件的 merged image（切换关键路径）；**图层预热**：再补背景图与全部图层的视觉边界；
  两者进度在状态栏独立显示，已预热则切换秒开；
- 预热窗口兼顾回看：桌面为后 3 + 前 1 页，另有前 2 页回看松弛（共 6 页）；
  Android 收紧为 前 1 + 当前 + 后 1（共 3 页），匹配移动端内存预算；
- 未命中预加载时异步加载 + 非模态进度框，**可以继续连按切换**，过期请求自动丢弃；
- 窗口外文档的大图自动回收，回退查看时自动重载；
- 加速路径：merged 解码走 Pillow 原生 C（约 7×）；ZIP 预测压缩走自研纯 C 扩展（约 200×）；
  RLE 由 psd-tools 官方 Cython 扩展覆盖；平坦图层的视觉边界走 **alpha 直取快路径**（约 38×），
  有蒙版 / 特效 / 剪贴关系的图层自动回退完整合成以保证边界正确；
- **隐藏图层不进结构**：图层列表在解析 PSD 时一次建好，隐藏图层（含隐藏在组里的）在这一步就被剔除，
  后续预热、统计、返修单自然全部忽略它们。

### 5. 问题标注体系

失败图层支持**多问题**标注，红框 + 类型 + 批注 + 编号一一对应。

- **红框**粗红镂空、中间透明、不遮原画面；坐标以 **PSD 世界坐标**保存，缩放 / 平移 / 改窗口后依然精确跟随；
- **21 类预制问题**：居中错误、字体选择错误、字体字重错误、文字描边粗细错误、文字颜色错误、字号错误、
  文字位置错误、文字间距错误、气泡处理错误、原文字擦除错误、背景擦除错误、网点对齐错误、网点残留、
  修图瑕疵、漏翻、漏字、错字、翻译错误、排版错误、文字溢出、其他；
- 每类问题都有快捷键（默认 `1`~`9`、`0`、`Q W E T Y U I O P S D`），下拉栏里每一项**直接显示自己的快捷键**，
  不用记也不用翻设置；
- **自动框选**（`A`）：不必手动拖框，直接按当前图层的视觉内容范围生成红框（上下左右各外扩 5 像素）；
- **自定义批注**（`Ctrl+Enter`）输入自由文本，例如「这里应使用 Bold，而不是 Regular」；
- **红框显示范围**默认**整页**——翻到哪页就看全哪页的标注（跨图层），可切换为仅当前图层；
- 右上问题列表**双击即可编辑**批注与类型；
- **编号检查/重排**：删改问题后编号可能出现空号跳号，点一次即按 PSD → 图层 → 创建顺序重排为 1..N 并保存。

### 6. 断点恢复：身份校验 + 自动保存

监制是长任务，随时可能被打断，所以"打开就能接着标"是刚需。

- 打开文件夹自动寻找任务文件（单 PSD 为同目录同名文件），验证通过即恢复上次位置、状态、红框与批注；
- **身份校验不做 PSD 深度解析**：单文件用完整 SHA-256；文件夹按规模抽样——2 个文件以内全量校验，
  3～9 个取首尾，10 个以上取首 / 中 / 尾，其余文件校验大小，失败一律禁止恢复，
  避免"张冠李戴"地把标注恢复到错误的任务上；
- 操作后自动保存（防抖），`Ctrl+S` 立即保存，底栏显示「已保存 / 未保存」；
- **随时收起当前任务**（`Ctrl+W`）：关闭前先同步落盘，任务文件留在原位，下次接着标；
  关闭后画布、面板、文档对象与缓存全部释放，内存回落；
  保存失败（磁盘满 / 只读 / 权限）会**中止关闭**并说明原因，不静默丢进度；
- 打开已移动的文件夹时会先做醒目强提醒，验证不通过绝不自动恢复。

### 7. MangaProof 返修单（PDF）

产物是**交给嵌字 / 修图执行修改的返修任务单**，不是统计数据报表。

- **封面**：任务名、生成时间、PSD 数、总图层、通过 / 未通过 / 未监制；未完成时明确标注「任务状态：未完成」；
- **目录 + 书签**：有问题明细时自动插入目录页，并写入 PDF 阅读器侧栏大纲；
  页码按实际落页回填并迭代到稳定，点目录即可精确跳转；
- **PSD 总览**：逐页列出进度与问题数，默认隐藏「全部通过且无问题」的页（表下注明隐藏数量）；
- **问题明细页**：页面图像 + **PDF 矢量红框** + ①②③ 编号 + 批注；同一 PSD 的问题合并到同一页、共用一张图；
- 全篇使用与界面同一份 **MiSans** 字体并内嵌到 PDF，缺字体时回退内置宋体；
  编号超出字体覆盖范围时自动改用 `(11)` 写法，不出现空白方块；
- 页面图像可选 **PNG 无损（默认）** 或 **JPEG（质量 60～95）**，红框与编号始终是矢量；
- 命名兼容嵌字脚本：所在文件夹为 `output` 时默认取上一级文件夹名；
- **完成后自动生成**（默认开，可关），也可以 `Ctrl+R` 随时手动生成；生成在后台线程跑，逐页进度可取消。

### 8. 统计与进度

- 当前 PSD：图层状态芯片墙（○ 未监制 / ✓ 通过 / ✗ 未通过，**可点击直接跳转**）+ 2×2 统计卡片；
- 总体：PSD 数、总图层、通过 / 未通过 / 未监制 + 进度条；
- 通过绿、未通过红、未监制灰、问题数警示橙，语义色贯穿界面、统计与返修单；
- **只统计 PSD 里可见的图层**：隐藏图层不计入任何进度与报告，省内存也省时间；
  重新显示后下次打开任务即自动回到列表，先前的监制记录仍在。

## 界面预览

> 截图位于 `README.assets/`，编号与场景对照见 [`截图说明.md`](./README.assets/截图说明.md)。

**主界面总览（Windows）** —— 左「任务与统计」、中画布、右「图层与问题」，底部状态栏显示预加载与保存状态：

![主界面总览](./README.assets/screenshot-01.png)

**图层自动定位** —— 当前图层内容对准视口中心，蓝色虚线框标出内容轮廓：

![图层定位](./README.assets/screenshot-02.png)

**Original ↔ 背景闪切**（动图）：

![闪切对比](./README.assets/screenshot-03.gif)

**设置对话框** —— 显示比例、对比模式与速度、红框范围、内存策略等：

![设置对话框](./README.assets/screenshot-04.png)

**MangaProof 返修单 PDF** —— 侧栏目录可跳转，页面图像上叠加矢量红框与编号：

![返修单 PDF](./README.assets/screenshot-05.png)

### 跨平台

| macOS | Linux |
| --- | --- |
| <img src="./README.assets/screenshot-06.png" width="440" alt="macOS"> | <img src="./README.assets/screenshot-07.png" width="440" alt="Linux"> |

| Android 平板 | Android 手机 |
| --- | --- |
| <img src="./README.assets/screenshot-08.png" width="440" alt="Android Pad"> | <img src="./README.assets/screenshot-09.png" width="440" alt="Android Phone"> |

| Android 折叠屏 | Android 模拟器 |
| --- | --- |
| <img src="./README.assets/screenshot-10.png" width="440" alt="Android Foldable"> | <img src="./README.assets/screenshot-11.png" width="440" alt="Android Emulator"> |

## 快速开始

### 方式一：直接使用构建产物（推荐）

从 [Releases](https://github.com/gunfub/MangaProof/releases) 下载对应平台安装包；
也可以到 [Actions](https://github.com/gunfub/MangaProof/actions/workflows/build.yml) 取最新一次构建的工件
（每次推送 `master` 都会重新构建）。

| 平台 | 产物 |
| --- | --- |
| Windows x64 | `MangaProof-<版本>-windows-x64.zip`（解压即用，无需安装） |
| macOS Intel / Apple Silicon | `MangaProof-<版本>-macos-x64.zip` / `-macos-arm64.zip` |
| Linux x64 / arm64 | `MangaProof-<版本>-linux-x64.tar.gz` / `-linux-arm64.tar.gz` |
| Android arm64-v8a / x86_64 | `MangaProof-<版本>-android-aarch64.apk` / `-android-x86_64.apk` |

> 构建产物内已包含编译好的 C 加速扩展，性能最佳。
> 桌面三端随 [Release](https://github.com/gunfub/MangaProof/releases) 发布；
> Android APK 目前只在 [android 工作流](https://github.com/gunfub/MangaProof/actions/workflows/android.yml) 的构建工件中提供。

### 方式二：从源码运行

```bash
# uv 管理的项目虚拟环境，不污染系统 Python
uv sync

# 启动（程序目录 = 本文件所在目录，settings.json 落在这里）
uv run main.py
```

打开单个 PSD / PSB 文件或整个漫画文件夹即可开始（文件夹默认只扫描当前层，需要时可开启递归扫描），
历史任务自动恢复；
下次直接从「文件 → 最近打开」回到打开过的任务（最近 10 个）；
一话标完按 `Ctrl+W` 关闭当前任务（进度已保存），再开下一话即可。

> **第一次使用**（程序目录下既没有 `settings.json` 也没有 `recent.json`）：启动后会直接把**设置页面**打开一次，
> 让你按自己的习惯过一遍——不改任何一项也完全没问题，默认值已在代码里定好。
> 主窗口顶部此时还有一条可关闭的提醒条，保存或正常退出过一次后就不再出现。
> 引导只展示、不预设也不推荐任何值，全程不写文件。

> **性能提示**：直接运行源码时，个别热点解码函数会退化为 psd-tools 的纯 Python 实现，
> 预加载 / 提取速度明显下降。想体验完整加速，按优先级推荐：
>
> 1. **直接使用构建产物**（见方式一）；
> 2. **从构建产物中提取编译好的 C 扩展**：把 `_internal/mangaproof/_psd_fast.so`
>    （Windows 为 `_psd_fast.pyd`）复制到本项目 `mangaproof/` 目录下即可生效；
> 3. **本地自行编译**：`uv run python scripts/build_accel.py`
>    （需 gcc / clang / MSVC；Windows 需 Visual Studio C++ 工具链）。

### 方式三：Android

从 [android 工作流](https://github.com/gunfub/MangaProof/actions/workflows/android.yml) 下载 APK 工件侧载安装
（Android 11 及以上；arm64-v8a 供真机，x86_64 供模拟器）。

- **需要先授予「所有文件访问」权限**，否则看不到共享存储里的漫画目录：
  `设置 → 应用 → MangaProof → 特殊应用权限 → 所有文件访问 → 允许管理所有文件`
  （各家 ROM 入口略有差异，部分机型在应用详情页顶部）；
- 受系统限制，`Android/data`、`Android/obb` 等目录即使授权后也不可读取；
- 强制横屏 + 全面屏显示，界面按设备形态自动缩放（手机 55%、平板与折叠屏内屏 75%，
  可在设置中于 50%～150% 之间调整，重启生效）；
- 画布右下角提供**浮动方向键**，触屏下也能完成上一个 / 下一个图层与 PSD 的切换。

#### Android 端的局限

受 Python 应用打包方式与移动端硬件条件的限制，以下几项是**已知且预期内**的，桌面端不受影响：

| 方面 | 表现 |
| --- | --- |
| 存储占用 | 安装包与安装后占用都明显大于同类原生应用（Qt 运行库与 Python 运行时一并打包） |
| 内存 | Android 端可用内存小：程序因此强制「激进」内存回收（图层像素缓存 256 MB、背景图池 68 MB，设置中不可调），预加载与保留窗口也从桌面的 6 页收窄到 3 页；大 PSD 下内存仍是最主要的约束 |
| 性能 | 移动端单核性能本就弱于桌面，且桌面版用于加速 ZIP 预测压缩解码的 C 扩展不在 Android 包内（该路径退回 psd-tools 原实现），大 PSD 的预加载与像素提取明显慢于桌面 |
| 触屏操作 | 没有物理键盘时单键快捷键无法触发，只能点按界面按钮（浮动方向键只覆盖图层与 PSD 切换），监制速度低于桌面 |

界面、快捷键、数据格式与返修单与桌面端是同一套，上面这些是体验层面的差距。

## 快捷键

均可在 设置 →「设置…」→「设置快捷键…」独立窗口中重绑定，也可一键恢复默认。

| 功能 | 默认 | 功能 | 默认 |
| --- | --- | --- | --- |
| 上一个 / 下一个 PSD | `↑` / `↓` | 上一个 / 下一个图层 | `←` / `→` |
| 当前图层通过 | `Enter` | 当前图层未通过 | `/` |
| 自动对比 | `Space` | 取消批注 / 停止对比 | `Esc` |
| 红框模式 | `R` | 自动框选当前图层 | `A` |
| 自定义批注 | `Ctrl+Enter` | 批注框内确认 / 换行 | `Enter` / `Shift+Enter` |
| 保存任务 | `Ctrl+S` | 关闭当前任务 | `Ctrl+W` |
| 打开 PSD / 文件夹 | `Ctrl+O` / `Ctrl+Shift+O` | 生成返修单 | `Ctrl+R` |
| 问题类型（21 类，可配置） | `1`~`9`、`0`、`Q W E T Y U I O P S D` | 最近打开 | 仅菜单，不占快捷键 |

> 界面上的下拉框（显示比例、问题类型、返修单选项、设置项…）**一律不响应鼠标滚轮**：
> 滚轮只用于滚动 / 缩放画面，改下拉框值请点击展开或用键盘 `↑` `↓`——
> 避免"想滚页面却顺手改掉了设置，还不知道原来选的是什么"。

## 设置与数据文件

所有数据分三层隔离，互不牵连：

```text
程序级设置   程序目录/settings.json      显示比例、对比模式与速度、红框范围、快捷键、PDF 选项、内存策略…
程序级状态   程序目录/recent.json        最近打开记录（与设置分开存，重置设置不会清掉历史）
任务级数据   漫画目录/.mangaproof.json    文件夹任务的进度、状态、红框与批注
            或 001.mangaproof.json      单 PSD 任务（同目录同名）
运行日志     程序目录/logs/mangaproof.log 2 MB × 4 轮转，出问题时先看这里
缓存         独立内存 LRU                非任务恢复必需，可随时丢弃
```

- 任务文件与 PSD 严格隔离，**绝不会写回 PSD**；任务校验：单 PSD 用完整 SHA-256，
  文件夹按规模抽样 Hash（≤2 个全量 / 3～9 个取首尾 / ≥10 个取首中尾）+ 其余文件大小校验，
  失败一律禁止恢复——不提供"强制恢复"选项，选择新建任务时旧进度会自动备份为 `.bak-日期时间`，不会被静默覆盖；
- 「最近打开」记录最近 **10** 个文件夹 / 单个 PSD，去重后最近的排最前，路径失效时点它才提示并移除该条；
- 桌面端设置对话框分为五组：**显示**（显示比例 / 问题红框范围 / 蓝色虚线框；界面缩放仅 Android 显示）、
  **自动对比**（模式 / 速度）、**任务**（递归扫描 / 控制台窗口 / 内存策略）、
  **MangaProof 返修单**（自动生成 / 名称 / 图片格式 / 总览选项）、**快捷键与滚轮**；
  「连续标注」开关在问题面板上，随手即可切换；

## 性能与内存

| 环节 | 机制 | 效果 |
| --- | --- | --- |
| PSD 解析 | 每个 PSD 只解析一次；隐藏图层在建结构时即剔除 | 切换图层不重读文件 |
| merged 解码 | Pillow 原生 C 路径，失败自动回退 psd-tools | 约 7× |
| ZIP 预测压缩 | 自研纯 C 扩展（`scripts/build_accel.py` 编译，运行时补丁进 psd-tools） | 约 200× |
| RLE 解码 | psd-tools 官方 Cython 扩展 | — |
| 图层视觉边界 | alpha 直取快路径，跳过合成 / ICC / RGBA 转换；蒙版 / 特效 / 剪贴图层回退完整合成 | 大图层 1.05s → 0.03s（约 38×） |
| 图层预热（15 页大 PSD 实测） | 两阶段预加载 + 每层像素只提取一次 | 39.0s → 4.7s（约 8.3×），典型单页约 10× |

> 表中倍数为开发期实测记录（见代码注释与提交记录），随 PSD 内容与硬件而变，不作为性能承诺。

内存回收策略桌面端三档热切换（无需重启）：

| 档位 | LRU 图像缓存 | bg 预生成池 |
| --- | --- | --- |
| 宽松 | 768 MB | 768 MB |
| 平衡（默认） | 512 MB | 512 MB |
| 激进 | 256 MB | 68 MB |

- 文档结构按窗口驱逐（三档一致）：仅当前页与邻域保留完整文档对象，窗口外惰性重建（重开约 0.2s），
  内存占用与书本页数无关；
- 当前页背景图被钉住，不会在闪切时被淘汰；
- **Android 端固定为「激进」档**（移动设备内存紧张，档位不交给用户选，设置页灰显）。

## 平台支持

| 平台 | 架构 | 说明 |
| --- | --- | --- |
| Windows | x64 | onedir 免安装，控制台可随时开关 |
| macOS | Intel / Apple Silicon | `.app`（macOS 11+），原生标题栏跟随系统深色偏好 |
| Linux | x64 / arm64 | onedir，附 `.desktop` 模板 |
| Android | arm64-v8a / x86_64 | APK 侧载，Android 11+，强制横屏全屏；存储占用、内存与性能均弱于桌面，见 [Android 端的局限](#android-端的局限) |

各平台共用同一套界面与快捷键：桌面端的菜单栏与工具栏在 Android 上完整保留，
界面整体缩放到适合触屏的比例，并补齐了缺失的符号字形（Android 无系统字体回退）。

## 构建与发布

本地打包（PyInstaller onedir / `.app`）：

```bash
# Windows → dist/MangaProof/     macOS → dist/MangaProof.app      Linux → dist/MangaProof/
uv run pyinstaller --clean --noconfirm packaging/main_win.spec
uv run pyinstaller --clean --noconfirm packaging/main_macos.spec
uv run pyinstaller --clean --noconfirm packaging/main_linux.spec
```

本地跑测试（离屏模式，无需真实显示器）：

```bash
QT_QPA_PLATFORM=offscreen uv run python -m pytest tests/ -q
```

> **对外分发时的许可义务（GPLv3 §6）**：本许可副本与第三方许可清单都会随产物分发
> （`licenses/LICENSE`、`licenses/THIRD_PARTY_LICENSES.md`）；程序内
> 「关于 → 许可证…／第三方许可…」展示的是**代码内置**的同一份全文，
> 因此任何产物形态（含 Android）都能离线查阅，不依赖文件是否被打进包。

CI（GitHub Actions）覆盖桌面与移动端：

- **`build.yml`**：推送 `master` 构建并上传工件，也可手动触发。
  矩阵 5 个平台组合（Windows x64 / Linux x64+arm64 / macOS Intel+Apple Silicon），
  每平台先做离屏启动冒烟测试再上传；Windows 的 C 加速扩展由 Linux 跑者用 mingw-w64 交叉编译，不依赖 MSVC 环境；
- **`android.yml`**：推送 `master` 或手动触发，用 PySide6 官方 Android 部署链路构建
  arm64-v8a（真机）与 x86_64（模拟器）两个 ABI 的 APK，并用仓库 keystore 重新签名，产物可直接侧载
  （固定基于 debug 包重签名：release 包的原生库在真机上不会被解压，当前不可用）；
- **`release.yml`**：推送 `v*` 标签或手动触发（补发/重跑），把上面两个工作流的工件汇总成一个 Release。
  前置条件是该标签指向的 commit 上两个构建都是**最新的 success**，且标签与源码版本一致
  （`__version__` / `pyproject.toml` 同步改过再打标签）；发布的资产是解开外层工件包后的单层封装：
  Windows / macOS 是 zip，Linux 是 tar.gz，Android 是 apk。自动建出的 Release **一律标记为
  Pre-release**（重跑补传也会先打回 Pre-release），经人工审核后才由人去掉该标记转为正式版。

## 项目结构

```text
mangaproof/
├── main.py            # 入口：日志 → Android 界面缩放 → QApplication → 主题 → 主窗口
├── ui/                # 主窗口、Viewer、图层/问题/统计面板、设置与许可对话框、预加载/返修单/编号线程
├── psd/               # PSD 加载、文档模型、图层模型、LRU 图像缓存
├── camera/            # Camera、视觉中心定位、自动缩放
├── review/            # 监制状态、问题模型、导航、编号重排、持久化 + 哈希校验
├── compare/           # 自动对比控制器（自动挡 / 手动挡，速度可配）
├── report/            # MangaProof 返修单 PDF（纯 Python）
├── config/            # settings.json / recent.json 与统一程序路径服务
├── storage/           # 文件选择器门面（Android 走 Qt 控件版对话框，见 docs/）
├── utils/             # 自然排序、日志、平台判定、退出分流
├── console.py         # 打包产物控制台可见性控制
├── fonts.py           # 统一字体加载（含 Android 符号回退）
├── psd_accel.py       # psd-tools 解码加速运行时补丁
└── _psd_fast.c        # 纯 C 解码加速（scripts/build_accel.py 编译）
packaging/             # 三平台 PyInstaller spec、Linux 桌面模板、Android 打包配置与 hook
scripts/               # 构建 / 加速扩展 / 图标生成脚本
docs/                  # Android 适配与打包研究文档
tests/                 # 测试套件：状态机 / 内存策略 / 打包 hook / GUI 冒烟（夹具为真实美术 PSD）
THIRD_PARTY_LICENSES.md  # 第三方组件与许可全文（由 scripts/build_third_party_doc.py 生成）
```

## 设计边界

四条技术边界贯穿始终，也是这个工具能被信任的原因：

- **独立于 Photoshop**：不调用任何 Photoshop API（UXP / JSX / CEP），视图控制全部自研；
- **PSD 只读**：绝不修改原始 PSD，红框、批注、状态一律不写入 PSD；
- **不重新合成**：Original 直接使用 PSD 自带的 merged / composite image，程序不实现 Photoshop Renderer
  （没有 merged image 时明确报错，不做 fallback）；
- **数据隔离**：监制数据全部存在任务文件中，与 PSD 文件彼此独立。

## 许可与致谢

- 本项目以 **GPL-3.0-only**（GNU General Public License v3.0，**仅此版本**）发布，
  Copyright (C) 2026 gunfub，许可全文见 [LICENSE](./LICENSE)；
  各源文件头部带有 `SPDX-License-Identifier: GPL-3.0-only` 标记；
  程序内可离线查看：**关于 → 许可证…**（全文内置在代码里，不依赖外部文件）；
- 界面与返修单统一使用小米 **MiSans** 字体（`font/MiSans-Medium.ttf`），
  依据《MiSans 字体知识产权许可协议》使用：不改编、不单独分发；字体文件缺失时回退内置宋体，生成不受影响；
  Android 端额外附带 `NotoSansSymbols2` 作为符号回退（该平台无系统字体回退）；
- 图标由 `ico/` 提供（Windows `.ico` / macOS `.icns` / Linux `.png` / Android 自适应图标）；
  macOS 的 `.icns` 由 `scripts/make_icns.py` 生成，输出前会适配 Apple 的图标网格
  （1024 画布 / 824 实体居中 / 四边 100px / 圆角 184px），因此不能拿 `ico.png` 直接转换；
- 第三方组件（Python、psd-tools、NumPy、PySide6 / Qt、shiboken6、reportlab、Pillow、
  attrs、charset-normalizer、MiSans 字体等）的版本、许可证与版权信息有两种查看方式，
  内容同源：
  - **不装软件直接看** → 仓库内 [`THIRD_PARTY_LICENSES.md`](./THIRD_PARTY_LICENSES.md)
    （含全部 29 个组件的版本、SPDX 标识、版权、主页与许可证全文）；
  - **程序内看** → **关于 → 第三方许可**。

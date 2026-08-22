# MangaProof v1.0

漫画翻译质量检查与返修标注工具 —— 面向漫画翻译 / 嵌字 / 修图 / 监制的独立桌面 QA 工作台。

## 核心原则

- **独立于 Photoshop**：不调用任何 Photoshop API（UXP/JSX/CEP），所有视图控制由 MangaProof 完成；
- **PSD 只读**：绝不修改原始 PSD（图层、可见性、批注、红框一律不写入）；
- **不重新合成 PSD**：Original 直接使用 PSD 自带 merged/composite image，程序不实现 Photoshop Renderer；
- **监制数据与 PSD 隔离**：通过/未通过状态、问题、红框、批注、进度全部保存在任务文件中。

## 技术栈

| 模块 | 技术 |
|---|---|
| PSD Engine | psd-tools（PSD/PSB 读取、图层树、像素、merged image） |
| Image Processing | NumPy（Alpha 分析、视觉边界） |
| GUI / Viewer | PySide6（暗色 UI、Canvas、Overlay、Camera、Zoom、Pan） |
| Task Engine | Python（监制状态、任务保存/恢复） |
| Report Engine | reportlab（纯 Python PDF，矢量红框 + 内置中文字体） |

## 运行

```bash
# 安装依赖（uv 管理的项目虚拟环境，不污染系统 Python）
uv sync

# 启动
python main.py            # 程序目录 = 本文件所在目录
# 或
uv run python main.py
```

## 打包（PyInstaller onedir / .app）

三个平台的 spec 配置文件位于 `packaging/`，需在对应平台上执行构建：

```bash
# Windows（在 Windows 上执行）→ dist/MangaProof/（含 MangaProof.exe）
uv run pyinstaller --clean --noconfirm packaging/main_win.spec

# macOS（在 macOS 上执行）→ dist/MangaProof.app
uv run pyinstaller --clean --noconfirm packaging/main_macos.spec

# Linux（在 Linux 上执行）→ dist/MangaProof/
uv run pyinstaller --clean --noconfirm packaging/main_linux.spec
```

图标：Windows 用 `ico/ico.ico`（exe 图标）、macOS 用 `ico/ico.icns`（App Bundle
图标）、Linux 无嵌入图标（窗口图标来自随包分发的 `ico/ico.png`，桌面图标见
`packaging/linux/mangaproof.desktop`，将其中 `@APPDIR@` 替换为安装目录后放入
`~/.local/share/applications/`）。

控制台行为（按平台）：
- **直接运行 `python main.py`**：控制台始终保留，设置开关不生效；
- **Windows 打包**：spec 使用 `console=True`（保留控制台子系统），运行时默认隐藏，
  可在「设置」中关闭「打包产物隐藏控制台窗口」即时恢复显示；
- **macOS / Linux 打包**：无独立控制台窗口，spec 使用 `console=False`（windowed，
  图形启动无终端输出），运行时无法也不应切换。

## 核心工作流

```
打开单个 PSD / 文件夹 → 扫描 PSD（自然排序）→ 自动恢复监制进度
→ 选择图层 → 视觉中心定位 + 按比例缩放 → Space 自动对比（Original ↔ BG）
→ Enter 通过 / "/" 未通过 → 失败图层拖红框 + 批注
→ 自动进入下一个未监制图层 → 全部完成 → 可选生成 MangaProof 返修单 PDF
```

## 快捷键（均可在设置中重绑定）

| 功能 | 默认 |
|---|---|
| 上一个/下一个 PSD | `↑` / `↓` |
| 上一个/下一个图层 | `←` / `→` |
| 当前图层通过 | `Enter` |
| 当前图层未通过 | `/` |
| 自动对比 | `Space` |
| 取消/退出批注操作 | `Esc` |
| 保存任务 | `Ctrl+S` |
| 红框模式 | `R` |
| 自定义批注 | `Ctrl+Enter` |
| 问题类型 | `1`~`9`、`0`、`Q`~`O`（预制 19 类，可配置） |
| 打开 PSD / 文件夹 | `Ctrl+O` / `Ctrl+Shift+O` |
| 生成返修单 | `Ctrl+R` |

## 数据文件（三层隔离）

```
程序级设置   程序目录/settings.json          （显示比例、快捷键、PDF 开关…）
任务级数据   漫画目录/.mangaproof.json       （文件夹任务）
            或 001.mangaproof.json          （单 PSD 任务，同目录同名）
缓存        独立内存 LRU（非任务恢复必需）
```

## 图标

- 运行时窗口图标：`ico/ico.png`（PySide6 直接加载，程序目录下 `ico/` 文件夹）；
- macOS 图标 `ico/ico.icns` 由 `uv run python scripts/make_icns.py` 生成（纯 Python，无需 iconutil）；`ico/ico.ico` 为 Windows 图标。

## 字体

- 运行时统一字体：`font/MiSans-Medium.ttf`（小米 MiSans，
  https://hyperos.mi.com/font/download）。直接运行从 `程序目录/font/` 加载；
  打包产物随包分发（PyInstaller 6 布局位于 `_internal/font/`），两种路径自动识别；
- 依据《MiSans 字体知识产权许可协议》使用：软件「关于」对话框注明使用 MiSans；
  不对字体做任何改编或二次开发；字体文件仅随本软件整体分发，不单独提供；
- 字体文件缺失时自动回退系统默认字体，不阻塞启动。

## 任务匹配验证

- 单 PSD：文件大小 + 完整 SHA-256；
- 文件夹：2～3 个分散抽样 SHA-256 + 其余文件 Size 检查（1~2 个文件全部 Hash，3~9 个抽 2 个，≥10 个抽 3 个）；
- 验证失败一律禁止恢复监制状态，不提供强制恢复。

## 项目结构

```
mangaproof/
├── main.py            # 入口
├── ui/                # 主窗口、Viewer、面板、设置对话框
├── psd/               # PSD 加载、文档模型、LRU 图像缓存
├── camera/            # Camera、视觉中心、自动缩放
├── review/            # 监制状态、问题、导航、持久化 + 哈希验证
├── compare/           # 自动对比控制器（250ms 闪切）
├── report/            # MangaProof 返修单 PDF（纯 Python）
├── config/            # settings.json 与统一程序路径服务
└── utils/             # 自然排序、日志
tests/                 # 测试夹具生成与冒烟测试
```

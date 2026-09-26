# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""程序级设置：settings.json（需求 §20.2、§30、§35、§55）。

软件级设置全部落在 程序目录/settings.json，与任务数据（.mangaproof.json）
彻底分离（需求 §58）。

注意：**最近打开记录不在这里**——它存在同目录的独立文件 recent.json
（见 config/recent.py）。settings.json 一旦载入旧版残留的 recent_paths，
只做一次性迁移读取，之后不再写回。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mangaproof.config import paths
from mangaproof.utils.platform import is_android_strict

log = logging.getLogger("mangaproof.config.settings")

SETTINGS_VERSION = 1

# ---------------------------------------------------------------------------
# 默认值
# ---------------------------------------------------------------------------

DEFAULT_KEYBINDINGS: dict[str, str] = {
    "prev_psd": "Up",
    "next_psd": "Down",
    "prev_layer": "Left",
    "next_layer": "Right",
    "pass_layer": "Return",
    "fail_layer": "/",
    "toggle_compare": "Space",
    "cancel_operation": "Esc",
    "save_task": "Ctrl+S",
    "custom_comment": "Ctrl+Return",
    "open_psd": "Ctrl+O",
    "open_folder": "Ctrl+Shift+O",
    "close_task": "Ctrl+W",
    "generate_report": "Ctrl+R",
    "redraw_mode": "R",
    "auto_box": "A",
}

# 核心快捷键的中文名（设置对话框表格 + 冲突提示共用）
CORE_SHORTCUT_LABELS: dict[str, str] = {
    "prev_psd": "上一个 PSD",
    "next_psd": "下一个 PSD",
    "prev_layer": "上一个图层",
    "next_layer": "下一个图层",
    "pass_layer": "当前图层通过",
    "fail_layer": "当前图层未通过",
    "toggle_compare": "自动对比",
    "cancel_operation": "取消/退出批注操作",
    "save_task": "保存任务",
    "custom_comment": "自定义批注",
    "open_psd": "打开单个 PSD",
    "open_folder": "打开文件夹",
    "close_task": "关闭当前任务",
    "generate_report": "生成返修单",
    "redraw_mode": "红框模式",
    "auto_box": "自动框选当前图层",
}

# 预制问题类型（需求 §34）及其默认快捷键（需求 §35，均可配置）
#
# 键位分配：数字行 1~9、0 给最常用的前 10 类（文字外观类问题集中在 1~6），
# 接着走字母上排 Q W E _ T Y U I O P（R 让给核心快捷键「红框模式」，
# 否则两者同绑 R 会被 Qt 判为歧义快捷键而**两个都不触发**），
# 最后两类用主行的 S、D 补足。
DEFAULT_ISSUE_TYPES: list[dict[str, str]] = [
    {"name": "居中错误", "key": "1"},
    {"name": "字体选择错误", "key": "2"},
    {"name": "字体字重错误", "key": "3"},
    {"name": "文字描边粗细错误", "key": "4"},
    {"name": "文字颜色错误", "key": "5"},
    {"name": "字号错误", "key": "6"},
    {"name": "文字位置错误", "key": "7"},
    {"name": "文字间距错误", "key": "8"},
    {"name": "气泡处理错误", "key": "9"},
    {"name": "原文字擦除错误", "key": "0"},
    {"name": "背景擦除错误", "key": "Q"},
    {"name": "网点对齐错误", "key": "W"},
    {"name": "网点残留", "key": "E"},
    {"name": "修图瑕疵", "key": "T"},
    {"name": "漏翻", "key": "Y"},
    {"name": "漏字", "key": "U"},
    {"name": "错字", "key": "I"},
    {"name": "翻译错误", "key": "O"},
    {"name": "排版错误", "key": "P"},
    {"name": "文字溢出", "key": "S"},
    {"name": "其他", "key": "D"},
]

# 问题类型表版本：默认键位表变更时 +1，载入旧 settings.json 时据此一次性升级
# （见 SettingsManager._migrate_issue_types）。v2：新增「文字描边粗细错误」
# 「文字颜色错误」并整体重排键位 + 下拉栏显示快捷键。
ISSUE_TYPES_VERSION = 2

# v1（旧版）默认键位表：用于判断用户是否改过某个类型的键位——
# 与旧默认值相同的（没动过）跟随新表，用户自己改过的保持不动。
_LEGACY_ISSUE_KEYS: dict[str, str] = {
    "居中错误": "1",
    "字体选择错误": "2",
    "字体字重错误": "3",
    "字号错误": "4",
    "文字位置错误": "5",
    "文字间距错误": "6",
    "气泡处理错误": "7",
    "原文字擦除错误": "8",
    "背景擦除错误": "9",
    "网点对齐错误": "0",
    "网点残留": "Q",
    "修图瑕疵": "W",
    "漏翻": "E",
    "漏字": "R",   # v1 与「红框模式 R」撞车，v2 起为 U
    "错字": "T",
    "翻译错误": "Y",
    "排版错误": "U",
    "文字溢出": "I",
    "其他": "O",
}

# 旧版本默认值：漏字 = R 与「红框模式」= R 撞车（Qt 歧义 → 两个都失效）。
# 载入旧 settings.json 时按此表自动让位，见 SettingsManager._heal_shortcut_conflicts。
_LEGACY_DUPLICATE_ISSUE_KEYS: dict[str, str] = {"漏字": "P"}


def normalize_key(seq: str) -> str:
    """快捷键序列归一化（仅用于比较是否撞车，不改变实际绑定值）。"""
    return "".join(str(seq).split()).upper()


def shortcut_entries(
    keybindings: dict[str, str],
    issue_types: list[dict[str, str]],
    custom_comment_key: str = "",
) -> list[tuple[str, str, str]]:
    """全部快捷键绑定 → [(序列, 分类, 名称)]（跳过空绑定）。"""
    entries: list[tuple[str, str, str]] = []
    for action, label in CORE_SHORTCUT_LABELS.items():
        seq = keybindings.get(action, DEFAULT_KEYBINDINGS.get(action, ""))
        if action == "custom_comment" and custom_comment_key:
            seq = custom_comment_key
        if seq:
            entries.append((str(seq), "核心快捷键", label))
    for item in issue_types:
        key = str(item.get("key", "") or "")
        if key:
            entries.append((key, "问题类型", str(item.get("name", ""))))
    return entries


def shortcut_conflicts(
    keybindings: dict[str, str],
    issue_types: list[dict[str, str]],
    custom_comment_key: str = "",
) -> dict[str, list[tuple[str, str]]]:
    """找出重复绑定的快捷键 → {序列: [(分类, 名称), ...]}。

    Qt 对同一窗口内重复的快捷键会判定为「歧义」（Ambiguous shortcut
    overload）并拒绝触发其中任何一个——表现为按键完全没反应。
    因此配置阶段必须能检查出来：设置对话框禁止保存冲突配置，
    运行时也会提示具体是哪两个动作撞车。
    """
    groups: dict[str, list[tuple[str, str]]] = {}
    for seq, kind, label in shortcut_entries(keybindings, issue_types, custom_comment_key):
        groups.setdefault(normalize_key(seq), []).append((kind, label))
    return {seq: names for seq, names in groups.items() if len(names) > 1}

DISPLAY_RATIOS: list[float] = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
DEFAULT_DISPLAY_RATIO = 0.6

#: 显示比例的合法区间（越界即回默认；档位表之外的合法值只能来自手工编辑的设置文件）
DISPLAY_RATIO_MIN = 0.01
DISPLAY_RATIO_MAX = 4.0

# bg / bg 拷贝 的独立自动显示比例（2026-09-25 追加）。
#
# 默认**关闭**：关闭后所有图层都用全局比例，行为与旧版完全一致。
# 打开后：`bg`（含"没有 bg 名 → 最底部有可用像素内容图层"的既有兜底，需求 §24）用
# DEFAULT_BG_DISPLAY_RATIO；`bg 拷贝`（中文版）/ `bg copy`（英文版，大小写不敏感）
# 用 DEFAULT_BG_COPY_DISPLAY_RATIO，**没有这个图层就不套用、也不做兜底**；
# 其余图层继续用全局比例。图层名的匹配规则见 `mangaproof/psd/document.py`。
DEFAULT_BG_RATIO_ENABLED = False
DEFAULT_BG_DISPLAY_RATIO = 1.0
DEFAULT_BG_COPY_DISPLAY_RATIO = 1.0

# 自动对比预设速度档位：(次/秒, 档位名)。默认"正常"= 4 次/秒，
# 即每张停留 250ms，与原硬编码行为一致（需求 §22）。
COMPARE_SPEED_TIERS: list[tuple[int, str]] = [
    (1, "慢"),
    (2, "较慢"),
    (4, "正常"),
    (5, "较快"),
    (8, "快"),
]
DEFAULT_COMPARE_SPEED_HZ = 4
DEFAULT_COMPARE_MODE = "auto"   # "auto" 自动切换 / "manual" 手动切换

# 裸滚轮（不按修饰键）行为：默认上下平移；可切换为缩放（需求 §27）。
# 触控板双指滚不受此设置影响，恒为双轴平移。
DEFAULT_WHEEL_MODE = "pan"      # "pan" 上下移动 / "zoom" 缩放

# 问题红框显示范围（需求 §39 Overlay 展示）：
# - "page"（默认）：始终显示当前 PSD（页）的全部问题——跨图层，一屏看全整页标注；
# - "layer"：只显示当前图层的问题（旧版本行为）。
ISSUE_SCOPES: tuple[str, ...] = ("page", "layer")
DEFAULT_ISSUE_SCOPE = "page"

# 当前图层视觉边界虚线框（蓝色）：定位辅助 Overlay，默认显示。
# 关闭后画布上不再绘制该框（定位、缩放行为完全不受影响）。
DEFAULT_SHOW_LAYER_OUTLINE = True

# 连续标注：默认关闭——进入拖框模式（红框模式 R / 问题类型快捷键）后，
# 标完一个问题即自动退出该模式；开启后问题面板右侧按钮常驻高亮，
# 可连续拖框标注（问题类型快捷键则保持同一类型待标）。
DEFAULT_CONTINUOUS_ANNOTATION = False

# 返修单页面图像格式：
# - "png"（默认）：无损，体积大；
# - "jpeg"：有损压缩，体积显著变小（漫画页面常见网点/渐变），质量可调。
REPORT_IMAGE_FORMATS: tuple[str, ...] = ("png", "jpeg")
DEFAULT_REPORT_IMAGE_FORMAT = "png"
JPEG_QUALITY_CHOICES: tuple[int, ...] = (60, 70, 80, 90, 95)
DEFAULT_JPEG_QUALITY = 80

# ---------------------------------------------------------------------------
# 自更新设置（需求 §11/§12/§13）
# ---------------------------------------------------------------------------
# 默认值（需求 §12）：分支 stable、渠道 Cloudflare R2、无代理、不限速、CDK 为空。
# 这些值在点「保存并检查更新」或「下载更新」时写盘（§13）。2026-09-24 起**下载前
# 也保存**：旧版只在检查那一支保存，于是"检查完之后再改设置"既不生效也不落盘。
# 点「取消」仍不保存本次未执行任何动作的修改。
UPDATE_BRANCHES: tuple[str, ...] = ("stable", "beta", "alpha")
DEFAULT_UPDATE_BRANCH = "stable"

#: 下载渠道：Cloudflare R2 / GitHub Release / MirrorChyan（需求 §28）
UPDATE_CHANNELS: tuple[str, ...] = ("r2", "github", "mirrorchyan")
DEFAULT_UPDATE_CHANNEL = "r2"

DEFAULT_UPDATE_PROXY = ""

#: 下载限速档位（MB/s）；0 = 不限速（需求 §30）
#: 仅作用于 GitHub 与 Cloudflare R2；MirrorChyan 不使用该设置。
SPEED_LIMIT_CHOICES: tuple[int, ...] = (0, 10, 20, 30, 40, 50)
DEFAULT_UPDATE_SPEED_LIMIT = 0

#: 更新设置里 CDK 明文的键名（仅 keyring 不可用时才出现，需求 §14/§15）。
#: 与 keyring 的逻辑命名保持一致：service = MangaProof, username = mirrorchyan_cdk。
UPDATE_CDK_KEY = "mirrorchyan_cdk"

# Cloudflare R2 公网前缀（需求 §23）。放在设置之外的原因：它不是用户可调项，
# 而是发布侧的事实；写在这里便于 UI/下载器共用，且改一处即可。
R2_PUBLIC_BASE = "https://download.mangaproof.priloba.com"

# 内存回收策略档位：宽松 / 平衡 / 激进。
# 各档预算（bg QImage 池字节上限、图层像素 LRU 字节上限）在
# mangaproof/ui/main_window.py 的 _MEMORY_POLICIES 中定义；
# 文档结构卸载（窗口外驱逐）三档一致。
#
# Android 端**只允许激进**（需求方决策）：移动设备内存紧张，档位不交给用户选。
# 平台默认值与强制逻辑见下方 default_memory_policy() 一族；桌面端不受影响。
MEMORY_POLICIES: tuple[str, ...] = ("relaxed", "balanced", "aggressive")
DEFAULT_MEMORY_POLICY = "balanced"
ANDROID_MEMORY_POLICY = "aggressive"

# 首次使用提醒条：桌面与 Android 的可调项不同（Android 锁定内存策略、
# 另有 Android 专有的界面缩放项），文案按平台给，避免指向不存在的设置项。
FIRST_RUN_BANNER_DESKTOP = (
    "第一次使用：建议先过一遍设置（显示比例、内存策略、返修单格式…），"
    "不调整就直接用默认值。"
)
FIRST_RUN_BANNER_ANDROID = (
    "第一次使用：建议先过一遍设置（界面缩放、显示比例、返修单格式…），"
    "不调整就直接用默认值。"
)


def android_memory_policy_locked() -> bool:
    """内存策略在 Android 上是否锁定为激进（**仅 Android**）。

    内存策略平台判定的唯一入口：默认值、读设置时的强制、设置页是否可改、
    启动后是否写回，全部走这里，避免多处各自判断平台（与 android_ui_scaling
    同构，判定本身见 utils/platform.is_android_strict 的编译期说明）。
    """
    return is_android_strict()


def default_memory_policy() -> str:
    """当前平台的默认内存策略档。

    - Android：恒 `"aggressive"`（锁定，见 android_memory_policy_locked）；
    - 桌面（Windows / Linux / macOS）：`DEFAULT_MEMORY_POLICY`（平衡）。
    """
    if android_memory_policy_locked():
        return ANDROID_MEMORY_POLICY
    return DEFAULT_MEMORY_POLICY


def effective_memory_policy(configured: Any) -> str:
    """把"设置里的档位"规整成"本平台实际该用的档位"。

    - Android：无论设置里写着什么，一律激进（旧版本写的档位、手工编辑过的值
      都可能带到这次启动）；
    - 桌面：非法/未知值回落 `DEFAULT_MEMORY_POLICY`。

    读取设置与热应用（ui/main_window.py 的 _apply_memory_policy）共用本函数，
    保证"文件里读到的值"和"实际生效的值"不会各判一套。
    """
    if android_memory_policy_locked():
        return ANDROID_MEMORY_POLICY
    return configured if configured in MEMORY_POLICIES else DEFAULT_MEMORY_POLICY


def first_run_banner() -> str:
    """首次使用提醒条文案（按平台）。Android 上内存策略不可调，文案不含它。"""
    if android_memory_policy_locked():
        return FIRST_RUN_BANNER_ANDROID
    return FIRST_RUN_BANNER_DESKTOP


# ---------------------------------------------------------------------------
# 界面缩放（**Android 专有**）
# ---------------------------------------------------------------------------
# 背景：Qt 在 Android 上 1 逻辑像素 == 1 dp（设备像素比 = 屏幕密度 / 160），
# 而本软件的界面是按 1440×900 桌面窗口设计的；平板上逻辑空间只有
# 1097×617 ~ 1280×800 dp，同样的控件占比更大、顶部整行工具栏（实测 1396 dp）
# 会被折进 "»" 扩展按钮。缩放的机制与取舍见
# docs/Android端界面适配_缩放与菜单栏.md。
#
# 硬约束：桌面端（Windows / Linux / macOS）**永远**是 1.0——由
# utils/platform.is_android_strict() 的编译期平台判定保证，与设置文件内容、
# 环境变量均无关。
DEFAULT_UI_SCALE = 1.0
# Android 默认缩放按**设备形态**分档：
#   · 手机（最小宽度 < 600 dp）→ 0.55：屏幕小、逻辑空间只有 ~390 dp 宽，
#     不缩得更小连界面都放不下；
#   · 折叠屏内屏 / 平板（≥ 600 dp）→ 0.75：1097 dp 宽设备上"整行工具栏不折叠"
#     的最大 5% 档（0.80 时工具栏逻辑宽约 1396 > 1097/0.8 ≈ 1371，会折叠）。
# 判定口径是"最小宽度 dp"= min(屏宽,屏高) ÷ 密度，也就是 Android 自己的
# sw600dp 约定；**不能只看 dpi**——折叠屏内屏的密度与手机同为 420 上下，
# 单看 dpi 会把折叠屏误判成手机。见 docs/Android端界面适配_缩放与菜单栏.md。
ANDROID_PHONE_UI_SCALE = 0.55
ANDROID_LARGE_UI_SCALE = 0.75
#: 设备形态分界（最小宽度 dp）：与 Android 的 sw600dp 一致
ANDROID_PHONE_MAX_SW_DP = 600
#: Android 侧（A11yEnvProvider）写入进程环境的最小宽度 dp
ANDROID_SW_DP_ENV = "MANGAPROOF_SW_DP"
#: 兼容旧名（原默认值）
ANDROID_DEFAULT_UI_SCALE = ANDROID_LARGE_UI_SCALE
UI_SCALE_MIN = 0.5
UI_SCALE_MAX = 1.5
UI_SCALE_STEP = 0.05

#: 本次启动实际生效的缩放（由 apply_startup_ui_scale 写入；默认 1.0）
_effective_ui_scale: float | None = None


def ui_scale_choices() -> list[float]:
    """设置页可选缩放档位：50%～150%，步进 5%（共 21 档）。"""
    count = round((UI_SCALE_MAX - UI_SCALE_MIN) / UI_SCALE_STEP)
    return [round(UI_SCALE_MIN + i * UI_SCALE_STEP, 2) for i in range(count + 1)]


def android_sw_dp() -> int | None:
    """当前设备的最小宽度（dp）：min(屏宽,屏高) ÷ 密度；拿不到返回 None。

    两个来源，按可靠性排序：

    1. **环境变量 `MANGAPROOF_SW_DP`**：Android 侧 ContentProvider
       （packaging/android/java/.../A11yEnvProvider.java）在任何 Activity 之前、
       用 DisplayMetrics 算好写进进程环境。这是**唯一在 QApplication 创建之前**
       （也就是写入 QT_SCALE_FACTOR 之前）能拿到的屏幕信息；
    2. **QScreen 逻辑尺寸兜底**：Qt 在 Android 上 1 逻辑像素 = 1 dp，所以
       min(宽,高) 就是最小宽度 dp；但只有 QApplication 创建之后才可用。
    """
    raw = os.environ.get(ANDROID_SW_DP_ENV)
    if raw:
        try:
            value = int(float(raw))
        except (TypeError, ValueError):
            value = 0
        if 100 <= value <= 4000:      # 明显不合理就当作没拿到
            return value
    return _sw_dp_from_screen()


def _sw_dp_from_screen() -> int | None:
    """从 QScreen 读最小宽度 dp（需 QApplication 已创建，否则返回 None）。"""
    try:
        from PySide6.QtGui import QGuiApplication

        if QGuiApplication.instance() is None:
            return None
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return None
        geometry = screen.geometry()
        return min(geometry.width(), geometry.height())
    except Exception:  # pragma: no cover - Qt 不可用等极端情况
        log.debug("读取 QScreen 尺寸失败", exc_info=True)
        return None


def android_device_class() -> str:
    """设备形态：``"phone"`` / ``"large"``（折叠屏内屏、平板）/ ``"unknown"``。

    按 Android 的 sw600dp 约定分类；拿不到屏幕信息时返回 "unknown"
    （默认值按 large 处理，宁可少缩也不要在未知设备上缩过头）。
    """
    sw_dp = android_sw_dp()
    if sw_dp is None:
        return "unknown"
    return "phone" if sw_dp < ANDROID_PHONE_MAX_SW_DP else "large"


# ---------------------------------------------------------------------------
# 预加载 / 保留窗口（平台分档）
# ---------------------------------------------------------------------------
# 索引偏移，均相对当前页 i（0 = 当前页）。
#
# 桌面：后 3 + 前 1 是**预加载邻域**，另加前 2 作**回看松弛**，共 6 页。
# Android：前 1 + 当前 + 后 1，共 3 页。理由见
# docs/Android端适配设计_原生文件读写_横屏全屏_分层图标_内存策略.md §5.4：
# Android 固定激进档（LRU 256MB），而单文件全图层像素实测就需 90–137MB
# （15 个真实样本）——6 页的需求远超预算，预热了也留不住，只是把单线程
# 预加载的解码预算花在会被立刻淘汰的数据上。
#
# 三处用途共用本表：预热队列、驱逐保留窗口、打开任务时保留的文档对象。
# 三者必须同源，否则会出现「保留了却没预热」或「预热了又立刻被驱逐」的错配。
PRELOAD_WINDOW_OFFSETS_DESKTOP: tuple[int, ...] = (-2, -1, 0, 1, 2, 3)
PRELOAD_WINDOW_OFFSETS_ANDROID: tuple[int, ...] = (-1, 0, 1)


def preload_window_offsets() -> tuple[int, ...]:
    """预加载 / 保留窗口的索引偏移（按平台）。

    唯一判定入口：预热队列、驱逐保留窗口、打开任务时保留哪些文档对象，
    全部走这里（判定本身见 utils/platform.is_android_strict 的编译期说明）。
    桌面端取值与改动前完全一致（后 3 + 前 1 + 前 2 松弛）。
    """
    if android_ui_scaling():
        return PRELOAD_WINDOW_OFFSETS_ANDROID
    return PRELOAD_WINDOW_OFFSETS_DESKTOP


def android_ui_scaling() -> bool:
    """界面缩放是否适用于当前平台（**仅 Android**）。

    界面缩放的唯一判定入口：设置页是否显示该选项、默认值、启动时是否写入
    QT_SCALE_FACTOR、改完是否提示重启，全部走这里，避免多处各自判断平台
    （判定本身见 utils/platform.is_android_strict 的编译期说明）。
    """
    return is_android_strict()


def default_ui_scale() -> float:
    """当前平台的默认缩放。

    - 桌面（Windows / Linux / macOS）：恒 1.0；
    - Android：按设备形态分档——手机（最小宽度 < 600 dp）**0.55**；
      折叠屏内屏 / 平板（≥ 600 dp）**0.75**；机型未知时按 0.75（少缩为妙）。
    """
    if not android_ui_scaling():
        return DEFAULT_UI_SCALE
    device_class = android_device_class()
    if device_class == "phone":
        return ANDROID_PHONE_UI_SCALE
    return ANDROID_LARGE_UI_SCALE


def clamp_ui_scale(value: Any, *, default: float = DEFAULT_UI_SCALE) -> float:
    """把界面缩放规整到合法档位。

    非法（非数字 / NaN）或越界（不在 [0.5, 1.5]）→ 返回 default；
    合法值对齐到 5% 步进。与 layer_display_ratio 等设置项一致：越界即回默认，
    不做"就近夹取"，避免把明显损坏的配置静默改成另一个意外值。
    """
    try:
        scale = float(value)
    except (TypeError, ValueError):
        return default
    if scale != scale:  # NaN
        return default
    if not (UI_SCALE_MIN <= scale <= UI_SCALE_MAX):
        return default
    return round(round(scale / UI_SCALE_STEP) * UI_SCALE_STEP, 2)


def _read_raw_settings(path: Path) -> dict[str, Any]:
    """容错读取 settings.json 原始字典（失败返回 {}）。

    仅供启动期"读一个键"使用：此时 SettingsManager 还没构造（必须在
    QApplication 之前拿到缩放），所以这里不复用它的解析逻辑。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def resolve_ui_scale(app_dir: Path | None = None) -> float:
    """决定本次启动的界面缩放。

    **桌面端直接返回 1.0**（连设置文件都不读）：即使 settings.json 里写着 0.5、
    即使环境里存在 ANDROID_ROOT 之类的变量，也不缩放——见 utils/platform.py。
    Android：读 settings.json 的 ui_scale；**缺键 → 按设备形态给默认值**
    （手机 0.55 / 折叠屏内屏与平板 0.75，见 default_ui_scale）；非法/越界 → 同默认。
    """
    if not android_ui_scaling():
        return DEFAULT_UI_SCALE
    fallback = default_ui_scale()
    directory = Path(app_dir) if app_dir is not None else paths.get_app_dir()
    raw = _read_raw_settings(directory / "settings.json")
    if "ui_scale" not in raw:
        return fallback
    return clamp_ui_scale(raw.get("ui_scale"), default=fallback)


def apply_startup_ui_scale(app_dir: Path | None = None) -> float:
    """在创建 QApplication **之前**调用：把缩放交给 Qt（Android 专有）。

    - Qt 只在启动时读一次 `QT_SCALE_FACTOR`（QHighDpiScaling 在 QGuiApplication
      初始化时取值），所以必须在此刻写入，改设置后需重启应用生效；
    - 桌面端不写任何环境变量（resolve_ui_scale 恒为 1.0）；
    - 用 `setdefault`：**绝不覆盖**用户/系统自己设置的 `QT_SCALE_FACTOR`；
    - 返回值与 effective_ui_scale() 记录的是"实际生效值"（含环境变量优先的情况）。
    """
    global _effective_ui_scale
    scale = resolve_ui_scale(app_dir)
    if scale != DEFAULT_UI_SCALE:
        os.environ.setdefault("QT_SCALE_FACTOR", f"{scale:g}")

    env_value = os.environ.get("QT_SCALE_FACTOR")
    if env_value:
        try:
            scale = float(env_value)
        except ValueError:
            pass
    _effective_ui_scale = scale
    return scale


def effective_ui_scale() -> float:
    """本次启动实际生效的界面缩放（未调用 apply_startup_ui_scale 时为 1.0）。"""
    return DEFAULT_UI_SCALE if _effective_ui_scale is None else _effective_ui_scale


def reconcile_android_ui_scale(manager: "SettingsManager") -> float | None:
    """运行期兜底：把"按设备形态得到的默认缩放"写回设置（**仅当用户从未设置过**）。

    正常路径不会做任何事：Android 侧 ContentProvider 在任何 Activity 之前就把
    最小宽度 dp 写进了进程环境（MANGAPROOF_SW_DP），启动前的 resolve_ui_scale()
    已经用对了默认值。

    兜底路径：环境变量缺失（provider 未生效等）时，启动前只能按"机型未知 → 0.75"
    处理；这里在 QApplication 已就绪后用 QScreen 重新判定，若得到的默认值与本次
    启动实际生效值不同，就写回设置（下次启动生效）并记一条警告日志。
    返回写入的值；无需修正时返回 None。
    """
    if not android_ui_scaling():
        return None
    raw = _read_raw_settings(paths.settings_path())
    if "ui_scale" in raw:
        return None                      # 用户已明确设置过（或上次已确定），不覆盖
    desired = default_ui_scale()
    current = effective_ui_scale()
    if abs(desired - current) < 1e-9:
        return None
    manager.settings.ui_scale = desired
    manager.save()
    log.warning(
        "设备形态默认缩放 = %.2f，与本次启动生效值 %.2f 不一致：已写入设置，重启应用后生效",
        desired,
        current,
    )
    return desired


def reconcile_android_memory_policy(manager: "SettingsManager") -> str | None:
    """启动期把 Android 锁定的内存策略写回 settings.json。

    读取时 SettingsManager 已经强制过（见 _from_dict → effective_memory_policy），
    所以**运行值一定是对的**；这里补的是磁盘一致性：旧版本写的 `balanced`、
    手工编辑过的档位，都不该在文件里留着一个永不生效的值。

    ⚠️ 判据必须是**文件里的原始值**，不能看 manager.settings.memory_policy——
    后者在读取阶段就被强制成 aggressive 了，拿它判断会永远"已一致"，写回形同虚设。

    文件不存在（首次运行）时不写：此时内存值就是默认激进，磁盘上没有旧值需要纠正，
    而凭空建出 settings.json 会让「首次使用」提醒条立刻收起（见 _refresh_settings_banner）。

    与 reconcile_android_ui_scale 的区别：那个只在"用户从未设置过"时写，
    因为它修的是平台默认值；本函数是**锁定**语义（需求方决策），无条件纠正。

    已一致时不落盘（幂等）。返回写入的值；无需纠正时返回 None。
    """
    if not android_memory_policy_locked():
        return None
    raw = _read_raw_settings(manager.path)
    if raw.get("memory_policy") == ANDROID_MEMORY_POLICY:
        return None
    if not raw:
        return None                      # 还没有配置文件：没什么可纠正的
    previous = raw.get("memory_policy")
    manager.settings.memory_policy = ANDROID_MEMORY_POLICY
    manager.save()
    log.info(
        "Android 内存策略锁定为 %s（配置里原值 %r）：已写回配置，运行值不受影响",
        ANDROID_MEMORY_POLICY,
        previous,
    )
    return ANDROID_MEMORY_POLICY


@dataclass
class UpdateSettings:
    """自更新设置（需求 §11~§15）。

    存储位置：``settings.json`` 的顶层 ``"update"`` 对象（需求 §13 的结构）：::

        {
            "update": {
                "branch": "stable",
                "channel": "r2",
                "proxy": "",
                "speed_limit": 0,
                "mirrorchyan_cdk": ""      # 仅 keyring 不可用时才有明文
            }
        }

    ``cdk`` 是**运行时字段**：桌面端优先放系统凭据库（keyring），
    只有 keyring 实测不可用时才落到这里的明文（§14/§15）。
    因此它不属于"用户设置"，但必须随设置一起读写，否则迁移逻辑无处落地。
    """

    branch: str = DEFAULT_UPDATE_BRANCH
    channel: str = DEFAULT_UPDATE_CHANNEL
    proxy: str = DEFAULT_UPDATE_PROXY
    speed_limit: int = DEFAULT_UPDATE_SPEED_LIMIT
    cdk: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转成 settings.json 里的 ``update`` 对象。"""
        return {
            "branch": self.branch,
            "channel": self.channel,
            "proxy": self.proxy,
            "speed_limit": self.speed_limit,
            UPDATE_CDK_KEY: self.cdk,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "UpdateSettings":
        """从 ``update`` 对象解析；非法值一律回落默认（与项目既有风格一致）。"""
        s = cls()
        if not isinstance(raw, dict):
            return s

        branch = str(raw.get("branch", DEFAULT_UPDATE_BRANCH) or "")
        s.branch = branch if branch in UPDATE_BRANCHES else DEFAULT_UPDATE_BRANCH

        channel = str(raw.get("channel", DEFAULT_UPDATE_CHANNEL) or "")
        s.channel = channel if channel in UPDATE_CHANNELS else DEFAULT_UPDATE_CHANNEL

        # 代理必须是字符串：非字符串（例如误写成数字）一律当作"没填"，
        # 不 str() 转换——那会把 5 变成 "5" 这种看起来像配置、实际发不出去的代理。
        proxy = raw.get("proxy", DEFAULT_UPDATE_PROXY)
        s.proxy = proxy.strip() if isinstance(proxy, str) else DEFAULT_UPDATE_PROXY

        try:
            limit = int(raw.get("speed_limit", DEFAULT_UPDATE_SPEED_LIMIT))
        except (TypeError, ValueError):
            limit = DEFAULT_UPDATE_SPEED_LIMIT
        s.speed_limit = limit if limit in SPEED_LIMIT_CHOICES else DEFAULT_UPDATE_SPEED_LIMIT

        s.cdk = str(raw.get(UPDATE_CDK_KEY, "") or "").strip() if isinstance(
            raw.get(UPDATE_CDK_KEY, ""), str
        ) else ""
        return s


@dataclass
class Settings:
    """运行时设置对象。"""

    layer_display_ratio: float = DEFAULT_DISPLAY_RATIO
    # bg / bg 拷贝 的独立自动显示比例（默认关；见 DEFAULT_BG_RATIO_ENABLED 的说明）
    bg_ratio_enabled: bool = DEFAULT_BG_RATIO_ENABLED
    bg_display_ratio: float = DEFAULT_BG_DISPLAY_RATIO
    bg_copy_display_ratio: float = DEFAULT_BG_COPY_DISPLAY_RATIO
    compare_mode: str = DEFAULT_COMPARE_MODE   # "auto" / "manual"
    compare_speed_hz: int = DEFAULT_COMPARE_SPEED_HZ
    wheel_mode: str = DEFAULT_WHEEL_MODE       # "pan" / "zoom"
    issue_scope: str = DEFAULT_ISSUE_SCOPE     # "page"（当前页全部）/ "layer"（仅当前图层）
    # 当前图层视觉边界虚线框（蓝色）是否显示（默认显示）
    show_layer_outline: bool = DEFAULT_SHOW_LAYER_OUTLINE
    # 连续标注（默认关：标完一个问题自动退出拖框模式）
    continuous_annotation: bool = DEFAULT_CONTINUOUS_ANNOTATION
    recursive_scan: bool = False
    generate_pdf_on_complete: bool = True
    report_name: str = ""
    # 返修单页面图像：png（无损）/ jpeg（压缩，体积小）+ JPEG 质量
    report_image_format: str = DEFAULT_REPORT_IMAGE_FORMAT
    report_jpeg_quality: int = DEFAULT_JPEG_QUALITY
    # 返修单 PSD 总览表是否隐藏「全部通过且无问题」的页（默认隐藏）
    report_hide_clean_files: bool = True
    hide_console: bool = True   # 打包产物隐藏控制台（直接运行 py 时始终显示）
    keybindings: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_KEYBINDINGS))
    issue_types: list[dict[str, str]] = field(
        default_factory=lambda: [dict(t) for t in DEFAULT_ISSUE_TYPES]
    )
    custom_comment_key: str = "Ctrl+Return"
    # 内存回收策略：aggressive（激进）/ balanced（平衡）/ relaxed（宽松）
    memory_policy: str = DEFAULT_MEMORY_POLICY
    # 界面缩放（**Android 专有**，桌面端恒 1.0；改后需重启应用生效）
    ui_scale: float = DEFAULT_UI_SCALE
    # 自更新设置（需求 §11~§15）：分支/渠道/代理/限速 + keyring 不可用时的明文 CDK
    update: UpdateSettings = field(default_factory=UpdateSettings)

    # -- 派生查询 ----------------------------------------------------------

    def issue_key_map(self) -> dict[str, str]:
        """问题类型名 -> 快捷键。"""
        return {t["name"]: t.get("key", "") for t in self.issue_types}

    def issue_type_names(self) -> list[str]:
        return [t["name"] for t in self.issue_types]

    def key_for_issue(self, name: str) -> str:
        return self.issue_key_map().get(name, "")

    def binding(self, action: str) -> str:
        return self.keybindings.get(action, DEFAULT_KEYBINDINGS.get(action, ""))

    def shortcut_conflicts(self) -> dict[str, list[tuple[str, str]]]:
        """当前配置里的重复快捷键，见模块级 shortcut_conflicts()。"""
        return shortcut_conflicts(
            self.keybindings, self.issue_types, self.custom_comment_key
        )

    def _heal_shortcut_conflicts(self) -> None:
        """把已知的历史撞车配置自动让位（旧版「漏字」与「红框模式」同绑 R）。

        只在旧默认值原样保留时生效：用户若已自行改绑任一侧，说明他有明确
        意图，不动。不修的话 Qt 判定歧义，两个动作都按不出来。
        """
        for item in self.issue_types:
            replacement = _LEGACY_DUPLICATE_ISSUE_KEYS.get(item.get("name", ""))
            if not replacement or not item.get("key"):
                continue
            if normalize_key(item["key"]) != "R":
                continue
            if normalize_key(self.binding("redraw_mode")) != "R":
                continue
            log.info("快捷键修复：问题类型「%s」R → %s（与红框模式撞车）",
                     item["name"], replacement)
            item["key"] = replacement

    def _migrate_issue_types(self, stored_version: int) -> None:
        """旧版问题类型表 → 新版默认键位（用户自己改过的键位保持不动）。

        - 键位仍是旧默认值的类型：跟随新表（如「漏字」R → U）；
        - 新版新增的类型：按默认位置插入，键位若被用户自定键占用则先留空；
        - 用户自己改过的键位、以及自建的类型：原样保留（自建的排在末尾）。
        """
        if stored_version >= ISSUE_TYPES_VERSION:
            return
        new_keys = {t["name"]: t["key"] for t in DEFAULT_ISSUE_TYPES}
        for item in self.issue_types:
            name = item.get("name", "")
            key = item.get("key", "")
            old_default = _LEGACY_ISSUE_KEYS.get(name)
            if old_default and normalize_key(key) == normalize_key(old_default):
                item["key"] = new_keys.get(name, key)

        current = {item["name"]: item for item in self.issue_types}
        used = {
            normalize_key(item.get("key", ""))
            for item in self.issue_types
            if item.get("key")
        }
        merged: list[dict[str, str]] = []
        for default in DEFAULT_ISSUE_TYPES:
            name = default["name"]
            if name in current:
                merged.append(current.pop(name))
                continue
            key = default["key"]
            if normalize_key(key) in used:      # 用户自定键占了这个位置 → 先留空
                key = ""
            else:
                used.add(normalize_key(key))
            merged.append({"name": name, "key": key})
        merged.extend(current.values())          # 用户自建类型保留在末尾
        self.issue_types = merged
        log.info("问题类型表已升级到 v%d（共 %d 类）", ISSUE_TYPES_VERSION, len(merged))


def parse_display_ratio(value: Any, default: float) -> float:
    """显示比例解析：非数字 / 越界一律回默认（档位表之外的合法值保留）。

    档位表（:data:`DISPLAY_RATIOS`）只是 UI 给的选择项；设置文件里手工写进来的
    合法值（0.01～4.0）一律尊重 —— 这是既有行为，加 bg 独立比例后继续沿用。
    """
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        return default
    if not (DISPLAY_RATIO_MIN <= ratio <= DISPLAY_RATIO_MAX):
        return default
    return ratio


class SettingsManager:
    """settings.json 的读写封装。"""

    def __init__(self, path: Path | None = None):
        self._path = path if path is not None else paths.settings_path()
        self._lock = threading.Lock()
        # 本次启动时配置文件是否存在：用于「首次使用」引导（只提示，不代替决策）。
        # 注意 save() 之后文件就存在了，所以这是启动快照，不是实时状态。
        self.was_missing = not self._path.exists()
        # 旧版残留的最近打开记录（settings.json 里的 recent_paths）：
        # 只读一次，交给 RecentManager 迁移到 recent.json，见 config/recent.py
        self._legacy_recent_paths: list[str] = []
        self.settings = self._load()

    @property
    def path(self) -> Path:
        """settings.json 的完整路径（构造时注入或平台默认）。

        启动期协调函数（reconcile_android_*）必须读**本实例**的路径而不是
        paths.settings_path()：测试会注入 tmp_path，硬取平台路径会写到真实
        程序目录去。
        """
        return self._path

    @property
    def has_settings_file(self) -> bool:
        """配置文件当前是否存在（用户确认过设置 / 程序正常退出过即存在）。"""
        return self._path.exists()

    @property
    def is_first_use(self) -> bool:
        """是否首次使用：启动时既没有 settings.json，也没有 recent.json。

        两者都没有 = 从没确认过设置、也从没打开过任务（老用户升级至少会有
        其中一个）。只用于决定要不要把设置页面直接摆到用户面前，不做任何
        预设或推荐——默认值已在代码里定好。
        """
        return self.was_missing and not self.recent_path.exists()

    @property
    def recent_path(self) -> Path:
        """最近打开记录文件：与 settings.json 同目录的独立 recent.json。"""
        return self._path.with_name(paths.RECENT_FILE_NAME)

    @property
    def legacy_recent_paths(self) -> list[str]:
        """旧版混在 settings.json 里的最近打开记录（新装程序为空）。"""
        return list(self._legacy_recent_paths)

    # -- 读写 --------------------------------------------------------------

    def _load(self) -> Settings:
        try:
            if not self._path.exists():
                return self._default_settings()
            with open(self._path, "r", encoding="utf-8") as f:
                raw: dict[str, Any] = json.load(f)
            return self._from_dict(raw)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            log.warning("读取 settings.json 失败，使用默认设置：%s", exc)
            return self._default_settings()

    @staticmethod
    def _default_settings() -> Settings:
        """无配置文件（或文件损坏）时的默认设置。

        界面缩放取**平台默认**（Android 0.75 / 桌面 1.0），与 resolve_ui_scale()
        保持一致——这样设置页显示的档位就是本次启动实际生效的档位。
        """
        s = Settings()
        s.ui_scale = default_ui_scale()
        return s

    def _from_dict(self, raw: dict[str, Any]) -> Settings:
        s = Settings()

        s.layer_display_ratio = parse_display_ratio(
            raw.get("layer_display_ratio", DEFAULT_DISPLAY_RATIO), DEFAULT_DISPLAY_RATIO
        )
        s.bg_ratio_enabled = bool(raw.get("bg_ratio_enabled", DEFAULT_BG_RATIO_ENABLED))
        s.bg_display_ratio = parse_display_ratio(
            raw.get("bg_display_ratio", DEFAULT_BG_DISPLAY_RATIO), DEFAULT_BG_DISPLAY_RATIO
        )
        s.bg_copy_display_ratio = parse_display_ratio(
            raw.get("bg_copy_display_ratio", DEFAULT_BG_COPY_DISPLAY_RATIO),
            DEFAULT_BG_COPY_DISPLAY_RATIO,
        )

        mode = raw.get("compare_mode", DEFAULT_COMPARE_MODE)
        s.compare_mode = mode if mode in ("auto", "manual") else DEFAULT_COMPARE_MODE

        try:
            hz = int(raw.get("compare_speed_hz", DEFAULT_COMPARE_SPEED_HZ))
        except (TypeError, ValueError):
            hz = DEFAULT_COMPARE_SPEED_HZ
        if not (1 <= hz <= 10):
            hz = DEFAULT_COMPARE_SPEED_HZ
        s.compare_speed_hz = hz

        wheel = raw.get("wheel_mode", DEFAULT_WHEEL_MODE)
        s.wheel_mode = wheel if wheel in ("pan", "zoom") else DEFAULT_WHEEL_MODE

        scope = raw.get("issue_scope", DEFAULT_ISSUE_SCOPE)
        s.issue_scope = scope if scope in ISSUE_SCOPES else DEFAULT_ISSUE_SCOPE

        s.show_layer_outline = bool(
            raw.get("show_layer_outline", DEFAULT_SHOW_LAYER_OUTLINE)
        )
        s.continuous_annotation = bool(
            raw.get("continuous_annotation", DEFAULT_CONTINUOUS_ANNOTATION)
        )

        s.recursive_scan = bool(raw.get("recursive_scan", False))
        s.generate_pdf_on_complete = bool(
            raw.get("generate_pdf_on_complete", True)
        )
        s.report_name = str(raw.get("report_name", "") or "")

        fmt = raw.get("report_image_format", DEFAULT_REPORT_IMAGE_FORMAT)
        s.report_image_format = (
            fmt if fmt in REPORT_IMAGE_FORMATS else DEFAULT_REPORT_IMAGE_FORMAT
        )
        try:
            quality = int(raw.get("report_jpeg_quality", DEFAULT_JPEG_QUALITY))
        except (TypeError, ValueError):
            quality = DEFAULT_JPEG_QUALITY
        if not (60 <= quality <= 95):
            quality = DEFAULT_JPEG_QUALITY
        s.report_jpeg_quality = quality
        s.report_hide_clean_files = bool(raw.get("report_hide_clean_files", True))
        s.hide_console = bool(raw.get("hide_console", True))
        s.custom_comment_key = str(
            raw.get("custom_comment_key", DEFAULT_KEYBINDINGS["custom_comment"])
        )

        kb = raw.get("keybindings", {})
        if isinstance(kb, dict):
            merged = dict(DEFAULT_KEYBINDINGS)
            for k, v in kb.items():
                if isinstance(v, str) and v.strip():
                    merged[k] = v.strip()
            s.keybindings = merged

        types = raw.get("issue_types")
        if isinstance(types, list) and types:
            cleaned = []
            seen = set()
            for item in types:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                cleaned.append({"name": name, "key": str(item.get("key", ""))})
            if cleaned:
                s.issue_types = cleaned

        try:
            types_version = int(raw.get("issue_types_version", 1))
        except (TypeError, ValueError):
            types_version = 1
        s._migrate_issue_types(types_version)
        s._heal_shortcut_conflicts()

        # 兼容旧版：最近打开原本混存在 settings.json（现为独立 recent.json）。
        # 这里只读不写——迁移由 config/recent.py 完成，之后该键自然消失。
        recent = raw.get("recent_paths", [])
        if isinstance(recent, list):
            self._legacy_recent_paths = [
                str(p) for p in recent if isinstance(p, str) and p.strip()
            ][:10]

        # 内存回收策略：Android 强制激进（effective_memory_policy 内部判平台），
        # 桌面端读文件值、非法值回落默认。
        s.memory_policy = effective_memory_policy(
            raw.get("memory_policy", DEFAULT_MEMORY_POLICY)
        )

        # 界面缩放（Android 专有）：缺键时取平台默认（Android 0.75 / 桌面 1.0），
        # 与实际生效值保持一致；桌面端该值不会被用于缩放（见 resolve_ui_scale）。
        s.ui_scale = clamp_ui_scale(raw.get("ui_scale"), default=default_ui_scale())

        # 自更新设置（需求 §13）：顶层 "update" 对象；缺键/非法值一律回落默认，
        # 因此旧版 settings.json 无需迁移即可直接读。
        s.update = UpdateSettings.from_dict(raw.get("update"))

        return s

    def save(self) -> None:
        with self._lock:
            try:
                payload = {
                    "settings_version": SETTINGS_VERSION,
                    "layer_display_ratio": self.settings.layer_display_ratio,
                    "bg_ratio_enabled": self.settings.bg_ratio_enabled,
                    "bg_display_ratio": self.settings.bg_display_ratio,
                    "bg_copy_display_ratio": self.settings.bg_copy_display_ratio,
                    "compare_mode": self.settings.compare_mode,
                    "compare_speed_hz": self.settings.compare_speed_hz,
                    "wheel_mode": self.settings.wheel_mode,
                    "issue_scope": self.settings.issue_scope,
                    "show_layer_outline": self.settings.show_layer_outline,
                    "continuous_annotation": self.settings.continuous_annotation,
                    "recursive_scan": self.settings.recursive_scan,
                    "generate_pdf_on_complete": self.settings.generate_pdf_on_complete,
                    "report_name": self.settings.report_name,
                    "report_image_format": self.settings.report_image_format,
                    "report_jpeg_quality": self.settings.report_jpeg_quality,
                    "report_hide_clean_files": self.settings.report_hide_clean_files,
                    "hide_console": self.settings.hide_console,
                    "custom_comment_key": self.settings.custom_comment_key,
                    "keybindings": self.settings.keybindings,
                    "issue_types": self.settings.issue_types,
                    "issue_types_version": ISSUE_TYPES_VERSION,
                    "memory_policy": self.settings.memory_policy,
                    "ui_scale": self.settings.ui_scale,
                    "update": self.settings.update.to_dict(),
                }
                tmp = self._path.with_suffix(".json.tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                tmp.replace(self._path)
            except OSError as exc:
                log.warning("写入 settings.json 失败：%s", exc)

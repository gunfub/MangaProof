# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""安装器 UI（需求 §43，调研报告 §11.2/§11.4）。

Tkinter GUI + **控制台降级**。降级不是"偶尔用一下"，而是必须随时可用：

- ``--cli`` 显式要求（无图形会话，如 SSH）；
- ``import tkinter`` 失败（本机就没有 tkinter）；
- ``Tk()`` 初始化失败（没有 ``DISPLAY``/``WAYLAND_DISPLAY``）。

三种情况的进度都会走 :class:`ConsoleReporter`，并且 **stdout 可能是 ``None``**
（PyInstaller windowed onefile，见 ``updater/main.py``），所以任何写操作都必须容错。

UI 必须同时呈现三层信息（§11.4，缺一不可）::

    正在恢复用户数据…                          3/12

    正在复制：logs/2026-09-22.log

    [██████████░░░░░░░░░░]  50%

1. 阶段（``on_phase`` → 中文文案）；
2. 当前具体文件操作（``on_item`` → 相对路径）；
3. 进度条 + 计数；阶段总数未知时进度条为不确定模式（``total = -1``）。

调色板逐值对齐 ``mangaproof/ui/theme.py``（**不 import 它**：那个模块依赖
PySide6，安装器必须完全独立于主程序运行环境）。色值来源：``mangaproof/ui/theme.py:18-32``。
"""

from __future__ import annotations

import logging
import os
import queue
import sys
import threading
import traceback
from typing import Callable

from updater import fonts
from updater.state import label_for

log = logging.getLogger("mangaproof.updater.ui")

# --------------------------------------------------------------------------- #
# 调色板（规格，逐值取自 mangaproof/ui/theme.py:18-32，不得自行改动）
# --------------------------------------------------------------------------- #
COLOR_BG_MAIN = "#2b2d30"        # theme.py:18 COLOR_BG_MAIN   —— 窗口/Toplevel 背景
COLOR_BG_PANEL = "#313438"       # theme.py:19 COLOR_BG_PANEL  —— 分组框、进度区背板
COLOR_BG_WIDGET = "#3a3d42"      # theme.py:20 COLOR_BG_WIDGET —— 按钮常态
COLOR_BG_HOVER = "#45494f"       # theme.py:21 COLOR_BG_HOVER  —— 按钮 hover
COLOR_BG_SELECTED = "#2d5f8a"    # theme.py:22 COLOR_BG_SELECTED —— 按钮 active
COLOR_BORDER = "#4a4e54"         # theme.py:23 COLOR_BORDER    —— 分隔线、边框
COLOR_TEXT = "#e4e6eb"           # theme.py:24 COLOR_TEXT      —— 标签文字
COLOR_TEXT_DIM = "#a0a4ab"       # theme.py:25 COLOR_TEXT_DIM  —— 路径/日志文本
COLOR_ACCENT = "#4a90d9"         # theme.py:26 COLOR_ACCENT    —— 进度条填充
COLOR_PASS = "#4caf50"           # theme.py:27 COLOR_PASS      —— 成功
COLOR_FAIL = "#e53935"           # theme.py:28 COLOR_FAIL      —— 失败
COLOR_WARN = "#f5a623"           # theme.py:29 COLOR_WARN      —— 警告

#: 字体优先级（调研报告 §11.2 的版式规格）；全都缺失时用 Tk 默认字体
FONT_CANDIDATES: tuple[str, ...] = (
    "MiSans",
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "Noto Sans CJK SC",
    "PingFang SC",
)

#: 窗口默认尺寸（版式：标题在上、进度区居中、明细在下、主按钮右下角）。
#: 明细区是**用户唯一的排错依据**（失败时窗口保持打开，让用户截图反馈），
#: 所以给足高度：640×560 起步，用户还能自己拉大（见 minsize 与 pack expand）。
WINDOW_WIDTH = 640
WINDOW_HEIGHT = 560
#: 明细区最小高度（行数）。用户把窗口拉小时也至少留这么多行，
#: 免得"关键那行错误"被挤出可视区。
DETAIL_MIN_LINES = 12
POLL_INTERVAL_MS = 60
AUTO_CLOSE_MS = 1500
LOG_LINES = 500


class Reporter:
    """安装器内核 → UI 的事件契约（调研报告 §11.4）。

    内核只调这四个方法，不关心是 Tk 还是控制台。
    """

    finished = False

    def phase(self, phase: str) -> None:  # pragma: no cover - 抽象
        """进入某个阶段（INIT/…/ROLLBACK，见 :data:`updater.state.PHASES`）。"""

    def item(self, rel_path: str, action: str = "") -> None:  # pragma: no cover - 抽象
        """当前正在处理的具体文件/目录（相对路径）+ 动作文案。"""

    def progress(self, done: int, total: int) -> None:  # pragma: no cover - 抽象
        """阶段内计数；``total`` 未知时为 ``-1``。"""

    def log(self, line: str) -> None:  # pragma: no cover - 抽象
        """追加一行明细。"""

    def finish(self, ok: bool, message: str = "") -> None:  # pragma: no cover - 抽象
        self.finished = True


class NullReporter(Reporter):
    """什么都不做的 reporter（单测/无 UI 场景）。"""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def log(self, line: str) -> None:
        self.lines.append(line)


class ConsoleReporter(Reporter):
    """控制台降级：stdout/stderr 可能为 ``None``（windowed onefile），全程容错。"""

    def __init__(self, stream=None) -> None:
        self.stream = stream
        self.lines: list[str] = []
        self._phase = ""
        self._last_bar = ""
        self._last_bucket = -1

    # -- 输出 ------------------------------------------------------------- #
    def _write(self, text: str) -> None:
        self.lines.append(text)
        targets = [self.stream, sys.stdout, sys.stderr]
        for target in targets:
            if target is None:
                continue
            try:
                target.write(text + "\n")
                target.flush()
                return
            except Exception:  # 流被关闭/不可写：继续尝试下一个
                continue
        log.info("%s", text)

    def _tty(self):
        target = self.stream or sys.stdout
        try:
            return target if target is not None and target.isatty() else None
        except Exception:
            return None

    def _write_inline(self, text: str) -> None:
        target = self._tty()
        if target is None:
            return
        try:
            target.write("\r" + text)
            target.flush()
        except Exception:
            return

    # -- 契约 ------------------------------------------------------------- #
    def phase(self, phase: str) -> None:
        self._phase = phase
        self._write(f"[阶段] {label_for(phase)}")

    def item(self, rel_path: str, action: str = "") -> None:
        text = f"{action}：{rel_path}" if action else str(rel_path)
        self._write(f"  {text}")

    def progress(self, done: int, total: int) -> None:
        if total is None or total < 0:
            self._write_inline("  [不确定进度] 处理中…")
            return
        ratio = 0.0 if total <= 0 else min(1.0, max(0.0, done / total))
        filled = int(ratio * 20)
        bar = "█" * filled + "░" * (20 - filled)
        line = f"  [{bar}] {ratio * 100:5.1f}%  {done}/{total}"
        self._last_bar = line
        if self._tty() is not None:
            # 交互终端：原地刷新进度条，完成时换行
            self._write_inline(line)
            if done >= total:
                self._write(line)
            return
        # 非交互（管道/日志文件：windowed onefile 下**日志是唯一线索**）：
        # 每跨过 10% 打一行，保证"计数"一定可见（调研报告 §11.4 第③层信息）
        bucket = int(ratio * 10)
        if bucket != self._last_bucket or done >= total:
            self._last_bucket = bucket
            self._write(line)

    def log(self, line: str) -> None:
        self._write(f"  {line}")

    def finish(self, ok: bool, message: str = "") -> None:
        self.finished = True
        head = "更新成功" if ok else "更新失败"
        self._write(f"[结果] {head}{('：' + message) if message else ''}")


class MultiReporter(Reporter):
    """把事件同时转给多个 reporter（例如控制台 + 状态文件/日志）。"""

    def __init__(self, *reporters: Reporter) -> None:
        self.reporters = [r for r in reporters if r is not None]

    def phase(self, phase: str) -> None:
        for reporter in self.reporters:
            reporter.phase(phase)

    def item(self, rel_path: str, action: str = "") -> None:
        for reporter in self.reporters:
            reporter.item(rel_path, action)

    def progress(self, done: int, total: int) -> None:
        for reporter in self.reporters:
            reporter.progress(done, total)

    def log(self, line: str) -> None:
        for reporter in self.reporters:
            reporter.log(line)

    def finish(self, ok: bool, message: str = "") -> None:
        self.finished = True
        for reporter in self.reporters:
            reporter.finish(ok, message)


class LoggingReporter(Reporter):
    """把三层信息写进日志（需求 §77：安装器状态与回滚原因都要有记录）。"""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger("mangaproof.updater.installer")

    def phase(self, phase: str) -> None:
        self.logger.info("阶段：%s（%s）", phase, label_for(phase))

    def item(self, rel_path: str, action: str = "") -> None:
        text = f"{action}：{rel_path}" if action else str(rel_path)
        self.logger.info("  %s", text)

    def progress(self, done: int, total: int) -> None:
        if total is None or total < 0:
            self.logger.debug("进度：%s（总数未知）", done)
        else:
            self.logger.debug("进度：%s/%s", done, total)

    def log(self, line: str) -> None:
        self.logger.info("  %s", line)


# --------------------------------------------------------------------------- #
# tkinter 可用性
# --------------------------------------------------------------------------- #

_TK_REASON: str | None = None


def tkinter_available() -> bool:
    """tkinter 是否可用（缺失时安装器必须优雅降级，不能崩）。"""
    return tkinter_unavailable_reason() is None


def tkinter_unavailable_reason() -> str | None:
    """不可用原因（None 表示可用）；结果会被缓存。"""
    global _TK_REASON
    if _TK_REASON is not None:
        return _TK_REASON or None
    try:
        import tkinter  # noqa: F401
    except Exception as exc:  # ImportError / ModuleNotFoundError / 任何导入期错误
        _TK_REASON = f"tkinter 不可用：{exc}"
        return _TK_REASON
    if os.name != "nt" and sys.platform != "darwin":
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            _TK_REASON = "没有图形会话（DISPLAY/WAYLAND_DISPLAY 均未设置）"
            return _TK_REASON
    _TK_REASON = ""
    return None


def _reset_tk_cache() -> None:  # 单测用
    global _TK_REASON
    _TK_REASON = None


# --------------------------------------------------------------------------- #
# 直接启动提示（需求 §83）
# --------------------------------------------------------------------------- #

#: 提示窗口标题（§83：直接双击 / 缺参启动的唯一提示出口）
LAUNCH_NOTICE_TITLE = "无法直接启动"

#: 正文（说清"谁能调用"）
LAUNCH_NOTICE_BODY = "此程序不能直接打开，只能由 MangaProof 主程序的「更新」功能自动调用。"

#: 操作提示（说清"要更新该怎么做"）
LAUNCH_NOTICE_HINT = "如需更新：打开 MangaProof →「更新」→「保存并检查更新」，按提示完成。"

#: 唯一的按钮（Esc / 回车 / 关窗同义）
LAUNCH_NOTICE_BUTTON = "退出"

#: 纯文本版（控制台降级与日志用；与窗口里的三段文案保持一致）
LAUNCH_NOTICE_TEXT = f"{LAUNCH_NOTICE_BODY}\n\n{LAUNCH_NOTICE_HINT}"

#: 提示窗口宽度（正文换行宽度按它算）与最小高度
NOTICE_WIDTH = 470
NOTICE_MIN_HEIGHT = 200


def launch_notice_text(detail: str = "") -> str:
    """提示正文 + 可选诊断行（缺参清单等，方便反馈主程序侧的传参问题）。"""
    text = LAUNCH_NOTICE_TEXT
    if detail:
        text = f"{text}\n\n{detail}"
    return text


def _write_stderr(text: str) -> None:
    """把提示写到 stderr（windowed onefile 下 stdout/stderr 可能是 ``None``）。"""
    stream = sys.stderr
    if stream is None:
        return
    try:
        print(text, file=stream)
        stream.flush()
    except Exception:  # pragma: no cover - 流已关闭等
        pass


def _show_dark_notice(detail: str = "") -> None:
    """§83 的深色提示窗：与安装器主窗口同一套调色板 / 字体 / 按钮样式。

    刻意做成"主窗口的缩小版"而不是系统原生弹窗：直接双击的人看到的应该是
    MangaProof 自己的界面语言（§11.2），而不是一个和本程序无关的系统框。
    窗口用一次性 ``Tk()``，确认后销毁；调用方保证有图形会话。
    """
    import tkinter as tk

    _enable_dpi_awareness()
    root = tk.Tk()
    try:
        root.title(LAUNCH_NOTICE_TITLE)
        root.configure(bg=COLOR_BG_MAIN)
        root.resizable(False, False)
        family = pick_font_family(root)      # 随包 MiSans（拿不到就用 Tk 默认字体）

        def font(size: int = 10, bold: bool = False):
            if not family:
                return None
            return (family, size, "bold") if bold else (family, size)

        pad = {"padx": 20}
        header = tk.Label(root, text=LAUNCH_NOTICE_TITLE, bg=COLOR_BG_MAIN, fg=COLOR_TEXT,
                          anchor="w", justify="left")
        if font(14, bold=True):
            header.configure(font=font(14, bold=True))
        header.pack(anchor="w", pady=(18, 10), **pad)

        body = tk.Label(root, text=LAUNCH_NOTICE_BODY, bg=COLOR_BG_MAIN, fg=COLOR_TEXT,
                        anchor="w", justify="left", wraplength=NOTICE_WIDTH - 40)
        if font(11):
            body.configure(font=font(11))
        body.pack(anchor="w", pady=(0, 8), **pad)

        hint = tk.Label(root, text=LAUNCH_NOTICE_HINT, bg=COLOR_BG_MAIN, fg=COLOR_TEXT_DIM,
                        anchor="w", justify="left", wraplength=NOTICE_WIDTH - 40)
        if font(10):
            hint.configure(font=font(10))
        hint.pack(anchor="w", pady=(0, 10), **pad)

        if detail:
            # 诊断行（缺了哪些参数）：万一是主程序传参 bug，这行能直接定位
            diagnose = tk.Label(root, text=detail, bg=COLOR_BG_MAIN, fg=COLOR_WARN,
                                anchor="w", justify="left", wraplength=NOTICE_WIDTH - 40)
            if font(9):
                diagnose.configure(font=font(9))
            diagnose.pack(anchor="w", pady=(0, 10), **pad)

        def close(*_args) -> None:
            try:
                root.destroy()
            except Exception:  # pragma: no cover - 已销毁
                pass

        bottom = tk.Frame(root, bg=COLOR_BG_MAIN)
        bottom.pack(fill="x", side="bottom", pady=(6, 16), **pad)
        button = tk.Button(
            bottom, text=LAUNCH_NOTICE_BUTTON, command=close,
            bg=COLOR_BG_WIDGET, fg=COLOR_TEXT, activebackground=COLOR_BG_SELECTED,
            activeforeground=COLOR_TEXT, relief="flat", padx=18, pady=4,
            highlightthickness=1, highlightbackground=COLOR_BORDER, cursor="hand2",
        )
        if font(10):
            button.configure(font=font(10))
        button.pack(side="right")
        button.bind("<Enter>", lambda _e: button.configure(bg=COLOR_BG_HOVER))
        button.bind("<Leave>", lambda _e: button.configure(bg=COLOR_BG_WIDGET))
        button.focus_set()

        root.protocol("WM_DELETE_WINDOW", close)
        root.bind("<Escape>", close)
        root.bind("<Return>", close)
        try:
            root.attributes("-topmost", True)
        except Exception:  # pragma: no cover - 少数 WM 不支持
            pass
        root.update_idletasks()
        width = max(NOTICE_WIDTH, root.winfo_reqwidth())
        height = max(NOTICE_MIN_HEIGHT, root.winfo_reqheight())
        left = max(0, (root.winfo_screenwidth() - width) // 2)
        top = max(0, (root.winfo_screenheight() - height) // 3)   # 略偏上，观感更稳
        root.geometry(f"{width}x{height}+{left}+{top}")
        root.mainloop()
    finally:
        try:
            root.destroy()
        except Exception:  # pragma: no cover - 窗口已销毁
            pass


def _show_native_message(title: str, text: str) -> None:
    """系统原生提示框：只在深色提示窗起不来时兜底（§83 的第三级降级）。

    单测用替换本函数的方式避免真的开窗口。
    """
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    try:
        root.withdraw()                      # 只要提示框，不要那个空白主窗口
        try:
            root.attributes("-topmost", True)
        except Exception:                    # pragma: no cover - 少数 WM 不支持
            pass
        messagebox.showwarning(title, text, parent=root)
    finally:
        try:
            root.destroy()
        except Exception:                    # pragma: no cover - 窗口已销毁
            pass


def show_launch_notice(detail: str = "", *, gui: bool = True) -> bool:
    """§83：安装器被直接打开时的提示；返回是否**真的弹了窗**。

    降级链（任何一步失败都往下走，全程不抛异常、不阻塞）：

    1. 深色提示窗（与安装器主窗口同一套视觉，§11.2）；
    2. 系统原生 ``messagebox``（深色窗建不起来时，例如 Tk 主题异常）；
    3. stderr + 日志（``--cli``、无图形会话、tkinter 缺失、上面两步都失败）。
    """
    text = launch_notice_text(detail)
    if not gui:
        log.info("--cli：直接启动提示降级为命令行输出")
        _write_stderr(f"{LAUNCH_NOTICE_TITLE}：{text}")
        return False
    reason = tkinter_unavailable_reason()
    if reason:
        log.info("不弹提示窗（%s），改为命令行提示", reason)
        _write_stderr(f"{LAUNCH_NOTICE_TITLE}：{text}")
        return False
    try:
        _show_dark_notice(detail)
    except Exception as exc:
        log.warning("深色提示窗显示失败（%s），改用系统提示框", exc)
    else:
        log.info("已提示用户：安装器不能直接启动（需求 §83）")
        return True
    try:
        _show_native_message(LAUNCH_NOTICE_TITLE, text)
    except Exception as exc:
        log.warning("系统提示框也显示失败（%s），改为命令行提示", exc)
        _write_stderr(f"{LAUNCH_NOTICE_TITLE}：{text}")
        return False
    log.info("已提示用户：安装器不能直接启动（系统提示框，需求 §83）")
    return True


def make_reporter(
    *,
    cli: bool = False,
    stream=None,
    with_logging: bool = True,
) -> Reporter:
    """需要 GUI 时返回 Tk reporter 的宿主，否则给控制台 reporter。

    注意：真正的 Tk reporter 由 :func:`run_with_ui` 创建（它需要窗口对象）；
    这里只用于"确定不用 GUI"的场景。
    """
    reporters: list[Reporter] = [ConsoleReporter(stream)]
    if with_logging:
        reporters.append(LoggingReporter())
    if cli:
        log.info("命令行模式（--cli）：不启动 GUI")
    elif not tkinter_available():
        log.info("降级为控制台进度：%s", tkinter_unavailable_reason())
    return MultiReporter(*reporters) if len(reporters) > 1 else reporters[0]


# --------------------------------------------------------------------------- #
# Tkinter GUI
# --------------------------------------------------------------------------- #


def _enable_dpi_awareness() -> None:
    """Windows：先 ``SetProcessDpiAwareness`` 再建 ``Tk()``（调研报告 §11.2）。"""
    if os.name != "nt":  # pragma: no cover - 平台分支
        return
    try:  # pragma: no cover
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # pragma: no cover
        pass


def pick_font_family(root) -> str | None:
    """按 §11.2 挑字体族：**内置 MiSans 优先** → 系统候选链 → ``None``（Tk 默认字体）。

    内置那一份由 :mod:`updater.fonts` 注册进本进程（spec 随包 ``font/``）；注册不上
    或没出现在 Tk 家族表里就照旧走系统链 —— 字体问题绝不能让安装器起不来。
    一个都没有时返回 ``None``（用 Tk 默认字体，不得崩溃）。
    """
    try:
        bundled = fonts.pick_family(root)
    except Exception as exc:  # pragma: no cover - fonts 内部已逐条兜底
        log.debug("内置字体不可用，改用系统字体：%s", exc)
        bundled = None
    if bundled:
        log.info("使用内置字体：%s", bundled)
        return bundled

    try:
        from tkinter import font as tkfont

        families = {str(name) for name in tkfont.families(root)}
    except Exception as exc:
        log.debug("读取系统字体失败，使用 Tk 默认字体：%s", exc)
        return None
    for name in FONT_CANDIDATES:
        if name in families:
            return name
    lowered = {name.lower(): name for name in families}
    for name in FONT_CANDIDATES:  # 模糊匹配（例如 "... Light"/"... UI"）
        for low, original in lowered.items():
            if name.lower() in low:
                return original
    return None


class TkReporter(Reporter):
    """把内核事件放进队列，由 Tk 主线程的 ``after`` 轮询消费（线程安全）。"""

    def __init__(self, events: "queue.Queue[tuple]", window: "InstallerWindow") -> None:
        self.events = events
        self.window = window

    def _push(self, kind: str, payload) -> None:
        try:
            self.events.put_nowait((kind, payload))
        except Exception:  # pragma: no cover - 队列满这种极端情况
            pass

    def phase(self, phase: str) -> None:
        self._push("phase", phase)

    def item(self, rel_path: str, action: str = "") -> None:
        self._push("item", (rel_path, action))

    def progress(self, done: int, total: int) -> None:
        self._push("progress", (done, total))

    def log(self, line: str) -> None:
        self._push("log", line)

    def finish(self, ok: bool, message: str = "") -> None:
        self.finished = True
        self._push("finish", (ok, message))


class InstallerWindow:
    """安装器主窗口。**构造失败（无显示）由调用方降级**，不抛给用户。"""

    def __init__(self, *, title: str, subtitle: str = "", version: str = "") -> None:
        _enable_dpi_awareness()
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.root = tk.Tk()
        self.root.title(title)
        self.root.configure(bg=COLOR_BG_MAIN)
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.root.minsize(520, 420)
        self.family = pick_font_family(self.root)
        if self.family:
            try:
                self.root.option_add("*Font", (self.family, 10))
            except Exception:  # pragma: no cover
                self.family = None
        self.events: "queue.Queue[tuple]" = queue.Queue()
        self.exit_code = 1
        self._done = False
        self._bar_mode = "determinate"
        self._build(subtitle=subtitle, version=version)

    # -- 构建 ------------------------------------------------------------- #
    def _font(self, size: int = 10, bold: bool = False):
        if not self.family:
            return None
        return (self.family, size, "bold") if bold else (self.family, size)

    def _label(self, parent, text: str, *, fg: str, size: int = 10, bold: bool = False, **kw):
        options = {"bg": COLOR_BG_MAIN, "fg": fg, "text": text, "anchor": "w", "justify": "left"}
        font = self._font(size, bold)
        if font:
            options["font"] = font
        options.update(kw)
        return self.tk.Label(parent, **options)

    def _build(self, *, subtitle: str, version: str) -> None:
        tk = self.tk
        pad = {"padx": 16}
        header = self._label(
            self.root,
            "MangaProof 更新安装器" + (f"　{version}" if version else ""),
            fg=COLOR_TEXT,
            size=14,
            bold=True,
        )
        header.pack(anchor="w", pady=(14, 2), **pad)
        if subtitle:
            self._label(self.root, subtitle, fg=COLOR_TEXT_DIM, size=9).pack(
                anchor="w", pady=(0, 8), **pad
            )

        # 阶段（第一层信息）
        self.phase_label = self._label(
            self.root, label_for("INIT"), fg=COLOR_ACCENT, size=11, bold=True
        )
        self.phase_label.pack(anchor="w", pady=(6, 6), **pad)

        # 进度区（第三层信息）
        panel = tk.Frame(self.root, bg=COLOR_BG_PANEL, highlightthickness=1,
                         highlightbackground=COLOR_BORDER)
        panel.pack(fill="x", pady=(0, 10), **pad)
        style = self.ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:  # pragma: no cover - 主题名依平台而定
            pass
        try:
            style.configure(
                "MP.Horizontal.TProgressbar",
                troughcolor=COLOR_BG_WIDGET,
                background=COLOR_ACCENT,
                bordercolor=COLOR_BORDER,
                lightcolor=COLOR_ACCENT,
                darkcolor=COLOR_ACCENT,
                thickness=14,
            )
        except Exception:  # pragma: no cover
            pass
        self.bar = self.ttk.Progressbar(panel, style="MP.Horizontal.TProgressbar",
                                        orient="horizontal", mode="determinate",
                                        maximum=1, value=0, length=460)
        self.bar.pack(side="left", fill="x", expand=True, padx=(10, 8), pady=10)
        self.count_label = self._label(panel, "—", fg=COLOR_TEXT_DIM, size=10)
        self.count_label.configure(bg=COLOR_BG_PANEL, width=10, anchor="e")
        self.count_label.pack(side="right", padx=(0, 10))

        # 当前文件操作（第二层信息）
        self.item_label = self._label(
            self.root, "准备中…", fg=COLOR_TEXT, size=10, wraplength=WINDOW_WIDTH - 40
        )
        self.item_label.pack(anchor="w", pady=(0, 8), **pad)

        # 明细区（失败时保留最后一条操作，便于用户截图反馈）
        # wrap="char"：Windows 的错误信息又长又没有空格断点（例如带完整路径的
        # WinError 32 文案），wrap="none" 会把右侧整段截掉——用户截图里就看不到
        # 关键信息了。按字符换行对中文/长路径都成立（"word" 模式在 CJK 与超长
        # 无空格串上会退化成不换行，仍然截断）。
        self.detail_frame = tk.Frame(self.root, bg=COLOR_BG_MAIN)
        self.detail_frame.pack(fill="both", expand=True, **pad)
        self.detail = tk.Text(
            self.detail_frame, height=DETAIL_MIN_LINES, bg=COLOR_BG_PANEL,
            fg=COLOR_TEXT_DIM, insertbackground=COLOR_TEXT, relief="flat",
            highlightthickness=1, highlightbackground=COLOR_BORDER, wrap="char",
        )
        detail_scroll = tk.Scrollbar(
            self.detail_frame, orient="vertical", command=self.detail.yview
        )
        self.detail.configure(yscrollcommand=detail_scroll.set)
        detail_scroll.pack(side="right", fill="y")
        font = self._font(9)
        if font:
            self.detail.configure(font=font)
        self.detail.configure(state="disabled")
        self.detail.pack(side="left", fill="both", expand=True)

        bottom = tk.Frame(self.root, bg=COLOR_BG_MAIN)
        bottom.pack(fill="x", pady=12, **pad)
        self.result_label = self._label(bottom, "", fg=COLOR_TEXT, size=10, bold=True)
        self.result_label.pack(side="left")
        self.close_button = tk.Button(
            bottom, text="关闭", command=self._on_close, state="disabled",
            bg=COLOR_BG_WIDGET, fg=COLOR_TEXT, activebackground=COLOR_BG_SELECTED,
            activeforeground=COLOR_TEXT, relief="flat", padx=18, pady=4,
            highlightthickness=1, highlightbackground=COLOR_BORDER, cursor="hand2",
        )
        font = self._font(10)
        if font:
            self.close_button.configure(font=font)
        self.close_button.pack(side="right")
        self.close_button.bind("<Enter>", lambda _e: self.close_button.configure(bg=COLOR_BG_HOVER))
        self.close_button.bind("<Leave>", lambda _e: self.close_button.configure(bg=COLOR_BG_WIDGET))
        self.root.protocol("WM_DELETE_WINDOW", self._on_close_request)
        self.root.lift()

    # -- 事件消费 --------------------------------------------------------- #
    def _append_detail(self, line: str) -> None:
        try:
            self.detail.configure(state="normal")
            self.detail.insert("end", line + "\n")
            if int(self.detail.index("end-1c").split(".")[0]) > LOG_LINES:
                self.detail.delete("1.0", "2.0")
            self.detail.see("end")
            self.detail.configure(state="disabled")
        except Exception:  # pragma: no cover - 窗口已销毁等
            pass

    def _set_bar(self, mode: str) -> None:
        if mode == self._bar_mode:
            return
        self._bar_mode = mode
        try:
            if mode == "indeterminate":
                self.bar.configure(mode="indeterminate")
                self.bar.start(12)
            else:
                self.bar.stop()
                self.bar.configure(mode="determinate")
        except Exception:  # pragma: no cover
            pass

    def _handle(self, kind: str, payload) -> None:
        if kind == "phase":
            self.phase_label.configure(text=label_for(str(payload)))
        elif kind == "item":
            rel_path, action = payload
            text = f"{action}：{rel_path}" if action else str(rel_path)
            self.item_label.configure(text=text)
            self._append_detail(text)
        elif kind == "progress":
            done, total = payload
            if total is None or total < 0:
                self._set_bar("indeterminate")
                self.count_label.configure(text="—")
            else:
                self._set_bar("determinate")
                self.count_label.configure(text=f"{done}/{total}")
                try:
                    self.bar.configure(maximum=max(1, int(total)), value=int(done))
                except Exception:  # pragma: no cover
                    pass
        elif kind == "log":
            self._append_detail(str(payload))
        elif kind == "finish":
            ok, message = payload
            self._done = True
            self._set_bar("determinate")
            if ok:
                self.phase_label.configure(text=label_for("SUCCESS"), fg=COLOR_PASS)
                self.result_label.configure(text="更新完成", fg=COLOR_PASS)
                self.item_label.configure(text="新版本已启动，本次更新成功。")
                self._auto_close()
            else:
                self.phase_label.configure(text=label_for("FAILED"), fg=COLOR_FAIL)
                self.result_label.configure(
                    text=f"更新失败：{message}" if message else "更新失败", fg=COLOR_FAIL
                )
                self.item_label.configure(
                    text="已回滚到旧版本；详细信息见上方与安装器日志。"
                )
                self._append_detail("（失败：窗口保持打开，便于截图反馈）")
            self.close_button.configure(state="normal")

    def _auto_close(self) -> None:
        if os.environ.get("MP_INSTALLER_HOLD"):
            return
        try:
            self.root.after(AUTO_CLOSE_MS, self.root.destroy)
        except Exception:  # pragma: no cover
            pass

    def _pump(self) -> None:
        drained = 0
        while drained < 200:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            try:
                self._handle(kind, payload)
            except Exception as exc:  # UI 出问题也不能让流程"卡在窗口上"
                log.warning("UI 事件处理失败（%s：%s）", kind, exc)
            drained += 1
        if not self._done:
            try:
                self.root.after(POLL_INTERVAL_MS, self._pump)
            except Exception:  # pragma: no cover
                pass

    # -- 生命周期 --------------------------------------------------------- #
    def _on_close(self) -> None:
        if not self._done:
            self._append_detail("安装正在进行，暂不能关闭窗口…")
            return
        try:
            self.root.destroy()
        except Exception:  # pragma: no cover
            pass

    def _on_close_request(self) -> None:
        self._on_close()

    def run(self, work: Callable[[Reporter], int]) -> int:
        """在后台线程跑 ``work``，主线程泵 Tk 事件；返回退出码。"""
        reporter = MultiReporter(TkReporter(self.events, self), LoggingReporter())
        worker = threading.Thread(
            target=self._worker, args=(work, reporter), name="installer", daemon=True
        )
        worker.start()
        try:
            self.root.after(POLL_INTERVAL_MS, self._pump)
            self.root.mainloop()
        except Exception as exc:  # pragma: no cover - 图形环境异常
            log.warning("GUI 主循环异常：%s", exc)
        worker.join(timeout=2.0)
        return self.exit_code

    def _worker(self, work: Callable[[Reporter], int], reporter: Reporter) -> None:
        try:
            code = int(work(reporter))
            self.exit_code = code
            if not reporter.finished:
                reporter.finish(code == 0, "" if code == 0 else f"退出码 {code}")
        except BaseException as exc:  # 后台线程的异常不会走 sys.excepthook
            log.error("安装器任务异常：%s\n%s", exc, traceback.format_exc())
            self.exit_code = 90
            try:
                reporter.finish(False, str(exc))
            except Exception:  # pragma: no cover
                pass


def run_with_ui(
    work: Callable[[Reporter], int],
    *,
    title: str = "MangaProof 更新安装器",
    subtitle: str = "",
    version: str = "",
    cli: bool = False,
) -> int:
    """有 GUI 就用 GUI，没有就控制台（**任何一步失败都降级，不抛异常**）。"""
    reason = "命令行模式（--cli）" if cli else tkinter_unavailable_reason()
    if not reason:
        try:
            window = InstallerWindow(title=title, subtitle=subtitle, version=version)
        except Exception as exc:
            reason = f"GUI 初始化失败：{exc}"
            log.warning("降级为控制台进度：%s（%s）", reason, traceback.format_exc(limit=3))
        else:
            return window.run(work)
    log.info("使用控制台进度：%s", reason)
    reporter = MultiReporter(ConsoleReporter(), LoggingReporter())
    try:
        code = int(work(reporter))
    except BaseException as exc:  # 兜底：控制台模式也要给出可读原因
        log.error("安装器任务异常：%s\n%s", exc, traceback.format_exc())
        reporter.finish(False, str(exc))
        return 90
    if not reporter.finished:
        reporter.finish(code == 0, "" if code == 0 else f"退出码 {code}")
    return code


__all__ = [
    "AUTO_CLOSE_MS",
    "COLOR_ACCENT",
    "COLOR_BG_HOVER",
    "COLOR_BG_MAIN",
    "COLOR_BG_PANEL",
    "COLOR_BG_SELECTED",
    "COLOR_BG_WIDGET",
    "COLOR_BORDER",
    "COLOR_FAIL",
    "COLOR_PASS",
    "COLOR_TEXT",
    "COLOR_TEXT_DIM",
    "COLOR_WARN",
    "ConsoleReporter",
    "FONT_CANDIDATES",
    "InstallerWindow",
    "LAUNCH_NOTICE_BODY",
    "LAUNCH_NOTICE_BUTTON",
    "LAUNCH_NOTICE_HINT",
    "LAUNCH_NOTICE_TEXT",
    "LAUNCH_NOTICE_TITLE",
    "LoggingReporter",
    "MultiReporter",
    "NOTICE_MIN_HEIGHT",
    "NOTICE_WIDTH",
    "NullReporter",
    "Reporter",
    "TkReporter",
    "launch_notice_text",
    "make_reporter",
    "pick_font_family",
    "run_with_ui",
    "show_launch_notice",
    "tkinter_available",
    "tkinter_unavailable_reason",
]

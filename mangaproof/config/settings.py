"""程序级设置：settings.json（需求 §20.2、§30、§35、§55）。

软件级设置全部落在 程序目录/settings.json，与任务数据（.mangaproof.json）
彻底分离（需求 §58）。
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mangaproof.config import paths

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
    "generate_report": "Ctrl+R",
    "redraw_mode": "R",
}

# 预制问题类型（需求 §34）及其默认快捷键（需求 §35，均可配置）
DEFAULT_ISSUE_TYPES: list[dict[str, str]] = [
    {"name": "居中错误", "key": "1"},
    {"name": "字体选择错误", "key": "2"},
    {"name": "字体字重错误", "key": "3"},
    {"name": "字号错误", "key": "4"},
    {"name": "文字位置错误", "key": "5"},
    {"name": "文字间距错误", "key": "6"},
    {"name": "气泡处理错误", "key": "7"},
    {"name": "原文字擦除错误", "key": "8"},
    {"name": "背景擦除错误", "key": "9"},
    {"name": "网点对齐错误", "key": "0"},
    {"name": "网点残留", "key": "Q"},
    {"name": "修图瑕疵", "key": "W"},
    {"name": "漏翻", "key": "E"},
    {"name": "漏字", "key": "R"},
    {"name": "错字", "key": "T"},
    {"name": "翻译错误", "key": "Y"},
    {"name": "排版错误", "key": "U"},
    {"name": "文字溢出", "key": "I"},
    {"name": "其他", "key": "O"},
]

DISPLAY_RATIOS: list[float] = [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
DEFAULT_DISPLAY_RATIO = 0.6

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


@dataclass
class Settings:
    """运行时设置对象。"""

    layer_display_ratio: float = DEFAULT_DISPLAY_RATIO
    compare_mode: str = DEFAULT_COMPARE_MODE   # "auto" / "manual"
    compare_speed_hz: int = DEFAULT_COMPARE_SPEED_HZ
    recursive_scan: bool = False
    generate_pdf_on_complete: bool = True
    report_name: str = ""
    hide_console: bool = True   # 打包产物隐藏控制台（直接运行 py 时始终显示）
    keybindings: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_KEYBINDINGS))
    issue_types: list[dict[str, str]] = field(
        default_factory=lambda: [dict(t) for t in DEFAULT_ISSUE_TYPES]
    )
    custom_comment_key: str = "Ctrl+Return"
    recent_paths: list[str] = field(default_factory=list)

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


class SettingsManager:
    """settings.json 的读写封装。"""

    def __init__(self, path: Path | None = None):
        self._path = path if path is not None else paths.settings_path()
        self._lock = threading.Lock()
        self.settings = self._load()

    # -- 读写 --------------------------------------------------------------

    def _load(self) -> Settings:
        try:
            if not self._path.exists():
                return Settings()
            with open(self._path, "r", encoding="utf-8") as f:
                raw: dict[str, Any] = json.load(f)
            return self._from_dict(raw)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            log.warning("读取 settings.json 失败，使用默认设置：%s", exc)
            return Settings()

    def _from_dict(self, raw: dict[str, Any]) -> Settings:
        s = Settings()

        ratio = raw.get("layer_display_ratio", DEFAULT_DISPLAY_RATIO)
        try:
            ratio = float(ratio)
        except (TypeError, ValueError):
            ratio = DEFAULT_DISPLAY_RATIO
        if not (0.01 <= ratio <= 4.0):
            ratio = DEFAULT_DISPLAY_RATIO
        s.layer_display_ratio = ratio

        mode = raw.get("compare_mode", DEFAULT_COMPARE_MODE)
        s.compare_mode = mode if mode in ("auto", "manual") else DEFAULT_COMPARE_MODE

        try:
            hz = int(raw.get("compare_speed_hz", DEFAULT_COMPARE_SPEED_HZ))
        except (TypeError, ValueError):
            hz = DEFAULT_COMPARE_SPEED_HZ
        if not (1 <= hz <= 10):
            hz = DEFAULT_COMPARE_SPEED_HZ
        s.compare_speed_hz = hz

        s.recursive_scan = bool(raw.get("recursive_scan", False))
        s.generate_pdf_on_complete = bool(
            raw.get("generate_pdf_on_complete", True)
        )
        s.report_name = str(raw.get("report_name", "") or "")
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

        recent = raw.get("recent_paths", [])
        if isinstance(recent, list):
            s.recent_paths = [str(p) for p in recent if isinstance(p, str)][:10]

        return s

    def save(self) -> None:
        with self._lock:
            try:
                payload = {
                    "settings_version": SETTINGS_VERSION,
                    "layer_display_ratio": self.settings.layer_display_ratio,
                    "compare_mode": self.settings.compare_mode,
                    "compare_speed_hz": self.settings.compare_speed_hz,
                    "recursive_scan": self.settings.recursive_scan,
                    "generate_pdf_on_complete": self.settings.generate_pdf_on_complete,
                    "report_name": self.settings.report_name,
                    "hide_console": self.settings.hide_console,
                    "custom_comment_key": self.settings.custom_comment_key,
                    "keybindings": self.settings.keybindings,
                    "issue_types": self.settings.issue_types,
                    "recent_paths": self.settings.recent_paths,
                }
                tmp = self._path.with_suffix(".json.tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                tmp.replace(self._path)
            except OSError as exc:
                log.warning("写入 settings.json 失败：%s", exc)

    def add_recent(self, path_str: str) -> None:
        recent = self.settings.recent_paths
        if path_str in recent:
            recent.remove(path_str)
        recent.insert(0, path_str)
        del recent[10:]
        self.settings.recent_paths = recent
        self.save()

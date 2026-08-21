"""统一程序路径服务（需求 §56、§57）。

所有程序级资源（settings.json、logs/ 等）统一通过 get_app_dir() 获得。
其他模块不得自行判断程序目录。
"""

from __future__ import annotations

import sys
from pathlib import Path


def get_app_dir() -> Path:
    """返回程序所在目录。

    兼容两种运行方式（需求 §56）：

    - 直接运行 Python（python main.py）→ 入口 Python 文件所在目录；
    - PyInstaller onedir → .exe 所在目录。

    绝不使用 os.getcwd()（当前工作目录由用户启动方式决定，不可信）。
    """
    if getattr(sys, "frozen", False):  # PyInstaller / onedir
        return Path(sys.executable).resolve().parent

    main_mod = sys.modules.get("__main__")
    main_file = getattr(main_mod, "__file__", None)
    if main_file:
        main_path = Path(main_file).resolve()
        # python -m mangaproof.main 时 __main__.__file__ 位于包内，
        # 程序目录应为包的上上级（项目根）。
        if main_path.name == "main.py" and main_path.parent.name == "mangaproof":
            return main_path.parent.parent
        return main_path.parent

    # 极端兜底：解释器所在目录
    return Path(sys.executable).resolve().parent


def settings_path() -> Path:
    """settings.json 的完整路径（程序目录下）。"""
    return get_app_dir() / "settings.json"


def logs_dir() -> Path:
    return get_app_dir() / "logs"

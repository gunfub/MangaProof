"""构建 mangaproof._psd_fast 原生扩展（纯 C，ctypes 加载，无需 Python.h）。

用法：
    uv run python scripts/build_accel.py

产物：mangaproof/_psd_fast.so（Linux/macOS）或 _psd_fast.pyd（Windows）。
编译器缺失/失败时打印警告并成功退出（应用运行时会自动回退
psd-tools 原实现，仅失去加速）。
"""

from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "mangaproof" / "_psd_fast.c"
OUT = ROOT / "mangaproof" / ("_psd_fast.pyd" if platform.system() == "Windows" else "_psd_fast.so")


def _build_command() -> list[str]:
    if platform.system() == "Windows":
        return ["cl", "/nologo", "/O2", "/LD", str(SRC), f"/Fe:{OUT}"]
    return ["cc", "-O3", "-shared", "-fPIC", "-o", str(OUT), str(SRC)]


def main() -> int:
    if not SRC.exists():
        print(f"缺少源文件：{SRC}", file=sys.stderr)
        return 1
    cmd = _build_command()
    print("编译:", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True, cwd=str(ROOT))
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"警告：加速扩展编译失败（{exc}），运行时将回退 psd-tools 原实现")
        return 0  # 不阻断打包/启动，仅失去加速
    if OUT.exists():
        print(f"已生成 {OUT}（{OUT.stat().st_size} 字节）")
        return 0
    print("警告：编译产物未生成，运行时将回退 psd-tools 原实现")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""构建 mangaproof._psd_fast 原生扩展（纯 C，ctypes 加载，无需 Python.h）。

用法：
    uv run python scripts/build_accel.py

产物：mangaproof/_psd_fast.so（Linux/macOS）或 _psd_fast.pyd（Windows）。
Windows 优先使用 PATH 上的 cl（如已 source vcvars64），否则自动经
vswhere 定位 Visual Studio 并调用 vcvars64.bat。

编译器缺失/失败时打印诊断并成功退出（应用运行时会自动回退
psd-tools 原实现，仅失去加速）。
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

# Windows 控制台默认 cp1252，打印中文会 UnicodeEncodeError → 强制 UTF-8
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "mangaproof" / "_psd_fast.c"
OUT = ROOT / "mangaproof" / ("_psd_fast.pyd" if platform.system() == "Windows" else "_psd_fast.so")


def _windows_vcvars() -> str | None:
    """定位 Visual Studio 的 vcvars64.bat（cl 需要其环境变量）。"""
    import glob

    candidates: list[Path] = []
    vswhere_paths = [
        Path("C:/Program Files (x86)/Microsoft Visual Studio/Installer/vswhere.exe"),
        Path("C:/Program Files/Microsoft Visual Studio/Installer/vswhere.exe"),
    ]
    for vswhere in vswhere_paths:
        if not vswhere.exists():
            continue
        try:
            out = subprocess.run(
                [str(vswhere), "-latest", "-products", "*", "-property", "installationPath"],
                capture_output=True,
                text=True,
                check=True,
            )
            root = out.stdout.strip()
            if root:
                candidates.append(Path(root) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat")
        except (OSError, subprocess.CalledProcessError):
            pass

    for pattern in (
        "C:/Program Files/Microsoft Visual Studio/*/*/VC/Auxiliary/Build/vcvars64.bat",
        "C:/Program Files (x86)/Microsoft Visual Studio/*/*/VC/Auxiliary/Build/vcvars64.bat",
        "C:/Program Files (x86)/Microsoft Visual Studio/*/BuildTools/VC/Auxiliary/Build/vcvars64.bat",
    ):
        candidates += [Path(p) for p in glob.glob(pattern)]

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def _build_command() -> list[str]:
    if platform.system() == "Windows":
        if shutil.which("cl"):
            return ["cl", "/nologo", "/O2", "/LD", str(SRC), f"/Fe:{OUT}"]
        vcvars = _windows_vcvars()
        if vcvars is None:
            raise FileNotFoundError("未找到 Visual Studio C++ 工具链（vswhere/vcvars64.bat）")
        return [
            "cmd", "/c",
            f'call "{vcvars}" >nul 2>&1 && cl /nologo /O2 /LD "{SRC}" /Fe:"{OUT}"',
        ]
    return ["cc", "-O3", "-shared", "-fPIC", "-o", str(OUT), str(SRC)]


def main() -> int:
    if not SRC.exists():
        print(f"缺少源文件：{SRC}", file=sys.stderr)
        return 1
    try:
        cmd = _build_command()
    except FileNotFoundError as exc:
        print(f"警告：{exc}；运行时将回退 psd-tools 原实现（仅失去加速）")
        return 0
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

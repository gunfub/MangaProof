# SPDX-FileCopyrightText: 2026 gunfub
# SPDX-License-Identifier: GPL-3.0-only

"""CI 工作流里 PyInstaller 调用点的守卫（只用标准库，不引入 PyYAML）。

起因是一个真实事故：给三个桌面 job 批量插入"构建安装器"步骤时，替换模板把
`run:` 行**硬编码**成了 `"${{ matrix.spec }}"`，而 Windows job 没有 matrix ——
展开成空串，CI 报 `Script file '' does not exist`，本地 pytest 完全发现不了
（YAML 依然合法）。所以这里对 workflow 文本做结构性断言。

运行：uv run python -m pytest tests/test_ci_workflows.py -v
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

BUILD = ROOT / ".github" / "workflows" / "build.yml"
TEXT = BUILD.read_text(encoding="utf-8")
LINES = TEXT.splitlines()

_JOB_RE = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")


def _job_blocks() -> dict[str, list[str]]:
    """把 build.yml 按 job 切成块（顶层两空格缩进的键）。"""
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    for line in LINES:
        m = _JOB_RE.match(line)
        if m and not line.startswith("    "):
            current = m.group(1)
            blocks[current] = []
            continue
        if current is not None:
            blocks[current].append(line)
    # 去掉非 job 的顶层块（on / permissions / env 等）
    return {k: v for k, v in blocks.items() if any(l.strip().startswith("- ") for l in v)}


def _pyinstaller_calls() -> list[str]:
    """所有 `pyinstaller … --clean <spec>` 调用行（含多行 run 块里的那一行）。"""
    return [l.strip() for l in LINES if "pyinstaller" in l and "--clean" in l]


@pytest.mark.parametrize("call", _pyinstaller_calls())
def test_pyinstaller_call_has_a_spec(call: str):
    rest = call.split("--clean", 1)[1].strip().rstrip("\\").strip()
    assert rest not in ('""', "''", ""), f"pyinstaller 的 spec 参数为空：{call!r}"
    assert rest.endswith(".spec") or rest == '"${{ matrix.spec }}"', (
        f"pyinstaller 的 spec 参数看起来不对：{call!r}"
    )


def test_pyinstaller_calls_are_found():
    """每个桌面 job 有两次调用：先建安装器，再建主程序（共 3 + 3）。"""
    calls = _pyinstaller_calls()
    installer = [c for c in calls if "packaging/installer.spec" in c]
    main = [c for c in calls if "packaging/installer.spec" not in c]
    assert len(installer) == 3, f"安装器构建应出现在 3 个桌面 job 里：{installer}"
    assert len(main) == 3, f"主程序构建应出现在 3 个桌面 job 里：{main}"


def test_matrix_spec_jobs_define_spec():
    """用到 matrix.spec 的 job，其矩阵 include 里必须有 spec 字段。"""
    for job, lines in _job_blocks().items():
        body = "\n".join(lines)
        if "matrix.spec" not in body:
            continue
        assert re.search(r"^\s+spec:\s+\S", body, re.M), (
            f"{job} 用了 matrix.spec，但矩阵里没有 spec 字段（会展开成空串）"
        )


def test_windows_job_uses_a_fixed_spec():
    """Windows job 没有矩阵，必须用固定 spec 路径（就是这次踩的坑）。"""
    windows = _job_blocks().get("build-windows")
    assert windows, "找不到 build-windows job"
    body = "\n".join(windows)
    assert "matrix.spec" not in body, "build-windows 没有矩阵，不得引用 matrix.spec"
    assert "packaging/main_win.spec" in body


def test_referenced_specs_exist():
    for spec in set(re.findall(r"packaging/[\w./-]+\.spec", TEXT)):
        assert (ROOT / spec).is_file(), f"workflow 引用的 spec 不存在：{spec}"
    for spec in set(re.findall(r"spec:\s+(\S+\.spec)", TEXT)):
        assert (ROOT / spec).is_file(), f"矩阵里的 spec 不存在：{spec}"


def test_installer_built_before_main_app_and_tkinter_checked():
    """每个桌面 job：先查 tkinter → 再建安装器 → 最后建主程序。"""
    desktop = [j for j in _job_blocks() if j.startswith("build-")]
    assert len(desktop) == 3, desktop
    for job in desktop:
        names = [
            m.group(1)
            for m in re.finditer(r"^      - name: (.+)$", "\n".join(_job_blocks()[job]), re.M)
        ]
        for required in (
            "Ensure tkinter for the installer GUI",
            "Build update installer",
            "Build with PyInstaller",
        ):
            assert required in names, f"{job} 缺少步骤：{required}"
        assert names.index("Build update installer") < names.index("Build with PyInstaller")


def test_installer_env_var_points_at_built_binary():
    """主程序 spec 通过 MANGAPROOF_INSTALLER 拿到安装器；Windows 必须指向 .exe。"""
    for job, lines in _job_blocks().items():
        body = "\n".join(lines)
        if "MANGAPROOF_INSTALLER" not in body:
            continue
        expected = ".exe" if job == "build-windows" else "MangaProof-update-installer"
        assert expected in body, f"{job} 的 MANGAPROOF_INSTALLER 指向疑似不对"


# --- R2 清理步骤：空前缀 vs 列举失败 必须分开处理 -------------------------

R2 = ROOT / ".github" / "workflows" / "r2_release.yml"


def test_r2_purge_distinguishes_empty_prefix_from_errors():
    """`aws s3 ls` 对空前缀是"无输出 + 退出码 1 + stderr 为空"，
    对凭据/权限错误则 stderr 有内容。二者混淆会掩盖真实故障
    （曾把"Access Key 长度 31"伪装成"前缀下没有对象"，随后 rm 又炸一次）。"""
    text = R2.read_text(encoding="utf-8")
    assert "无需删除" in text, "空前缀必须走「无需删除」分支"
    assert "前缀为空" in text, "必须显式区分「列举失败」与「前缀为空」"
    # 不能再用 `|| true` 无差别吞掉列举结果
    assert 's3 ls "s3://${R2_BUCKET}/${CHANNEL}/" --recursive --endpoint-url "$R2_ENDPOINT" || true' not in text


def test_r2_purge_only_deletes_the_target_channel():
    text = R2.read_text(encoding="utf-8")
    assert "alpha/ 与 beta/ 一律不动" in text
    for other in ("alpha", "beta", "stable"):
        assert f"s3://${{R2_BUCKET}}/{other}" not in text, (
            f"出现了硬编码的 {other} 前缀删除目标 —— 必须只用 ${{CHANNEL}}"
        )


def test_r2_never_hardcodes_credentials():
    text = R2.read_text(encoding="utf-8")
    assert "secrets.R2_ACCESS_KEY_ID" in text and "secrets.R2_SECRET_ACCESS_KEY" in text
    assert "secrets.R2_ACCOUNT_ID" in text
    # 不允许把密钥字面量写进 workflow
    assert not __import__("re").search(r"AKIA[0-9A-Z]{16}", text)


def test_r2_default_bucket_is_the_real_one():
    """曾经默认回落到调研阶段自己拟的占位名 'mangaproof'（不是真实存储桶）。
    真实桶名 = download-mangaproof（2026-09-22 需求方确认）。"""
    text = R2.read_text(encoding="utf-8")
    assert "'download-mangaproof'" in text, "R2_BUCKET 默认值必须是真实桶名"
    assert "|| 'mangaproof'" not in text, "不得回落到自己拟的占位桶名"


# --- 发布闸门：release 事件 vs 自动 dispatch -------------------------------

WF = ROOT / ".github" / "workflows"
THREE = ("mirrorchyan_release.yml", "mirrorchyan_release_note.yml", "r2_release.yml")


@pytest.mark.parametrize("name", THREE)
def test_release_workflows_listen_to_edited_and_released(name: str):
    """人工取消 Pre-release 在 GitHub 上对应 release 的 `released` 动作，不是 `edited`：
    只监听 edited 会让「人工放行」永远不触发（实测：连一条 skipped 运行都没有）。"""
    text = (WF / name).read_text(encoding="utf-8")
    assert "release:" in text
    assert "types: [edited, released]" in text, f"{name} 必须同时监听 edited 与 released"


def test_release_yml_no_longer_dispatches_mirror_workflows():
    """release.yml 不得再自动 dispatch 两个 mirror 工作流：
    它在建完 Pre-release 后立刻执行，会绕过 Pre-release 闸门；
    而且 GITHUB_TOKEN 触发的事件本就不会拉起其它工作流。"""
    text = (WF / "release.yml").read_text(encoding="utf-8")
    code_lines = [l for l in text.splitlines() if not l.lstrip().startswith("#")]
    assert not any("gh workflow run" in l for l in code_lines), (
        "release.yml 里不应再出现 gh workflow run（注释除外）"
    )
    # 该权限只为 dispatch 而加，现在应降级（只看代码行，注释里会提到它）
    assert not any(l.strip() == "actions: write" for l in code_lines), (
        "不再 dispatch 其它工作流后应把 actions 降到 read"
    )
    assert any(l.strip() == "actions: read" for l in code_lines)


@pytest.mark.parametrize("name", ["mirrorchyan_release.yml"])
def test_mirrorchyan_upload_guards_keep_prerelease_out(name: str):
    """事件触发时必须要求 !prerelease（预发行保持原行为），手动触发才放行。"""
    text = (WF / name).read_text(encoding="utf-8")
    pattern = ("github.repository_owner == 'gunfub' && "
               "(github.event_name == 'workflow_dispatch' || !github.event.release.prerelease)")
    assert text.count(pattern) == 2, f"{name} 的两个 job 都要有同一套守卫"


@pytest.mark.parametrize("name", THREE)
def test_event_triggered_tag_comes_from_the_payload(name: str):
    """事件触发时 tag 必须取自 release 载荷；取 inputs.tag 会得到空串，
    动作会退化成「最新 Release」，可能同步错版本。"""
    text = (WF / name).read_text(encoding="utf-8")
    assert "github.event.release.tag_name" in text, f"{name} 缺少 release 载荷取 tag"
    assert "inputs.tag" in text, f"{name} 还应保留手动触发的 tag 输入"


def test_bash_run_blocks_are_syntactically_valid():
    """把 run: | 的 bash 块抽出来做 `bash -n`（YAML 合法 ≠ 脚本合法）。
    跳过 pwsh/powershell 步骤 —— 那些不是 bash，交给 bash 检查必然误报。"""
    import re
    import subprocess
    import tempfile

    checked = 0
    for path in sorted(WF.glob("*.yml")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if line.strip() != "run: |":
                continue
            # 向上找同一 step 的 shell 声明（最多 6 行内）
            shell = ""
            for j in range(max(0, i - 6), i):
                if lines[j].strip().startswith("shell:"):
                    shell = lines[j].split(":", 1)[1].strip()
            if shell and shell not in ("bash", "sh"):
                continue
            block: list[str] = []
            for k in range(i + 1, len(lines)):
                nxt = lines[k]
                if nxt.strip() and not nxt.startswith(" " * 10):
                    break
                block.append(nxt[10:] if nxt.startswith(" " * 10) else nxt)
            with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
                fh.write("\n".join(block))
                name = fh.name
            result = subprocess.run(["bash", "-n", name], capture_output=True, text=True)
            assert result.returncode == 0, f"{path.name} 第 {i + 1} 行的 bash 块语法错误：{result.stderr[:200]}"
            checked += 1
    assert checked >= 10, f"只检查到 {checked} 个 bash 块，提取逻辑可能失效"


def test_android_workflow_gates_the_file_provider():
    """需求 §65：APK 必须**既声明** FileProvider、**又**带上它的类。

    这两条都只能在打包后查，而漏掉的后果不对称：
    · 少声明 provider → openUrl 返回 false（功能失效，用户看得见）；
    · 声明了但没有类 → Android 在**进程启动时**实例化该 provider →
      ClassNotFoundException，**启动即崩**，且构建期不报错。

    真机踩过第二类：v1.1.14.alpha 的 APK 6 个 dex 里没有任何 androidx 类。
    所以这两个卡口要一起守住（只留一个都会漏掉另一类）。
    """
    text = (WF / "android.yml").read_text(encoding="utf-8")
    assert 'unzip -p "$APK" AndroidManifest.xml' in text, \
        "缺「清单里有 FileProvider」的校验"
    assert "androidx.core.content.FileProvider" in text
    assert "classes*.dex" in text and "grep -qa" in text, \
        "缺「dex 里有 FileProvider 类」的校验（这条才是防启动崩溃的）"
    assert 'echo "::error::dex 里没有 androidx.core.content.FileProvider' in text
    assert "aapt2\" dump xmltree" not in text, \
        "清单校验不要依赖 aapt2 的参数顺序（参数不被接受时会变成假失败）"


def test_android_workflow_strips_nul_before_grepping_the_manifest():
    """回归：二进制 AXML 的字符串池是 **UTF-16LE**，直接 grep ASCII 必然假失败。

    2026-10-06 真实踩过：hook 其实注入成功了（同一个 hook 注入的既有
    `com.priloba.mangaproof.a11y.env` 就在清单里、UTF-16LE 下可命中），
    但校验 grep 的是 ASCII 字节 → 报"清单缺少 FileProvider" →
    **APK 都编好了，却卡在最后一道校验上**。
    """
    text = (WF / "android.yml").read_text(encoding="utf-8")
    assert "tr -d '\\0'" in text, "清单校验必须先去掉 NUL 字节（AXML 是 UTF-16LE）"
    assert 'tr -d \'\\0\' < "$MAN" > "$MAN_TXT"' in text, \
        "要先转存成去 NUL 的文本，再对**它**做判断"
    for needle in ("androidx.core.content.FileProvider",
                   "com.priloba.mangaproof.qtprovider"):
        assert f'grep -qa "{needle}" "$MAN_TXT"' in text, \
            f"{needle} 必须针对去 NUL 后的文本判断"


def test_nul_stripping_is_what_makes_utf16_manifests_matchable(tmp_path):
    """技术验证：UTF-16LE 下直接 grep 失败、去掉 NUL 之后成功。

    上一条只断言"workflow 里有 tr -d"（形式化）；这条真的构造一份 UTF-16LE
    清单跑同样的管道，把"为什么必须这么做"钉住 —— 否则后人很容易"顺手简化"
    掉那个看似多余的 tr。
    """
    import shutil
    import subprocess

    tr, grep = shutil.which("tr"), shutil.which("grep")
    if not (tr and grep):
        pytest.skip("缺少 tr/grep（非 POSIX 环境）")

    marker = "com.priloba.mangaproof.qtprovider"
    raw = tmp_path / "AndroidManifest.bin"
    raw.write_bytes(f"AXML{marker}END".encode("utf-16-le"))

    plain = subprocess.run([grep, "-qa", marker, str(raw)]).returncode
    assert plain != 0, "UTF-16LE 下直接 grep 本就该失败 —— 这正是当初的坑"

    stripped = subprocess.run(
        ["bash", "-c", f"tr -d '\\0' < '{raw}' | grep -qa '{marker}'"]
    ).returncode
    assert stripped == 0, "去掉 NUL 之后必须命中"


def test_android_workflow_caches_the_gnu_sourced_recipe():
    """回归：freetype 是**唯一**来自 GNU/Savannah 的 recipe，必须把它缓存起来。

    2026-10-06 savannah 不可达让整条安卓构建挂了两次（社区同款：buildozer#1932，
    维护者答"GNU's website and servers are down"，约 18 小时自愈）。缓存命中时构建
    **完全不碰 GNU** —— 靠 p4a 的 `P4A_FREETYPE_DIR`：
    `download_if_necessary()` 见到它就跳过下载（recipe.py:382-392），
    `unpack()` 只 `cp -a` 到 build 目录（recipe.py:459-473），所以缓存不会被弄脏。
    """
    text = (WF / "android.yml").read_text(encoding="utf-8")
    assert "path: ${{ runner.temp }}/p4a-freetype" in text, "缺 freetype 缓存路径"
    assert "key: p4a-freetype-src-${{ env.FREETYPE_VERSION }}" in text, \
        "缓存 key 必须带 freetype 版本，否则 p4a 升版本后会静默用旧源码"
    assert "FREETYPE_VERSION:" in text
    assert "P4A_FREETYPE_DIR=$SRC" in text and "$GITHUB_ENV" in text, \
        "必须导出给后续的 Build APK 步骤，否则缓存了也不会被 p4a 使用"
    # 缓存有效性判据：只有发布 tarball 才带 configure（recipe 直接跑 ./configure）
    assert '"$SRC/configure"' in text, "缺「缓存里必须有 configure」的有效性校验"
    # 冷缓存仍要下这一次，且要能明确报出"上游不可达"
    assert "download-mirror.savannah.gnu.org" in text
    assert "拿不到 freetype 源码" in text

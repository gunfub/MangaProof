"""任务持久化与文件身份验证（需求 §5～§10、§58）。

- 单 PSD：同目录同名 <name>.mangaproof.json；
- 文件夹：<folder>/.mangaproof.json；
- 单文件匹配：文件大小 + 完整 SHA-256（需求 §7.2）；
- 文件夹匹配：2～3 个抽样 Hash + 其他文件 Size 检查（需求 §7.3～§7.6）；
- 绝不通过 PSD 内部结构做身份验证（需求 §7.1）。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from mangaproof.psd import loader
from mangaproof.review.state import FileRecord, TaskState

log = logging.getLogger("mangaproof.review.persistence")

PROGRESS_SUFFIX = ".mangaproof.json"
FOLDER_PROGRESS_NAME = ".mangaproof.json"


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# 进度文件路径（需求 §6.1、§6.2）
# ---------------------------------------------------------------------------

def progress_path_for_single(psd_path: Path) -> Path:
    """001.psd → 001.mangaproof.json"""
    return psd_path.with_name(psd_path.stem + PROGRESS_SUFFIX)


def progress_path_for_folder(folder: Path) -> Path:
    """Chapter01/ → Chapter01/.mangaproof.json"""
    return folder / FOLDER_PROGRESS_NAME


# ---------------------------------------------------------------------------
# 身份记录（需求 §7.3）
# ---------------------------------------------------------------------------

def sample_indices(count: int) -> List[int]:
    """抽样 Hash 文件索引：1～2 全部；3～9 两个；≥10 三个（首/中/尾分散）。"""
    if count <= 2:
        return list(range(count))
    if count <= 9:
        return [0, count - 1]
    mid = (count - 1) // 2
    indices = [0, mid, count - 1]
    return sorted(set(indices))


def build_file_records(
    files: List[Path], base_dir: Path
) -> Tuple[List[FileRecord], List[Path]]:
    """为任务建立文件身份记录。

    files 需已按自然排序；返回 (records, 抽样文件绝对路径列表)。
    相对路径统一使用 posix 风格（/），保证跨平台一致。
    """
    base = base_dir.resolve()
    records: List[FileRecord] = []
    samples = sample_indices(len(files))
    sample_paths: List[Path] = []
    for idx, path in enumerate(files):
        abs_path = path.resolve()
        rel = abs_path.relative_to(base).as_posix()
        size = loader.file_size(abs_path)
        mtime = abs_path.stat().st_mtime
        sha = None
        if idx in samples:
            sha = loader.file_sha256(abs_path)
            sample_paths.append(abs_path)
        records.append(
            FileRecord(
                relative_path=rel,
                file_name=abs_path.name,
                size=size,
                mtime=mtime,
                sample_sha256=sha,
            )
        )
    return records, sample_paths


# ---------------------------------------------------------------------------
# 匹配验证（需求 §7.2～§7.6）
# ---------------------------------------------------------------------------

def verify_single(task: TaskState, actual_file: Path) -> Tuple[bool, str]:
    """单 PSD 验证：文件大小 + 完整 SHA-256。"""
    if not task.files:
        return False, "任务中没有文件记录"
    record = task.files[0]
    try:
        size = loader.file_size(actual_file)
    except OSError as exc:
        return False, f"无法读取文件：{exc}"
    if size != record.size:
        return False, (
            f"文件大小不匹配（任务记录 {record.size} 字节，"
            f"当前文件 {size} 字节）"
        )
    sha = loader.file_sha256(actual_file)
    if record.sample_sha256 and sha != record.sample_sha256:
        return False, "SHA-256 不匹配：当前文件与任务记录不是同一个 PSD"
    return True, ""


def verify_folder(task: TaskState, actual_files: List[Path], base_dir: Path) -> Tuple[bool, str]:
    """文件夹验证（需求 §7.4）：
    抽样文件 → 大小 + SHA-256；其他文件 → 只检查大小。
    任何一项不通过即整体不匹配（需求 §7.6）。
    """
    if not task.files:
        return False, "任务中没有文件记录"

    base = base_dir.resolve()
    actual_map = {
        p.resolve().relative_to(base).as_posix(): p.resolve()
        for p in actual_files
    }

    # 1) 抽样 Hash 文件：大小 + SHA-256
    sample_count = 0
    for record in task.files:
        if not record.sample_sha256:
            continue
        sample_count += 1
        path = actual_map.get(record.relative_path)
        if path is None:
            return False, f"抽样文件缺失：{record.relative_path}"
        try:
            size = loader.file_size(path)
        except OSError as exc:
            return False, f"无法读取 {record.relative_path}：{exc}"
        if size != record.size:
            return False, (
                f"抽样文件大小不匹配：{record.relative_path}"
                f"（任务记录 {record.size}，实际 {size}）"
            )
        sha = loader.file_sha256(path)
        if sha != record.sample_sha256:
            return False, f"抽样文件 SHA-256 不匹配：{record.relative_path}"

    # 2) 其他文件：存在 + 大小一致
    for record in task.files:
        if record.sample_sha256:
            continue
        path = actual_map.get(record.relative_path)
        if path is None:
            return False, f"任务文件缺失：{record.relative_path}"
        try:
            size = loader.file_size(path)
        except OSError as exc:
            return False, f"无法读取 {record.relative_path}：{exc}"
        if size != record.size:
            return False, (
                f"文件大小不匹配：{record.relative_path}"
                f"（任务记录 {record.size}，实际 {size}）"
            )

    # 3) 多出的文件不属于任务（不阻止恢复，但不算匹配失败）
    return True, f"验证通过（抽样 {sample_count} 个 Hash + 全部 Size）"


# ---------------------------------------------------------------------------
# 保存 / 加载（需求 §8、§9、§10）
# ---------------------------------------------------------------------------

def save_task(task: TaskState, path: Path, backup_on_conflict: bool = False) -> None:
    """原子写入任务文件（临时文件 + replace）。"""
    task.updated_at = now_iso()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(task.to_dict(), f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def load_task(path: Path) -> TaskState:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError("任务文件格式错误：根节点必须是对象")
    task = TaskState.from_dict(raw)
    # schema version 高于当前 → 拒绝加载（避免数据损坏）
    if task.schema_version > 1:
        raise ValueError(
            f"任务文件 schema_version={task.schema_version} 高于本程序支持的版本"
        )
    return task


def backup_progress_file(path: Path) -> Optional[Path]:
    """把冲突的旧进度文件重命名为 .bak-<时间戳>，返回备份路径。"""
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(path.name + f".bak-{stamp}")
    try:
        os.replace(path, backup)
        return backup
    except OSError as exc:
        log.warning("备份旧进度文件失败：%s", exc)
        return None


def create_task_single(psd_path: Path) -> Tuple[TaskState, List[Path]]:
    """为单个 PSD 创建新任务（完整 SHA-256 身份）。"""
    psd_path = psd_path.resolve()
    records, samples = build_file_records([psd_path], psd_path.parent)
    task = TaskState(
        task_name=psd_path.stem,
        task_type="single",
        source=str(psd_path),
        current_file=records[0].relative_path,
        files=records,
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    return task, samples


def create_task_folder(folder: Path, files: List[Path]) -> Tuple[TaskState, List[Path]]:
    """为文件夹创建新任务（抽样 Hash 身份）。"""
    folder = folder.resolve()
    records, samples = build_file_records(files, folder)
    task = TaskState(
        task_name=folder.name,
        task_type="folder",
        source=str(folder),
        current_file=records[0].relative_path if records else "",
        files=records,
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    return task, samples

"""后台任务加载器：打开 PSD/文件夹时把耗时操作放到子线程。

耗时步骤（扫描、抽样 SHA-256 验证、逐个 PSD 解析图层树）全部在
QThread 中执行，通过 progress(done, total, message) 信号把阶段信息
推给 UI 的进度对话框，防止大批量 PSD 打开时界面假死。

主线程只负责：启动 worker、显示进度、处理结果（恢复/不匹配/新建/取消）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QThread, Signal

from mangaproof.psd.document import PSDDocument
from mangaproof.psd.loader import scan_psd_files
from mangaproof.review import persistence
from mangaproof.review.persistence import (
    LoadCancelled,
    create_task_folder,
    create_task_single,
    load_task,
    progress_path_for_folder,
    progress_path_for_single,
    verify_folder,
    verify_single,
)
from mangaproof.review.state import TaskState

log = logging.getLogger("mangaproof.ui.task_loader")

KIND_OK = "ok"                 # 任务就绪（含恢复或新建）
KIND_MISMATCH = "mismatch"     # 进度文件验证失败，禁止恢复
KIND_NO_FILES = "no_files"     # 文件夹中没有 PSD
KIND_CANCELLED = "cancelled"   # 用户取消


@dataclass
class TaskLoadResult:
    kind: str
    task: Optional[TaskState] = None
    base_dir: Optional[Path] = None
    layer_ids_by_file: Dict[str, List[str]] = field(default_factory=dict)
    layer_names_by_file: Dict[str, List[str]] = field(default_factory=dict)
    docs: Dict[str, object] = field(default_factory=dict)   # rel -> PSDDocument
    reason: str = ""
    rebind: bool = False        # 原路径与当前选择不同（需求 §7.7）
    file_errors: List[str] = field(default_factory=list)


class TaskLoadWorker(QThread):
    """后台加载线程。

    progress(done, total, message)：阶段进度；
    succeeded(TaskLoadResult)：完成（含取消/不匹配等非致命结果）；
    failed(str)：致命错误。
    """

    progress = Signal(int, int, str)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        mode: str,                  # "single" | "folder"
        path: Path,
        recursive: bool = False,
        force_fresh: bool = False,  # 放弃旧进度，强制新建任务
        layer_cache=None,           # 共享图层像素 LRU（内存策略统一预算）
        parent=None,
    ):
        super().__init__(parent)
        self._mode = mode
        self._path = Path(path)
        self._recursive = recursive
        self._force_fresh = force_fresh
        self._layer_cache = layer_cache
        self._cancel = False

    def request_cancel(self) -> None:
        self._cancel = True

    # -- 线程入口 ----------------------------------------------------------

    def run(self) -> None:
        try:
            result = self._load()
        except LoadCancelled:
            self.succeeded.emit(TaskLoadResult(kind=KIND_CANCELLED))
            return
        except Exception as exc:
            log.exception("任务加载失败：%s", self._path)
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(result)

    # -- 内部 --------------------------------------------------------------

    def _cb(self, done: int, total: int, message: str) -> None:
        """进度回调：转发进度；用户取消时抛出 LoadCancelled 中断。"""
        self.progress.emit(done, total, message)
        if self._cancel:
            raise LoadCancelled()

    def _load(self) -> TaskLoadResult:
        if self._mode == "single":
            return self._load_single()
        return self._load_folder()

    def _load_folder(self) -> TaskLoadResult:
        folder = self._path
        files = scan_psd_files(folder, self._recursive)
        self._cb(0, max(len(files), 1), f"扫描 PSD 文件：找到 {len(files)} 个")
        if not files:
            return TaskLoadResult(kind=KIND_NO_FILES)

        task: Optional[TaskState] = None
        progress_path = progress_path_for_folder(folder)
        if not self._force_fresh and progress_path.exists():
            try:
                task = load_task(progress_path)
            except (OSError, ValueError) as exc:
                log.warning("读取进度文件失败：%s", exc)
                task = None
            if task is not None:
                ok, reason = verify_folder(task, files, folder, progress_cb=self._cb)
                if ok:
                    rebind = bool(task.source) and Path(task.source) != folder.resolve()
                    return self._parse(task, folder, rebind=rebind)
                return TaskLoadResult(kind=KIND_MISMATCH, reason=reason)

        task, _ = create_task_folder(folder, files, progress_cb=self._cb)
        return self._parse(task, folder, rebind=False)

    def _load_single(self) -> TaskLoadResult:
        path = self._path
        task: Optional[TaskState] = None
        progress_path = progress_path_for_single(path)
        if not self._force_fresh and progress_path.exists():
            try:
                task = load_task(progress_path)
            except (OSError, ValueError) as exc:
                log.warning("读取进度文件失败：%s", exc)
                task = None
            if task is not None:
                ok, reason = verify_single(task, path, progress_cb=self._cb)
                if ok:
                    rebind = bool(task.source) and Path(task.source) != path.resolve()
                    return self._parse(task, path.parent, rebind=rebind)
                return TaskLoadResult(kind=KIND_MISMATCH, reason=reason)

        task, _ = create_task_single(path, progress_cb=self._cb)
        return self._parse(task, path.parent, rebind=False)

    def _parse(self, task: TaskState, base_dir: Path, rebind: bool) -> TaskLoadResult:
        """逐个解析 PSD 图层树（每个 PSD 只解析一次，需求 §59）。

        流式扫描：解析一页 → 存 ids/names → 窗口外页面立即释放，
        只保留「当前页 + 后 3 + 前 1」的完整文档对象。这样打开大书时
        内存峰值 O(窗口) 而非 O(全书)（文档结构 ≈ 文件大小）。
        """
        layer_ids: Dict[str, List[str]] = {}
        layer_names: Dict[str, List[str]] = {}
        docs: Dict[str, object] = {}
        errors: List[str] = []
        total = len(task.files)
        keep = _window_set(task)
        for i, record in enumerate(task.files):
            rel = record.relative_path
            self._cb(i, total, f"解析 PSD（{i + 1}/{total}）：{record.file_name}")
            try:
                doc = PSDDocument(
                    base_dir / rel,
                    layer_cache=self._layer_cache,
                )
                ids = [info.id for info in doc.layers]
                names = [info.name for info in doc.layers]
                layer_ids[rel] = ids
                layer_names[rel] = names
                if rel in keep:
                    docs[rel] = doc
                else:
                    # 窗口外：只留 id/name 列表（监制进度数据源），
                    # 文档对象立即释放，重看时按需重建。
                    doc.release()
            except Exception as exc:
                log.warning("解析失败 %s：%s", rel, exc)
                errors.append(f"{rel}：无法读取该 PSD 文件")
        self._cb(total, total, "加载完成")
        return TaskLoadResult(
            kind=KIND_OK,
            task=task,
            base_dir=base_dir,
            layer_ids_by_file=layer_ids,
            layer_names_by_file=layer_names,
            docs=docs,
            rebind=rebind,
            file_errors=errors,
        )


def _window_set(task: TaskState) -> set:
    """任务打开时保留完整文档对象的窗口集合（当前页 + 后 3 + 前 1）。

    与 MainWindow._schedule_preloads 的邻域策略一致；当前页无效时
    退化为「第一页 + 后 3」。
    """
    rels = [r.relative_path for r in task.files]
    try:
        i = rels.index(task.current_file)
    except ValueError:
        i = 0
    keep = {rels[j] for j in (i, i + 1, i + 2, i + 3, i - 1) if 0 <= j < len(rels)}
    return keep or {rels[0]} if rels else set()

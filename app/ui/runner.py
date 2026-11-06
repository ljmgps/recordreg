"""GUI 后台批处理线程。

界面线程只负责画，所有推理都在这里跑。信号一发一回，主线程不碰模型。
停止/暂停都是"当前文件跑完再停"，不中断单文件——半成品比没跑更麻烦。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QThread, Signal


@dataclass
class TaskItem:
    path: Path
    sha: str = ""
    status: str = "等待"
    stage: str = ""
    progress: float = 0.0
    duration: float = 0.0
    speakers: int = 0
    turns: int = 0
    chars: int = 0
    elapsed: float = 0.0
    out_md: Path | None = None
    out_json: Path | None = None
    error: str = ""

    @property
    def name(self) -> str:
        return self.path.name


class ScanWorker(QThread):
    """后台读取文件信息：时长、sha256、是否已处理。

    300 个文件要读一遍元信息并算哈希，放在主线程会让界面卡成"未响应"。
    这里边扫边发信号，列表一行行长出来，用户能看见进度。
    """

    item_ready = Signal(object)          # TaskItem
    progress = Signal(int, int, str)     # done, total, 当前文件名
    finished_scan = Signal(int)          # 实际扫描数量

    def __init__(self, files: list[Path], cfg, store, parent=None) -> None:
        super().__init__(parent)
        self.files = files
        self.cfg = cfg
        self.store = store
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:  # noqa: C901
        from ..core.audio import probe, sha256_of
        from ..core.writer import TOOL_VERSION

        total = len(self.files)
        done = 0
        for f in self.files:
            if self._stop.is_set():
                break
            item = TaskItem(path=f)
            try:
                item.duration = probe(f, self.cfg.paths.ffmpeg).duration_sec
            except Exception:  # noqa: BLE001
                item.duration = 0.0
            try:
                item.sha = sha256_of(f)
            except OSError:
                item.sha = ""
            try:
                if item.sha and self.store.is_done(item.sha, TOOL_VERSION):
                    item.status = "已处理"
                    item.progress = 1.0
            except Exception:  # noqa: BLE001
                pass
            done += 1
            self.item_ready.emit(item)
            self.progress.emit(done, total, f.name)
        self.finished_scan.emit(done)


class BatchWorker(QThread):
    models_ready = Signal(str)
    models_failed = Signal(str)
    file_started = Signal(int, str)
    file_progress = Signal(int, str, float)
    file_done = Signal(int, object)
    file_failed = Signal(int, str)
    file_skipped = Signal(int, str)
    batch_finished = Signal(int, int, int, float)
    log = Signal(str)

    def __init__(self, cfg, items: list[TaskItem], device: str | None = None,
                 force: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.items = items
        self.device = device
        self.force = force
        self._stop = threading.Event()
        self._pause = threading.Event()

    # ---- 控制 ----
    def stop(self) -> None:
        self._stop.set()
        self._pause.clear()

    def pause(self) -> None:
        self._pause.set()

    def resume(self) -> None:
        self._pause.clear()

    @property
    def is_paused(self) -> bool:
        return self._pause.is_set()

    # ---- 主循环 ----
    def run(self) -> None:  # noqa: C901
        from app.core.pipeline import process_file
        from app.core.registry import ModelRegistry
        from app.core.store import Store
        from app.core.writer import TOOL_VERSION, write_all

        t_all = time.time()
        ok = skipped = failed = 0

        try:
            registry = ModelRegistry.get(self.cfg, self.device)
            self.log.emit(f"加载模型（{registry.device}）…")
            t0 = time.time()
            registry.vad()
            registry.asr()
            self.models_ready.emit(registry.device)
            self.log.emit(f"模型就绪，用时 {time.time() - t0:.1f}s")
        except Exception as e:  # noqa: BLE001
            self.models_failed.emit(str(e))
            return

        store = Store(self.cfg.paths.db)
        batch_id = store.start_batch(len(self.items))

        try:
            for i, item in enumerate(self.items):
                if self._stop.is_set():
                    self.log.emit("已停止")
                    break

                # 暂停：停在两个文件之间，不中断当前文件
                while self._pause.is_set() and not self._stop.is_set():
                    time.sleep(0.1)
                if self._stop.is_set():
                    self.log.emit("已停止")
                    break

                # 去重：内容相同且已完成则跳过
                if not self.force and item.sha and store.is_done(item.sha, TOOL_VERSION):
                    skipped += 1
                    item.status = "已处理"
                    item.progress = 1.0
                    self.file_skipped.emit(i, item.path.name)
                    continue

                self.file_started.emit(i, item.path.name)
                item.status = "处理中"
                item.progress = 0.0
                last_emit = [0.0]

                def _prog(stage: str, frac: float, idx: int = i) -> None:
                    now = time.time()
                    # 节流到 ~15fps，否则信号洪水会让界面卡顿
                    if now - last_emit[0] < 0.06 and frac < 0.999:
                        return
                    last_emit[0] = now
                    item.stage = stage
                    item.progress = frac
                    self.file_progress.emit(idx, stage, frac)

                try:
                    if item.sha:
                        st = item.path.stat()
                        store.mark_running(item.sha, item.path, item.path.name,
                                           item.path.suffix.lstrip(".").lower(),
                                           st.st_size, st.st_mtime)
                    doc = process_file(item.path, self.cfg, registry,
                                       sha=item.sha or None, progress=_prog)
                except Exception as e:  # noqa: BLE001
                    failed += 1
                    item.status = "失败"
                    item.error = str(e)
                    if item.sha:
                        store.mark_failed(item.sha, str(e))
                    self.file_failed.emit(i, str(e))
                    self.log.emit(f"✗ {item.path.name}: {e}")
                    continue

                try:
                    paths = write_all(doc, self.cfg)
                except Exception as e:  # noqa: BLE001
                    failed += 1
                    item.status = "失败"
                    item.error = str(e)
                    self.file_failed.emit(i, str(e))
                    self.log.emit(f"✗ 写出失败 {item.path.name}: {e}")
                    continue

                item.status = "完成"
                item.progress = 1.0
                item.duration = doc.info.duration_sec
                item.speakers = len(doc.speakers)
                item.turns = len(doc.turns)
                item.chars = doc.char_count
                item.elapsed = doc.elapsed_sec
                item.out_md = paths.get("md")
                item.out_json = paths.get("json")

                if item.sha:
                    store.mark_done(
                        item.sha,
                        out_md=str(item.out_md) if item.out_md else None,
                        out_json=str(item.out_json) if item.out_json else None,
                        duration_sec=doc.info.duration_sec,
                        speaker_count=len(doc.speakers),
                        turn_count=len(doc.turns),
                        char_count=doc.char_count,
                        elapsed_sec=doc.elapsed_sec,
                        model_ver=TOOL_VERSION,
                    )

                ok += 1
                spk = f"{len(doc.speakers)}人" if doc.has_speaker else "单人"
                self.file_done.emit(i, item)
                self.log.emit(
                    f"✓ {item.path.name}  {spk}·{len(doc.turns)}轮·{doc.char_count}字  "
                    f"{doc.elapsed_sec:.1f}s"
                )
        finally:
            store.finish_batch(batch_id, ok, skipped, failed)
            store.close()
            self.batch_finished.emit(ok, skipped, failed, time.time() - t_all)

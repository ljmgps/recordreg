"""主窗口：任务队列、进度、日志。"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..config import Config, load_config
from ..core.audio import SUPPORTED_EXTS
from ..core.store import Store
from ..core.writer import TOOL_VERSION
from .runner import BatchWorker, ScanWorker, TaskItem

STATUS_COLOR = {
    "等待": "#8a919c",
    "处理中": "#2563eb",
    "完成": "#12805c",
    "失败": "#c4291c",
    "已处理": "#8a919c",
    "已跳过": "#8a919c",
}


class MainWindow(QMainWindow):
    COLS = ["文件名", "时长", "状态", "进度", "说话人", "轮次", "字数", "耗时"]

    def __init__(self, cfg: Config | None = None) -> None:
        super().__init__()
        self.cfg = cfg or load_config()
        self.store = Store(self.cfg.paths.db)
        self.items: list[TaskItem] = []
        self.worker: BatchWorker | None = None
        self.scan: ScanWorker | None = None
        self._running = False
        self._scanning = False
        self._scan_skipped = 0
        self._batch_t0 = 0.0

        self.setWindowTitle("RecordReg · 录音转写与说话人分离")
        self.resize(1180, 760)
        self.setAcceptDrops(True)
        self._build_ui()
        self._refresh_device_label()

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(14, 12, 14, 10)
        outer.setSpacing(10)

        # 工具栏
        bar = QWidget()
        bar.setObjectName("Toolbar")
        hb = QHBoxLayout(bar)
        hb.setContentsMargins(0, 0, 0, 8)
        hb.setSpacing(8)

        self.btn_add_files = QPushButton("添加文件")
        self.btn_add_dir = QPushButton("添加文件夹")
        self.btn_clear = QPushButton("清空")
        self.btn_start = QPushButton("开始处理")
        self.btn_start.setObjectName("primary")
        self.btn_pause = QPushButton("暂停")
        self.btn_stop = QPushButton("停止")
        self.btn_stop.setObjectName("danger")
        self.btn_settings = QPushButton("设置")
        self.btn_open = QPushButton("打开输出目录")
        self.chk_force = QCheckBox("强制重跑")

        for w in (self.btn_add_files, self.btn_add_dir, self.btn_clear):
            hb.addWidget(w)
        hb.addSpacing(12)
        hb.addWidget(self.btn_start)
        hb.addWidget(self.btn_pause)
        hb.addWidget(self.btn_stop)
        hb.addStretch(1)
        hb.addWidget(self.chk_force)
        hb.addSpacing(6)
        hb.addWidget(self.btn_settings)
        hb.addWidget(self.btn_open)
        outer.addWidget(bar)

        # 表格
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(False)
        self.table.doubleClicked.connect(self._on_double_click)

        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in range(1, len(self.COLS)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.setColumnWidth(1, 70)
        self.table.setColumnWidth(3, 110)
        # 表头：文件名左对齐，数值列居中
        for c in range(len(self.COLS)):
            hi = self.table.horizontalHeaderItem(c)
            if hi is not None:
                hi.setTextAlignment(
                    (Qt.AlignLeft | Qt.AlignVCenter) if c == 0 else Qt.AlignCenter
                )

        # 空列表提示（叠在表格上）
        self.empty_hint = QLabel(
            "把音频文件或文件夹拖到这里\n或点击上方「添加文件」「添加文件夹」", self.table
        )
        self.empty_hint.setAlignment(Qt.AlignCenter)
        self.empty_hint.setStyleSheet("color:#b7bcc5; font-size:13px; line-height:150%;")
        self.empty_hint.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.empty_hint.show()

        # 日志
        self.log = QPlainTextEdit()
        self.log.setObjectName("Log")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setPlaceholderText("处理日志会显示在这里")

        split = QSplitter(Qt.Vertical)
        split.addWidget(self.table)
        split.addWidget(self.log)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 1)
        split.setSizes([560, 140])
        outer.addWidget(split, 1)

        # 状态栏
        sb = self.statusBar()
        sb.setContentsMargins(10, 3, 10, 3)
        self.lbl_status = QLabel("就绪 · 把音频文件或文件夹拖进窗口")
        self.progress = QProgressBar()
        self.progress.setFixedWidth(220)
        self.progress.setTextVisible(False)
        self.lbl_device = QLabel("")
        self.lbl_device.setStyleSheet("color:#8a919c;")
        sb.addWidget(self.lbl_status, 1)
        sb.addPermanentWidget(self.progress)
        sb.addPermanentWidget(self.lbl_device)

        # 信号
        self.btn_add_files.clicked.connect(self._pick_files)
        self.btn_add_dir.clicked.connect(self._pick_dir)
        self.btn_clear.clicked.connect(self._clear)
        self.btn_start.clicked.connect(self._start)
        self.btn_pause.clicked.connect(self._toggle_pause)
        self.btn_stop.clicked.connect(self._stop)
        self.btn_settings.clicked.connect(self._open_settings)
        self.btn_open.clicked.connect(self._open_output)
        self._set_running(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self._update_empty_hint()

    def _refresh_device_label(self) -> None:
        try:
            from ..core.registry import detect_device

            dev = detect_device()
            self.lbl_device.setText(f"设备 {dev}  ·  版本 {TOOL_VERSION}")
        except Exception:  # noqa: BLE001
            self.lbl_device.setText(f"版本 {TOOL_VERSION}")

    def _log(self, msg: str) -> None:
        self.log.appendPlainText(time.strftime("[%H:%M:%S] ") + msg)

    # ---------------- 添加文件 ----------------
    def _pick_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择录音文件", "",
            "音频文件 (*.mp3 *.aac *.m4a);;所有文件 (*.*)",
        )
        if files:
            self.add_paths([Path(f) for f in files])

    def _pick_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择文件夹（含子文件夹）")
        if d:
            self.add_paths([Path(d)])

    def add_paths(self, paths: list[Path]) -> None:
        """添加文件或目录；目录递归扫描，元信息交给后台线程读。"""
        if self._scanning:
            self._log("上一批文件还在读取，请稍候…")
            return

        found: list[Path] = []
        for p in paths:
            if p.is_dir():
                found.extend(sorted(p.rglob("*")))
            elif p.is_file():
                found.append(p)
        found = [f for f in found if f.is_file() and f.suffix.lower() in SUPPORTED_EXTS]
        if not found:
            self._log("没有找到支持的音频文件（.mp3 / .aac / .m4a）")
            return

        known = {str(it.path.resolve()).lower() for it in self.items}
        new_files = [f for f in found if str(f.resolve()).lower() not in known]
        if not new_files:
            self._log("这些文件已经在列表里了")
            return

        # 读元信息 + 算哈希要几秒到几十秒，放后台，界面显示进度而不是"未响应"
        self._scan_skipped = 0
        self._set_scanning(True, len(new_files))
        self._log(f"正在读取 {len(new_files)} 个文件的信息…")

        self.scan = ScanWorker(new_files, self.cfg, self.store)
        self.scan.item_ready.connect(self._on_item_ready)
        self.scan.progress.connect(self._on_scan_progress)
        self.scan.finished_scan.connect(self._on_scan_finished)
        self.scan.finished.connect(self._on_scan_thread_done)
        self.scan.start()

    def _on_item_ready(self, item: TaskItem) -> None:
        self.items.append(item)
        self._append_row(item)
        if item.status == "已处理":
            self._scan_skipped += 1

    def _on_scan_progress(self, done: int, total: int, name: str) -> None:
        self.progress.setValue(done)
        short = name if len(name) <= 28 else name[:26] + "…"
        self.lbl_status.setText(f"正在读取 {done}/{total}：{short}")

    def _on_scan_finished(self, count: int) -> None:
        self._set_scanning(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        msg = f"已添加 {count} 个文件"
        if self._scan_skipped:
            msg += f"，其中 {self._scan_skipped} 个之前处理过（需要重跑请勾选「强制重跑」）"
        self._log(msg)
        self.lbl_status.setText(f"队列中共 {len(self.items)} 个文件")
        self._update_empty_hint()

    def _on_scan_thread_done(self) -> None:
        if self.scan is not None:
            self.scan.deleteLater()
            self.scan = None

    def _append_row(self, item: TaskItem) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        self._set_row(r, item)
        self._update_empty_hint()

    def _update_empty_hint(self) -> None:
        """空列表时在表格中间显示引导文字。"""
        try:
            vp = self.table.viewport().geometry()
            self.empty_hint.setGeometry(vp)
            self.empty_hint.setVisible(self.table.rowCount() == 0)
        except Exception:  # noqa: BLE001
            pass

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_empty_hint()

    def _set_row(self, r: int, item: TaskItem) -> None:
        dur = f"{item.duration/60:.1f} 分" if item.duration else "-"
        spk = f"{item.speakers} 人" if item.speakers else "-"
        turn = str(item.turns) if item.turns else "-"
        chars = str(item.chars) if item.chars else "-"
        eta = f"{item.elapsed:.1f}s" if item.elapsed else "-"
        prog = f"{int(item.progress*100)}%  {item.stage}" if item.status == "处理中" else \
               ("100%" if item.progress >= 1 else "-")

        values = [item.name, dur, item.status, prog, spk, turn, chars, eta]
        for c, v in enumerate(values):
            cell = self.table.item(r, c)
            if cell is None:
                cell = QTableWidgetItem()
                self.table.setItem(r, c, cell)
            cell.setText(v)
            if c == 2:
                cell.setForeground(QColor(STATUS_COLOR.get(item.status, "#1f2328")))
                f = cell.font()
                f.setWeight(QFont.DemiBold if item.status in ("完成", "失败") else QFont.Normal)
                cell.setFont(f)
            elif c == 0:
                cell.setToolTip(str(item.path))
            if c != 0:
                cell.setTextAlignment(Qt.AlignCenter)

    # ---------------- 运行控制 ----------------
    def _set_running(self, running: bool) -> None:
        self._running = running
        if not running:
            self.btn_pause.setText("暂停")
        self._refresh_buttons()

    def _set_scanning(self, scanning: bool, total: int = 0) -> None:
        self._scanning = scanning
        if scanning:
            self.progress.setRange(0, max(total, 1))
            self.progress.setValue(0)
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        busy = self._running or self._scanning
        for b in (self.btn_add_files, self.btn_add_dir, self.btn_clear,
                  self.btn_settings, self.btn_start):
            b.setEnabled(not busy)
        self.btn_pause.setEnabled(self._running)
        self.btn_stop.setEnabled(self._running)
        self.chk_force.setEnabled(not busy)

    def _start(self, force: bool = False) -> None:
        if not self.items:
            QMessageBox.information(self, "提示", "请先添加音频文件或文件夹。")
            return
        if self.worker is not None and self.worker.isRunning():
            return
        if self._scanning:
            QMessageBox.information(self, "请稍候", "文件信息还在读取中，读完再开始。")
            return

        self.cfg = load_config()          # 重新读配置，保证设置改动生效
        force = bool(force) or self.chk_force.isChecked()
        self._set_running(True)
        self.progress.setValue(0)
        self._batch_t0 = time.time()
        self.lbl_status.setText("正在加载模型…")

        self.worker = BatchWorker(self.cfg, self.items, device=None, force=force)
        self.worker.models_ready.connect(self._on_models_ready)
        self.worker.models_failed.connect(self._on_models_failed)
        self.worker.file_started.connect(self._on_file_started)
        self.worker.file_progress.connect(self._on_progress)
        self.worker.file_done.connect(self._on_done)
        self.worker.file_failed.connect(self._on_failed)
        self.worker.file_skipped.connect(self._on_skipped)
        self.worker.batch_finished.connect(self._on_finished)
        self.worker.log.connect(self._log)
        self.worker.finished.connect(self._on_thread_finished)
        self.worker.start()

    def _toggle_pause(self) -> None:
        if self.worker is None:
            return
        if self.worker.is_paused:
            self.worker.resume()
            self.btn_pause.setText("暂停")
            self.lbl_status.setText("继续处理…")
            self._log("已继续")
        else:
            self.worker.pause()
            self.btn_pause.setText("继续")
            self.lbl_status.setText("将在当前文件完成后暂停")
            self._log("已请求暂停（当前文件完成后生效）")

    def _stop(self) -> None:
        if self.worker is None:
            return
        if QMessageBox.question(
            self, "停止", "当前文件处理完后停止，之后的文件保持待处理。确定吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) == QMessageBox.Yes:
            self.worker.stop()
            self.lbl_status.setText("正在停止…")

    # ---------------- worker 回调 ----------------
    def _on_models_ready(self, device: str) -> None:
        self.lbl_status.setText("模型就绪，开始处理…")
        self.lbl_device.setText(f"设备 {device}  ·  版本 {TOOL_VERSION}")

    def _on_models_failed(self, msg: str) -> None:
        self._set_running(False)
        QMessageBox.critical(self, "模型加载失败", msg)
        self.lbl_status.setText("模型加载失败")

    def _on_file_started(self, idx: int, name: str) -> None:
        self.lbl_status.setText(f"正在处理：{name}")
        if idx < len(self.items):
            self._set_row(idx, self.items[idx])

    def _on_progress(self, idx: int, stage: str, frac: float) -> None:
        if idx >= len(self.items):
            return
        item = self.items[idx]
        item.stage = stage
        item.progress = frac
        self._set_row(idx, item)
        total = len(self.items) or 1
        self.progress.setValue(int((idx + frac) / total * 100))

    def _on_done(self, idx: int, item: TaskItem) -> None:
        if idx < len(self.items):
            self._set_row(idx, item)

    def _on_failed(self, idx: int, msg: str) -> None:
        if idx < len(self.items):
            self.items[idx].status = "失败"
            self.items[idx].error = msg
            self._set_row(idx, self.items[idx])

    def _on_skipped(self, idx: int, name: str) -> None:
        if idx < len(self.items):
            self.items[idx].status = "已处理"
            self.items[idx].progress = 1.0
            self._set_row(idx, self.items[idx])
        self.progress.setValue(int((idx + 1) / max(len(self.items), 1) * 100))

    def _on_finished(self, ok: int, skipped: int, failed: int, elapsed: float) -> None:
        self._set_running(False)
        self.progress.setValue(100)
        parts = [f"成功 {ok}"]
        if skipped:
            parts.append(f"跳过 {skipped}")
        if failed:
            parts.append(f"失败 {failed}")
        summary = " · ".join(parts)
        self.lbl_status.setText(f"完成：{summary}，用时 {elapsed/60:.1f} 分钟")
        self._log(f"===== 批次结束：{summary}，用时 {elapsed/60:.1f} 分钟 =====")
        if ok == 0 and skipped > 0:
            QMessageBox.information(
                self, "全部跳过",
                f"这 {skipped} 个文件之前都处理过了，所以全部跳过。\n\n"
                f"如果想重新处理，勾选右上角的「强制重跑」再点开始。",
            )
        elif failed:
            QMessageBox.warning(self, "部分失败", f"{summary}\n\n失败的文件在列表中标红，"
                                                  f"把鼠标停在文件名上可看完整路径。")

    def _on_thread_finished(self) -> None:
        if self.worker is not None:
            self.worker.deleteLater()
            self.worker = None

    # ---------------- 其他 ----------------
    def _clear(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        self.items.clear()
        self.table.setRowCount(0)
        self._update_empty_hint()
        self.progress.setValue(0)
        self.lbl_status.setText("就绪 · 把音频文件或文件夹拖进窗口")

    def _open_output(self) -> None:
        import os
        import subprocess

        out = self.cfg.paths.output
        out.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(out))  # noqa: S606
        except Exception:  # noqa: BLE001
            subprocess.Popen(["explorer", str(out)])

    def _open_settings(self) -> None:
        from .settings_dialog import SettingsDialog

        dlg = SettingsDialog(self.cfg, self.store, self)
        if dlg.exec():
            self.cfg = load_config()
            self._refresh_device_label()
            self._log("设置已保存，下次开始处理时生效")

    def _on_double_click(self, index) -> None:
        r = index.row()
        if r >= len(self.items):
            return
        item = self.items[r]
        if not item.out_json or not Path(item.out_json).exists():
            if item.status == "失败":
                QMessageBox.warning(self, "处理失败", item.error or "未知错误")
            else:
                QMessageBox.information(self, "提示", "这个文件还没有处理结果。")
            return
        from .preview_dialog import PreviewDialog

        dlg = PreviewDialog(item, self.store, self)
        dlg.exec()
        if dlg.changed:
            self._log(f"{item.name}：说话人名称已更新")

    # ---------------- 拖拽 ----------------
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.add_paths(paths)
            event.acceptProposedAction()

    def closeEvent(self, event) -> None:
        if self.scan is not None and self.scan.isRunning():
            self.scan.stop()
            self.scan.wait(5000)
        if self.worker is not None and self.worker.isRunning():
            if QMessageBox.question(
                self, "退出", "还有文件在处理，确定退出吗？\n（会在当前文件处理完后关闭）",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            ) != QMessageBox.Yes:
                event.ignore()
                return
            self.worker.stop()
            # 单个文件最长可能几十秒，等够再关；等不到就不关，避免线程被强杀
            if not self.worker.wait(90000):
                QMessageBox.warning(
                    self, "仍在处理",
                    "当前文件还没有处理完，请稍等片刻再关闭窗口。",
                )
                event.ignore()
                return
        try:
            self.store.close()
        except Exception:  # noqa: BLE001
            pass
        event.accept()

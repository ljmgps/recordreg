"""结果预览：看转写文本，给说话人改名。"""

from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
)

from ..core.store import Store
from .runner import TaskItem


class PreviewDialog(QDialog):
    def __init__(self, item: TaskItem, store: Store, parent=None) -> None:
        super().__init__(parent)
        self.item = item
        self.store = store
        self.changed = False
        self.payload: dict = {}
        self.setWindowTitle(f"预览 · {item.path.name}")
        self.resize(1080, 700)
        self._load()
        self._build()

    # ---------- 数据 ----------
    def _load(self) -> None:
        try:
            with open(self.item.out_json, "r", encoding="utf-8") as f:
                self.payload = json.load(f)
        except Exception:  # noqa: BLE001
            self.payload = {"speakers": [], "segments": [], "stats": {}}

    def _read_md(self) -> str:
        try:
            return Path(self.item.out_md).read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            return "（无法读取 Markdown 文件）"

    # ---------- 界面 ----------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(10)

        # 概要
        stats = self.payload.get("stats", {})
        file_info = self.payload.get("file", {})
        alias_default = self.item.path.stem
        head = QLabel(
            f"<b>{alias_default}</b><br>"
            f"<span style='color:#8a919c'>"
            f"{file_info.get('duration_sec', 0)/60:.1f} 分钟 · "
            f"{stats.get('speaker_count', 0)} 位说话人 · "
            f"{stats.get('turn_count', 0)} 轮 · {stats.get('char_count', 0)} 字 · "
            f"RTF {stats.get('rtf', 0)}</span>"
        )
        outer.addWidget(head)

        split = QSplitter(Qt.Horizontal)

        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(False)
        self.browser.document().setDefaultStyleSheet(
            "h1{font-size:19px; margin:4px 0 10px 0;}"
            "p{margin:6px 0; line-height:160%;}"
            "code{background:#f2f3f5; padding:1px 4px; border-radius:3px;"
            " font-family:Consolas; font-size:12px; color:#5b6472;}"
            "strong{color:#1f2328;}"
        )
        self.browser.setMarkdown(self._read_md())
        split.addWidget(self.browser)

        right = QTableWidget(0, 3)
        right.setHorizontalHeaderLabels(["说话人", "名称", "时长"])
        right.verticalHeader().setVisible(False)
        right.setSelectionBehavior(QAbstractItemView.SelectRows)
        right.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        right.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        right.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        right.setFixedWidth(300)
        self.tbl_spk = right

        for spk in self.payload.get("speakers", []):
            r = right.rowCount()
            right.insertRow(r)
            id_item = QTableWidgetItem(spk.get("id", ""))
            id_item.setFlags(id_item.flags() & ~Qt.ItemIsEditable)
            name_item = QTableWidgetItem(spk.get("alias") or spk.get("id", ""))
            dur_item = QTableWidgetItem(f"{spk.get('total_sec', 0)/60:.1f} 分")
            dur_item.setFlags(dur_item.flags() & ~Qt.ItemIsEditable)
            right.setItem(r, 0, id_item)
            right.setItem(r, 1, name_item)
            right.setItem(r, 2, dur_item)

        right_container = QVBoxLayout()
        tip = QLabel("双击「名称」列即可改名")
        tip.setStyleSheet("color:#8a919c; padding:2px 2px 6px 2px;")
        right_container.addWidget(tip)
        right_container.addWidget(right, 1)
        right_widget = QLabel()
        right_widget.setLayout(right_container)
        right_widget.setMinimumWidth(300)
        split.addWidget(right_widget)

        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        outer.addWidget(split, 1)

        # 按钮
        row = QHBoxLayout()
        btn_md = QPushButton("打开 Markdown")
        btn_json = QPushButton("打开 JSON")
        btn_save = QPushButton("保存改名")
        btn_save.setObjectName("primary")
        btn_close = QPushButton("关闭")
        btn_md.clicked.connect(lambda: self._open(self.item.out_md))
        btn_json.clicked.connect(lambda: self._open(self.item.out_json))
        btn_save.clicked.connect(self._save_aliases)
        btn_close.clicked.connect(self.accept)
        row.addWidget(btn_md)
        row.addWidget(btn_json)
        row.addStretch(1)
        row.addWidget(btn_save)
        row.addWidget(btn_close)
        outer.addLayout(row)

    # ---------- 操作 ----------
    def _open(self, path: Path | None) -> None:
        if not path:
            return
        try:
            os.startfile(str(path))  # noqa: S606
        except Exception:  # noqa: BLE001
            pass

    def _save_aliases(self) -> None:
        speakers = self.payload.get("speakers", [])
        if not speakers or not self.tbl_spk.rowCount():
            self.accept()
            return

        # 收集新名称
        new_names: dict[str, str] = {}
        for r in range(self.tbl_spk.rowCount()):
            sid = self.tbl_spk.item(r, 0).text().strip()
            name = self.tbl_spk.item(r, 1).text().strip()
            if sid and name:
                new_names[sid] = name

        for spk in speakers:
            sid = spk.get("id")
            if sid in new_names:
                spk["alias"] = new_names[sid]

        # 写回 json
        try:
            Path(self.item.out_json).write_text(
                json.dumps(self.payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:  # noqa: BLE001
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.warning(self, "保存失败", str(e))
            return

        # 改写 md 里的标签（格式固定为 **说话人N**）
        try:
            md_path = Path(self.item.out_md)
            text = md_path.read_text(encoding="utf-8")
            for sid, name in new_names.items():
                try:
                    num = int(sid.replace("SPK_", ""))
                except ValueError:
                    continue
                text = text.replace(f"**说话人{num}**", f"**{name}**")
            md_path.write_text(text, encoding="utf-8")
            self.browser.setMarkdown(text)
        except Exception:  # noqa: BLE001
            pass

        # 记住别名，下次可复用
        try:
            for sid, name in new_names.items():
                self.store.set_alias(self.item.sha, sid, name)
        except Exception:  # noqa: BLE001
            pass

        self.changed = True
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.information(self, "已保存", "说话人名称已更新到 Markdown 与 JSON。")

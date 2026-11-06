"""设置对话框：说话人参数、输出目录、热词表。"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..config import Config, DEFAULT_CONFIG_PATH


def update_config_file(path: Path, updates: dict) -> None:
    """把改动写回 config.yaml。

    用 yaml 原样读、改、写。原文件里的手写注释会丢失（保存后会重写成
    统一格式），但结构化内容与未涉及字段都完整保留。
    热词表单独排版，避免 yaml 默认的嵌套列表写法太难读。
    """
    data: dict = {}
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    for k, v in updates.items():
        if isinstance(v, dict) and isinstance(data.get(k), dict):
            data[k].update(v)
        else:
            data[k] = v

    hotwords = data.pop("hotwords", []) or []
    header = (
        "# RecordReg 配置\n"
        "# 本文件由「设置」界面维护，也可以手动编辑；保存设置时会重写格式。\n"
        "# 更详细的说明见 docs/DESIGN.md\n\n"
    )
    body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)

    lines = [header.rstrip("\n"), "", body.rstrip("\n")]
    if hotwords:
        lines.append("")
        lines.append("# 高频名词纠正：[\"识别成\", \"应该是\"]")
        lines.append("hotwords:")
        for entry in hotwords:
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                a = json.dumps(str(entry[0]), ensure_ascii=False)
                b = json.dumps(str(entry[1]), ensure_ascii=False)
                lines.append(f"  - [{a}, {b}]")
    else:
        lines.append("")
        lines.append("hotwords: []")
    lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


class SettingsDialog(QDialog):
    def __init__(self, cfg: Config, store=None, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        if store is None:
            from ..core.store import Store

            self.store = Store(cfg.paths.db)
            self._own_store = True
        else:
            self.store = store
            self._own_store = False
        self.setWindowTitle("设置")
        self.resize(680, 740)
        self._build()

    def closeEvent(self, event) -> None:
        if self._own_store:
            try:
                self.store.close()
            except Exception:  # noqa: BLE001
                pass
        super().closeEvent(event)

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(12)

        # --- 说话人 ---
        gb1 = QGroupBox("说话人分离")
        f1 = QFormLayout(gb1)
        f1.setContentsMargins(14, 16, 14, 12)
        f1.setSpacing(10)

        self.cmb_mode = QComboBox()
        self.cmb_mode.addItem("固定人数（2 人对谈，推荐）", "fixed_k")
        self.cmb_mode.addItem("自动估计人数（3 人以上会议）", "auto")
        idx = self.cmb_mode.findData(self.cfg.speaker.mode)
        self.cmb_mode.setCurrentIndex(max(idx, 0))
        f1.addRow("聚类模式", self.cmb_mode)

        self.spn_k = QSpinBox()
        self.spn_k.setRange(2, 8)
        self.spn_k.setValue(max(2, self.cfg.speaker.k))
        self.spn_k.setSuffix(" 人")
        f1.addRow("固定人数", self.spn_k)

        self.chk_smooth = QCheckBox("对聚类结果做平滑修正（推荐）")
        self.chk_smooth.setChecked(self.cfg.speaker.smoothing)
        f1.addRow("", self.chk_smooth)

        hint = QLabel("2 人对谈请用固定人数，自动模式容易把咳嗽/笑声也当成一个人。")
        hint.setStyleSheet("color:#8a919c;")
        hint.setWordWrap(True)
        f1.addRow("", hint)
        layout.addWidget(gb1)

        # --- 输出 ---
        gb2 = QGroupBox("输出")
        f2 = QFormLayout(gb2)
        f2.setContentsMargins(14, 16, 14, 12)
        f2.setSpacing(10)
        row = QHBoxLayout()
        self.edt_out = QLineEdit(str(self.cfg.paths.output))
        btn_browse = QPushButton("浏览…")
        btn_browse.setFixedWidth(72)
        btn_browse.clicked.connect(self._browse_out)
        row.addWidget(self.edt_out, 1)
        row.addWidget(btn_browse)
        f2.addRow("输出目录", row)
        layout.addWidget(gb2)

        # --- 热词表 ---
        gb3 = QGroupBox("高频名词纠正表")
        v3 = QVBoxLayout(gb3)
        v3.setContentsMargins(14, 16, 14, 12)
        v3.setSpacing(8)

        tip = QLabel("左侧填「识别错误的写法」，右侧填「正确写法」。转写完成后自动替换。")
        tip.setStyleSheet("color:#8a919c;")
        tip.setWordWrap(True)
        v3.addWidget(tip)

        self.tbl_word = QTableWidget(0, 2)
        self.tbl_word.setHorizontalHeaderLabels(["识别成", "应该是"])
        self.tbl_word.verticalHeader().setVisible(False)
        self.tbl_word.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tbl_word.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tbl_word.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_word.setMinimumHeight(190)
        v3.addWidget(self.tbl_word, 1)

        row3 = QHBoxLayout()
        btn_add = QPushButton("添加一行")
        btn_del = QPushButton("删除选中")
        btn_add.clicked.connect(lambda: self._add_word("", ""))
        btn_del.clicked.connect(self._del_word)
        row3.addWidget(btn_add)
        row3.addWidget(btn_del)
        row3.addStretch(1)
        v3.addLayout(row3)

        layout.addWidget(gb3, 1)

        # --- 索引数据库 ---
        gb4 = QGroupBox("索引数据库")
        v4 = QVBoxLayout(gb4)
        v4.setContentsMargins(14, 16, 14, 12)
        v4.setSpacing(8)

        self.lbl_db = QLabel("")
        v4.addWidget(self.lbl_db)

        row4 = QHBoxLayout()
        self.btn_purge = QPushButton("清理失效记录")
        self.btn_clear_db = QPushButton("清空索引")
        self.btn_clear_db.setObjectName("danger")
        self.btn_purge.clicked.connect(self._purge_db)
        self.btn_clear_db.clicked.connect(self._clear_db)
        row4.addWidget(self.btn_purge)
        row4.addWidget(self.btn_clear_db)
        row4.addStretch(1)
        v4.addLayout(row4)

        tip4 = QLabel(
            "索引只记录「哪些文件处理过」，不保存转写内容，几百条也就几 MB。\n"
            "清空后这些文件会被当作新文件重新识别，请确认不再需要跳过它们时再操作。"
        )
        tip4.setStyleSheet("color:#8a919c;")
        tip4.setWordWrap(True)
        v4.addWidget(tip4)
        layout.addWidget(gb4)

        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Save).setText("保存")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        self._load_words()
        self._refresh_db()

    # ---- 索引数据库 ----
    def _refresh_db(self) -> None:
        try:
            s = self.store.db_stats()
            self.lbl_db.setText(
                f"共 {s['total']} 条记录（完成 {s['done']} · 失败 {s['failed']}），"
                f"占用 {s['size_mb']:.2f} MB"
            )
        except Exception as e:  # noqa: BLE001
            self.lbl_db.setText(f"无法读取索引信息：{e}")

    def _purge_db(self) -> None:
        try:
            n = self.store.purge_missing()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "操作失败", str(e))
            return
        self._refresh_db()
        QMessageBox.information(
            self, "完成",
            f"已清理 {n} 条「源文件已不存在」的记录。" if n else "没有需要清理的记录。",
        )

    def _clear_db(self) -> None:
        try:
            total = self.store.db_stats()["total"]
        except Exception:  # noqa: BLE001
            total = 0
        if total == 0:
            QMessageBox.information(self, "提示", "索引已经是空的。")
            return
        if QMessageBox.question(
            self, "清空索引",
            f"将删除全部 {total} 条记录。\n\n"
            f"清空后，这些文件再次加入队列时会被当成新文件重新识别（可能很耗时）。\n"
            f"已生成的转写结果（output 目录里的 md / json）不会被删除。\n\n"
            f"确定清空吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        try:
            self.store.clear_all()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "操作失败", str(e))
            return
        self._refresh_db()
        QMessageBox.information(self, "已清空", f"已删除 {total} 条索引记录。")

    # ---- 热词 ----
    def _load_words(self) -> None:
        self.tbl_word.setRowCount(0)
        for entry in self.cfg.hotwords or []:
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                self._add_word(str(entry[0]), str(entry[1]))

    def _add_word(self, wrong: str, right: str) -> None:
        r = self.tbl_word.rowCount()
        self.tbl_word.insertRow(r)
        self.tbl_word.setItem(r, 0, QTableWidgetItem(wrong))
        self.tbl_word.setItem(r, 1, QTableWidgetItem(right))
        self.tbl_word.setCurrentCell(r, 0 if not wrong else 1)
        if not wrong:
            self.tbl_word.editItem(self.tbl_word.item(r, 0))

    def _del_word(self) -> None:
        rows = sorted({i.row() for i in self.tbl_word.selectedIndexes()}, reverse=True)
        for r in rows:
            self.tbl_word.removeRow(r)

    def _collect_words(self) -> list[list[str]]:
        out: list[list[str]] = []
        for r in range(self.tbl_word.rowCount()):
            a = (self.tbl_word.item(r, 0).text() if self.tbl_word.item(r, 0) else "").strip()
            b = (self.tbl_word.item(r, 1).text() if self.tbl_word.item(r, 1) else "").strip()
            if a and b:
                out.append([a, b])
        return out

    def _browse_out(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择输出目录", self.edt_out.text())
        if d:
            self.edt_out.setText(d)

    # ---- 保存 ----
    def _save(self) -> None:
        out_dir = Path(self.edt_out.text().strip())
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "目录不可用", f"无法使用该输出目录：\n{e}")
            return

        path = self.cfg.source or DEFAULT_CONFIG_PATH
        updates = {
            "speaker": {
                "mode": self.cmb_mode.currentData(),
                "k": int(self.spn_k.value()),
                "smoothing": bool(self.chk_smooth.isChecked()),
            },
            "paths": {"output": str(out_dir)},
            "hotwords": self._collect_words(),
        }
        try:
            update_config_file(Path(path), updates)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "保存失败", str(e))
            return
        self.accept()

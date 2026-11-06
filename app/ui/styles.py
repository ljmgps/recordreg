"""界面样式：浅色、克制、信息密度优先。"""

QSS = """
QWidget {
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
    color: #1f2328;
}
QMainWindow, QDialog {
    background: #f6f7f9;
}

/* ---- 顶部工具栏 ---- */
#Toolbar {
    background: #ffffff;
    border-bottom: 1px solid #e4e6ea;
}
QPushButton {
    background: #ffffff;
    border: 1px solid #d6d9df;
    border-radius: 6px;
    padding: 6px 14px;
    min-height: 18px;
}
QPushButton:hover  { background: #f2f5fa; border-color: #b9c2cf; }
QPushButton:pressed{ background: #e6ecf6; }
QPushButton:disabled { color: #a8adb5; background: #f4f5f7; border-color: #e6e8ec; }

QPushButton#primary {
    background: #2563eb; color: #ffffff; border: none; font-weight: 500;
}
QPushButton#primary:hover   { background: #1d4ed8; }
QPushButton#primary:disabled{ background: #aec3f4; color: #ffffff; }

QPushButton#danger { color: #b42318; border-color: #f0c2bd; }
QPushButton#danger:hover { background: #fdf1f0; }
QPushButton#danger:disabled { color: #cbc5c4; border-color: #e9e7e7; background: #f7f6f6; }

/* ---- 表格 ---- */
QTableWidget {
    background: #ffffff;
    border: 1px solid #e4e6ea;
    border-radius: 10px;
    gridline-color: #f2f3f5;
    selection-background-color: #e8effc;
    selection-color: #1f2328;
}
QTableWidget::item { padding: 8px 6px; border: none; }
QTableWidget::item:selected { background: #e8effc; }
QHeaderView::section {
    background: #fafbfc;
    border: none;
    border-bottom: 1px solid #e4e6ea;
    padding: 9px 6px;
    font-weight: 500;
    color: #5b6472;
}
QTableCornerButton::section { background: #fafbfc; border: none; }

/* ---- 日志 ---- */
QPlainTextEdit#Log {
    background: #1e2024;
    color: #d6d9de;
    border: 1px solid #2b2e33;
    border-radius: 10px;
    font-family: "Cascadia Mono", Consolas, monospace;
    font-size: 12px;
    padding: 8px;
}

/* ---- 进度条 ---- */
QProgressBar {
    border: none;
    border-radius: 5px;
    background: #e9ebef;
    height: 10px;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk { background: #2563eb; border-radius: 5px; }

QStatusBar {
    background: #ffffff;
    border-top: 1px solid #e4e6ea;
    color: #5b6472;
    padding: 2px 12px;
}
QStatusBar::item { border: none; }

/* ---- 输入控件 ---- */
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {
    background: #ffffff;
    border: 1px solid #d6d9df;
    border-radius: 6px;
    padding: 5px 8px;
    min-height: 18px;
}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus { border-color: #2563eb; }
QComboBox::drop-down { border: none; width: 20px; }

QCheckBox { spacing: 8px; }

QGroupBox {
    border: 1px solid #e4e6ea;
    border-radius: 8px;
    margin-top: 14px;
    padding-top: 10px;
    font-weight: 500;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
    color: #5b6472;
}

QSplitter::handle { background: transparent; height: 8px; }

QScrollBar:vertical {
    background: transparent; width: 10px; margin: 2px;
}
QScrollBar::handle:vertical {
    background: #cfd3da; border-radius: 5px; min-height: 30px;
}
QScrollBar::handle:vertical:hover { background: #b6bcc6; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }

QTextBrowser {
    background: #ffffff;
    border: 1px solid #e4e6ea;
    border-radius: 10px;
    padding: 16px;
}
"""

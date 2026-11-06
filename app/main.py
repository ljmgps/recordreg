"""GUI 入口。

    .venv\\Scripts\\pythonw.exe app\\main.py          正常启动
    .venv\\Scripts\\python.exe  app\\main.py --smoke-test   启动、截图、自动退出（自检用）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import PROJECT_ROOT, load_config, setup_runtime_env  # noqa: E402


def _install_excepthook() -> None:
    """pythonw 启动时没有控制台，异常必须落盘，否则窗口一关什么都查不到。"""

    def _hook(exc_type, exc, tb):
        import traceback

        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            p = PROJECT_ROOT / "logs" / "gui_error.log"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _hook


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="recordreg-gui")
    p.add_argument("-c", "--config", default=None, help="配置文件路径")
    p.add_argument("--smoke-test", action="store_true", help="启动后截图并退出，用于自检")
    p.add_argument("--smoke-add", default=None, help="冒烟测试时自动添加该目录下的文件")
    p.add_argument("--smoke-run", action="store_true", help="冒烟测试时自动开始处理")
    args = p.parse_args(argv)

    _install_excepthook()
    setup_runtime_env()

    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from app.ui.main_window import MainWindow
    from app.ui.styles import QSS

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv[:1])
    app.setApplicationName("RecordReg")
    app.setStyleSheet(QSS)

    cfg = load_config(args.config)
    win = MainWindow(cfg)
    win.show()

    if args.smoke_test:
        out = PROJECT_ROOT / "logs" / "ui_smoke.png"
        out.parent.mkdir(parents=True, exist_ok=True)

        if args.smoke_add:
            from pathlib import Path as _P

            win.add_paths([_P(args.smoke_add)])
        if args.smoke_run:
            QTimer.singleShot(300, lambda: win._start(force=True))

        def _shot() -> None:
            try:
                win.grab().save(str(out))
                print(f"smoke ok -> {out}")
            except Exception as e:  # noqa: BLE001
                print(f"smoke shot failed: {e}")
            if win.worker is not None and win.worker.isRunning():
                win.worker.stop()
                win.worker.wait(120000)
            app.quit()

        delay = 26000 if args.smoke_run else 1200
        QTimer.singleShot(delay, _shot)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

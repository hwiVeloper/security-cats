"""GUI 시작점."""
import ctypes
import logging
import tkinter as tk
import traceback
from tkinter import messagebox

from .. import APP_NAME, paths
from ..core import config as cfgmod
from . import theme
from .main_window import MainWindow


def _setup_logging():
    paths.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=paths.LOGS_DIR / "iptrace.log", level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def run():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    paths.ensure_runtime_files()
    _setup_logging()
    root = tk.Tk()
    root.withdraw()
    try:
        cfg = cfgmod.load()
    except cfgmod.ConfigError as e:
        messagebox.showerror(APP_NAME, f"{e}\n설정 파일을 고치거나 지운 뒤 다시 실행하세요.")
        return

    def report_error(exc, val, tb):
        logging.error("".join(traceback.format_exception(exc, val, tb)))
        messagebox.showerror(APP_NAME, f"오류가 났습니다: {val}\n자세한 내용은 logs\\iptrace.log 를 확인하세요.", parent=root)

    root.report_callback_exception = report_error
    theme.setup(root, cfg["general"].get("theme", "light"))
    MainWindow(root, cfg)
    root.deiconify()
    root.mainloop()


if __name__ == "__main__":
    run()

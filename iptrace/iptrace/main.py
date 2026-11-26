"""exe 진입점: 실행 파일 이름이 -cli로 끝나거나 인자가 있으면 CLI, 아니면 GUI.

화면용 exe는 콘솔 창이 없어서 시작하다 오류가 나면 아무것도 안 보이고 꺼진다.
그래서 시작 중 오류는 exe 옆(쓸 수 없으면 TEMP 폴더)의 iptrace_error.log에 남기고 메시지 창으로 알려 준다.
"""
import os
import sys
import tempfile
import traceback
from datetime import datetime


def _error_log_path() -> str:
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.getcwd()
    for folder in (base, tempfile.gettempdir()):
        path = os.path.join(folder, "iptrace_error.log")
        try:
            with open(path, "a", encoding="utf-8"):
                return path
        except OSError:
            continue
    return ""


def report_startup_error(exc: BaseException) -> None:
    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    path = _error_log_path()
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} =====\n")
            f.write(f"exe: {sys.executable}\npython: {sys.version}\n{detail}")
    msg = (f"IPtrace를 시작하지 못했습니다.\n\n원인: {type(exc).__name__}: {exc}\n\n"
           f"자세한 내용: {path or '(기록 실패)'}")
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, "IPtrace 시작 오류", 0x10)
    except Exception:  # noqa: BLE001 - 메시지 창을 못 띄우면 표준 오류로라도 남긴다
        print(msg, file=sys.stderr)


def main():
    exe = os.path.splitext(os.path.basename(sys.argv[0]))[0].lower()
    if exe.endswith("-cli") or len(sys.argv) > 1:
        from .cli import main as cli_main
        sys.exit(cli_main())
    try:
        from .gui.app import run
        run()
    except SystemExit:
        raise
    except BaseException as e:  # noqa: BLE001 - 시작 실패 원인을 사용자에게 보여주기 위해 모두 받는다
        report_startup_error(e)
        sys.exit(1)


if __name__ == "__main__":
    main()

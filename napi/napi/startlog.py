"""시작 기록(napi.log)과 화면 로드 감시.

일부 PC에서 창이 하얗게 빈 채로 멈추는 문제를 재현 없이 추적하려고 둔다.
PyInstaller windowed 빌드에는 콘솔이 없어서 pywebview가 남기는 오류(예: WebView2 초기화 실패)가
어디에도 보이지 않으므로, 그 로그와 시작 단계를 파일로 남긴다. 기록은 매번 새로 쓰고
직전 실행 하나만 napi.log.1 로 남긴다. 여기 함수들은 앱 시작을 막지 않도록 예외를 내지 않는다.
"""
from __future__ import annotations

import logging
import os
import platform
import sys
import threading
from collections.abc import Callable
from pathlib import Path

LOG_NAME = "napi.log"
_LOGGERS = ("napi", "pywebview")
# WebView2 동작을 바꾸는 환경 변수. 사용자가 직접 걸어 둔 값이 있으면 기록에 남긴다.
_WEBVIEW2_ENV = (
    "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
    "WEBVIEW2_BROWSER_EXECUTABLE_FOLDER",
    "WEBVIEW2_USER_DATA_FOLDER",
    "WEBVIEW2_RELEASE_CHANNEL_PREFERENCE",
)

log = logging.getLogger("napi")


class _FirstError(logging.Handler):
    """처음 기록된 ERROR 이상 메시지를 기억한다 (로드 실패 안내에 보여 줄 원인)."""

    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.message: str | None = None

    def emit(self, record: logging.LogRecord) -> None:
        if self.message is None:
            try:
                self.message = record.getMessage().strip()
            except Exception:
                self.message = str(record.msg)


first_error = _FirstError()
_file_handler: logging.Handler | None = None


def _detach() -> None:
    global _file_handler
    for name in _LOGGERS:
        lg = logging.getLogger(name)
        lg.removeHandler(first_error)
        if _file_handler is not None:
            lg.removeHandler(_file_handler)
    if _file_handler is not None:
        _file_handler.close()
        _file_handler = None


def setup(path: Path) -> Path | None:
    """기록 파일을 연다. 열지 못하면 None (앱은 그대로 뜬다). 다시 부르면 앞의 기록을 닫고 새로 연다."""
    global _file_handler
    _detach()
    first_error.message = None
    for name in _LOGGERS:  # 파일을 못 열어도 원인 안내에 쓸 첫 오류는 모은다
        lg = logging.getLogger(name)
        lg.addHandler(first_error)
        if lg.level == logging.NOTSET or lg.level > logging.INFO:
            lg.setLevel(logging.INFO)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            os.replace(path, path.with_name(path.name + ".1"))
        handler = logging.FileHandler(path, mode="w", encoding="utf-8")
    except OSError:
        return None
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    _file_handler = handler
    for name in _LOGGERS:
        logging.getLogger(name).addHandler(handler)
    return path


def environment(webview2: str | None) -> str:
    """기록 첫 줄에 남길 실행 환경 요약."""
    from napi import __version__

    parts = [
        f"napi {__version__}",
        f"{platform.platform()} {platform.machine()}",
        f"python {platform.python_version()}",
        "exe" if getattr(sys, "frozen", False) else "source",
        f"WebView2 {webview2 or '없음'}",
    ]
    for name in _WEBVIEW2_ENV:
        value = os.environ.get(name)
        if value:
            parts.append(f"{name}={value}")
    return " | ".join(parts)


class LoadWatch:
    """페이지가 loaded 를 알리지 않은 채 시간이 지나면 한 번 알린다."""

    def __init__(self, on_timeout: Callable[[], None]) -> None:
        self._on_timeout = on_timeout
        self._lock = threading.Lock()
        self._loaded = False
        self._fired = False

    def loaded(self) -> None:
        with self._lock:
            first = not self._loaded
            self._loaded = True
        if first:
            log.info("페이지 로드 완료")

    def timeout(self) -> None:
        with self._lock:
            if self._loaded or self._fired:
                return
            self._fired = True
        log.error("시간 안에 페이지가 로드되지 않았어요")
        try:
            self._on_timeout()
        except Exception:
            log.exception("로드 실패 안내를 띄우지 못했어요")


def not_loaded_message(log_path: Path | None) -> str:
    """화면이 뜨지 않을 때 보여 줄 안내."""
    lines = ["napi 화면을 불러오지 못하고 있어요.", ""]
    if first_error.message:
        reason = first_error.message.splitlines()[0][:300]
        lines += [f"원인: {reason}", ""]
    lines += [
        "조금 뒤 화면이 나타나면 이 창은 닫아도 돼요.",
        "계속 하얀 화면이면 아래 기록 파일을 담당자에게 보내 주세요.",
    ]
    if log_path is not None:
        lines.append(str(log_path))
    return "\n".join(lines)

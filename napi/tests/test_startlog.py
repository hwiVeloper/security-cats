"""시작 기록(napi.log)과 화면 로드 감시."""
from __future__ import annotations

import logging
import sys

import pytest

from napi import __main__ as entry
from napi import startlog
from napi.api import Api


@pytest.fixture(autouse=True)
def _detach_after():
    yield
    startlog._detach()  # noqa: SLF001 — 다른 테스트로 파일 핸들러가 새지 않게


def test_setup_writes_napi_and_pywebview_logs(tmp_path):
    path = startlog.setup(tmp_path / "napi.log")
    assert path == tmp_path / "napi.log"
    logging.getLogger("napi").info("시작 기록")
    logging.getLogger("pywebview").error("WebView2 initialization failed with exception:\n boom")
    text = path.read_text(encoding="utf-8")
    assert "INFO [napi] 시작 기록" in text
    assert "ERROR [pywebview] WebView2 initialization failed" in text
    assert startlog.first_error.message.startswith("WebView2 initialization failed")


def test_setup_keeps_previous_run_and_resets_first_error(tmp_path):
    path = tmp_path / "napi.log"
    startlog.setup(path)
    logging.getLogger("napi").error("첫 실행 오류")
    startlog.setup(path)
    assert startlog.first_error.message is None
    logging.getLogger("napi").info("두 번째 실행")
    assert "첫 실행 오류" in (tmp_path / "napi.log.1").read_text(encoding="utf-8")
    text = path.read_text(encoding="utf-8")
    assert "두 번째 실행" in text and "첫 실행 오류" not in text
    handlers = logging.getLogger("napi").handlers
    assert sum(isinstance(h, logging.FileHandler) for h in handlers) == 1  # 다시 열어도 하나


def test_setup_failure_returns_none_but_still_collects_errors(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    assert startlog.setup(blocker / "napi.log") is None  # 폴더 자리에 파일이 있다
    logging.getLogger("pywebview").error("init failed")
    assert startlog.first_error.message == "init failed"


def test_environment_mentions_webview2_and_overrides(monkeypatch):
    monkeypatch.setenv("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "--disable-gpu")
    text = startlog.environment("131.0.2903.70")
    assert "napi " in text and "WebView2 131.0.2903.70" in text
    assert "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--disable-gpu" in text
    monkeypatch.delenv("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS")
    assert "WebView2 없음" in startlog.environment(None)


def test_load_watch_fires_once_when_not_loaded():
    calls = []
    watch = startlog.LoadWatch(lambda: calls.append(1))
    watch.timeout()
    watch.timeout()
    watch.loaded()  # 늦게라도 뜨면 기록만 남는다
    assert calls == [1]


def test_load_watch_silent_after_loaded():
    calls = []
    watch = startlog.LoadWatch(lambda: calls.append(1))
    watch.loaded()
    watch.timeout()
    assert calls == []


def test_load_watch_swallows_callback_error():
    def boom():
        raise RuntimeError("no message box")

    startlog.LoadWatch(boom).timeout()


def test_not_loaded_message(tmp_path):
    startlog.setup(tmp_path / "napi.log")
    assert "원인" not in startlog.not_loaded_message(None)
    logging.getLogger("pywebview").error("WebView2 initialization failed with exception:\n detail")
    text = startlog.not_loaded_message(tmp_path / "napi.log")
    assert "원인: WebView2 initialization failed with exception:" in text
    assert "detail" not in text  # 첫 줄만
    assert text.endswith(str(tmp_path / "napi.log"))


# ---------------------------------------------------------------- __main__


def test_webview2_version_picks_highest():
    values = {
        ("HKEY_LOCAL_MACHINE", entry._DOTNET_KEY, "Release"): 528040,
        ("HKEY_CURRENT_USER",
         r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}", "pv"): "120.0.1.2",
        ("HKEY_LOCAL_MACHINE",
         r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
         "pv"): "131.0.2903.70",
    }

    def read(hive, path, name):
        return values.get((hive, path, name))

    assert entry.webview2_version(read, machine="AMD64") == (131, 0, 2903, 70)
    assert entry.webview2_version(lambda *a: None, machine="AMD64") is None


class _Handlers:
    def __init__(self):
        self.items = []

    def __iadd__(self, fn):
        self.items.append(fn)
        return self


class _Webview:
    def __init__(self):
        self.window = None
        self.start_func = None

    def create_window(self, title, **kw):
        win = type("W", (), {})()
        win.events = type("E", (), {})()
        win.events.closed = _Handlers()
        win.events.loaded = _Handlers()
        win.events.shown = _Handlers()
        win.real_url = "http://127.0.0.1:12345/index.html"
        self.window = win
        return win

    def start(self, func=None, **kw):
        self.start_func = func


@pytest.fixture
def started(monkeypatch, tmp_path):
    """main()을 가짜 webview로 돌리고 (webview, 안내 창 목록, 시간 초과 콜백)을 돌려준다."""
    monkeypatch.setenv("NAPI_HOME", str(tmp_path))
    monkeypatch.setitem(sys.modules, "pyi_splash", None)
    fake = _Webview()
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr(entry, "webview2_available", lambda: True)
    boxes = []
    monkeypatch.setattr(entry, "_message_box", lambda text, flags=0x10: boxes.append((text, flags)))
    monkeypatch.setattr(Api, "_shutdown", lambda self: None)
    timeouts = []
    monkeypatch.setattr(entry, "_close_splash_later",
                        lambda delay=None, then=None: timeouts.append(then))
    entry.main()
    fake.start_func()  # GUI 루프가 시작되면 pywebview가 부른다
    return fake, boxes, timeouts, tmp_path / "napi.log"


def test_main_writes_start_log(started):
    fake, _, _, log_path = started
    for fn in fake.window.events.shown.items:
        fn()
    for fn in fake.window.events.loaded.items:
        fn()
    text = log_path.read_text(encoding="utf-8")
    assert "시작: napi " in text
    assert "창 표시: http://127.0.0.1:12345/index.html" in text
    assert "페이지 로드 완료" in text


def test_main_warns_when_page_never_loads(started):
    fake, boxes, timeouts, log_path = started
    logging.getLogger("pywebview").error("WebView2 initialization failed with exception:\n x")
    (then,) = timeouts
    then()
    ((text, flags),) = boxes
    assert "화면을 불러오지 못하고 있어요" in text
    assert "원인: WebView2 initialization failed" in text
    assert str(log_path) in text
    assert flags == 0x30 | 0x40000  # 경고 + 항상 위
    assert "시간 안에 페이지가 로드되지 않았어요" in log_path.read_text(encoding="utf-8")


def test_main_no_warning_when_page_loaded(started):
    fake, boxes, timeouts, _ = started
    for fn in fake.window.events.loaded.items:
        fn()
    timeouts[0]()
    assert boxes == []

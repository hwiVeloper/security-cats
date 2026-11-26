"""앱 진입점: python -m napi"""
from __future__ import annotations

import platform
import sys
import threading
import traceback
from collections.abc import Callable
from pathlib import Path

from napi import startlog

TITLE = "napi — 잠자는 API를 찾아요"
WEBVIEW2_MISSING = ("WebView2 런타임이 필요해요. "
                    "https://go.microsoft.com/fwlink/p/?LinkId=2124703 에서 설치해 주세요")

# pywebview 6.2.1 winforms._is_chromium()과 같은 기준이다. pywebview는 WebView2가 없으면
# 예외 없이 MSHTML(IE)로 넘어가므로, 창을 만들기 전에 직접 확인한다.
_DOTNET_KEY = r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full"
_DOTNET_MIN_RELEASE = 394802  # .NET 4.6.2
_WEBVIEW2_CLIENTS = (
    "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",  # runtime
    "{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}",  # beta
    "{0D50BFEC-CD6A-4F9A-964C-C7416E3ACB10}",  # dev
    "{65C35B14-6C1D-4122-AC46-7148CC9D6497}",  # canary
)
_WEBVIEW2_MIN = (86, 0, 622, 0)
BACKGROUND = "#FBF7F2"  # app.css --bg. 페이지가 그려지기 전 흰 화면 대신 보이는 창 배경색
SPLASH_TIMEOUT = 15.0  # 창이 loaded 를 못 알려도 로딩 화면은 이 시간(초) 뒤에 닫는다

RegistryReader = Callable[[str, str, str], object]


_splash_lock = threading.Lock()


def splash(text: str | None = None, pct: int = 0, close: bool = False) -> None:
    """exe 로딩 화면(PyInstaller 스플래시)의 진행률·문구 갱신 / 닫기. 절대 예외를 내지 않는다.

    로딩 화면은 exe 압축 해제 전에 부트로더가 띄우고, 압축 해제 동안 0~75%를 채운다.
    이후 단계는 "@<퍼센트>|<문구>" 형식으로 보내 75~100%를 채운다 (해석은 napi.spec 의 SPLASH_PROGRESS_TCL).
    소스 실행(python -m napi)에는 pyi_splash 모듈이 없으므로 아무 것도 하지 않는다.
    loaded 이벤트 스레드와 시간 초과 타이머가 함께 부를 수 있어서 잠금으로 순서를 지킨다.
    """
    try:
        import pyi_splash  # type: ignore[import-not-found]
    except Exception:
        return
    try:
        with _splash_lock:
            if not pyi_splash.is_alive():
                return
            if close:
                pyi_splash.close()
            elif text:
                pyi_splash.update_text(f"@{int(pct)}|{text}")
    except Exception:
        pass


def _close_splash() -> None:
    splash(close=True)


def _close_splash_later(delay: float = SPLASH_TIMEOUT,
                        then: Callable[[], None] | None = None) -> None:
    """webview.start 가 GUI 루프를 시작한 뒤 부른다. loaded 가 오지 않아도 로딩 화면이 남지 않게 한다.

    then 은 로딩 화면을 닫은 뒤 이어서 부른다 (페이지가 아직 안 떴으면 안내).
    """
    def fire() -> None:
        _close_splash()
        if then is not None:
            then()

    timer = threading.Timer(delay, fire)
    timer.daemon = True  # 창을 먼저 닫아도 프로세스 종료를 붙잡지 않게
    timer.start()


def ui_index_path() -> Path:
    """PyInstaller 번들에서는 sys._MEIPASS/napi/ui, 아니면 패키지 안의 ui."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "napi" / "ui" / "index.html"
    return Path(__file__).parent / "ui" / "index.html"


def _read_registry(hive: str, path: str, name: str) -> object:
    try:
        import winreg
        with winreg.OpenKey(getattr(winreg, hive), path) as key:
            return winreg.QueryValueEx(key, name)[0]
    except (OSError, ImportError, AttributeError):
        return None


def _version(value: object) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in str(value).split("."))
    except ValueError:
        return ()


def webview2_version(read: RegistryReader = _read_registry,
                     machine: str | None = None) -> tuple[int, ...] | None:
    """pywebview가 edgechromium으로 쓸 WebView2 버전 중 가장 높은 것. 쓸 수 없으면 None."""
    release = read("HKEY_LOCAL_MACHINE", _DOTNET_KEY, "Release")
    if not isinstance(release, int) or release < _DOTNET_MIN_RELEASE:
        return None
    machine = platform.machine() if machine is None else machine
    hklm = r"Microsoft\EdgeUpdate" if machine == "x86" else r"WOW6432Node\Microsoft\EdgeUpdate"
    best: tuple[int, ...] = ()
    for client in _WEBVIEW2_CLIENTS:
        for hive, base in (("HKEY_CURRENT_USER", r"Microsoft\EdgeUpdate"),
                           ("HKEY_LOCAL_MACHINE", hklm)):
            best = max(best, _version(read(hive, rf"SOFTWARE\{base}\Clients\{client}", "pv")))
    return best if best >= _WEBVIEW2_MIN else None


def webview2_available(read: RegistryReader = _read_registry,
                       machine: str | None = None) -> bool:
    """pywebview가 edgechromium을 쓸 수 있으면 True."""
    return webview2_version(read, machine) is not None


def _message_box(text: str, flags: int = 0x10) -> None:  # MB_ICONERROR
    import ctypes
    ctypes.windll.user32.MessageBoxW(None, text, "napi", flags)


_MB_WARNING_TOPMOST = 0x30 | 0x40000  # MB_ICONWARNING | MB_TOPMOST: 하얀 창 뒤에 숨지 않게


def _start_log() -> Path | None:
    """시작 기록을 열고 실행 환경을 남긴다. 실패해도 앱은 뜬다."""
    try:
        from napi.paths import app_dir
        path = startlog.setup(app_dir() / startlog.LOG_NAME)
        version = webview2_version()
        startlog.log.info("시작: %s", startlog.environment(
            ".".join(map(str, version)) if version else None))
        return path
    except Exception:
        return None


def main() -> None:
    splash("설정을 불러오는 중…", 78)
    log_path = _start_log()
    if not webview2_available():
        startlog.log.error("WebView2 런타임을 찾지 못했어요")
        splash(close=True)  # 로딩 화면(항상 위)이 안내 창을 가리지 않게 먼저 닫는다
        _message_box(WEBVIEW2_MISSING)
        raise SystemExit(1)

    import webview

    from napi.api import Api

    api = Api()
    splash("화면을 준비하는 중…", 85)
    window = webview.create_window(TITLE, url=str(ui_index_path()), js_api=api,
                                   width=1280, height=820, min_size=(1000, 640),
                                   background_color=BACKGROUND)
    # 창이 닫히면 진행 중인 스캔을 취소해서 프로세스가 원격 명령을 붙든 채 남지 않게 한다.
    window.events.closed += api._shutdown
    # 페이지가 로드되면 로딩 화면을 닫는다. 첫 렌더 전까지는 index.html 의 로딩 표시가 이어받는다.
    window.events.loaded += _close_splash
    # 일부 PC에서는 WebView2가 초기화·로드에 실패해도 pywebview가 아무것도 알리지 않아 창이 빈 채로 남는다.
    # 시간 안에 loaded 가 오지 않으면 원인과 기록 파일 위치를 알린다.
    watch = startlog.LoadWatch(
        lambda: _message_box(startlog.not_loaded_message(log_path), _MB_WARNING_TOPMOST))
    window.events.loaded += watch.loaded
    window.events.shown += lambda: startlog.log.info(
        "창 표시: %s", getattr(window, "real_url", None))
    window.events.closed += lambda: startlog.log.info("창 닫힘")
    try:
        webview.start(lambda: _close_splash_later(then=watch.timeout), gui="edgechromium")
    except Exception as e:
        startlog.log.exception("webview.start 실패")
        splash(close=True)
        if sys.stderr is not None:  # PyInstaller windowed 빌드에서는 stderr가 없다
            traceback.print_exc()
        _message_box(f"napi를 시작하지 못했어요: {type(e).__name__}")
        raise SystemExit(1)
    finally:
        api._shutdown()


if __name__ == "__main__":
    main()

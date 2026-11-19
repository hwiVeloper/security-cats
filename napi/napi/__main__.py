"""앱 진입점: python -m napi"""
from __future__ import annotations

import platform
import sys
import traceback
from collections.abc import Callable
from pathlib import Path

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

RegistryReader = Callable[[str, str, str], object]


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


def webview2_available(read: RegistryReader = _read_registry,
                       machine: str | None = None) -> bool:
    """pywebview가 edgechromium을 쓸 수 있으면 True."""
    release = read("HKEY_LOCAL_MACHINE", _DOTNET_KEY, "Release")
    if not isinstance(release, int) or release < _DOTNET_MIN_RELEASE:
        return False
    machine = platform.machine() if machine is None else machine
    hklm = r"Microsoft\EdgeUpdate" if machine == "x86" else r"WOW6432Node\Microsoft\EdgeUpdate"
    for client in _WEBVIEW2_CLIENTS:
        for hive, base in (("HKEY_CURRENT_USER", r"Microsoft\EdgeUpdate"),
                           ("HKEY_LOCAL_MACHINE", hklm)):
            pv = read(hive, rf"SOFTWARE\{base}\Clients\{client}", "pv")
            if _version(pv) >= _WEBVIEW2_MIN:
                return True
    return False


def _message_box(text: str) -> None:
    import ctypes
    ctypes.windll.user32.MessageBoxW(None, text, "napi", 0x10)  # MB_ICONERROR


def main() -> None:
    if not webview2_available():
        _message_box(WEBVIEW2_MISSING)
        raise SystemExit(1)

    import webview

    from napi.api import Api

    api = Api()
    window = webview.create_window(TITLE, url=str(ui_index_path()), js_api=api,
                                   width=1280, height=820, min_size=(1000, 640))
    # 창이 닫히면 진행 중인 스캔을 취소해서 프로세스가 원격 명령을 붙든 채 남지 않게 한다.
    window.events.closed += api._shutdown
    try:
        webview.start(gui="edgechromium")
    except Exception as e:
        if sys.stderr is not None:  # PyInstaller windowed 빌드에서는 stderr가 없다
            traceback.print_exc()
        _message_box(f"napi를 시작하지 못했어요: {type(e).__name__}")
        raise SystemExit(1)
    finally:
        api._shutdown()


if __name__ == "__main__":
    main()

"""앱 진입점: python -m napi"""
from __future__ import annotations

import sys
from pathlib import Path

TITLE = "napi — 잠자는 API를 찾아요"
WEBVIEW2_MISSING = ("WebView2 런타임이 필요해요. "
                    "https://go.microsoft.com/fwlink/p/?LinkId=2124703 에서 설치해 주세요")


def ui_index_path() -> Path:
    """PyInstaller 번들에서는 sys._MEIPASS/napi/ui, 아니면 패키지 안의 ui."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "napi" / "ui" / "index.html"
    return Path(__file__).parent / "ui" / "index.html"


def main() -> None:
    import webview

    from napi.api import Api

    webview.create_window(TITLE, url=str(ui_index_path()), js_api=Api(),
                          width=1280, height=820, min_size=(1000, 640))
    try:
        webview.start(gui="edgechromium")
    except Exception:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, WEBVIEW2_MISSING, "napi", 0x10)
        raise SystemExit(1)


if __name__ == "__main__":
    main()

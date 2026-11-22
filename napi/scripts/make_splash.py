"""assets/splash.png 생성 — napi.exe 시작 시 바로 뜨는 로딩 화면 이미지.

사용: .venv/Scripts/python scripts/make_splash.py  (결과 파일은 저장소에 포함)
맑은 고딕(Windows 기본)으로 그리고, 없으면 굴림 → Pillow 기본 글꼴 순으로 대신한다.
진행 막대·단계 문구·퍼센트는 이미지에 넣지 않고 napi.spec 의 Tcl 스크립트(SPLASH_PROGRESS_TCL)가 그린다.
아래 BAR_* / TEXT_Y 좌표는 napi.spec 과 같아야 한다.
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_icon import draw as draw_cat  # noqa: E402

OUT = ROOT / "assets" / "splash.png"
W, H = 560, 300
BG = (0xFB, 0xF7, 0xF2)
BORDER = (0xEF, 0xE5, 0xDA)
INK = (0x3A, 0x2A, 0x20)
MUTED = (0x8A, 0x7A, 0x6E)
DIM = (0xB0, 0xA2, 0x96)
ACCENT = (0xF2, 0x9A, 0x3F)

# napi.spec 의 SPLASH_PROGRESS_TCL 과 맞출 좌표 (여기서는 비워 둔다)
BAR_X0, BAR_X1, BAR_Y0, BAR_Y1 = 40, 520, 212, 218
TEXT_Y = 246  # 단계 문구(왼쪽)·퍼센트(오른쪽)의 아래 기준선
FOOTER_Y = 262

FONT_DIR = Path(r"C:\Windows\Fonts")


def _font(bold: bool, size: int):
    for name in (("malgunbd.ttf",) if bold else ()) + ("malgun.ttf", "gulim.ttc"):
        p = FONT_DIR / name
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


def _version() -> str:
    try:
        with open(ROOT / "pyproject.toml", "rb") as f:
            return str(tomllib.load(f)["project"]["version"])
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return ""


def make() -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W - 1, H - 1], outline=BORDER, width=1)
    d.rectangle([0, 0, W - 1, 3], fill=ACCENT)

    cat = draw_cat().resize((112, 112), Image.LANCZOS)
    img.paste(cat, (34, 44), cat)

    d.text((162, 50), "napi", font=_font(True, 46), fill=INK)
    d.text((165, 116), "잠자는 API를 찾아요", font=_font(False, 17), fill=MUTED)

    # 진행 막대(BAR_*), 단계 문구·퍼센트(TEXT_Y)는 Tcl 이 그린다. 여기는 비워 둔다.
    small = _font(False, 11)
    d.text((BAR_X0, FOOTER_Y), "원본 로그는 서버 밖으로 나가지 않아요 · 비밀번호는 저장하지 않아요",
           font=small, fill=DIM)
    ver = _version()
    if ver:
        ver = f"v{ver}"
        d.text((BAR_X1 - d.textlength(ver, font=small), FOOTER_Y), ver, font=small, fill=DIM)
    return img


def main() -> None:
    make().save(OUT)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()

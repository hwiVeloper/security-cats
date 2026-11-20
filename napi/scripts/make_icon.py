"""assets/napi.svg(잠든 냅냥 얼굴)를 Pillow로 그려 assets/napi.ico를 만든다.

사용: .venv/Scripts/python scripts/make_icon.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT_ICO = ROOT / "assets" / "napi.ico"

SIZE = 256
SS = 4  # 슈퍼샘플링 배율
VIEW = (36, 36, 128, 132)  # SVG viewBox (x, y, w, h)
BROWN = (0x5A, 0x3D, 0x2B, 255)
CREAM = (0xFF, 0xFA, 0xF3, 255)
ORANGE = (0xF6, 0xA5, 0x4C, 255)
PINK_IN = (0xFF, 0xC6, 0xC9, 255)
BLUSH = (0xFF, 0x9E, 0xB0, int(255 * .6))
NOSE = (0xFF, 0x8F, 0xA3, 255)
HEAD = [(46, 118), (46, 80, 70, 68, 100, 68), (130, 68, 154, 80, 154, 118),
        (154, 148, 132, 160, 100, 160), (68, 160, 46, 148, 46, 118)]


def _cubic(p0, p1, p2, p3, n=40):
    return [((1 - t) ** 3 * p0[0] + 3 * (1 - t) ** 2 * t * p1[0] + 3 * (1 - t) * t * t * p2[0] + t ** 3 * p3[0],
             (1 - t) ** 3 * p0[1] + 3 * (1 - t) ** 2 * t * p1[1] + 3 * (1 - t) * t * t * p2[1] + t ** 3 * p3[1])
            for t in (i / n for i in range(n + 1))]


def _quad(p0, p1, p2, n=24):
    return [((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0],
             (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1])
            for t in (i / n for i in range(n + 1))]


def _head_points():
    pts, cur = [], HEAD[0]
    for seg in HEAD[1:]:
        pts += _cubic(cur, seg[0:2], seg[2:4], seg[4:6])
        cur = seg[4:6]
    return pts


class Canvas:
    def __init__(self):
        self.k = SIZE * SS / VIEW[3]
        self.ox = (SIZE * SS - VIEW[2] * self.k) / 2
        self.img = Image.new("RGBA", (SIZE * SS, SIZE * SS), (0, 0, 0, 0))

    def p(self, pt):
        return ((pt[0] - VIEW[0]) * self.k + self.ox, (pt[1] - VIEW[1]) * self.k)

    def layer(self):
        return Image.new("RGBA", self.img.size, (0, 0, 0, 0))

    def composite(self, layer):
        self.img = Image.alpha_composite(self.img, layer)

    def poly(self, pts, fill=None, outline=None, width=0, mask=None):
        layer = self.layer()
        d = ImageDraw.Draw(layer)
        xy = [self.p(q) for q in pts]
        if fill:
            d.polygon(xy, fill=fill)
        if outline:
            self._stroke(d, xy + [xy[0]], width, outline)
        if mask is not None:
            clipped = self.layer()
            clipped.paste(layer, mask=mask)
            layer = clipped
        self.composite(layer)

    def ellipse(self, cx, cy, rx, ry, fill, mask=None):
        layer = self.layer()
        (x0, y0), (x1, y1) = self.p((cx - rx, cy - ry)), self.p((cx + rx, cy + ry))
        ImageDraw.Draw(layer).ellipse([x0, y0, x1, y1], fill=fill)
        if mask is not None:
            clipped = self.layer()
            clipped.paste(layer, mask=mask)
            layer = clipped
        self.composite(layer)

    def line(self, pts, width, color):
        layer = self.layer()
        self._stroke(ImageDraw.Draw(layer), [self.p(q) for q in pts], width, color, caps=True)
        self.composite(layer)

    def _stroke(self, d, xy, width, color, caps=False):
        w = max(1, round(width * self.k))
        d.line(xy, fill=color, width=w, joint="curve")
        r = w / 2
        for x, y in (xy if caps else xy[:-1]):  # 둥근 이음/끝
            d.ellipse([x - r, y - r, x + r, y + r], fill=color)


def draw() -> Image.Image:
    c = Canvas()
    # 왼쪽 귀(주황), 오른쪽 귀(크림)
    left = [(58, 96), (52, 54)] + _quad((52, 54), (53, 45), (61, 49)) + [(92, 74)]
    right = [(108, 74), (139, 49)] + _quad((139, 49), (147, 45), (148, 54)) + [(142, 96)]
    c.poly(left, ORANGE, BROWN, 4)
    c.poly([(62, 86), (59, 61), (80, 76)], PINK_IN)
    c.poly(right, CREAM, BROWN, 4)
    c.poly([(120, 76), (141, 61), (138, 86)], PINK_IN)
    head = _head_points()
    c.poly(head, CREAM)
    # 머리 모양으로 자른 주황 얼룩
    mask = Image.new("L", c.img.size, 0)
    ImageDraw.Draw(mask).polygon([c.p(q) for q in head], fill=255)
    c.ellipse(62, 76, 30, 22, ORANGE, mask)
    c.ellipse(128, 70, 10, 7, ORANGE[:3] + (230,), mask)
    c.poly(head, None, BROWN, 4)
    # 볼터치, 코
    c.ellipse(66, 132, 10, 6, BLUSH)
    c.ellipse(134, 132, 10, 6, BLUSH)
    c.ellipse(100, 125, 3.4, 2.4, NOSE)
    # 감은 눈, ω 입
    c.line(_quad((68, 116), (77, 124), (86, 116)), 4, BROWN)
    c.line(_quad((114, 116), (123, 124), (132, 116)), 4, BROWN)
    c.line(_quad((93, 130), (96.5, 134), (100, 130)) + _quad((100, 130), (103.5, 134), (107, 130)), 3, BROWN)
    return c.img.resize((SIZE, SIZE), Image.LANCZOS)


def main() -> None:
    img = draw()
    img.save(OUT_ICO, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
    print(f"wrote {OUT_ICO}")


if __name__ == "__main__":
    main()

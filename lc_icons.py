#!/usr/bin/env python3
"""
Legacy Cloud icon factory (shared with the AxionHost bot).

Draws colourful, hosting-themed ANIMATED emojis (GIF) from scratch with Pillow,
so the bot can create its own custom emojis without any external assets.

CLI:
    python lc_icons.py build   [outdir]   -> writes ax_<name>.gif for every icon
    python lc_icons.py preview [outdir]   -> writes preview.png + preview.gif sheets

Library (used by emojis.py):
    render_all()                 -> {name: gif_bytes}
    render(name)                 -> gif_bytes
    make_custom(shape, hex, txt) -> gif_bytes  (for /emoji make)
"""
from __future__ import annotations

import io
import math
import os
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

SIZE = 96          # final emoji size (px)
SS = 4             # supersampling factor for smooth edges
BIG = SIZE * SS
FRAMES = 16
FRAME_MS = 70
MAX_BYTES = 256 * 1024   # Discord emoji upload limit

C = dict(
    violet=(139, 92, 246), indigo=(99, 102, 241), blue=(59, 130, 246),
    sky=(56, 189, 248), cyan=(34, 211, 238), teal=(20, 184, 166),
    green=(34, 197, 94), lime=(163, 230, 53), yellow=(250, 204, 21),
    gold=(251, 191, 36), orange=(249, 115, 22), red=(239, 68, 68),
    pink=(236, 72, 153), white=(248, 250, 252), slate=(30, 41, 59),
    dark=(15, 23, 42), grey=(148, 163, 184), steel=(71, 85, 105),
)


def rgba(c, *rest):
    """rgba((r,g,b)), rgba((r,g,b), a) or rgba(r, g, b, a)."""
    if isinstance(c, (tuple, list)):
        a = rest[0] if rest else 255
        return (c[0], c[1], c[2], a)
    r, g, b = c, rest[0], rest[1]
    return (r, g, b, rest[2] if len(rest) > 2 else 255)


def shade(c, f):
    return tuple(max(0, min(255, int(v * f))) for v in c)


def lighten(c, f):
    return tuple(int(v + (255 - v) * f) for v in c)


def pulse(t, k=0.0):
    """0..1 smooth loop."""
    return 0.5 + 0.5 * math.sin(2 * math.pi * (t + k))


# --------------------------------------------------------------------------
# tiny drawing toolkit working in a 96x96 coordinate space
# --------------------------------------------------------------------------
class SD:
    """Scaled drawing. Opaque colours (and alpha 0 = punch a hole) draw straight onto
    the layer; translucent colours are drawn on a scratch layer and alpha-composited,
    so highlights blend over shapes and fading rings stay clean on empty layers."""

    def __init__(self, layer):
        self.layer = layer
        self.d = ImageDraw.Draw(layer)

    @staticmethod
    def _b(box):
        return [v * SS for v in box]

    @staticmethod
    def _p(pts):
        return [(x * SS, y * SS) for x, y in pts]

    @staticmethod
    def _translucent(*cols):
        return any(isinstance(c, tuple) and len(c) == 4 and 0 < c[3] < 255 for c in cols)

    def _go(self, meth, args, kw, cols):
        if self._translucent(*cols):
            tmp = Image.new("RGBA", self.layer.size, (0, 0, 0, 0))
            getattr(ImageDraw.Draw(tmp), meth)(*args, **kw)
            self.layer.alpha_composite(tmp)
        else:
            getattr(self.d, meth)(*args, **kw)

    def ellipse(self, box, fill=None, outline=None, width=1):
        self._go("ellipse", (self._b(box),), dict(fill=fill, outline=outline, width=max(1, int(width * SS))),
                 (fill, outline))

    def rect(self, box, fill=None, outline=None, width=1):
        self._go("rectangle", (self._b(box),), dict(fill=fill, outline=outline, width=max(1, int(width * SS))),
                 (fill, outline))

    def rrect(self, box, r, fill=None, outline=None, width=1):
        self._go("rounded_rectangle", (self._b(box),),
                 dict(radius=r * SS, fill=fill, outline=outline, width=max(1, int(width * SS))),
                 (fill, outline))

    def poly(self, pts, fill=None, outline=None):
        self._go("polygon", (self._p(pts),), dict(fill=fill, outline=outline), (fill, outline))

    def line(self, pts, fill, width=2):
        self._go("line", (self._p(pts),), dict(fill=fill, width=max(1, int(width * SS)), joint="curve"), (fill,))
        r = width / 2
        for x, y in pts:                       # round caps
            self.ellipse((x - r, y - r, x + r, y + r), fill=fill)

    def arc(self, box, a0, a1, fill, width=2):
        self._go("arc", (self._b(box), a0, a1), dict(fill=fill, width=max(1, int(width * SS))), (fill,))

    def pie(self, box, a0, a1, fill):
        self._go("pieslice", (self._b(box), a0, a1), dict(fill=fill), (fill,))


class Cv:
    def __init__(self):
        self.im = Image.new("RGBA", (BIG, BIG), (0, 0, 0, 0))

    def new(self):
        layer = Image.new("RGBA", (BIG, BIG), (0, 0, 0, 0))
        return layer, SD(layer)

    def commit(self, layer, mask=None, rot=None, alpha=1.0):
        if rot:
            ang, (cx, cy) = rot
            layer = layer.rotate(ang, center=(cx * SS, cy * SS), resample=Image.BICUBIC)
        if mask is not None:
            layer.putalpha(ImageChops.multiply(layer.getchannel("A"), mask.getchannel("A")))
        if alpha < 1.0:
            layer.putalpha(layer.getchannel("A").point(lambda v: int(v * alpha)))
        self.im.alpha_composite(layer)

    def glow(self, fn, color, blur=5, alpha=0.8):
        layer, d = self.new()
        fn(d, rgba(color))
        layer = layer.filter(ImageFilter.GaussianBlur(blur * SS))
        self.commit(layer, alpha=alpha)

    def out(self):
        return self.im.resize((SIZE, SIZE), Image.LANCZOS)


def sparkle(d, x, y, r, color):
    """4-point twinkle star."""
    if r <= 0.3:
        return
    k = r * 0.28
    d.poly([(x, y - r), (x + k, y - k), (x + r, y), (x + k, y + k),
            (x, y + r), (x - k, y + k), (x - r, y), (x - k, y - k)], fill=color)


def star_pts(cx, cy, ro, ri, rot=0.0, n=5):
    pts = []
    for i in range(n * 2):
        r = ro if i % 2 == 0 else ri
        a = rot + math.pi * i / n - math.pi / 2
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


# --------------------------------------------------------------------------
# icons: f(cv, t) with t in [0,1)
# --------------------------------------------------------------------------
def rocket(cv, t):
    bob = 2.5 * math.sin(2 * math.pi * t)
    for i in range(2):                                   # sparkles / speed lines
        L, d = cv.new()
        y = (20 + 60 * ((t + i * 0.5) % 1))
        d.line([(18 + i * 58, y), (18 + i * 58, y + 10)], rgba(C['sky'], 150), 2)
        cv.commit(L)
    L, d = cv.new()
    fl = 9 + 6 * math.sin(2 * math.pi * t * 3)
    cy = 66 + bob
    d.poly([(40, cy), (48, cy + fl + 9), (56, cy)], rgba(C['orange']))
    d.poly([(43, cy), (48, cy + fl), (53, cy)], rgba(C['yellow']))
    d.poly([(36, 50 + bob), (22, 70 + bob), (36, 64 + bob)], rgba(C['red']))
    d.poly([(60, 50 + bob), (74, 70 + bob), (60, 64 + bob)], rgba(C['red']))
    d.ellipse((35, 6 + bob, 61, 50 + bob), rgba(C['red']))
    d.ellipse((35, 14 + bob, 61, 58 + bob), rgba(C['white']))
    d.rect((35, 36 + bob, 61, 66 + bob), rgba(C['white']))
    d.rect((35, 60 + bob, 61, 66 + bob), rgba(C['grey']))
    d.ellipse((40, 30 + bob, 56, 46 + bob), rgba(C['dark']))
    d.ellipse((42, 32 + bob, 54, 44 + bob), rgba(C['cyan']))
    d.ellipse((44, 34 + bob, 48, 38 + bob), rgba(C['white'], 200))
    cv.commit(L)


def server(cv, t):
    L, d = cv.new()
    for i in range(3):
        y = 11 + i * 27
        edge = [C['violet'], C['blue'], C['cyan']][i]
        d.rrect((12, y, 84, y + 22), 6, fill=rgba(C['slate']), outline=rgba(edge), width=2.5)
        d.rrect((19, y + 7, 50, y + 15), 2, fill=rgba(C['steel']))
        d.line([(23, y + 11), (46, y + 11)], rgba(C['dark']), 1.5)
        for j in range(3):
            on = pulse(t, i * 0.23 + j * 0.31) > 0.45
            col = [C['green'], C['cyan'], C['orange']][j]
            d.ellipse((58 + j * 8, y + 7, 65 + j * 8, y + 14), rgba(col if on else shade(col, 0.3)))
    cv.commit(L)


def bolt(cv, t):
    p = pulse(t)
    pts = [(56, 4), (22, 54), (44, 54), (36, 92), (76, 38), (52, 38)]
    cv.glow(lambda d, c: d.poly(pts, c), C['yellow'], blur=4 + 4 * p, alpha=0.5 + 0.4 * p)
    L, d = cv.new()
    d.poly(pts, rgba(C['gold']), outline=rgba(C['orange']))
    d.poly([(54, 12), (31, 50), (47, 50)], rgba(lighten(C['yellow'], .5), 200))
    cv.commit(L)


def online(cv, t):
    for k in (0.0, 0.5):
        ph = (t + k) % 1
        L, d = cv.new()
        r = 20 + 22 * ph
        d.ellipse((48 - r, 48 - r, 48 + r, 48 + r), outline=rgba(C['green'], int(220 * (1 - ph))), width=3)
        cv.commit(L)
    L, d = cv.new()
    d.ellipse((28, 28, 68, 68), rgba(C['green']))
    d.ellipse((33, 31, 55, 47), rgba(lighten(C['green'], .55), 170))
    d.ellipse((28, 28, 68, 68), outline=rgba(shade(C['green'], .75)), width=2.5)
    cv.commit(L)


def offline(cv, t):
    on = pulse(t) > 0.35
    ph = t
    L, d = cv.new()
    r = 20 + 18 * ph
    d.ellipse((48 - r, 48 - r, 48 + r, 48 + r), outline=rgba(C['red'], int(200 * (1 - ph))), width=3)
    cv.commit(L)
    L, d = cv.new()
    col = C['red'] if on else shade(C['red'], .6)
    d.ellipse((28, 28, 68, 68), rgba(col))
    d.ellipse((33, 31, 55, 47), rgba(lighten(col, .5), 150))
    d.rrect((38, 45, 58, 51), 3, rgba(C['white']))
    cv.commit(L)


def maintenance(cv, t):
    ang = -360 * t
    L, d = cv.new()
    n = 8
    pts = []
    for i in range(n * 4):
        a = 2 * math.pi * i / (n * 4)
        r = 40 if (i % 4) in (1, 2) else 31
        pts.append((48 + r * math.cos(a), 48 + r * math.sin(a)))
    d.poly(pts, rgba(C['orange']))
    d.ellipse((24, 24, 72, 72), rgba(shade(C['orange'], .9)), outline=rgba(C['gold']), width=3)
    d.ellipse((35, 35, 61, 61), rgba(0, 0, 0, 0))
    cv.commit(L, rot=(ang, (48, 48)))
    L, d = cv.new()
    d.ellipse((38, 38, 58, 58), rgba(C['dark']))
    d.ellipse((42, 42, 54, 54), rgba(C['yellow']))
    cv.commit(L)


def shield(cv, t):
    pts = [(48, 6), (82, 18), (80, 52), (48, 90), (16, 52), (14, 18)]
    M, md = cv.new()
    md.poly(pts, rgba(C['white']))
    L, d = cv.new()
    d.poly(pts, rgba(C['blue']))
    d.poly([(48, 14), (74, 23), (72, 50), (48, 80), (24, 50), (22, 23)], rgba(C['indigo']))
    x = -30 + 150 * t
    d.poly([(x, 0), (x + 16, 0), (x - 24, 96), (x - 40, 96)], rgba(C['white'], 110))
    cv.commit(L, mask=M)
    L, d = cv.new()
    d.poly(pts, outline=rgba(C['sky']))
    d.line([(33, 46), (44, 58), (65, 34)], rgba(C['white']), 7)
    cv.commit(L)


def diamond(cv, t):
    L, d = cv.new()
    d.poly([(26, 18), (70, 18), (86, 38), (48, 84), (10, 38)], rgba(C['cyan']))
    d.poly([(10, 38), (86, 38), (48, 84)], rgba(C['blue']))
    d.poly([(26, 18), (38, 38), (10, 38)], rgba(lighten(C['cyan'], .5)))
    d.poly([(70, 18), (58, 38), (86, 38)], rgba(C['sky']))
    d.poly([(38, 38), (58, 38), (48, 18), (26, 18)], rgba(lighten(C['cyan'], .3)))
    d.poly([(58, 38), (38, 38), (48, 84)], rgba(C['indigo']))
    d.poly([(26, 18), (70, 18), (86, 38), (48, 84), (10, 38)], outline=rgba(C['white'], 200))
    cv.commit(L)
    L, d = cv.new()
    for k, (x, y) in enumerate([(24, 26), (74, 22), (62, 60)]):
        sparkle(d, x, y, 12 * max(0.0, math.sin(math.pi * ((t + k * .33) % 1))), rgba(C['white']))
    cv.commit(L)


def ticket(cv, t):
    ang = 8 * math.sin(2 * math.pi * t)
    L, d = cv.new()
    d.rrect((8, 22, 88, 74), 8, rgba(C['pink']))
    d.rrect((8, 22, 88, 74), 8, outline=rgba(lighten(C['pink'], .4)), width=2)
    d.ellipse((0, 40, 16, 56), rgba(0, 0, 0, 0))
    d.ellipse((80, 40, 96, 56), rgba(0, 0, 0, 0))
    for y in range(28, 70, 7):
        d.line([(62, y), (62, y + 3)], rgba(C['white'], 200), 1.5)
    d.poly(star_pts(35, 48, 13, 6), rgba(C['gold']))
    d.line([(70, 38), (80, 38)], rgba(C['white'], 220), 2)
    d.line([(70, 48), (80, 48)], rgba(C['white'], 220), 2)
    d.line([(70, 58), (78, 58)], rgba(C['white'], 220), 2)
    cv.commit(L, rot=(ang, (48, 48)))


def billing(cv, t):
    L, d = cv.new()
    zz = []
    for i in range(7):
        zz.append((18 + i * 10.0, 86 if i % 2 == 0 else 78))
    d.poly([(18, 8), (78, 8)] + list(reversed(zz)), rgba(C['white']))
    d.poly([(18, 8), (78, 8), (78, 20), (18, 20)], rgba(C['green']))
    d.poly(star_pts(48, 14, 0.1, 0.1), rgba(C['white']))
    prog = min(1.0, (t * 1.5))
    for i in range(4):
        w = 44 * max(0.0, min(1.0, prog * 4 - i))
        col = C['grey'] if i < 3 else C['green']
        d.rrect((26, 28 + i * 12, 26 + max(w, 0.1), 34 + i * 12), 3, rgba(col))
    cv.commit(L)


def support(cv, t):
    L, d = cv.new()
    d.rrect((8, 10, 88, 66), 16, rgba(C['blue']))
    d.poly([(24, 62), (24, 88), (50, 62)], rgba(C['blue']))
    d.rrect((8, 10, 88, 66), 16, outline=rgba(C['sky']), width=2.5)
    d.rrect((14, 14, 82, 32), 9, rgba(255, 255, 255, 40))
    cv.commit(L)
    L, d = cv.new()
    for i in range(3):
        y = 38 - 7 * max(0.0, math.sin(2 * math.pi * (t - i * 0.12)))
        d.ellipse((27 + i * 17, y - 5.5, 38 + i * 17, y + 5.5), rgba(C['white']))
    cv.commit(L)


def globe(cv, t):
    M, md = cv.new()
    md.ellipse((10, 10, 86, 86), rgba(C['white']))
    L, d = cv.new()
    d.ellipse((10, 10, 86, 86), rgba(C['blue']))
    d.ellipse((10, 10, 86, 50), rgba(C['sky'], 70))
    for off, (yy, hh, ww) in enumerate([(26, 14, 22), (44, 18, 30), (60, 12, 24)]):
        for rep in (-1, 0, 1):
            x = ((t * 76 + off * 30) % 76) - 38 + rep * 76 + 48
            d.ellipse((x - ww / 2, yy, x + ww / 2, yy + hh), rgba(C['green']))
    for k in range(3):
        a = (t + k / 3.0) % 1
        rx = 38 * abs(math.cos(math.pi * a))
        d.ellipse((48 - rx, 10, 48 + rx, 86), outline=rgba(lighten(C['sky'], .5), 140), width=1.5)
    for y in (30, 48, 66):
        d.line([(10, y), (86, y)], rgba(lighten(C['sky'], .5), 90), 1.2)
    cv.commit(L, mask=M)
    L, d = cv.new()
    d.ellipse((10, 10, 86, 86), outline=rgba(C['cyan']), width=3)
    d.ellipse((22, 18, 40, 30), rgba(255, 255, 255, 90))
    cv.commit(L)


def backup(cv, t):
    L, d = cv.new()
    col = C['sky']
    d.ellipse((12, 36, 48, 72), rgba(col))
    d.ellipse((30, 22, 70, 62), rgba(col))
    d.ellipse((52, 38, 86, 72), rgba(col))
    d.rrect((24, 48, 74, 72), 8, rgba(col))
    d.ellipse((34, 28, 52, 40), rgba(255, 255, 255, 80))
    cv.commit(L)
    L, d = cv.new()
    b = 5 * math.sin(2 * math.pi * t)
    d.line([(48, 66 + b), (48, 40 + b)], rgba(C['white']), 7)
    d.line([(36, 50 + b), (48, 38 + b), (60, 50 + b)], rgba(C['white']), 7)
    d.rrect((22, 82, 74, 87), 2, rgba(C['green']))
    cv.commit(L)


def stats(cv, t):
    L, d = cv.new()
    cols = [C['violet'], C['blue'], C['cyan'], C['green']]
    for i in range(4):
        h = 18 + 46 * (0.35 + 0.65 * pulse(t, -i * 0.16))
        x = 12 + i * 19
        d.rrect((x, 82 - h, x + 14, 82), 3, rgba(cols[i]))
        d.rrect((x + 2, 84 - h, x + 6, 82 - h + 8), 1, rgba(255, 255, 255, 90))
    d.line([(8, 85), (88, 85)], rgba(C['grey']), 2.5)
    cv.commit(L)


def crown(cv, t):
    L, d = cv.new()
    pts = [(10, 70), (6, 28), (28, 48), (48, 14), (68, 48), (90, 28), (86, 70)]
    d.poly(pts, rgba(C['gold']))
    d.poly(pts, outline=rgba(C['orange']))
    d.rrect((10, 66, 86, 80), 4, rgba(C['orange']))
    d.rrect((12, 68, 84, 72), 2, rgba(C['yellow'], 160))
    for x, col in [(26, C['red']), (48, C['sky']), (70, C['green'])]:
        d.ellipse((x - 5, 69, x + 5, 79), rgba(col))
    for x, y in [(6, 28), (48, 14), (90, 28)]:
        d.ellipse((x - 5, y - 5, x + 5, y + 5), rgba(C['yellow']))
    cv.commit(L)
    L, d = cv.new()
    x = -30 + 150 * t
    d.poly([(x, 0), (x + 14, 0), (x - 20, 96), (x - 34, 96)], rgba(255, 255, 255, 130))
    M, md = cv.new()
    md.poly(pts, rgba(C['white']))
    cv.commit(L, mask=M)


def bell(cv, t):
    ang = 16 * math.sin(2 * math.pi * t) * (1 if t < 0.8 else 0.3)
    L, d = cv.new()
    d.ellipse((42, 8, 54, 20), rgba(C['gold']))
    d.pie((18, 14, 78, 74), 180, 360, rgba(C['gold']))
    d.poly([(18, 44), (78, 44), (86, 74), (10, 74)], rgba(C['gold']))
    d.rrect((8, 70, 88, 78), 4, rgba(C['orange']))
    d.ellipse((28, 24, 40, 40), rgba(255, 255, 255, 100))
    cv.commit(L, rot=(ang, (48, 10)))
    L, d = cv.new()
    sw = 6 * math.sin(2 * math.pi * t + 0.8)
    d.ellipse((40 + sw, 76, 56 + sw, 90), rgba(C['red']))
    cv.commit(L)


def check(cv, t):
    p = min(1.0, t * 2.2)
    sc = 1 + 0.06 * math.sin(math.pi * min(1.0, t * 2))
    L, d = cv.new()
    r = 38 * sc
    d.ellipse((48 - r, 48 - r, 48 + r, 48 + r), rgba(C['green']))
    d.ellipse((48 - r, 48 - r, 48 + r, 48 + r), outline=rgba(lighten(C['green'], .4)), width=3)
    d.ellipse((24, 18, 46, 34), rgba(255, 255, 255, 80))
    a, b, c = (28, 50), (43, 64), (69, 34)
    if p < 0.5:
        q = p / 0.5
        pts = [a, (a[0] + (b[0] - a[0]) * q, a[1] + (b[1] - a[1]) * q)]
    else:
        q = (p - 0.5) / 0.5
        pts = [a, b, (b[0] + (c[0] - b[0]) * q, b[1] + (c[1] - b[1]) * q)]
    d.line(pts, rgba(C['white']), 8)
    cv.commit(L)


def cross(cv, t):
    sh = 3 * math.sin(2 * math.pi * t * 4) * (1 if t < 0.5 else 0)
    L, d = cv.new()
    d.ellipse((10, 10, 86, 86), rgba(C['red']))
    d.ellipse((10, 10, 86, 86), outline=rgba(lighten(C['red'], .4)), width=3)
    d.ellipse((24, 18, 46, 34), rgba(255, 255, 255, 80))
    d.line([(32, 32), (64, 64)], rgba(C['white']), 8)
    d.line([(64, 32), (32, 64)], rgba(C['white']), 8)
    cv.commit(L, rot=(sh * 2, (48, 48)))


def warn(cv, t):
    sc = 1 + 0.07 * math.sin(2 * math.pi * t)
    cv.glow(lambda d, c: d.poly([(48, 8), (90, 82), (6, 82)], c), C['yellow'], blur=5, alpha=0.35 + 0.3 * pulse(t))
    L, d = cv.new()
    s = sc
    pts = [(48, 48 - 40 * s), (48 + 43 * s, 48 + 36 * s), (48 - 43 * s, 48 + 36 * s)]
    d.poly(pts, rgba(C['yellow']), outline=rgba(C['orange']))
    d.rrect((44.5, 34, 51.5, 60), 3, rgba(C['dark']))
    d.ellipse((44, 65, 52, 73), rgba(C['dark']))
    cv.commit(L)


def fire(cv, t):
    def flame(sc, col, dy=0.0, ph=0.0):
        L, d = cv.new()
        w = 1 + 0.08 * math.sin(2 * math.pi * (t * 2 + ph))
        h = 1 + 0.10 * math.sin(2 * math.pi * (t * 3 + ph))
        cx, base = 48, 88 - dy
        pts = [(cx, base - 78 * sc * h),
               (cx + 14 * sc * w, base - 50 * sc), (cx + 30 * sc * w, base - 28 * sc),
               (cx + 28 * sc * w, base - 8 * sc), (cx + 12 * sc * w, base),
               (cx - 12 * sc * w, base), (cx - 28 * sc * w, base - 8 * sc),
               (cx - 30 * sc * w, base - 28 * sc), (cx - 12 * sc * w, base - 52 * sc)]
        d.poly(pts, rgba(col))
        cv.commit(L)
    cv.glow(lambda d, c: d.ellipse((20, 30, 76, 90), c), C['orange'], blur=7, alpha=0.4)
    flame(1.0, C['red'])
    flame(0.74, C['orange'], 0, 0.2)
    flame(0.46, C['yellow'], 0, 0.5)


def lock(cv, t):
    L, d = cv.new()
    d.arc((26, 8, 70, 60), 180, 360, rgba(C['grey']), 9)
    d.line([(30, 34), (30, 48)], rgba(C['grey']), 9)
    d.line([(66, 34), (66, 48)], rgba(C['grey']), 9)
    d.rrect((16, 42, 80, 88), 10, rgba(C['gold']), outline=rgba(C['orange']), width=2.5)
    d.rrect((20, 46, 76, 56), 5, rgba(255, 255, 255, 70))
    d.ellipse((41, 56, 55, 70), rgba(C['dark']))
    d.poly([(44, 66), (52, 66), (54, 80), (42, 80)], rgba(C['dark']))
    cv.commit(L)
    L, d = cv.new()
    x = -30 + 150 * t
    d.poly([(x, 0), (x + 12, 0), (x - 18, 96), (x - 30, 96)], rgba(255, 255, 255, 100))
    M, md = cv.new()
    md.rrect((16, 42, 80, 88), 10, rgba(C['white']))
    cv.commit(L, mask=M)


def gift(cv, t):
    hop = 4 * abs(math.sin(2 * math.pi * t))
    L, d = cv.new()
    d.rrect((14, 44, 82, 88), 6, rgba(C['pink']))
    d.rect((43, 44, 53, 88), rgba(C['gold']))
    cv.commit(L)
    L, d = cv.new()
    d.rrect((8, 30, 88, 48), 6, rgba(lighten(C['pink'], .15)))
    d.rect((43, 30, 53, 48), rgba(C['gold']))
    d.ellipse((24, 10, 50, 32), outline=rgba(C['gold']), width=5)
    d.ellipse((46, 10, 72, 32), outline=rgba(C['gold']), width=5)
    d.ellipse((42, 24, 54, 36), rgba(C['gold']))
    cv.commit(L)
    L, d = cv.new()
    for k, (x, y) in enumerate([(14, 22), (82, 20), (70, 70)]):
        sparkle(d, x, y - hop, 8 * max(0.0, math.sin(math.pi * ((t + k * .3) % 1))), rgba(C['yellow']))
    cv.commit(L)


def star(cv, t):
    ang = 360 * t
    cv.glow(lambda d, c: d.poly(star_pts(48, 50, 36, 16), c), C['yellow'], blur=6, alpha=0.4 + 0.3 * pulse(t))
    L, d = cv.new()
    d.poly(star_pts(48, 50, 38, 17), rgba(C['gold']), outline=rgba(C['orange']))
    d.poly(star_pts(48, 50, 22, 10), rgba(C['yellow']))
    cv.commit(L, rot=(ang, (48, 50)))
    L, d = cv.new()
    sparkle(d, 20, 20, 9 * pulse(t), rgba(C['white']))
    sparkle(d, 78, 74, 9 * pulse(t, .5), rgba(C['white']))
    cv.commit(L)


def cpu(cv, t):
    L, d = cv.new()
    for i in range(5):
        p = 24 + i * 12
        d.rect((p - 2, 8, p + 2, 22), rgba(C['grey']))
        d.rect((p - 2, 74, p + 2, 88), rgba(C['grey']))
        d.rect((8, p - 2, 22, p + 2), rgba(C['grey']))
        d.rect((74, p - 2, 88, p + 2), rgba(C['grey']))
    d.rrect((20, 20, 76, 76), 8, rgba(C['slate']), outline=rgba(C['violet']), width=3)
    cv.commit(L)
    p = pulse(t)
    cv.glow(lambda d, c: d.rrect((34, 34, 62, 62), 5, c), C['cyan'], blur=5, alpha=0.4 + 0.5 * p)
    L, d = cv.new()
    d.rrect((34, 34, 62, 62), 5, rgba(lighten(C['cyan'], .2 * p)), outline=rgba(C['white'], 200), width=1.5)
    d.line([(40, 48), (48, 48), (48, 40)], rgba(C['dark']), 2.5)
    d.line([(56, 48), (48, 48), (48, 56)], rgba(C['dark']), 2.5)
    cv.commit(L)


def robot(cv, t):
    blink = (0.62 < t < 0.7)
    L, d = cv.new()
    d.line([(48, 22), (48, 10)], rgba(C['grey']), 3)
    d.ellipse((42, 4, 54, 16), rgba(C['pink'] if pulse(t * 2) > .5 else shade(C['pink'], .5)))
    d.rrect((14, 22, 82, 80), 16, rgba(C['violet']), outline=rgba(C['indigo']), width=2.5)
    d.rrect((22, 30, 74, 66), 10, rgba(C['dark']))
    d.rrect((6, 40, 14, 60), 3, rgba(C['indigo']))
    d.rrect((82, 40, 90, 60), 3, rgba(C['indigo']))
    ey = 10 if not blink else 2
    for x in (34, 62):
        d.ellipse((x - 7, 48 - ey / 2, x + 7, 48 + ey / 2), rgba(C['cyan']))
    d.arc((36, 50, 60, 66), 20, 160, rgba(C['cyan']), 3)
    d.rrect((30, 71, 66, 76), 2, rgba(255, 255, 255, 60))
    cv.commit(L)


def wifi(cv, t):
    L, d = cv.new()
    d.ellipse((42, 68, 54, 80), rgba(C['green']))
    cv.commit(L)
    for i in range(3):
        on = (t * 4) % 4
        lit = on >= i + 0.5 or on < 0.3
        col = C['green'] if lit else shade(C['green'], .3)
        r = 14 + i * 15
        L, d = cv.new()
        d.arc((48 - r, 74 - r, 48 + r, 74 + r), 225, 315, rgba(col), 7)
        cv.commit(L)


def megaphone(cv, t):
    L, d = cv.new()
    d.poly([(14, 38), (34, 38), (66, 16), (66, 72), (34, 52), (14, 52)], rgba(C['orange']))
    d.rrect((8, 36, 30, 54), 5, rgba(C['red']))
    d.poly([(66, 16), (74, 12), (74, 76), (66, 72)], rgba(C['gold']))
    d.rrect((22, 52, 34, 78), 4, rgba(C['red']))
    d.poly([(34, 38), (66, 16), (66, 30), (34, 44)], rgba(255, 255, 255, 70))
    cv.commit(L)
    for i in range(3):
        ph = (t + i / 3.0) % 1
        L, d = cv.new()
        r = 6 + 22 * ph
        d.arc((74 - r, 44 - r, 74 + r, 44 + r), -50, 50, rgba(C['sky'], int(230 * (1 - ph))), 4)
        cv.commit(L)


def members(cv, t):
    p = pulse(t)
    L, d = cv.new()
    d.ellipse((56, 24, 80, 48), rgba(C['pink']))
    d.pie((46, 46, 90, 92), 180, 360, rgba(C['pink']))
    cv.commit(L)
    L, d = cv.new()
    d.ellipse((14, 20 - 2 * p, 42, 48 - 2 * p), rgba(C['sky']))
    d.pie((4, 46 - 2 * p, 52, 100 - 2 * p), 180, 360, rgba(C['blue']))
    d.ellipse((20, 25 - 2 * p, 30, 33 - 2 * p), rgba(255, 255, 255, 90))
    cv.commit(L)


def ram(cv, t):
    L, d = cv.new()
    for i in range(16):                                   # gold pins
        x = 9 + i * 5.0
        d.rect((x, 66, x + 3, 77), rgba(C['gold']))
    d.rrect((6, 24, 90, 68), 5, rgba((22, 130, 90)), outline=rgba((74, 222, 128)), width=2)
    d.rrect((6, 24, 90, 32), 3, rgba(C['violet']))        # heat-spreader strip
    d.ellipse((44, 62, 52, 70), rgba(0, 0, 0, 0))         # notch
    cv.commit(L)
    L, d = cv.new()
    for i in range(4):
        x = 12 + i * 20
        p = pulse(t, -i * 0.18)
        col = tuple(int(a + (b - a) * p) for a, b in zip(C['dark'], C['cyan']))
        d.rrect((x, 37, x + 16, 58), 3, rgba(col), outline=rgba(C['white'], 120), width=1)
        d.rect((x + 4, 43, x + 12, 45), rgba(C['white'], int(80 + 120 * p)))
    cv.commit(L)


def disk(cv, t):
    L, d = cv.new()
    d.rrect((14, 26, 90, 70), 7, rgba(C['slate']), outline=rgba(C['cyan']), width=2.5)
    d.rrect((4, 36, 18, 60), 2, rgba(C['gold']))
    for i in range(5):
        d.line([(6, 38 + i * 5), (16, 38 + i * 5)], rgba(C['orange']), 1.2)
    d.rrect((26, 33, 82, 63), 5, rgba(C['indigo']))
    d.poly([(52, 38), (44, 50), (51, 50), (47, 59), (60, 46), (53, 46)], rgba(C['yellow']))
    d.ellipse((82, 31, 88, 37), rgba(C['green'] if pulse(t * 2) > 0.4 else shade(C['green'], .3)))
    cv.commit(L)
    L, d = cv.new()
    x = 26 + 44 * t
    d.rect((x, 33, x + 9, 63), rgba(C['white'], 110))
    M, md = cv.new()
    md.rrect((26, 33, 82, 63), 5, rgba(C['white']))
    cv.commit(L, mask=M)


def money(cv, t):
    w = max(0.14, abs(math.cos(2 * math.pi * t)))
    cv.glow(lambda d, c: d.ellipse((48 - 36 * w, 12, 48 + 36 * w, 84), c), C['yellow'], blur=5, alpha=0.35)
    L, d = cv.new()
    d.ellipse((48 - 38 * w, 10, 48 + 38 * w, 86), rgba(C['orange']))
    d.ellipse((48 - 35 * w, 13, 48 + 35 * w, 83), rgba(C['gold']))
    d.ellipse((48 - 28 * w, 20, 48 + 28 * w, 76), outline=rgba(C['orange']), width=2)
    d.ellipse((48 - 30 * w, 16, 48 - 6 * w, 34), rgba(255, 255, 255, 90))
    cv.commit(L)
    if w > 0.55:
        L = Image.new("RGBA", (BIG, BIG), (0, 0, 0, 0))
        dr = ImageDraw.Draw(L)
        f = _font(int(50 * SS * min(1.0, w + 0.1)))
        bb = dr.textbbox((0, 0), "$", font=f)
        dr.text((48 * SS - (bb[2] - bb[0]) / 2 - bb[0], 48 * SS - (bb[3] - bb[1]) / 2 - bb[1]), "$", font=f, fill=rgba(C['orange']))
        cv.im.alpha_composite(L)
    L, d = cv.new()
    sparkle(d, 78, 18, 9 * max(0.0, math.sin(math.pi * t)), rgba(C['white']))
    cv.commit(L)


ICONS = {
    "rocket": rocket, "server": server, "bolt": bolt, "online": online, "offline": offline,
    "maintenance": maintenance, "shield": shield, "diamond": diamond, "ticket": ticket,
    "billing": billing, "support": support, "globe": globe, "backup": backup, "stats": stats,
    "crown": crown, "bell": bell, "check": check, "cross": cross, "warn": warn, "fire": fire,
    "lock": lock, "gift": gift, "star": star, "cpu": cpu, "robot": robot, "wifi": wifi,
    "megaphone": megaphone, "members": members,
    "ram": ram, "disk": disk, "money": money,
}

# unicode fallbacks used until custom emojis are installed
FALLBACK = {
    "rocket": "🚀", "server": "🖥️", "bolt": "⚡", "online": "🟢", "offline": "🔴",
    "maintenance": "🛠️", "shield": "🛡️", "diamond": "💎", "ticket": "🎫", "billing": "🧾",
    "support": "💬", "globe": "🌐", "backup": "💾", "stats": "📊", "crown": "👑", "bell": "🔔",
    "check": "✅", "cross": "❌", "warn": "⚠️", "fire": "🔥", "lock": "🔒", "gift": "🎁",
    "star": "⭐", "cpu": "🧠", "robot": "🤖", "wifi": "📶", "megaphone": "📢", "members": "👥",
    "ram": "🎛️", "disk": "💽", "money": "💵",
}

DESCRIPTIONS = {
    "rocket": "launch / deploy / speed", "server": "VPS / machines", "bolt": "uptime / power",
    "online": "service online", "offline": "service offline", "maintenance": "maintenance",
    "shield": "security / protection", "diamond": "premium plans", "ticket": "tickets",
    "billing": "billing / invoices", "support": "support chat", "globe": "nodes / network",
    "backup": "backups", "stats": "stats / usage", "crown": "owner / VIP", "bell": "notifications",
    "check": "success", "cross": "error / denied", "warn": "warnings", "fire": "hot offers",
    "lock": "locked / secure", "gift": "giveaways", "star": "rating / featured",
    "cpu": "hardware / CPU", "robot": "AI assistant", "wifi": "latency / ping",
    "megaphone": "announcements", "members": "community",
    "ram": "RAM / memory", "disk": "NVMe disk / storage", "money": "price / payments",
}


# --------------------------------------------------------------------------
# rendering / encoding
# --------------------------------------------------------------------------
def _frame(fn, t):
    cv = Cv()
    fn(cv, t)
    return cv.out()


def _to_p(im):
    """RGBA -> palette frame with 1-bit transparency on index 255."""
    alpha = im.getchannel("A")
    p = im.convert("RGB").convert("P", palette=Image.ADAPTIVE, colors=255)
    p.paste(255, alpha.point(lambda a: 255 if a < 128 else 0))
    p.info["transparency"] = 255
    return p


def encode_gif(frames, ms=FRAME_MS):
    pf = [_to_p(f) for f in frames]
    buf = io.BytesIO()
    pf[0].save(buf, format="GIF", save_all=True, append_images=pf[1:], duration=ms,
               loop=0, disposal=2, transparency=255, optimize=False)
    return buf.getvalue()


def render_frames(name):
    fn = ICONS[name]
    return [_frame(fn, i / FRAMES) for i in range(FRAMES)]


def render(name):
    data = encode_gif(render_frames(name))
    if len(data) > MAX_BYTES:               # shrink fallback (should not happen at 96px)
        small = [f.resize((64, 64), Image.LANCZOS) for f in render_frames(name)]
        data = encode_gif(small)
    return data


def render_all():
    return {name: render(name) for name in ICONS}


# ---- custom badge emojis for /emoji make ---------------------------------
def _font(px):
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
                 "DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf"):
        try:
            return ImageFont.truetype(path, px)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=px)
    except Exception:
        return ImageFont.load_default()


def parse_hex(value, default=C['violet']):
    v = (value or "").strip().lstrip("#")
    if len(v) == 3:
        v = "".join(ch * 2 for ch in v)
    try:
        return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
    except Exception:
        return default


SHAPES = ("pulse", "spin", "glow", "star", "heart")


def make_custom(shape="pulse", color="#8b5cf6", text=""):
    col = parse_hex(color)
    text = (text or "")[:3]
    frames = []
    for i in range(FRAMES):
        t = i / FRAMES
        cv = Cv()
        if shape == "spin":
            L, d = cv.new()
            d.ellipse((10, 10, 86, 86), rgba(shade(col, .45)))
            d.arc((10, 10, 86, 86), 360 * t, 360 * t + 120, rgba(lighten(col, .3)), 8)
            d.arc((10, 10, 86, 86), 360 * t + 180, 360 * t + 300, rgba(col), 8)
            d.ellipse((24, 24, 72, 72), rgba(col))
            cv.commit(L)
        elif shape == "glow":
            p = pulse(t)
            cv.glow(lambda d, c: d.rrect((14, 14, 82, 82), 18, c), col, blur=4 + 5 * p, alpha=0.5 + 0.4 * p)
            L, d = cv.new()
            d.rrect((16, 16, 80, 80), 16, rgba(col), outline=rgba(lighten(col, .5)), width=3)
            d.rrect((22, 20, 74, 38), 9, rgba(255, 255, 255, 70))
            cv.commit(L)
        elif shape == "star":
            cv.glow(lambda d, c: d.poly(star_pts(48, 50, 36, 16), c), col, blur=5, alpha=.5)
            L, d = cv.new()
            d.poly(star_pts(48, 50, 38, 17), rgba(col), outline=rgba(lighten(col, .5)))
            cv.commit(L, rot=(360 * t, (48, 50)))
        elif shape == "heart":
            s = 1 + 0.08 * math.sin(2 * math.pi * t * 2)
            L, d = cv.new()
            d.ellipse((48 - 34 * s, 48 - 30 * s, 48 + 2 * s, 48 + 8 * s), rgba(col))
            d.ellipse((48 - 2 * s, 48 - 30 * s, 48 + 34 * s, 48 + 8 * s), rgba(col))
            d.poly([(48 - 33 * s, 48 - 4 * s), (48 + 33 * s, 48 - 4 * s), (48, 48 + 36 * s)], rgba(col))
            cv.commit(L)
        else:  # pulse
            for k in (0.0, 0.5):
                ph = (t + k) % 1
                L, d = cv.new()
                r = 24 + 20 * ph
                d.ellipse((48 - r, 48 - r, 48 + r, 48 + r), outline=rgba(col, int(210 * (1 - ph))), width=3)
                cv.commit(L)
            L, d = cv.new()
            d.ellipse((22, 22, 74, 74), rgba(col), outline=rgba(lighten(col, .5)), width=3)
            d.ellipse((30, 26, 52, 40), rgba(255, 255, 255, 80))
            cv.commit(L)
        im = cv.out()
        if text:
            L = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
            dr = ImageDraw.Draw(L)
            f = _font(34 if len(text) == 1 else 26 if len(text) == 2 else 20)
            bb = dr.textbbox((0, 0), text, font=f)
            tw, th = bb[2] - bb[0], bb[3] - bb[1]
            lum = 0.299 * col[0] + 0.587 * col[1] + 0.114 * col[2]
            ink = C['dark'] if lum > 160 else C['white']
            dr.text(((SIZE - tw) / 2 - bb[0], (SIZE - th) / 2 - bb[1]), text, font=f, fill=rgba(ink))
            im.alpha_composite(L)
        frames.append(im)
    return encode_gif(frames)


# ---- preview sheets -------------------------------------------------------
def build_preview(outdir):
    names = list(ICONS)
    cols = 7
    rows = math.ceil(len(names) / cols)
    cell_w, cell_h, pad = SIZE + 24, SIZE + 34, 8
    sheet_w, sheet_h = cols * cell_w + pad, rows * cell_h + pad
    font = _font(11)
    bg = (24, 26, 33)
    all_frames = {n: render_frames(n) for n in names}

    def sheet(idx):
        im = Image.new("RGB", (sheet_w, sheet_h), bg)
        dr = ImageDraw.Draw(im)
        for k, n in enumerate(names):
            x = pad + (k % cols) * cell_w + 12
            y = pad + (k // cols) * cell_h + 4
            tile = all_frames[n][idx % FRAMES]
            im.paste(tile, (x, y), tile)
            bb = dr.textbbox((0, 0), n, font=font)
            dr.text((x + (SIZE - (bb[2] - bb[0])) / 2, y + SIZE + 6), n, font=font, fill=(160, 170, 190))
        return im

    sheet(4).save(os.path.join(outdir, "preview.png"))
    frames = [sheet(i) for i in range(FRAMES)]
    frames[0].save(os.path.join(outdir, "preview.gif"), save_all=True, append_images=frames[1:],
                   duration=FRAME_MS, loop=0, optimize=False)


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "build"
    out = argv[2] if len(argv) > 2 else "emojis"
    os.makedirs(out, exist_ok=True)
    if cmd == "preview":
        build_preview(out)
        print(f"preview.png + preview.gif written to {out}/")
        return 0
    worst = 0
    for name in ICONS:
        data = render(name)
        worst = max(worst, len(data))
        with open(os.path.join(out, f"ax_{name}.gif"), "wb") as fh:
            fh.write(data)
    print(f"{len(ICONS)} animated icons written to {out}/ (largest {worst // 1024} KB, limit 256 KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

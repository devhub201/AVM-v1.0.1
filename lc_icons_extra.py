"""Extra animated icons for AVM (Legacy Cloud). Importing this module registers them in lc_icons."""
import math

import lc_icons as B
from lc_icons import C, rgba, shade, lighten, pulse, sparkle


def idle(cv, t):
    L, d = cv.new()
    r = 20 + 18 * t
    d.ellipse((48 - r, 48 - r, 48 + r, 48 + r), outline=rgba(C['yellow'], int(200 * (1 - t))), width=3)
    cv.commit(L)
    L, d = cv.new()
    d.ellipse((28, 28, 68, 68), rgba(C['yellow']))
    d.ellipse((33, 31, 55, 47), rgba(lighten(C['yellow'], .55), 170))
    d.ellipse((28, 28, 68, 68), outline=rgba(shade(C['orange'], .9)), width=2.5)
    a = 2 * math.pi * t
    d.line([(48, 48), (48 + 11 * math.sin(a), 48 - 11 * math.cos(a))], rgba(shade(C['orange'], .7)), 3)
    d.line([(48, 48), (48, 40)], rgba(shade(C['orange'], .7)), 3)
    cv.commit(L)


def book(cv, t):
    L, d = cv.new()
    d.rrect((8, 22, 90, 80), 6, rgba(C['indigo']), outline=rgba(shade(C['indigo'], .7)), width=2)
    d.rrect((13, 26, 48, 74), 3, rgba(C['white']))
    d.rrect((48, 26, 83, 74), 3, rgba(lighten(C['grey'], .75)))
    for i in range(4):
        d.line([(18, 37 + i * 9), (43, 37 + i * 9)], rgba(C['grey']), 2)
        d.line([(54, 37 + i * 9), (78, 37 + i * 9)], rgba(C['grey']), 2)
    cv.commit(L)
    if t < 0.8:
        ph = t / 0.8
        xe = 48 + 35 * math.cos(math.pi * ph)
        L, d = cv.new()
        d.poly([(48, 25), (xe, 22 + 3 * math.sin(math.pi * ph)), (xe, 72), (48, 74)], rgba(C['white']),
               outline=rgba(C['grey']))
        cv.commit(L)


def clipboard(cv, t):
    L, d = cv.new()
    d.rrect((20, 14, 76, 88), 8, rgba(C['slate']), outline=rgba(C['cyan']), width=2.5)
    d.rrect((34, 8, 62, 24), 5, rgba(C['gold']), outline=rgba(C['orange']), width=2)
    d.rrect((26, 26, 70, 82), 4, rgba(C['white']))
    for i in range(3):
        y = 38 + i * 15
        prog = max(0.0, min(1.0, t * 3.4 - i))
        d.line([(40, y), (40 + 24 * prog, y)], rgba(C['indigo']), 4)
        d.ellipse((31, y - 4, 39, y + 4), rgba(C['green'] if prog >= 1 else C['grey']))
    cv.commit(L)


def user(cv, t):
    p = pulse(t)
    cv.glow(lambda d, c: d.ellipse((22, 10, 74, 62), c), C['sky'], blur=5, alpha=0.2 + 0.3 * p)
    L, d = cv.new()
    d.ellipse((14, 58, 82, 128), rgba(C['blue']), outline=rgba(C['indigo']), width=2.5)
    d.ellipse((30, 12, 66, 48), rgba(C['sky']), outline=rgba(C['blue']), width=2.5)
    d.ellipse((35, 16, 52, 30), rgba(255, 255, 255, 110))
    cv.commit(L)


def gear(cv, t):
    L, d = cv.new()
    for i in range(8):
        a = math.radians(i * 45)
        pts = []
        for dx, dy in ((-7, -7), (7, -7), (7, 7), (-7, 7)):
            ca, sa = math.cos(a), math.sin(a)
            pts.append((48 + 34 * ca + dx * ca - dy * sa, 48 + 34 * sa + dx * sa + dy * ca))
        d.poly(pts, rgba(C['grey']))
    d.ellipse((17, 17, 79, 79), rgba(C['grey']), outline=rgba(C['steel']), width=2.5)
    d.ellipse((24, 24, 72, 72), rgba(lighten(C['grey'], .25)))
    d.ellipse((35, 35, 61, 61), (0, 0, 0, 0))
    d.ellipse((35, 35, 61, 61), outline=rgba(C['steel']), width=2.5)
    d.ellipse((28, 27, 46, 39), rgba(255, 255, 255, 90))
    cv.commit(L, rot=(-45 * t, (48, 48)))


def link(cv, t):
    p = pulse(t)
    L, d = cv.new()
    d.rrect((4, 34, 56, 62), 14, outline=rgba(C['cyan']), width=7)
    d.rrect((40, 34, 92, 62), 14, outline=rgba(C['violet']), width=7)
    d.rrect((7, 37, 53, 59), 11, outline=rgba(255, 255, 255, int(60 + 80 * p)), width=1.5)
    cv.commit(L, rot=(45, (48, 48)))
    L, d = cv.new()
    sparkle(d, 78, 16, 9 * max(0.0, math.sin(math.pi * t)), rgba(C['white']))
    cv.commit(L)


def hourglass(cv, t):
    L, d = cv.new()
    d.rrect((20, 6, 76, 15), 3, rgba(C['gold']), outline=rgba(C['orange']), width=2)
    d.rrect((20, 81, 76, 90), 3, rgba(C['gold']), outline=rgba(C['orange']), width=2)
    d.poly([(25, 15), (71, 15), (52, 48), (71, 81), (25, 81), (44, 48)], rgba(255, 255, 255, 45), outline=rgba(C['grey']))
    cv.commit(L)
    L, d = cv.new()
    top_h = 31 * (1 - t)
    if top_h > 1:
        y0 = 48 - top_h
        w = 23 * top_h / 31
        d.poly([(48 - w, y0), (48 + w, y0), (48, 48)], rgba(C['gold']))
    bot_h = 31 * t
    if bot_h > 1:
        y1 = 81 - bot_h
        hw = 23 * (y1 - 48) / 33
        d.poly([(48 - hw, y1), (48 + hw, y1), (71, 81), (25, 81)], rgba(C['gold']))
    if t < 0.96:
        d.line([(48, 48), (48, 81 - bot_h)], rgba(C['yellow']), 2)
    cv.commit(L)


def key(cv, t):
    sw = 8 * math.sin(2 * math.pi * t)
    L, d = cv.new()
    d.ellipse((8, 26, 46, 64), rgba(C['gold']), outline=rgba(C['orange']), width=2.5)
    d.ellipse((21, 39, 33, 51), (0, 0, 0, 0))
    d.rrect((42, 39, 90, 52), 3, rgba(C['gold']), outline=rgba(C['orange']), width=2)
    d.rect((68, 52, 76, 66), rgba(C['gold']), outline=rgba(C['orange']), width=1.5)
    d.rect((80, 52, 88, 60), rgba(C['gold']), outline=rgba(C['orange']), width=1.5)
    d.ellipse((13, 30, 28, 40), rgba(255, 255, 255, 110))
    cv.commit(L, rot=(sw, (48, 48)))
    L, d = cv.new()
    sparkle(d, 80, 22, 8 * max(0.0, math.sin(math.pi * ((t * 2) % 1))), rgba(C['white']))
    cv.commit(L)


def refresh(cv, t):
    L, d = cv.new()
    d.arc((17, 17, 79, 79), 205, 335, rgba(C['cyan']), 9)
    d.arc((17, 17, 79, 79), 25, 155, rgba(C['teal']), 9)
    for ang, col in ((335, C['cyan']), (155, C['teal'])):
        a = math.radians(ang)
        px, py = 48 + 31 * math.cos(a), 48 + 31 * math.sin(a)
        tx, ty = -math.sin(a), math.cos(a)
        nx, ny = math.cos(a), math.sin(a)
        d.poly([(px + tx * 15, py + ty * 15), (px + nx * 12, py + ny * 12), (px - nx * 12, py - ny * 12)], rgba(col))
    cv.commit(L, rot=(-360 * t, (48, 48)))


def pickaxe(cv, t):
    sw = -20 + 40 * (0.5 - 0.5 * math.cos(2 * math.pi * t))
    L, d = cv.new()
    d.line([(34, 86), (56, 28)], rgba((150, 98, 52)), 8)
    d.line([(34, 86), (56, 28)], rgba((196, 140, 80), 150), 2.5)
    outer, inner = [], []
    for i in range(13):
        a = math.radians(200 + 140 * i / 12)
        outer.append((56 + 31 * math.cos(a), 32 + 28 * math.sin(a)))
        inner.append((56 + 22 * math.cos(a), 32 + 19 * math.sin(a)))
    d.poly(outer + inner[::-1], rgba(C['grey']), outline=rgba(C['steel']))
    d.poly([(outer[2][0], outer[2][1]), (outer[5][0], outer[5][1]), (inner[5][0], inner[5][1])], rgba(255, 255, 255, 90))
    cv.commit(L, rot=(sw, (34, 86)))
    L, d = cv.new()
    if t > 0.45 and t < 0.7:
        sparkle(d, 22, 28, 10 * math.sin(math.pi * (t - 0.45) / 0.25), rgba(C['yellow']))
    cv.commit(L)


def node(cv, t):
    pts = [(48, 24), (22, 70), (74, 70)]
    edges = [(0, 1), (0, 2), (1, 2)]
    L, d = cv.new()
    for a, b in edges:
        d.line([pts[a], pts[b]], rgba(C['steel']), 3)
    cv.commit(L)
    L, d = cv.new()
    for a, b in edges:
        for k in (0.0, 0.5):
            s = (t + k) % 1
            x = pts[a][0] + (pts[b][0] - pts[a][0]) * s
            y = pts[a][1] + (pts[b][1] - pts[a][1]) * s
            d.ellipse((x - 3.5, y - 3.5, x + 3.5, y + 3.5), rgba(C['cyan']))
    for i, (x, y) in enumerate(pts):
        d.rrect((x - 15, y - 12, x + 15, y + 12), 4, rgba(C['slate']), outline=rgba(C['cyan']), width=2)
        d.line([(x - 10, y - 4), (x + 1, y - 4)], rgba(C['grey']), 2)
        d.line([(x - 10, y + 4), (x - 2, y + 4)], rgba(C['grey']), 2)
        on = pulse(t * 2, i / 3) > 0.35
        d.ellipse((x + 5, y - 3, x + 11, y + 3), rgba(C['green'] if on else shade(C['green'], .35)))
    cv.commit(L)


def container(cv, t):
    bob = 2.5 * math.sin(2 * math.pi * t)
    L, d = cv.new()
    d.poly([(14, 36 + bob), (62, 36 + bob), (84, 22 + bob), (36, 22 + bob)], rgba(lighten(C['blue'], .4)),
           outline=rgba(shade(C['blue'], .6)))
    d.poly([(62, 36 + bob), (84, 22 + bob), (84, 62 + bob), (62, 78 + bob)], rgba(shade(C['blue'], .65)),
           outline=rgba(shade(C['blue'], .5)))
    d.rect((14, 36 + bob, 62, 78 + bob), rgba(C['blue']), outline=rgba(shade(C['blue'], .6)), width=2)
    for x in (26, 38, 50):
        d.line([(x, 41 + bob), (x, 73 + bob)], rgba(lighten(C['blue'], .35), 170), 2)
    cv.commit(L)
    L, d = cv.new()
    x = -20 + 130 * t
    d.poly([(x, 0), (x + 10, 0), (x - 20, 96), (x - 30, 96)], rgba(255, 255, 255, 90))
    M, md = cv.new()
    md.rect((14, 36 + bob, 62, 78 + bob), rgba(C['white']))
    cv.commit(L, mask=M)


EXTRA = {
    "idle": (idle, "🟡", "idle / starting"), "book": (book, "📚", "docs / guide"),
    "clipboard": (clipboard, "📋", "lists / plans"), "user": (user, "👤", "user / owner"),
    "gear": (gear, "⚙️", "settings"), "link": (link, "🔗", "links / connections"),
    "hourglass": (hourglass, "⏳", "waiting / in progress"), "key": (key, "🔑", "keys / access"),
    "refresh": (refresh, "🔄", "reload / reinstall"), "pickaxe": (pickaxe, "⛏️", "mining guard"),
    "node": (node, "🛰️", "nodes / cluster"), "container": (container, "📦", "containers / VPS"),
}
for _name, (_fn, _fb, _desc) in EXTRA.items():
    B.ICONS[_name] = _fn
    B.FALLBACK[_name] = _fb
    B.DESCRIPTIONS[_name] = _desc

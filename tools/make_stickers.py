"""Booth sticker pack (drawn here, no external art).

Usage: python tools/make_stickers.py stickers

Writes every PNG plus stickers.json into the folder. This overwrites the
built-in PNGs and stickers.json, so keep your own stickers somewhere else
first (or add them to SPEC at the bottom of this file).
"""
import json
import math
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "stickers")
SS = 4                                   # supersampling, for smooth edges

PINK, PINK_DK, PINK_LT = (244, 143, 177), (214, 92, 135), (255, 214, 226)
WHITE, CREAM = (255, 252, 250), (255, 246, 236)
GOLD, GOLD_DK, GOLD_LT = (236, 196, 98), (190, 140, 50), (255, 236, 170)
RED, RED_DK, RED_LT = (220, 50, 70), (150, 22, 42), (255, 150, 160)
BLACK, INK = (34, 30, 34), (70, 50, 60)


# ---------------------------------------------------------------- helpers ---
def bez(p0, p1, p2, n=32):
    """Quadratic Bezier points."""
    return [((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0],
             (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1])
            for t in (i / (n - 1) for i in range(n))]


def cbez(p0, p1, p2, p3, n=40):
    """Cubic Bezier points."""
    out = []
    for i in range(n):
        t = i / (n - 1)
        a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3
        out.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
                    a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return out


def heart_pts(cx, cy, size, rot=0.0, n=120):
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        x = 16 * math.sin(t) ** 3
        y = -(13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t))
        x, y = x / 17.0 * size, y / 17.0 * size
        c, s = math.cos(rot), math.sin(rot)
        pts.append((cx + x * c - y * s, cy + x * s + y * c))
    return pts


def star_pts(cx, cy, R, r=None, n=5, rot=0.0):
    r = R * 0.45 if r is None else r
    pts = []
    for i in range(2 * n):
        a = rot - math.pi / 2 + math.pi * i / n
        rad = R if i % 2 == 0 else r
        pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
    return pts


def ellipse_pts(cx, cy, rx, ry, rot=0.0, n=90):
    out = []
    for i in range(n):
        t = 2 * math.pi * i / n
        x, y = rx * math.cos(t), ry * math.sin(t)
        out.append((cx + x * math.cos(rot) - y * math.sin(rot), cy + x * math.sin(rot) + y * math.cos(rot)))
    return out


def mirror(pts, cx):
    return [(2 * cx - x, y) for x, y in pts]


class Canvas:
    """Draw in logical pixels; the image is SS times bigger and scaled down on save."""

    def __init__(self, w, h):
        self.w, self.h = w, h
        self.im = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.im)

    @staticmethod
    def s(pts):
        return [(x * SS, y * SS) for x, y in pts]

    def poly(self, pts, fill, line=None, w=6):
        p = self.s(pts)
        self.d.polygon(p, fill=fill)
        if line:
            self.d.line(p + [p[0], p[1]], fill=line, width=int(w * SS), joint="curve")

    def ell(self, cx, cy, rx, ry, fill=None, line=None, w=6):
        self.d.ellipse([(cx - rx) * SS, (cy - ry) * SS, (cx + rx) * SS, (cy + ry) * SS],
                       fill=fill, outline=line, width=int(w * SS) if line else 0)

    def circle(self, cx, cy, r, fill=None, line=None, w=6):
        self.ell(cx, cy, r, r, fill, line, w)

    def line(self, pts, col, w):
        self.d.line(self.s(pts), fill=col, width=int(w * SS), joint="curve")
        r = w / 2.0                                   # round caps
        for x, y in (pts[0], pts[-1]):
            self.circle(x, y, r, fill=col)

    def rect(self, x0, y0, x1, y1, fill, line=None, w=6, radius=0):
        self.d.rounded_rectangle([x0 * SS, y0 * SS, x1 * SS, y1 * SS], radius=radius * SS,
                                 fill=fill, outline=line, width=int(w * SS) if line else 0)

    def shine(self, cx, cy, rx, ry, alpha=200, rot=0.0):
        self.poly(ellipse_pts(cx, cy, rx, ry, rot), (255, 255, 255, alpha))

    def layer(self):
        """A blank canvas of the same size (for blur / glow layers)."""
        return Canvas(self.w, self.h)

    def paste(self, other, blur=0):
        im = other.im.filter(ImageFilter.GaussianBlur(blur * SS)) if blur else other.im
        self.im.alpha_composite(im)
        self.d = ImageDraw.Draw(self.im)

    def save(self, name):
        # Not cropped: the canvas is laid out so its centre is the anchor point
        # and its width is what "width" in stickers.json measures.
        im = self.im.resize((self.w, self.h), Image.LANCZOS)
        im.save(OUT / name)
        print("wrote", name, im.size)


def flower(c, cx, cy, r, petal, center=GOLD, line=None, petals=5, rot=0.0):
    for i in range(petals):
        a = rot + 2 * math.pi * i / petals
        px, py = cx + math.cos(a) * r * 0.62, cy + math.sin(a) * r * 0.62
        c.poly(ellipse_pts(px, py, r * 0.55, r * 0.38, a), petal, line, 3 if line else 0)
    c.circle(cx, cy, r * 0.3, center)


def leaf(c, cx, cy, size, rot, col=(120, 180, 110), dark=(80, 140, 80)):
    c.poly(ellipse_pts(cx, cy, size, size * 0.42, rot), col, dark, 2.5)


# ------------------------------------------------------------------ cute ---
def bow(name, fill=PINK, dark=PINK_DK, light=PINK_LT):
    c = Canvas(640, 440)
    cx, cy = 320, 165
    for sgn in (-1, 1):                     # ribbon tails
        tail = [(cx + sgn * 10, cy + 30), (cx + sgn * 120, cy + 250), (cx + sgn * 92, cy + 238),
                (cx + sgn * 78, cy + 268), (cx + sgn * 2, cy + 60)]
        c.poly(tail, fill, dark, 6)
    for sgn in (-1, 1):                     # loops
        c.poly(ellipse_pts(cx + sgn * 150, cy - 8, 155, 100, sgn * 0.22), fill, dark, 7)
        c.poly(ellipse_pts(cx + sgn * 105, cy + 4, 75, 45, sgn * 0.22), dark + (50,))
        c.ell(cx + sgn * 205, cy - 63, 30, 12, light + (220,))
    c.rect(cx - 46, cy - 58, cx + 46, cy + 58, fill, dark, 7, radius=32)
    c.ell(cx - 11, cy - 30, 13, 10, light + (230,))
    c.save(name)


def blush():
    c = Canvas(300, 200)
    soft = c.layer()
    soft.ell(150, 100, 120, 60, (255, 120, 150, 150))
    c.paste(soft, blur=26)
    for x in (95, 140, 185):
        c.line([(x, 120), (x + 26, 78)], (230, 80, 120, 230), 9)
    c.poly(heart_pts(235, 45, 26), (240, 90, 130, 240))
    c.save("blush.png")


def tiara():
    c = Canvas(760, 360)
    peaks = [(120, 190), (220, 120), (300, 170), (380, 40), (460, 170), (540, 120), (640, 190)]
    valleys = [(170, 260), (260, 230), (340, 240), (420, 240), (500, 230), (590, 260)]
    pts = [(40, 330)]
    for i, p in enumerate(peaks):
        pts.append(p)
        if i < len(valleys):
            pts.append(valleys[i])
    pts += [(720, 330), (380, 300)]
    c.poly(pts, GOLD, GOLD_DK, 8)
    for (x, y), r in zip(peaks, (18, 22, 18, 34, 18, 22, 18)):
        c.circle(x, y, r, PINK, PINK_DK, 5)
    c.poly(heart_pts(380, 245, 40), PINK, PINK_DK, 4)
    c.save("tiara.png")


def heart_glasses():
    c = Canvas(900, 380)
    for cx in (240, 660):
        c.poly(heart_pts(cx, 200, 190), PINK_DK)
        c.poly(heart_pts(cx, 196, 155), (255, 120, 165, 150))
        c.ell(cx - 70, 128, 25, 22, (255, 255, 255, 170))
    c.line([(395, 150), (450, 128), (505, 150)], PINK_DK, 26)
    c.save("heart_glasses.png")


def floating_hearts():
    c = Canvas(1000, 420)
    spots = [(90, 300, 48, -0.3, PINK), (210, 170, 64, -0.15, PINK_DK), (370, 90, 46, -0.05, PINK_LT),
             (500, 60, 70, 0.0, PINK), (640, 95, 44, 0.08, PINK_DK), (790, 170, 62, 0.18, PINK_LT),
             (910, 300, 46, 0.3, PINK)]
    for x, y, sz, rot, col in spots:
        c.poly(heart_pts(x, y, sz, rot), col + (235,), (255, 255, 255, 220), 5)
    c.save("hearts.png")


def pearl_headband():
    c = Canvas(1000, 360)
    n = 17
    for i in range(n):
        a = math.pi * (1.08 + 0.84 * i / (n - 1))
        x, y = 500 + 440 * math.cos(a), 360 + 300 * math.sin(a)
        r = 30 if i % 2 == 0 else 24
        c.circle(x, y, r, (250, 245, 238), (205, 190, 192), 4)
        c.circle(x - r * 0.3, y - r * 0.35, r * 0.3, (255, 255, 255, 235))
    c.poly(heart_pts(500, 66, 36), PINK, PINK_DK, 4)
    c.save("pearl_headband.png")


def flower_crown():
    c = Canvas(1100, 380)
    n = 11
    pts = [(550 + 480 * math.cos(math.pi * (1.06 + 0.88 * i / (n - 1))),
            380 + 300 * math.sin(math.pi * (1.06 + 0.88 * i / (n - 1)))) for i in range(n)]
    for i in range(n - 1):                        # leaves between the flowers
        (x0, y0), (x1, y1) = pts[i], pts[i + 1]
        a = math.atan2(y1 - y0, x1 - x0)
        leaf(c, (x0 + x1) / 2, (y0 + y1) / 2 - 10, 34, a - 0.5)
        leaf(c, (x0 + x1) / 2, (y0 + y1) / 2 + 12, 30, a + 0.5)
    cols = [(255, 170, 195), (255, 250, 245), (255, 205, 170), (230, 180, 240)]
    for i, (x, y) in enumerate(pts):
        r = 46 if i % 2 == 0 else 36
        flower(c, x, y, r, cols[i % len(cols)], GOLD, (220, 140, 160), rot=i * 0.4)
    c.save("flower_crown.png")


def butterfly(c, cx, cy, s, rot, col, dark):
    for sgn in (-1, 1):
        up = ellipse_pts(cx + sgn * 0.48 * s * math.cos(rot) + 0.15 * s * math.sin(rot),
                         cy + sgn * 0.48 * s * math.sin(rot) - 0.35 * s, 0.5 * s, 0.36 * s,
                         rot + sgn * 0.55)
        lo = ellipse_pts(cx + sgn * 0.36 * s * math.cos(rot), cy + sgn * 0.36 * s * math.sin(rot) + 0.25 * s,
                         0.34 * s, 0.26 * s, rot - sgn * 0.5)
        c.poly(lo, col, dark, 4)
        c.poly(up, col, dark, 4)
    for sgn in (-1, 1):
        c.circle(cx + sgn * 0.5 * s, cy - 0.38 * s, 0.1 * s, (255, 255, 255, 210))
    c.poly(ellipse_pts(cx, cy, 0.09 * s, 0.42 * s, rot), dark)


def butterflies():
    c = Canvas(900, 360)
    butterfly(c, 170, 230, 150, -0.25, (190, 215, 255), (100, 130, 200))
    butterfly(c, 450, 120, 180, 0.0, (240, 190, 255), (160, 100, 190))
    butterfly(c, 730, 230, 150, 0.25, (255, 190, 210), PINK_DK)
    c.save("butterflies.png")


def sparkles():
    c = Canvas(1000, 440)
    rng = random.Random(4)
    spots = [(90, 320, 52), (230, 170, 70), (400, 80, 44), (520, 60, 86), (680, 110, 50),
             (820, 190, 72), (930, 330, 44), (330, 250, 30), (650, 250, 30)]
    glow = c.layer()
    for x, y, r in spots:
        glow.circle(x, y, r * 0.8, (255, 240, 180, 150))
    c.paste(glow, blur=14)
    for x, y, r in spots:
        c.poly(star_pts(x, y, r, r * 0.26, 4), GOLD_LT, GOLD_DK, 4)
    for _ in range(26):
        c.circle(rng.uniform(40, 960), rng.uniform(40, 400), rng.uniform(5, 10), (255, 255, 255, 230))
    c.save("sparkles.png")


def cherries():
    c = Canvas(340, 380)
    c.line(bez((110, 270), (140, 140), (200, 50)), (90, 140, 70), 10)
    c.line(bez((240, 280), (220, 150), (200, 50)), (90, 140, 70), 10)
    leaf(c, 245, 70, 60, -0.5, (120, 190, 100), (70, 130, 70))
    for x, y in ((105, 285), (240, 295)):
        c.circle(x, y, 72, RED, RED_DK, 6)
        c.ell(x - 26, y - 28, 20, 13, (255, 255, 255, 200))
    c.save("cherries.png")


# ---------------------------------------------------------------- hewan ---
def cat_ears():
    c = Canvas(900, 360)
    for cx in (210, 690):
        sgn = -1 if cx < 450 else 1
        outer = [(cx - 160, 340), (cx + sgn * 30, 20), (cx + 160, 340)]
        c.poly(outer, (70, 55, 60), (40, 30, 35), 8)
        c.poly([(cx - 95, 330), (cx + sgn * 25, 95), (cx + 95, 330)], PINK)
        c.ell(cx + sgn * 10, 175, 18, 25, PINK_LT + (200,))
    c.im = c.im.filter(ImageFilter.GaussianBlur(3))
    c.save("cat_ears.png")


def ears_round(name, col, inner, dark, w=900, r=130, gap=560, hi=True):
    c = Canvas(w, 2 * r + 40)
    for cx in (w / 2 - gap / 2, w / 2 + gap / 2):
        c.circle(cx, r + 20, r, col, dark, 8)
        c.circle(cx, r + 34, r * 0.6, inner)
        if hi:
            c.ell(cx - r * 0.45, r * 0.6, r * 0.18, r * 0.12, (255, 255, 255, 120))
    c.save(name)


def bunny_ears():
    c = Canvas(640, 620)
    for cx, rot in ((200, -0.18), (440, 0.18)):
        def ear(rx, ry):
            pts = []
            for i in range(100):
                t = 2 * math.pi * i / 100
                x, y = rx * math.sin(t), -ry * (1 - math.cos(t))
                pts.append((cx + x * math.cos(rot) - y * math.sin(rot), 600 + x * math.sin(rot) + y * math.cos(rot)))
            return pts
        c.poly(ear(95, 290), WHITE, (205, 190, 195), 7)
        c.poly(ear(52, 230), PINK)
    c.save("bunny_ears.png")


def whiskers(name, nose, nose_dk, line=(70, 60, 64)):
    c = Canvas(760, 260)
    for sgn in (-1, 1):
        for dy, bend in ((-34, -26), (0, 0), (34, 26)):
            p0 = (380 + sgn * 95, 140 + dy * 0.3)
            p2 = (380 + sgn * 350, 140 + dy * 2.2)
            c.line(bez(p0, ((p0[0] + p2[0]) / 2, (p0[1] + p2[1]) / 2 + bend - 18), p2), line, 7)
    pts = cbez((380, 168), (330, 150), (318, 92), (380, 96)) + cbez((380, 96), (442, 92), (430, 150), (380, 168))
    c.poly(pts, nose, nose_dk, 5)
    c.ell(362, 112, 14, 8, (255, 255, 255, 170))
    c.save(name)


def dog_ears():
    c = Canvas(1040, 560)
    for sgn in (-1, 1):
        cx = 520 + sgn * 330
        top = (cx - sgn * 40, 40)
        pts = (cbez(top, (cx + sgn * 160, 40), (cx + sgn * 190, 380), (cx + sgn * 60, 520))
               + cbez((cx + sgn * 60, 520), (cx - sgn * 40, 560), (cx - sgn * 150, 420), (cx - sgn * 130, 120))
               + cbez((cx - sgn * 130, 120), (cx - sgn * 120, 60), (cx - sgn * 80, 40), top))
        c.poly(pts, (160, 108, 72), (96, 62, 40), 8)
        inner = [(x * 0.62 + cx * 0.38, y * 0.62 + 300 * 0.38 + 40) for x, y in pts]
        c.poly(inner, (196, 146, 108))
    c.save("dog_ears.png")


def dog_nose():
    c = Canvas(320, 240)
    pts = cbez((160, 205), (60, 190), (10, 50), (160, 45)) + cbez((160, 45), (310, 50), (260, 190), (160, 205))
    c.poly(pts, BLACK, (10, 10, 10), 4)
    c.ell(115, 85, 38, 16, (255, 255, 255, 160))
    c.ell(205, 80, 14, 8, (255, 255, 255, 110))
    c.save("dog_nose.png")


def fox_ears():
    c = Canvas(940, 430)
    for sgn in (-1, 1):
        cx = 470 + sgn * 280
        outer = [(cx - 150, 410), (cx + sgn * 50, 20), (cx + 150, 410)]
        c.poly(outer, (236, 130, 52), (160, 76, 24), 8)
        c.poly([(cx - 90, 400), (cx + sgn * 40, 130), (cx + 90, 400)], (255, 240, 226))
        tip = [(cx + sgn * 50 - 34, 90), (cx + sgn * 50, 20), (cx + sgn * 50 + 34, 90)]
        c.poly(tip, (90, 50, 30))
    c.save("fox_ears.png")


def pig_ears():
    c = Canvas(900, 320)
    for sgn in (-1, 1):
        cx = 450 + sgn * 300
        pts = cbez((cx - 130, 300), (cx - 120, 140), (cx + sgn * 40, 30), (cx + sgn * 70, 20)) + \
            cbez((cx + sgn * 70, 20), (cx + sgn * 60, 140), (cx + 130, 220), (cx + 130, 300))
        c.poly(pts, (255, 178, 190), (214, 110, 130), 7)
        c.poly([(x * 0.6 + cx * 0.4, y * 0.6 + 120) for x, y in pts], (255, 140, 160))
    c.save("pig_ears.png")


def pig_nose():
    c = Canvas(340, 250)
    c.ell(170, 125, 150, 105, (255, 170, 185), (214, 100, 125), 7)
    for sgn in (-1, 1):
        c.ell(170 + sgn * 55, 128, 24, 40, (200, 80, 105))
    c.ell(120, 60, 40, 14, (255, 255, 255, 140))
    c.save("pig_nose.png")


def antlers():
    c = Canvas(1100, 560)
    brown, dark = (150, 100, 62), (98, 62, 36)
    for sgn in (-1, 1):
        def X(x):
            return 550 + sgn * x
        main = bez((X(170), 540), (X(230), 300), (X(420), 60))
        b1 = bez((X(205), 380), (X(330), 330), (X(410), 250))
        b2 = bez((X(250), 250), (X(230), 140), (X(250), 60))
        b3 = bez((X(330), 160), (X(440), 170), (X(520), 140))
        for p in (main, b1, b2, b3):
            c.line(p, dark, 46)
        for p in (main, b1, b2, b3):
            c.line(p, brown, 32)
    c.save("antlers.png")


def red_nose():
    c = Canvas(200, 200)
    glow = c.layer()
    glow.circle(100, 100, 80, (255, 80, 80, 140))
    c.paste(glow, blur=10)
    c.circle(100, 100, 70, RED, RED_DK, 5)
    c.ell(76, 72, 22, 14, (255, 255, 255, 210))
    c.save("red_nose.png")


def frog_eyes():
    c = Canvas(820, 320)
    for cx in (230, 590):
        c.circle(cx, 170, 140, (110, 190, 90), (60, 130, 60), 8)
        c.circle(cx, 165, 95, WHITE, (60, 130, 60), 5)
        c.circle(cx + 12, 175, 50, BLACK)
        c.circle(cx - 6, 152, 18, (255, 255, 255))
    c.save("frog_eyes.png")


def duck_bill():
    c = Canvas(520, 260)
    lower = cbez((70, 140), (110, 250), (410, 250), (450, 140))
    c.poly(lower + [(260, 150)], (236, 132, 30), (170, 84, 14), 6)
    upper = cbez((20, 130), (40, 50), (480, 50), (500, 130)) + cbez((500, 130), (400, 175), (120, 175), (20, 130))
    c.poly(upper, (255, 196, 70), (190, 110, 20), 6)
    for sgn in (-1, 1):
        c.ell(260 + sgn * 34, 92, 10, 5, (190, 110, 20))
    c.ell(150, 85, 60, 10, (255, 255, 255, 150))
    c.save("duck_bill.png")


# ---------------------------------------------------------------- pesta ---
def party_hat():
    c = Canvas(420, 640)
    tri = [(210, 70), (40, 560), (380, 560)]
    c.poly(tri, (120, 200, 230), (50, 120, 160), 7)
    for i, (x0, x1) in enumerate(((150, 260), (110, 300), (75, 340))):       # stripes
        y0 = 70 + (i * 2 + 1) * 70
        h = 50
        t0, t1 = (y0 - 70) / 490.0, (y0 + h - 70) / 490.0
        c.poly([(210 - 170 * t0, y0), (210 + 170 * t0, y0), (210 + 170 * t1, y0 + h), (210 - 170 * t1, y0 + h)],
               (255, 160, 190))
    for x, y in ((180, 200), (240, 300), (160, 420), (270, 470), (210, 360)):
        c.circle(x, y, 13, (255, 236, 120))
    for i in range(9):                                                       # fringe
        c.circle(55 + i * 39, 565, 26, WHITE, (210, 200, 205), 4)
    for a in range(10):
        ang = 2 * math.pi * a / 10
        c.line([(210, 60), (210 + 52 * math.cos(ang), 60 + 52 * math.sin(ang))], (255, 120, 160), 14)
    c.circle(210, 60, 26, (255, 90, 140))
    c.save("party_hat.png")


def king_crown():
    c = Canvas(820, 460)
    pts = [(80, 420), (60, 140), (230, 270), (300, 70), (410, 240), (520, 70), (590, 270), (760, 140), (740, 420)]
    c.poly(pts, GOLD, GOLD_DK, 9)
    c.rect(70, 360, 750, 430, GOLD_LT, GOLD_DK, 7, radius=16)
    for x, y in ((60, 140), (300, 70), (520, 70), (760, 140)):
        c.circle(x, y, 26, GOLD_LT, GOLD_DK, 5)
    for x, col in ((200, (60, 140, 230)), (410, RED), (620, (60, 180, 120))):
        c.poly(ellipse_pts(x, 395, 40, 26), col, (40, 30, 30), 4)
        c.ell(x - 14, 386, 10, 6, (255, 255, 255, 200))
    c.poly(star_pts(410, 250, 50, 22), (255, 250, 220), GOLD_DK, 4)
    c.save("king_crown.png")


def halo():
    c = Canvas(760, 260)
    glow = c.layer()
    glow.ell(380, 130, 320, 80, None, (255, 230, 140, 220), 40)
    c.paste(glow, blur=16)
    c.ell(380, 130, 320, 80, None, GOLD_DK, 34)
    c.ell(380, 130, 320, 80, None, (255, 222, 120), 24)
    c.ell(380, 124, 300, 68, None, (255, 248, 210, 200), 6)
    c.save("halo.png")


def devil_horns():
    c = Canvas(940, 380)
    for sgn in (-1, 1):
        cx = 470 + sgn * 250
        def X(x):
            return cx + sgn * x
        pts = (cbez((X(-80), 370), (X(-90), 220), (X(40), 130), (X(120), 20))
               + cbez((X(120), 20), (X(80), 160), (X(110), 260), (X(80), 370)))
        c.poly(pts, (220, 40, 50), (130, 10, 24), 8)
        c.line(bez((X(-40), 330), (X(-30), 210), (X(70), 110)), (255, 140, 140, 170), 12)
    c.save("devil_horns.png")


PIXEL_GLASSES = [
    "##############################",
    "##############################",
    "   #########    #########     ",
    "   ##..#####    ##..#####     ",
    "   #..######    #..######     ",
    "    #######      #######      ",
    "     #####        #####       ",
]


def pixel_glasses():
    b = 30
    c = Canvas(len(PIXEL_GLASSES[0]) * b, len(PIXEL_GLASSES) * b)
    for r, row in enumerate(PIXEL_GLASSES):
        for col, ch in enumerate(row):
            if ch != " ":
                c.rect(col * b, r * b, col * b + b, r * b + b, BLACK if ch == "#" else WHITE)
    c.save("pixel_glasses.png")


def star_glasses():
    c = Canvas(940, 380)
    for cx in (250, 690):
        c.poly(star_pts(cx, 200, 180, 92), (255, 210, 60), (200, 130, 20), 8)
        c.poly(star_pts(cx, 205, 130, 66), (255, 140, 190, 170))
        c.ell(cx - 50, 150, 22, 14, (255, 255, 255, 180))
    c.line(bez((400, 165), (470, 130), (540, 165)), (200, 130, 20), 22)
    c.save("star_glasses.png")


def round_glasses():
    c = Canvas(940, 320)
    for cx in (250, 690):
        c.circle(cx, 170, 140, (210, 230, 240, 70), GOLD_DK, 16)
        c.circle(cx, 170, 140, None, GOLD, 9)
        c.ell(cx - 60, 110, 30, 16, (255, 255, 255, 150))
    c.line(bez((390, 160), (470, 120), (550, 160)), GOLD_DK, 14)
    c.line([(110, 150), (20, 130)], GOLD_DK, 14)
    c.line([(830, 150), (920, 130)], GOLD_DK, 14)
    c.save("round_glasses.png")


def mustache():
    c = Canvas(640, 230)
    half = (cbez((320, 70), (260, 30), (180, 40), (150, 110))
            + cbez((150, 110), (120, 170), (40, 170), (30, 110))
            + cbez((30, 110), (60, 200), (220, 200), (320, 130)))
    c.poly(half, (70, 44, 30), (40, 24, 16), 5)
    c.poly(mirror(half, 320), (70, 44, 30), (40, 24, 16), 5)
    c.save("mustache.png")


def tongue():
    c = Canvas(280, 300)
    pts = cbez((30, 40), (10, 230), (130, 300), (140, 270)) + cbez((140, 270), (150, 300), (270, 230), (250, 40))
    c.poly(pts, (240, 100, 120), (180, 50, 70), 6)
    c.line(bez((140, 60), (135, 150), (140, 220)), (200, 70, 90), 8)
    c.ell(90, 110, 22, 40, (255, 200, 210, 160))
    c.save("tongue.png")


def confetti():
    c = Canvas(1100, 520)
    rng = random.Random(7)
    cols = [(255, 120, 160), (120, 200, 240), (255, 220, 90), (150, 220, 150), (200, 150, 240), (255, 160, 90)]
    for _ in range(70):
        a = rng.uniform(math.pi * 1.0, math.pi * 2.0)
        rad = rng.uniform(0.55, 1.0)
        x, y = 550 + 520 * rad * math.cos(a), 500 + 480 * rad * math.sin(a)
        col = rng.choice(cols)
        kind = rng.random()
        if kind < 0.4:
            rot = rng.uniform(0, math.pi)
            c.poly([(x + 26 * math.cos(rot + k * math.pi / 2) * (1 if k % 2 == 0 else 0.45),
                     y + 26 * math.sin(rot + k * math.pi / 2) * (1 if k % 2 == 0 else 0.45)) for k in range(4)], col)
        elif kind < 0.75:
            c.circle(x, y, rng.uniform(10, 16), col)
        else:
            c.line(bez((x - 26, y), (x, y - 24), (x + 26, y)), col, 9)
    c.save("confetti.png")


def grad_cap():
    c = Canvas(900, 560)
    c.poly([(240, 250), (660, 250), (640, 460), (260, 460)], BLACK, (10, 10, 10), 4)
    c.poly([(450, 60), (870, 210), (450, 340), (30, 210)], (44, 40, 46), (10, 10, 10), 6)
    c.ell(450, 200, 26, 16, GOLD)
    c.line(bez((450, 200), (640, 230), (720, 260)), GOLD, 10)
    c.line([(720, 260), (720, 420)], GOLD, 10)
    for i in range(7):
        c.line([(708 + i * 4, 420), (700 + i * 7, 520)], GOLD_DK, 6)
    c.circle(720, 420, 18, GOLD)
    c.save("grad_cap.png")


def santa_hat():
    c = Canvas(980, 660)
    body = (cbez((120, 520), (180, 200), (420, 40), (720, 90))
            + cbez((720, 90), (860, 130), (880, 260), (860, 380))
            + cbez((860, 380), (820, 250), (700, 220), (760, 520)))
    c.poly(body, RED, RED_DK, 8)
    c.line(bez((300, 400), (420, 200), (640, 140)), (255, 120, 120, 140), 18)
    for i in range(12):
        c.circle(110 + i * 60, 540 + (8 if i % 2 else -4), 62, WHITE, (220, 210, 214), 5)
    c.circle(860, 400, 78, WHITE, (220, 210, 214), 5)
    c.save("santa_hat.png")


def beret():
    c = Canvas(860, 380)
    c.poly(ellipse_pts(430, 230, 400, 130, -0.08), (196, 40, 52), (120, 16, 30), 8)
    c.poly(ellipse_pts(420, 190, 290, 70, -0.08), (220, 70, 80, 140))
    c.line([(460, 110), (470, 40)], (120, 16, 30), 22)
    c.save("beret.png")


def cowboy_hat():
    c = Canvas(1000, 460)
    tan, dark = (196, 140, 86), (120, 78, 40)
    brim = cbez((30, 300), (200, 460), (800, 460), (970, 300)) + cbez((970, 300), (860, 380), (140, 380), (30, 300))
    c.poly(brim, tan, dark, 8)
    crown = (cbez((260, 340), (230, 120), (330, 40), (420, 80)) + cbez((420, 80), (480, 110), (520, 110), (580, 80))
             + cbez((580, 80), (670, 40), (770, 120), (740, 340)))
    c.poly(crown, tan, dark, 8)
    c.poly([(255, 290), (745, 290), (742, 340), (258, 340)], (120, 60, 40))
    c.poly(star_pts(500, 315, 26, 11), GOLD)
    c.save("cowboy_hat.png")


# ----------------------------------------------------------------- love ---
def heart_eyes():
    c = Canvas(940, 340)
    for cx in (250, 690):
        c.poly(heart_pts(cx, 175, 165), RED, RED_DK, 8)
        c.shine(cx - 70, 105, 34, 20, 200, -0.5)
    c.save("heart_eyes.png")


def cheek_heart():
    c = Canvas(220, 210)
    c.poly(heart_pts(110, 110, 90), (255, 255, 255))
    c.poly(heart_pts(110, 110, 72), (240, 70, 110))
    c.shine(80, 78, 16, 10, 200, -0.6)
    c.save("cheek_heart.png")


def kiss():
    c = Canvas(320, 220)
    col, dark = (210, 30, 70, 230), (160, 10, 50, 230)
    upper = (cbez((20, 110), (70, 30), (120, 20), (160, 60)) + cbez((160, 60), (200, 20), (250, 30), (300, 110))
             + cbez((300, 110), (220, 120), (100, 120), (20, 110)))
    lower = cbez((20, 115), (90, 135), (230, 135), (300, 115)) + cbez((300, 115), (250, 220), (70, 220), (20, 115))
    c.poly(upper, col, dark, 4)
    c.poly(lower, col, dark, 4)
    c.ell(130, 160, 40, 10, (255, 140, 170, 150))
    c.ell(110, 70, 26, 8, (255, 140, 170, 130))
    c.save("kiss.png")


def heart_halo():
    c = Canvas(1100, 420)
    n = 13
    cols = [RED, PINK, (255, 255, 255), PINK_DK]
    for i in range(n):
        a = math.pi * (1.0 + i / (n - 1))
        x, y = 550 + 490 * math.cos(a), 400 + 330 * math.sin(a)
        size = 34 + 12 * math.sin(math.pi * i / (n - 1))
        c.poly(heart_pts(x, y, size, (a + math.pi / 2) * 0.4), cols[i % len(cols)], (255, 255, 255), 4)
    c.save("heart_halo.png")


def love_bubbles():
    c = Canvas(560, 560)
    for x, y, s, col in ((150, 440, 90, PINK), (330, 300, 70, RED_LT), (200, 170, 50, PINK_DK),
                         (420, 110, 40, PINK), (470, 420, 34, RED_LT)):
        c.poly(heart_pts(x, y, s, -0.2), col + (230,), (255, 255, 255), 5)
        c.shine(x - s * 0.4, y - s * 0.35, s * 0.18, s * 0.1, 200, -0.6)
    c.save("love_bubbles.png")


# ----------------------------------------------------------------- glam ---
def cheek_star():
    c = Canvas(220, 220)
    c.poly(star_pts(110, 115, 100, 46), (255, 255, 255))
    c.poly(star_pts(110, 115, 80, 36), (255, 206, 70))
    c.shine(90, 85, 14, 8, 200, -0.6)
    c.save("cheek_star.png")


def gems():
    c = Canvas(1000, 160)
    cols = [(200, 235, 255), (255, 200, 230), (255, 255, 255), (220, 200, 255)]
    for cx in (290, 710):
        for i in range(7):
            t = (i - 3) / 3.0
            x, y = cx + t * 150, 60 + 50 * t * t
            r = 26 - abs(i - 3) * 4
            c.poly(star_pts(x, y, r * 1.2, r * 0.92, 8), cols[i % len(cols)], (110, 110, 170), 3)
            c.poly(star_pts(x, y, r * 0.7, r * 0.5, 8), (255, 255, 255, 170))
            c.circle(x - r * 0.35, y - r * 0.35, r * 0.25, (255, 255, 255, 250))
    c.save("gems.png")


def freckles():
    c = Canvas(720, 220)
    rng = random.Random(3)
    for side in (-1, 1):
        for _ in range(16):
            x = 360 + side * rng.uniform(70, 330)
            y = 110 + rng.uniform(-60, 60) + (abs(x - 360) - 200) * 0.12
            c.circle(x, y, rng.uniform(5, 9), (150, 90, 60, 150))
    for _ in range(6):
        c.circle(360 + rng.uniform(-50, 50), 60 + rng.uniform(-30, 30), rng.uniform(4, 7), (150, 90, 60, 130))
    c.save("freckles.png")


def hibiscus():
    c = Canvas(460, 460)
    leaf(c, 120, 340, 110, 2.4, (90, 170, 90), (50, 120, 60))
    leaf(c, 330, 360, 100, 0.8, (90, 170, 90), (50, 120, 60))
    for i in range(5):
        a = -math.pi / 2 + 2 * math.pi * i / 5
        px, py = 230 + 105 * math.cos(a), 220 + 105 * math.sin(a)
        c.poly(ellipse_pts(px, py, 110, 85, a), (255, 90, 120), (190, 40, 70), 5)
    c.circle(230, 220, 46, (200, 30, 70))
    c.line(bez((230, 220), (280, 150), (320, 110)), (255, 220, 120), 10)
    for dx, dy in ((0, 0), (16, -10), (-6, -16), (12, 12)):
        c.circle(322 + dx, 104 + dy, 10, GOLD)
    c.save("hibiscus.png")


def tear_glitter():
    c = Canvas(1000, 260)
    rng = random.Random(11)
    for cx in (290, 710):
        for i in range(12):
            y = 26 + i * 18
            x = cx + rng.uniform(-22, 22) + i * 3
            r = rng.uniform(10, 20) * (1.0 - i / 24.0)
            col = rng.choice([(170, 215, 255), (255, 190, 230), (255, 255, 255), (215, 190, 255)])
            c.poly(star_pts(x, y, r, r * 0.35, 4), col + (250,), (110, 110, 180), 2)
    c.save("tear_glitter.png")


# ------------------------------------------------------------------ main ---
def draw_all():
    OUT.mkdir(parents=True, exist_ok=True)
    bow("bow.png")
    bow("bow_white.png", (255, 250, 247), (226, 150, 172), (255, 255, 255))
    bow("bow_red.png", RED, RED_DK, RED_LT)
    bow("bow_lilac.png", (210, 180, 245), (150, 110, 200), (240, 228, 255))
    blush(); tiara(); heart_glasses(); floating_hearts(); pearl_headband(); flower_crown()
    butterflies(); sparkles(); cherries()
    cat_ears(); bunny_ears()
    ears_round("bear_ears.png", (150, 100, 66), (210, 160, 126), (96, 62, 38))
    ears_round("panda_ears.png", (40, 38, 42), (80, 78, 84), (10, 10, 10))
    ears_round("mouse_ears.png", (170, 170, 180), (255, 180, 196), (110, 110, 120), w=1000, r=170, gap=620)
    whiskers("whiskers.png", (250, 150, 170), (200, 90, 120))
    whiskers("whiskers_dark.png", (60, 50, 54), (20, 16, 18))
    dog_ears(); dog_nose(); fox_ears(); pig_ears(); pig_nose(); antlers(); red_nose()
    frog_eyes(); duck_bill()
    party_hat(); king_crown(); halo(); devil_horns(); pixel_glasses(); star_glasses()
    round_glasses(); mustache(); tongue(); confetti(); grad_cap(); santa_hat(); beret(); cowboy_hat()
    heart_eyes(); cheek_heart(); kiss(); heart_halo(); love_bubbles()
    cheek_star(); gems(); freckles(); hibiscus(); tear_glitter()


def P(file, anchor, width, dy=0.0, dx=0.0, rot=0.0):
    p = {"file": file, "anchor": anchor, "width": width}
    if dy:
        p["dy"] = dy
    if dx:
        p["dx"] = dx
    if rot:
        p["rot"] = rot
    return p


BLUSH = P("blush.png", "cheeks", 0.34)

# (id, label, category, [parts]). anchor + width (face widths) + dy/dx (face
# widths, dy < 0 = up) + rot (degrees, extra tilt). See stickers/README.md.
SPEC = [
    # Cute
    ("pita", "Pita", "Cute", [P("bow.png", "head", 0.8, -0.08)]),
    ("pita_putih", "Putih", "Cute", [P("bow_white.png", "head", 0.8, -0.08)]),
    ("pita_merah", "Merah", "Cute", [P("bow_red.png", "head", 0.8, -0.08)]),
    ("pita_lilac", "Lilac", "Cute", [P("bow_lilac.png", "head", 0.8, -0.08)]),
    ("pita_kembar", "Pita Duo", "Cute", [P("bow.png", "head", 0.45, 0.12, -0.42, -20),
                                            P("bow.png", "head", 0.45, 0.12, 0.42, 20)]),
    ("pita_blush", "Pita Pipi", "Cute", [P("bow.png", "head", 0.8, -0.08), BLUSH]),
    ("blush", "Blush", "Cute", [P("blush.png", "cheeks", 0.36)]),
    ("tiara", "Tiara", "Cute", [P("tiara.png", "head", 0.85, -0.08)]),
    ("bando_mutiara", "Mutiara", "Cute", [P("pearl_headband.png", "head", 1.15, 0.12)]),
    ("mahkota_bunga", "Flower", "Cute", [P("flower_crown.png", "head", 1.25, 0.08)]),
    ("kupu_kupu", "Kupu-kupu", "Cute", [P("butterflies.png", "head", 1.15, -0.05)]),
    ("sparkle", "Sparkle", "Cute", [P("sparkles.png", "head", 1.6, -0.12)]),
    ("ceri", "Ceri", "Cute", [P("cherries.png", "head", 0.36, 0.1, 0.4, 15)]),
    # Hewan
    ("kucing", "Kucing", "Hewan", [P("cat_ears.png", "head", 1.4), BLUSH]),
    ("kucing_kumis", "Meong", "Hewan", [P("cat_ears.png", "head", 1.4),
                                               P("whiskers_dark.png", "nose", 1.1, 0.02)]),
    ("kelinci", "Kelinci", "Hewan", [P("bunny_ears.png", "head", 0.95, -0.35)]),
    ("kelinci_hidung", "Bunny", "Hewan", [P("bunny_ears.png", "head", 0.95, -0.35),
                                                 P("whiskers.png", "nose", 1.0, 0.02)]),
    ("anjing", "Anjing", "Hewan", [P("dog_ears.png", "head", 1.5, 0.32), P("dog_nose.png", "nose", 0.36, 0.0)]),
    ("beruang", "Beruang", "Hewan", [P("bear_ears.png", "head", 1.2, -0.15), BLUSH]),
    ("panda", "Panda", "Hewan", [P("panda_ears.png", "head", 1.2, -0.15), BLUSH]),
    ("rubah", "Rubah", "Hewan", [P("fox_ears.png", "head", 1.35, -0.12), P("whiskers_dark.png", "nose", 1.1, 0.02)]),
    ("tikus", "Tikus", "Hewan", [P("mouse_ears.png", "head", 1.45, -0.15), P("whiskers.png", "nose", 1.0, 0.02)]),
    ("babi", "Babi", "Hewan", [P("pig_ears.png", "head", 1.3, -0.08), P("pig_nose.png", "nose", 0.36, 0.02)]),
    ("rusa", "Rusa", "Hewan", [P("antlers.png", "head", 1.6, -0.25), P("red_nose.png", "nose", 0.2, 0.0)]),
    ("kodok", "Kodok", "Hewan", [P("frog_eyes.png", "head", 1.0, -0.15), BLUSH]),
    ("bebek", "Bebek", "Hewan", [P("duck_bill.png", "mouth", 0.7, -0.04)]),
    # Pesta
    ("topi_pesta", "Ultah", "Pesta", [P("party_hat.png", "head", 0.55, -0.32, 0.2, 18)]),
    ("mahkota", "Mahkota", "Pesta", [P("king_crown.png", "head", 0.9, -0.25)]),
    ("malaikat", "Malaikat", "Pesta", [P("halo.png", "head", 0.95, -0.28)]),
    ("setan", "Tanduk", "Pesta", [P("devil_horns.png", "head", 1.15, -0.1)]),
    ("kacamata_pixel", "Pixel", "Pesta", [P("pixel_glasses.png", "eyes", 1.1, 0.04, -0.03)]),
    ("kacamata_bintang", "Star", "Pesta", [P("star_glasses.png", "eyes", 1.12, 0.0)]),
    ("kacamata_bulat", "Retro", "Pesta", [P("round_glasses.png", "eyes", 1.1, 0.01)]),
    ("kumis", "Kumis", "Pesta", [P("mustache.png", "mouth", 0.6, -0.1)]),
    ("lidah", "Melet", "Pesta", [P("tongue.png", "mouth", 0.28, 0.12)]),
    ("konfeti", "Konfeti", "Pesta", [P("confetti.png", "head", 1.8, -0.1)]),
    ("wisuda", "Wisuda", "Pesta", [P("grad_cap.png", "head", 1.2, -0.32, 0.0, -6)]),
    ("santa", "Santa", "Pesta", [P("santa_hat.png", "head", 1.3, -0.3, 0.05)]),
    ("beret", "Beret", "Pesta", [P("beret.png", "head", 1.1, -0.22, 0.08, -10)]),
    ("koboi", "Koboi", "Pesta", [P("cowboy_hat.png", "head", 1.5, -0.3)]),
    # Love
    ("love", "Love", "Love", [P("hearts.png", "head", 1.7, -0.2)]),
    ("kacamata", "Kacamata", "Love", [P("heart_glasses.png", "eyes", 1.05, 0.02)]),
    ("mata_love", "Mata Love", "Love", [P("heart_eyes.png", "eyes", 1.0, 0.0)]),
    ("pipi_love", "Pipi Love", "Love", [P("cheek_heart.png", "cheeks", 0.2)]),
    ("kiss", "Kiss", "Love", [P("kiss.png", "cheeks", 0.26, -0.02, 0.0, -15)]),
    ("lingkar_love", "Love Ring", "Love", [P("heart_halo.png", "head", 1.5, -0.05)]),
    ("love_melayang", "Love Pop", "Love", [P("love_bubbles.png", "head", 0.6, 0.05, 0.55)]),
    ("love_blush", "Love Pipi", "Love", [P("hearts.png", "head", 1.7, -0.2), BLUSH]),
    # Glam
    ("pipi_bintang", "Pipi Star", "Glam", [P("cheek_star.png", "cheeks", 0.2)]),
    ("permata", "Permata", "Glam", [P("gems.png", "eyes", 1.0, 0.14)]),
    ("glitter", "Glitter", "Glam", [P("tear_glitter.png", "eyes", 1.0, 0.24)]),
    ("freckles", "Freckles", "Glam", [P("freckles.png", "nose", 0.78, -0.06)]),
    ("bunga_telinga", "Hibiscus", "Glam", [P("hibiscus.png", "head", 0.42, 0.5, 0.55)]),
    ("glam_sparkle", "Glam", "Glam", [P("sparkles.png", "head", 1.6, -0.12),
                                                   P("gems.png", "eyes", 1.0, 0.14)]),
    ("bunga_blush", "Bunga", "Glam", [P("flower_crown.png", "head", 1.25, 0.08), BLUSH]),
]


def write_spec():
    spec = {"stickers": [{"id": i, "label": label, "category": cat, "parts": parts}
                         for i, label, cat, parts in SPEC]}
    (OUT / "stickers.json").write_text(json.dumps(spec, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote stickers.json ({len(SPEC)} stickers)")


if __name__ == "__main__":
    draw_all()
    write_spec()

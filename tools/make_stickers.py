"""Coquette sticker set for the booth (drawn, no external art)."""
import math, sys, json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

# Usage: python tools/make_stickers.py stickers
OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
SS = 4                               # supersampling
PINK, PINK_DK, PINK_LT = (244, 143, 177), (214, 92, 135), (255, 214, 226)
WHITE, GOLD, GOLD_DK = (255, 252, 250), (236, 196, 98), (190, 140, 50)
LINE = (120, 50, 75)

def canvas(w, h):
    return Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))

def done(im, name, w, h):
    im = im.resize((w, h), Image.LANCZOS)
    im.save(OUT / name)
    print("wrote", name, im.size)

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

def outline_poly(d, pts, fill, line, width):
    d.polygon(pts, fill=fill)
    d.line(pts + [pts[0]], fill=line, width=width, joint="curve")

# 1. Bow (pita) ---------------------------------------------------------------
def ellipse_pts(cx, cy, rx, ry, rot, n=90):
    out = []
    for i in range(n):
        t = 2 * math.pi * i / n
        x, y = rx * math.cos(t), ry * math.sin(t)
        out.append((cx + x * math.cos(rot) - y * math.sin(rot), cy + x * math.sin(rot) + y * math.cos(rot)))
    return out

def bow():
    W, H = 640, 440
    im = canvas(W, H); d = ImageDraw.Draw(im); k = SS
    cx, cy = 320, 165
    for sgn in (-1, 1):                     # ribbon tails
        tail = [(cx + sgn * 10, cy + 30), (cx + sgn * 120, cy + 250), (cx + sgn * 92, cy + 238),
                (cx + sgn * 78, cy + 268), (cx + sgn * 2, cy + 60)]
        outline_poly(d, [(x * k, y * k) for x, y in tail], PINK, PINK_DK, 6 * k)
    for sgn in (-1, 1):                     # loops
        loop = ellipse_pts((cx + sgn * 150) * k, (cy - 8) * k, 155 * k, 100 * k, sgn * 0.22)
        outline_poly(d, loop, PINK, PINK_DK, 7 * k)
        fold = ellipse_pts((cx + sgn * 105) * k, (cy + 4) * k, 75 * k, 45 * k, sgn * 0.22)
        d.polygon(fold, fill=PINK_DK + (50,))
        d.ellipse([(cx + sgn * 205 - 30) * k, (cy - 75) * k, (cx + sgn * 205 + 30) * k, (cy - 52) * k],
                  fill=PINK_LT + (220,))
    d.rounded_rectangle([(cx - 46) * k, (cy - 58) * k, (cx + 46) * k, (cy + 58) * k], radius=32 * k,
                        fill=PINK, outline=PINK_DK, width=7 * k)
    d.ellipse([(cx - 24) * k, (cy - 40) * k, (cx + 2) * k, (cy - 20) * k], fill=PINK_LT + (230,))
    done(im, "bow.png", W, H)

# 2. Cat ears -----------------------------------------------------------------
def cat_ears():
    W, H = 900, 360
    im = canvas(W, H); d = ImageDraw.Draw(im); k = SS
    for cx in (210, 690):
        sgn = -1 if cx < W / 2 else 1
        tip = (cx + sgn * 30, 20)
        outer = [(cx - 160, 340), tip, (cx + 160, 340)]
        outer = [(x * k, y * k) for x, y in outer]
        d.polygon(outer, fill=(70, 55, 60))
        d.line(outer + [outer[0]], fill=(40, 30, 35), width=8 * k, joint="curve")
        inner = [(cx - 95, 330), (cx + sgn * 25, 95), (cx + 95, 330)]
        d.polygon([(x * k, y * k) for x, y in inner], fill=PINK)
        d.ellipse([(cx + sgn * 10 - 18) * k, 150 * k, (cx + sgn * 10 + 18) * k, 200 * k], fill=PINK_LT + (200,))
    im = im.filter(ImageFilter.GaussianBlur(3))
    done(im, "cat_ears.png", W, H)

# 3. Bunny ears ---------------------------------------------------------------
def bunny_ears():
    W, H = 640, 620
    im = canvas(W, H); d = ImageDraw.Draw(im); k = SS
    for cx, rot in ((200, -0.18), (440, 0.18)):
        def ear(rx, ry, fill, outline=None, width=0):
            pts = []
            for i in range(100):
                t = 2 * math.pi * i / 100
                x, y = rx * math.sin(t), -ry * (1 - math.cos(t)) / 2 * 2
                x2 = x * math.cos(rot) - y * math.sin(rot)
                y2 = x * math.sin(rot) + y * math.cos(rot)
                pts.append(((cx + x2) * k, (600 + y2) * k))
            d.polygon(pts, fill=fill)
            if outline:
                d.line(pts + [pts[0]], fill=outline, width=width, joint="curve")
        ear(95, 290, WHITE, (205, 190, 195), 7 * k)
        ear(52, 230, PINK)
    done(im, "bunny_ears.png", W, H)

# 4. Heart glasses ------------------------------------------------------------
def heart_glasses():
    W, H = 900, 380
    im = canvas(W, H); d = ImageDraw.Draw(im); k = SS
    for cx in (240, 660):
        outer = heart_pts(cx * k, 200 * k, 190 * k)
        d.polygon(outer, fill=PINK_DK)
        lens = heart_pts(cx * k, 196 * k, 155 * k)
        d.polygon(lens, fill=(255, 120, 165, 150))
        d.ellipse([(cx - 95) * k, 105 * k, (cx - 45) * k, 150 * k], fill=(255, 255, 255, 170))
    d.line([(395 * k, 150 * k), (450 * k, 128 * k), (505 * k, 150 * k)], fill=PINK_DK, width=26 * k, joint="curve")
    done(im, "heart_glasses.png", W, H)

# 5. Blush (one cheek; drawn twice) -------------------------------------------
def blush():
    W, H = 300, 200
    base = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0)); d = ImageDraw.Draw(base); k = SS
    d.ellipse([30 * k, 40 * k, 270 * k, 160 * k], fill=(255, 120, 150, 150))
    base = base.filter(ImageFilter.GaussianBlur(26 * k))
    d = ImageDraw.Draw(base)
    for i, x in enumerate((95, 140, 185)):
        d.line([(x * k, 120 * k), ((x + 26) * k, 78 * k)], fill=(230, 80, 120, 230), width=9 * k)
    d.polygon(heart_pts(235 * k, 45 * k, 26 * k), fill=(240, 90, 130, 240))
    done(base, "blush.png", W, H)

# 6. Tiara --------------------------------------------------------------------
def tiara():
    W, H = 760, 360
    im = canvas(W, H); d = ImageDraw.Draw(im); k = SS
    pts = [(40, 330)]
    peaks = [(120, 190), (220, 120), (300, 170), (380, 40), (460, 170), (540, 120), (640, 190)]
    valleys = [(170, 260), (260, 230), (340, 240), (420, 240), (500, 230), (590, 260)]
    for i, p in enumerate(peaks):
        pts.append(p)
        if i < len(valleys):
            pts.append(valleys[i])
    pts += [(720, 330), (380, 300)]
    outline_poly(d, [(x * k, y * k) for x, y in pts], GOLD, GOLD_DK, 8 * k)
    for (x, y), r in zip(peaks, (18, 22, 18, 34, 18, 22, 18)):
        d.ellipse([(x - r) * k, (y - r) * k, (x + r) * k, (y + r) * k], fill=PINK, outline=PINK_DK, width=5 * k)
    d.polygon(heart_pts(380 * k, 245 * k, 40 * k), fill=PINK, outline=PINK_DK)
    done(im, "tiara.png", W, H)

# 7. Floating hearts ----------------------------------------------------------
def hearts():
    W, H = 1000, 420
    im = canvas(W, H); d = ImageDraw.Draw(im); k = SS
    spots = [(90, 300, 48, -0.3, PINK), (210, 170, 64, -0.15, PINK_DK), (370, 90, 46, -0.05, PINK_LT),
             (500, 60, 70, 0.0, PINK), (640, 95, 44, 0.08, PINK_DK), (790, 170, 62, 0.18, PINK_LT),
             (910, 300, 46, 0.3, PINK)]
    for x, y, sz, rot, col in spots:
        pts = heart_pts(x * k, y * k, sz * k, rot)
        d.polygon(pts, fill=col + (235,))
        d.line(pts + [pts[0]], fill=(255, 255, 255, 220), width=5 * k)
    done(im, "hearts.png", W, H)

bow(); cat_ears(); bunny_ears(); heart_glasses(); blush(); tiara(); hearts()

# placement: anchor + size relative to face width + vertical offset (face widths, up = -)
spec = {"stickers": [
    {"id": "pita", "label": "Pita", "parts": [{"file": "bow.png", "anchor": "head", "width": 0.8, "dy": -0.08}]},
    {"id": "kucing", "label": "Kucing", "parts": [{"file": "cat_ears.png", "anchor": "head", "width": 1.4, "dy": 0.0},
                                                  {"file": "blush.png", "anchor": "cheeks", "width": 0.34}]},
    {"id": "kelinci", "label": "Kelinci", "parts": [{"file": "bunny_ears.png", "anchor": "head", "width": 0.95, "dy": -0.35}]},
    {"id": "kacamata", "label": "Kacamata", "parts": [{"file": "heart_glasses.png", "anchor": "eyes", "width": 1.05, "dy": 0.02}]},
    {"id": "blush", "label": "Blush", "parts": [{"file": "blush.png", "anchor": "cheeks", "width": 0.36}]},
    {"id": "tiara", "label": "Tiara", "parts": [{"file": "tiara.png", "anchor": "head", "width": 0.85, "dy": 0.0}]},
    {"id": "love", "label": "Love", "parts": [{"file": "hearts.png", "anchor": "head", "width": 1.7, "dy": -0.2}]},
    {"id": "pita_blush", "label": "Pita + Blush", "parts": [{"file": "bow.png", "anchor": "head", "width": 0.8, "dy": -0.08},
                                                            {"file": "blush.png", "anchor": "cheeks", "width": 0.34}]},
]}
(OUT / "stickers.json").write_text(json.dumps(spec, indent=2) + "\n")
print("wrote stickers.json")

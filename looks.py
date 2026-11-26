"""Photobooth film looks for the filter panel (CPU, OpenCV + numpy).

Each look: BGR uint8 in, BGR uint8 out, same size. Grain and vignette scale
with the picture, so the small preview and the full-size print match.
"""

import cv2
import numpy as np

LOOK_IDS = ["booth_sepia", "booth_bw", "silver", "film", "kstudio", "redroom", "flash", "faded"]
LOOK_LABELS = {
    "booth_sepia": "Vintage Booth", "booth_bw": "Classic B&W", "silver": "Silver",
    "film": "Film Warm", "kstudio": "K-Studio", "redroom": "Red Room",
    "flash": "Retro Flash", "faded": "Faded Film",
}


def _curve(points):
    """256-entry LUT through (x, y) control points, 0..255."""
    xs, ys = zip(*points)
    return np.clip(np.interp(np.arange(256), xs, ys), 0, 255).astype(np.uint8)


def _luma(img):
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _vignette(img, amount):
    h, w = img.shape[:2]
    sh, sw = max(2, h // 8), max(2, w // 8)
    yy, xx = np.mgrid[0:sh, 0:sw].astype(np.float32)
    r = np.sqrt(((xx - sw / 2) / (sw / 2)) ** 2 + ((yy - sh / 2) / (sh / 2)) ** 2) / 1.41
    m = 1.0 - amount * np.clip(r, 0, 1) ** 2.2
    m = cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR)
    return np.clip(img.astype(np.float32) * m[..., None], 0, 255).astype(np.uint8)


def _grain(img, strength, mono=True, seed=11):
    """Film grain sized to the picture (about 1 grain per 1/1400 of the long side)."""
    h, w = img.shape[:2]
    k = max(1.0, max(h, w) / 1400.0)
    gh, gw = max(2, int(h / k)), max(2, int(w / k))
    rng = np.random.default_rng(seed)
    n = rng.normal(0, 1, (gh, gw) if mono else (gh, gw, 3)).astype(np.float32)
    n = cv2.resize(n, (w, h), interpolation=cv2.INTER_LINEAR)
    if n.ndim == 2:
        n = n[..., None]
    return np.clip(img.astype(np.float32) + n * strength, 0, 255).astype(np.uint8)


def _tone(gray, lut_b, lut_g, lut_r):
    return cv2.merge([cv2.LUT(gray, lut_b), cv2.LUT(gray, lut_g), cv2.LUT(gray, lut_r)])


def _saturation(img, k):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[..., 1] = np.clip(hsv[..., 1] * k, 0, 255)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)


def _glow(img, amount, radius_frac=0.012):
    r = max(2.0, max(img.shape[:2]) * radius_frac)
    blur = cv2.GaussianBlur(img, (0, 0), r).astype(np.float32) / 255.0
    base = img.astype(np.float32) / 255.0
    screen = 1 - (1 - base) * (1 - blur)
    return np.clip((base + (screen - base) * amount) * 255, 0, 255).astype(np.uint8)


def look_booth_sepia(img):
    """Old photobooth strip: warm brown mono, soft contrast, cream highlights."""
    g = cv2.LUT(_luma(img), _curve([(0, 18), (60, 62), (128, 132), (200, 205), (255, 240)]))
    out = _tone(g, _curve([(0, 10), (128, 104), (255, 214)]),
                _curve([(0, 14), (128, 122), (255, 232)]),
                _curve([(0, 22), (128, 146), (255, 244)]))
    return _grain(_vignette(out, 0.35), 7)


def look_booth_bw(img):
    """Classic booth black & white: deep blacks, bright whites, a bit of grain."""
    g = cv2.LUT(_luma(img), _curve([(0, 0), (40, 18), (128, 132), (215, 238), (255, 255)]))
    return _grain(_vignette(cv2.cvtColor(g, cv2.COLOR_GRAY2BGR), 0.25), 6)


def look_silver(img):
    """Soft silver mono: lifted blacks, gentle contrast, cool grey."""
    g = cv2.LUT(_luma(img), _curve([(0, 34), (128, 134), (255, 236)]))
    out = _tone(g, _curve([(0, 40), (255, 240)]), _curve([(0, 36), (255, 236)]),
                _curve([(0, 32), (255, 232)]))
    return _grain(out, 4)


def look_film(img):
    """Warm film (Portra-like): warm highlights, soft shadows, calm greens."""
    b, g, r = cv2.split(img)
    r = cv2.LUT(r, _curve([(0, 18), (128, 140), (255, 252)]))
    g = cv2.LUT(g, _curve([(0, 14), (128, 130), (255, 244)]))
    b = cv2.LUT(b, _curve([(0, 20), (128, 116), (255, 226)]))
    out = _saturation(cv2.merge([b, g, r]), 0.9)
    return _grain(_vignette(out, 0.18), 4, mono=False)


def look_kstudio(img):
    """Korean studio: bright, airy, low contrast, a soft peach-pink glow."""
    out = cv2.LUT(img, _curve([(0, 22), (64, 88), (128, 158), (200, 222), (255, 250)]))
    b, g, r = cv2.split(out)
    r = cv2.LUT(r, _curve([(0, 6), (255, 255)]))
    b = cv2.LUT(b, _curve([(0, 8), (255, 248)]))
    out = _saturation(cv2.merge([b, g, r]), 0.85)
    return _glow(out, 0.35)


def look_redroom(img):
    """Red studio curtain: rich reds, warm shadows, deeper contrast."""
    b, g, r = cv2.split(img)
    r = cv2.LUT(r, _curve([(0, 10), (100, 118), (255, 255)]))
    g = cv2.LUT(g, _curve([(0, 0), (128, 116), (255, 236)]))
    b = cv2.LUT(b, _curve([(0, 0), (128, 108), (255, 226)]))
    out = _saturation(cv2.merge([b, g, r]), 1.15)
    out = cv2.LUT(out, _curve([(0, 0), (60, 48), (190, 200), (255, 255)]))
    return _grain(_vignette(out, 0.4), 4)


def look_flash(img):
    """Retro direct flash: punchy, bright centre, teal shadows, warm highlights."""
    out = cv2.LUT(img, _curve([(0, 0), (50, 34), (128, 140), (220, 245), (255, 255)]))
    b, g, r = cv2.split(out)
    b = cv2.LUT(b, _curve([(0, 14), (128, 126), (255, 240)]))
    r = cv2.LUT(r, _curve([(0, 0), (128, 132), (255, 255)]))
    out = _saturation(cv2.merge([b, g, r]), 1.1)
    return _grain(_vignette(out, 0.5), 5)


def look_faded(img):
    """Faded film: milky blacks, low saturation, slightly green-cyan shadows."""
    b, g, r = cv2.split(cv2.LUT(img, _curve([(0, 40), (128, 136), (255, 232)])))
    g = cv2.LUT(g, _curve([(0, 8), (255, 252)]))
    b = cv2.LUT(b, _curve([(0, 10), (255, 246)]))
    out = _saturation(cv2.merge([b, g, r]), 0.7)
    return _grain(out, 5, mono=False)


_LOOKS = {
    "booth_sepia": look_booth_sepia, "booth_bw": look_booth_bw, "silver": look_silver,
    "film": look_film, "kstudio": look_kstudio, "redroom": look_redroom,
    "flash": look_flash, "faded": look_faded,
}


def apply_look(img_bgr, name):
    fn = _LOOKS.get(name)
    if fn is None or img_bgr is None or img_bgr.size == 0:
        return img_bgr
    return fn(img_bgr)

"""Photo effects for the filter panel: lens, lighting, face-aware and
artistic looks. CPU only (OpenCV + numpy), no GPU, no network.

Every effect takes a BGR uint8 image and returns a new BGR uint8 image of the
same size. Strengths are relative to image size, so the small preview and
the full-res print look the same.

Face-aware effects use OpenCV's YuNet detector (models/face_detection_yunet_
2023mar.onnx, ~230 KB). If the model file is missing they fall back to the
Haar cascade that ships with opencv-python.
"""

import os
import sys
import logging
import threading

import cv2
import numpy as np

LOG = logging.getLogger("effects")

EFFECT_IDS = [
    "fisheye", "vignette", "glow", "lightleak", "film", "duotone", "neon",
    "spotlight", "bigeyes", "bighead",
    "sketch", "comic", "popart", "vintage70",
]
EFFECT_LABELS = {
    "fisheye": "Fisheye", "vignette": "Vignette", "glow": "Glow",
    "lightleak": "Light Leak", "film": "Film", "duotone": "Duotone",
    "neon": "Neon", "spotlight": "Spotlight", "bigeyes": "Big Eyes",
    "bighead": "Big Head", "sketch": "Sketch", "comic": "Comic",
    "popart": "Pop Art", "vintage70": "Vintage 70s",
}


# ---------------------------------------------------------------- helpers
def _app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _screen(a, b):
    """Screen blend, float images in [0, 1]."""
    return 1.0 - (1.0 - a) * (1.0 - b)


def _f(img):
    return img.astype(np.float32) / 255.0


def _u8(img):
    return np.clip(img * 255.0, 0, 255).astype(np.uint8)


def _vignette_mask(h, w, amount=0.55, power=2.0):
    r = _radial(h, w)
    return (1.0 - amount * np.power(r, power))[..., None]


def _grain(h, w, sigma=0.035, seed=7):
    # Fixed seed: preview and print get the same grain pattern.
    rng = np.random.default_rng(seed)
    g = rng.normal(0.0, sigma, (h, w)).astype(np.float32)
    k = max(1, int(round(min(h, w) / 900)))   # grain size scales with image
    if k > 1:
        g = cv2.GaussianBlur(g, (0, 0), k * 0.6)
        g *= sigma / max(1e-6, g.std())
    return g[..., None]


def _blur(img, sigma):
    """Big Gaussian blur, fast: blur a downscaled copy, scale back up."""
    h, w = img.shape[:2]
    f = min(1.0, 3.0 / max(1e-6, sigma))
    if f >= 0.9:
        return cv2.GaussianBlur(img, (0, 0), sigma)
    sw, sh = max(2, int(w * f)), max(2, int(h * f))
    small = cv2.resize(img, (sw, sh), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), sigma * f)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def _radial(h, w, step=4):
    """Normalised distance from centre (0 centre .. 1 corners), computed at
    1/step resolution and upscaled - smooth fields don't need full res."""
    sh, sw = max(2, h // step), max(2, w // step)
    ys, xs = np.mgrid[0:sh, 0:sw].astype(np.float32)
    dx, dy = xs - (sw - 1) / 2.0, ys - (sh - 1) / 2.0
    r = np.sqrt(dx * dx + dy * dy)
    r /= max(1e-6, r.max())
    return cv2.resize(r, (w, h), interpolation=cv2.INTER_LINEAR)


def _work_size(img, long_side):
    """Downscale for slow filters; returns (small, scale back factor)."""
    h, w = img.shape[:2]
    s = long_side / float(max(h, w))
    if s >= 1.0:
        return img, None
    small = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    return small, (w, h)


def _restore(img, size):
    if size is None:
        return img
    return cv2.resize(img, size, interpolation=cv2.INTER_CUBIC)


# ---------------------------------------------------------------- faces
_det_lock = threading.Lock()
_yunet = None
_haar = None


def detect_faces(img):
    """List of faces: dict(box=(x, y, w, h), eyes=[(x, y), (x, y)]) in the
    image's own pixel coords. Biggest face first."""
    global _yunet, _haar
    h, w = img.shape[:2]
    s = min(1.0, 640.0 / max(h, w))
    small = cv2.resize(img, (max(1, int(w * s)), max(1, int(h * s)))) if s < 1 else img
    sh, sw = small.shape[:2]
    faces = []
    with _det_lock:
        if _yunet is None:
            path = os.path.join(_app_dir(), "models", "face_detection_yunet_2023mar.onnx")
            try:
                if os.path.exists(path) and hasattr(cv2, "FaceDetectorYN"):
                    _yunet = cv2.FaceDetectorYN.create(path, "", (sw, sh), 0.6, 0.3, 50)
                else:
                    _yunet = False
                    LOG.warning(f"[FX] YuNet model not found at {path}; using Haar")
            except Exception as e:
                LOG.warning(f"[FX] YuNet load failed ({e}); using Haar")
                _yunet = False
        if _yunet:
            _yunet.setInputSize((sw, sh))
            _, res = _yunet.detect(small)
            for r in (res if res is not None else []):
                x, y, fw, fh = (r[:4] / s).tolist()
                eyes = [(r[4] / s, r[5] / s), (r[6] / s, r[7] / s)]
                faces.append(dict(box=(x, y, fw, fh), eyes=eyes))
        else:
            if _haar is None:
                _haar = cv2.CascadeClassifier(
                    os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            for (x, y, fw, fh) in _haar.detectMultiScale(gray, 1.1, 5, minSize=(30, 30)):
                x, y, fw, fh = x / s, y / s, fw / s, fh / s
                eyes = [(x + 0.3 * fw, y + 0.4 * fh), (x + 0.7 * fw, y + 0.4 * fh)]
                faces.append(dict(box=(x, y, fw, fh), eyes=eyes))
    faces.sort(key=lambda f: -f["box"][2] * f["box"][3])
    return faces


def _magnify(img, centers):
    """Local bulge: centers = [(cx, cy, radius, strength)]. strength 0..0.6."""
    h, w = img.shape[:2]
    mx, my = None, None
    for cx, cy, R, s in centers:
        R = max(4.0, float(R))
        x0, x1 = int(max(0, cx - R)), int(min(w, cx + R + 1))
        y0, y1 = int(max(0, cy - R)), int(min(h, cy + R + 1))
        if x1 <= x0 or y1 <= y0:
            continue
        if mx is None:
            my, mx = np.mgrid[0:h, 0:w].astype(np.float32)
        ys, xs = my[y0:y1, x0:x1], mx[y0:y1, x0:x1]
        dx, dy = xs - cx, ys - cy
        t = np.sqrt(dx * dx + dy * dy) / R
        k = np.where(t < 1.0, 1.0 - s * (1.0 - t * t) ** 2, 1.0).astype(np.float32)
        mx[y0:y1, x0:x1] = cx + dx * k
        my[y0:y1, x0:x1] = cy + dy * k
    if mx is None:
        return img.copy()
    return cv2.remap(img, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


# ---------------------------------------------------------------- effects
def fx_fisheye(img):
    h, w = img.shape[:2]
    step = 4
    sh, sw = max(2, h // step), max(2, w // step)
    ys, xs = np.mgrid[0:sh, 0:sw].astype(np.float32)
    cx, cy = (sw - 1) / 2.0, (sh - 1) / 2.0
    dx, dy = xs - cx, ys - cy
    r = np.sqrt(dx * dx + dy * dy) / np.sqrt(cx * cx + cy * cy)
    # r_src = r^1.45: centre magnified, corners stay filled (no black ring).
    k = np.power(np.maximum(r, 1e-6), 0.45).astype(np.float32)
    mx = cv2.resize(((cx + dx * k) * (w / float(sw))).astype(np.float32), (w, h),
                    interpolation=cv2.INTER_LINEAR)
    my = cv2.resize(((cy + dy * k) * (h / float(sh))).astype(np.float32), (w, h),
                    interpolation=cv2.INTER_LINEAR)
    out = cv2.remap(img, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    return _u8(_f(out) * _vignette_mask(h, w, 0.5, 2.5))


def fx_vignette(img):
    h, w = img.shape[:2]
    return _u8(_f(img) * _vignette_mask(h, w, 0.6, 2.2))


def fx_glow(img):
    f = _f(img)
    h, w = img.shape[:2]
    lum = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    bright = f * np.clip((lum - 0.55) / 0.45, 0, 1)[..., None]
    bloom = _blur(bright, min(h, w) * 0.03)
    soft = _blur(f, min(h, w) * 0.006)
    out = _screen(f * 0.8 + soft * 0.2, bloom * 1.1)
    return _u8(out * 1.04)


def fx_lightleak(img):
    h, w = img.shape[:2]
    lw, lh = 64, max(8, int(64 * h / w))
    leak = np.zeros((lh, lw, 3), np.float32)
    # Warm blobs bleeding in from the left-top and right edge (BGR).
    cv2.circle(leak, (0, int(lh * 0.15)), int(lw * 0.45), (0.25, 0.45, 1.0), -1)
    cv2.circle(leak, (int(lw * 0.08), int(lh * 0.6)), int(lw * 0.25), (0.45, 0.25, 1.0), -1)
    cv2.circle(leak, (lw, int(lh * 0.8)), int(lw * 0.35), (0.2, 0.75, 1.0), -1)
    leak = cv2.GaussianBlur(leak, (0, 0), lw * 0.12)
    leak = cv2.resize(leak, (w, h), interpolation=cv2.INTER_CUBIC)
    f = _f(img)
    return _u8(_screen(f, np.clip(leak * 0.75, 0, 1)))


def fx_film(img):
    h, w = img.shape[:2]
    f = _f(img)
    f = 0.06 + f * 0.9                              # lifted blacks
    f = f + 0.08 * np.sin((f - 0.5) * np.pi) * 0.5  # gentle S curve
    hsv = cv2.cvtColor(_u8(f), cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[..., 1] *= 0.82
    f = _f(cv2.cvtColor(np.clip(hsv, 0, 255).astype(np.uint8), cv2.COLOR_HSV2BGR))
    shadow = (1.0 - f.mean(axis=2, keepdims=True)) ** 2
    f = f + shadow * np.array([0.03, 0.02, -0.02], np.float32)   # teal shadows
    f = f * _vignette_mask(h, w, 0.35, 2.4) + _grain(h, w, 0.03)
    return _u8(f)


def fx_duotone(img):
    lum = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    lum = cv2.equalizeHist(_u8(lum)).astype(np.float32) / 255.0 * 0.6 + lum * 0.4
    dark = np.array([0.35, 0.10, 0.25], np.float32)    # deep plum (BGR)
    light = np.array([0.80, 0.78, 1.00], np.float32)   # pale pink
    return _u8(dark + (light - dark) * lum[..., None])


def fx_neon(img):
    h, w = img.shape[:2]
    t = np.linspace(0.0, 1.0, w, dtype=np.float32)[None, :, None]
    magenta = np.array([1.0, 0.25, 1.0], np.float32)
    cyan = np.array([1.0, 0.95, 0.2], np.float32)
    light = magenta * (1 - t) + cyan * t
    f = _f(img)
    lum = f.mean(axis=2, keepdims=True)
    out = f * 0.55 + light * lum * 0.65
    edge = _blur(out, min(h, w) * 0.02)
    return _u8(_screen(out, edge * light * 0.35) * _vignette_mask(h, w, 0.4))


def fx_spotlight(img):
    h, w = img.shape[:2]
    faces = detect_faces(img)
    step = 4
    sh, sw = max(2, h // step), max(2, w // step)
    ys, xs = np.mgrid[0:sh, 0:sw].astype(np.float32)
    xs *= step; ys *= step
    mask = np.zeros((sh, sw), np.float32)
    spots = []
    for fc in faces:
        x, y, fw, fh = fc["box"]
        spots.append((x + fw / 2, y + fh * 0.55, fw * 0.95, fh * 1.25))
    if not spots:
        spots.append((w / 2, h * 0.42, w * 0.28, h * 0.28))
    for cx, cy, sx, sy in spots:
        mask = np.maximum(mask, np.exp(-(((xs - cx) / sx) ** 2 + ((ys - cy) / sy) ** 2)))
    m = cv2.resize(mask, (w, h), interpolation=cv2.INTER_LINEAR)[..., None]
    f = _f(img)
    warm = f * np.array([0.94, 1.0, 1.08], np.float32) * 1.15
    out = f * 0.25 * (1 - m) + warm * m
    return _u8(out)


def fx_bigeyes(img):
    centers = []
    for fc in detect_faces(img):
        (lx, ly), (rx, ry) = fc["eyes"]
        d = np.hypot(rx - lx, ry - ly)
        if d < 4:
            continue
        for ex, ey in ((lx, ly), (rx, ry)):
            centers.append((ex, ey, d * 0.5, 0.42))
    return _magnify(img, centers)


def fx_bighead(img):
    centers = []
    for fc in detect_faces(img):
        x, y, fw, fh = fc["box"]
        centers.append((x + fw / 2, y + fh * 0.45, max(fw, fh) * 1.15, 0.5))
    return _magnify(img, centers)


def fx_sketch(img):
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(cv2.equalizeHist(gray), (0, 0), max(0.8, min(h, w) * 0.0012))
    inv = 255 - gray
    blur = _blur(inv, max(2.0, min(h, w) * 0.012))
    sk = cv2.divide(gray, 255 - blur, scale=256).astype(np.float32) / 255.0
    sk = np.clip(sk, 0, 1) ** 2.2            # darker, bolder pencil lines
    paper = np.array([0.93, 0.97, 1.0], np.float32)   # warm paper tint
    return _u8(sk[..., None] * paper)


def fx_comic(img):
    small, size = _work_size(img, 1000)
    col = small
    for _ in range(3):
        col = cv2.bilateralFilter(col, 9, 50, 7)
    lab = cv2.cvtColor(col, cv2.COLOR_BGR2LAB).astype(np.float32)
    levels = 5
    lab[..., 0] = (np.floor(lab[..., 0] / 256 * levels) + 0.5) * (256 / levels)
    lab[..., 1:] = 128 + (lab[..., 1:] - 128) * 1.5      # punchier colour
    col = cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)
    gray = cv2.medianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), 7)
    edges = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                  cv2.THRESH_BINARY, 11, 5)
    edges = cv2.erode(edges, np.ones((2, 2), np.uint8))   # thicker ink lines
    out = cv2.bitwise_and(col, col, mask=edges)
    return _restore(out, size)


def fx_popart(img):
    """Warhol-style 2x2: four posterised copies in different colours."""
    h, w = img.shape[:2]
    hw, hh = w // 2, h // 2
    tile = cv2.resize(img, (hw, hh), interpolation=cv2.INTER_AREA)
    lum = cv2.cvtColor(tile, cv2.COLOR_BGR2GRAY)
    lum = cv2.GaussianBlur(lum, (0, 0), 1.0)
    t1, t2 = np.percentile(lum, 35), np.percentile(lum, 70)
    idx = np.where(lum < t1, 0, np.where(lum < t2, 1, 2))
    palettes = [  # BGR: shadow, mid, highlight
        [(90, 20, 140), (200, 80, 255), (140, 240, 255)],
        [(120, 60, 0), (230, 180, 40), (180, 255, 255)],
        [(40, 30, 150), (60, 140, 255), (220, 230, 255)],
        [(80, 90, 20), (130, 220, 120), (255, 220, 250)],
    ]
    out = np.zeros((hh * 2, hw * 2, 3), np.uint8)
    for i, pal in enumerate(palettes):
        r, c = divmod(i, 2)
        out[r * hh:(r + 1) * hh, c * hw:(c + 1) * hw] = np.array(pal, np.uint8)[idx]
    if out.shape[:2] != (h, w):
        out = cv2.resize(out, (w, h), interpolation=cv2.INTER_NEAREST)
    return out


def fx_vintage70(img):
    h, w = img.shape[:2]
    f = _f(img)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[..., 1] *= 0.65
    f = _f(cv2.cvtColor(np.clip(hsv, 0, 255).astype(np.uint8), cv2.COLOR_HSV2BGR))
    f = 0.08 + f * 0.82                                              # faded
    f = f * np.array([0.80, 0.98, 1.10], np.float32) + np.array([0.02, 0.03, 0.05], np.float32)
    f = cv2.GaussianBlur(f, (0, 0), max(0.6, min(h, w) * 0.0008))   # soft lens
    f = f * _vignette_mask(h, w, 0.45, 2.0) + _grain(h, w, 0.04, seed=70)
    return _u8(f)


_FX = {
    "fisheye": fx_fisheye, "vignette": fx_vignette, "glow": fx_glow,
    "lightleak": fx_lightleak, "film": fx_film, "duotone": fx_duotone,
    "neon": fx_neon, "spotlight": fx_spotlight, "bigeyes": fx_bigeyes,
    "bighead": fx_bighead, "sketch": fx_sketch, "comic": fx_comic,
    "popart": fx_popart, "vintage70": fx_vintage70,
}


def apply_effect(img_bgr, name):
    fn = _FX.get(name)
    if fn is None or img_bgr is None or img_bgr.size == 0:
        return img_bgr
    try:
        return fn(np.ascontiguousarray(img_bgr))
    except Exception as e:
        LOG.error(f"[FX] {name} failed: {e}")
        return img_bgr

"""Face stickers (TikTok-style): PNG stickers placed on every detected face.

Stickers live in stickers/ next to the app: PNG files with transparency plus
stickers/stickers.json, which lists each sticker as one or more parts:

    {"id": "pita", "label": "Pita",
     "parts": [{"file": "bow.png", "anchor": "head", "width": 0.8, "dy": -0.08}]}

anchor: "head" (above the forehead), "eyes", "cheeks" (drawn on both cheeks),
"nose" or "mouth". width: sticker width as a share of the face width. dy / dx:
extra shift in face widths (dy < 0 = up), along the face's own tilt. rot:
extra tilt in degrees (optional). category: group name for the picker tabs.
New stickers need no code: add the PNG and a line in stickers.json.

Faces come from effects.detect_faces (YuNet: eyes, nose, mouth corners).
LiveTracker smooths positions between camera frames so stickers don't jitter.
"""

import json
import logging
import math
import os
import sys
import threading

import cv2
import numpy as np

from effects import detect_faces

LOG = logging.getLogger("stickers")

_lock = threading.Lock()
_catalog = None          # [sticker dict], loaded once
_images = {}             # file name -> BGRA array


def _sticker_dir():
    """stickers/ next to the app (your own set), else the copy built into the exe."""
    dirs = []
    if getattr(sys, "frozen", False):
        dirs.append(os.path.join(os.path.dirname(sys.executable), "stickers"))
        if getattr(sys, "_MEIPASS", None):
            dirs.append(os.path.join(sys._MEIPASS, "stickers"))
    dirs.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "stickers"))
    for d in dirs:
        if os.path.exists(os.path.join(d, "stickers.json")):
            return d
    return dirs[0]


def file_path(file_name):
    return os.path.join(_sticker_dir(), file_name)


def catalog():
    """Stickers from stickers.json whose files exist: [{"id", "label", "parts"}]."""
    global _catalog
    with _lock:
        if _catalog is not None:
            return _catalog
        out = []
        path = os.path.join(_sticker_dir(), "stickers.json")
        LOG.info(f"[STICKER] reading {path}")
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            for st in data.get("stickers", []):
                parts = [p for p in st.get("parts", [])
                         if os.path.exists(os.path.join(_sticker_dir(), str(p.get("file", ""))))]
                if st.get("id") and parts:
                    out.append(dict(st, parts=parts))
        except Exception as e:
            LOG.warning(f"[STICKER] cannot read {path}: {e}")
        _catalog = out
        LOG.info(f"[STICKER] {len(out)} sticker(s): {', '.join(s['id'] for s in out)}")
        return out


def categories():
    """Category names in the order they first appear in stickers.json."""
    out = []
    for st in catalog():
        c = st.get("category") or "Lainnya"
        if c not in out:
            out.append(c)
    return out


def find(sticker_id):
    for st in catalog():
        if st["id"] == sticker_id:
            return st
    return None


def image(file_name):
    """BGRA sticker image (cached)."""
    with _lock:
        img = _images.get(file_name)
        if img is None:
            img = cv2.imread(os.path.join(_sticker_dir(), file_name), cv2.IMREAD_UNCHANGED)
            if img is not None and img.ndim == 3 and img.shape[2] == 3:
                img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
            _images[file_name] = img
        return img


def _geometry(face):
    """Face frame: eye centre, unit vectors along (x) and down (y) the face, width."""
    (ax, ay), (bx, by) = sorted(face["eyes"], key=lambda p: p[0])
    ex, ey = (ax + bx) / 2.0, (ay + by) / 2.0
    ang = math.atan2(by - ay, bx - ax)
    ux, uy = math.cos(ang), math.sin(ang)        # along the eye line
    vx, vy = -uy, ux                             # down the face
    fw = max(float(face["box"][2]), 2.2 * math.hypot(bx - ax, by - ay))
    return (ex, ey), (ux, uy), (vx, vy), fw, math.degrees(ang), ((ax, ay), (bx, by))


def _placements(face, part):
    """[(cx, cy, width_px, angle_deg)] for one sticker part on one face."""
    (ex, ey), (ux, uy), (vx, vy), fw, ang, (le, re) = _geometry(face)
    width = float(part.get("width", 1.0)) * fw
    dx = float(part.get("dx", 0.0)) * fw
    dy = float(part.get("dy", 0.0)) * fw
    anchor = part.get("anchor", "head")
    tilt = float(part.get("rot", 0.0))

    def at(px, py):
        return (px + ux * dx + vx * dy, py + uy * dx + vy * dy, width, ang + tilt)

    if anchor == "eyes":
        return [at(ex, ey)]
    if anchor == "nose":
        nx, ny = face.get("nose") or (ex + vx * 0.35 * fw, ey + vy * 0.35 * fw)
        return [at(nx, ny)]
    if anchor == "mouth":
        m = face.get("mouth")
        mx, my = ((m[0][0] + m[1][0]) / 2.0, (m[0][1] + m[1][1]) / 2.0) if m else \
            (ex + vx * 0.6 * fw, ey + vy * 0.6 * fw)
        return [at(mx, my)]
    if anchor == "cheeks":
        out = []
        for (px, py), side in ((le, -1), (re, 1)):
            cx = px + vx * 0.30 * fw + ux * side * 0.06 * fw
            cy = py + vy * 0.30 * fw + uy * side * 0.06 * fw
            out.append(at(cx, cy))
        return out
    # head: above the forehead, about 0.55 face widths over the eyes
    return [at(ex - vx * 0.55 * fw, ey - vy * 0.55 * fw)]


def _paste(img, rgba, cx, cy, width, angle_deg):
    """Alpha-blend a BGRA sticker onto img (BGR, in place), centred, scaled, rotated."""
    sh, sw = rgba.shape[:2]
    scale = width / float(sw)
    if scale <= 0 or width < 4:
        return
    m = cv2.getRotationMatrix2D((sw / 2.0, sh / 2.0), -angle_deg, scale)
    cos, sin = abs(m[0, 0]), abs(m[0, 1])
    ow, oh = int(sh * sin + sw * cos) + 2, int(sh * cos + sw * sin) + 2
    m[0, 2] += ow / 2.0 - sw / 2.0
    m[1, 2] += oh / 2.0 - sh / 2.0
    warped = cv2.warpAffine(rgba, m, (ow, oh), flags=cv2.INTER_LINEAR,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    x0, y0 = int(round(cx - ow / 2.0)), int(round(cy - oh / 2.0))
    H, W = img.shape[:2]
    ix0, iy0, ix1, iy1 = max(0, x0), max(0, y0), min(W, x0 + ow), min(H, y0 + oh)
    if ix0 >= ix1 or iy0 >= iy1:
        return
    piece = warped[iy0 - y0:iy1 - y0, ix0 - x0:ix1 - x0]
    a = piece[:, :, 3:4].astype(np.float32) / 255.0
    dst = img[iy0:iy1, ix0:ix1].astype(np.float32)
    img[iy0:iy1, ix0:ix1] = (piece[:, :, :3].astype(np.float32) * a + dst * (1.0 - a)).astype(np.uint8)


def draw(img_bgr, sticker_id, faces=None):
    """Copy of the photo with the sticker on every face (faces detected if not given)."""
    st = find(sticker_id) if sticker_id else None
    if st is None or img_bgr is None:
        return img_bgr
    if faces is None:
        try:
            faces = detect_faces(img_bgr)
        except Exception as e:
            LOG.warning(f"[STICKER] face detection failed: {e}")
            return img_bgr
    if not faces:
        return img_bgr
    out = img_bgr.copy()
    for face in faces:
        for part in st["parts"]:
            rgba = image(part["file"])
            if rgba is None:
                continue
            for cx, cy, width, ang in _placements(face, part):
                _paste(out, rgba, cx, cy, width, ang)
    return out


class LiveTracker:
    """Faces for a stream of frames (live view, video): detection every frame or
    every other frame, positions smoothed, a face kept for a few frames after it
    is lost so stickers don't blink."""

    def __init__(self, smooth=0.55, keep=6, every=1):
        self.smooth, self.keep, self.every = smooth, keep, max(1, every)
        self._tracks = []            # [{"face": dict, "miss": int}]
        self._n = 0

    @staticmethod
    def _center(face):
        x, y, w, h = face["box"]
        return x + w / 2.0, y + h / 2.0

    @staticmethod
    def _blend(old, new, k):
        def mix(a, b):
            if isinstance(a, (tuple, list)) and a and isinstance(a[0], (tuple, list)):
                return [mix(p, q) for p, q in zip(a, b)]
            if isinstance(a, (tuple, list)):
                return tuple(k * p + (1 - k) * q for p, q in zip(a, b))
            return k * a + (1 - k) * b
        return {key: mix(old[key], new[key]) for key in new if key in old}

    def faces(self, frame):
        self._n += 1
        if self._n % self.every and self._tracks:
            return [t["face"] for t in self._tracks]
        try:
            found = detect_faces(frame)
        except Exception:
            found = []
        used = set()
        for t in self._tracks:
            cx, cy = self._center(t["face"])
            best, best_d = None, None
            for i, f in enumerate(found):
                if i in used:
                    continue
                fx, fy = self._center(f)
                d = math.hypot(fx - cx, fy - cy)
                if d < 0.6 * t["face"]["box"][2] and (best_d is None or d < best_d):
                    best, best_d = i, d
            if best is None:
                t["miss"] += 1
            else:
                used.add(best)
                t["face"] = self._blend(t["face"], found[best], self.smooth)
                t["miss"] = 0
        # A track that just lost its face while a new face appeared elsewhere is the
        # same person who moved fast: jump there instead of leaving a ghost sticker
        # at the old spot for a few frames.
        for t in self._tracks:
            if t["miss"] == 0:
                continue
            free = [i for i in range(len(found)) if i not in used]
            if not free:
                break
            cx, cy = self._center(t["face"])
            j = min(free, key=lambda i: math.hypot(self._center(found[i])[0] - cx,
                                                   self._center(found[i])[1] - cy))
            used.add(j)
            t["face"], t["miss"] = found[j], 0
        self._tracks = [t for t in self._tracks if t["miss"] <= self.keep]
        self._tracks += [{"face": f, "miss": 0} for i, f in enumerate(found) if i not in used]
        return [t["face"] for t in self._tracks]

    def draw(self, frame, sticker_id):
        if not sticker_id:
            return frame
        return draw(frame, sticker_id, self.faces(frame))

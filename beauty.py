"""Beauty camera for the booth: natural skin retouch on every face.

beautify(img_bgr, intensity) -> new image (same size). intensity 0..1 scales
everything at once. Steps, per face (MediaPipe face landmarks, 478 points):

  1. Skin smoothing: frequency separation. The face's low frequencies are
     smoothed edge-preservingly (bilateral) and a little of the finest texture is
     put back, so pores / fine lines / marks fade without a plastic look. Eyes,
     brows and lips are cut out of the skin mask; the mask also follows the skin
     colour (hair strands stay sharp) and has feathered edges (no mask line at
     the jaw, ears or hairline).
  2. Light & tone: soft brightening of the skin, redness evened toward the
     face's own skin colour, lighter under-eyes, a thin glow on the cheekbones,
     nose bridge and chin (ring-light look).
  3. Details: local contrast on the eyes, whiter teeth when the mouth is open.
  4. Contour (light): slightly slimmer jaw and slightly bigger eyes (max ~5-10%
     at the default intensity), with small local warps that end inside the face.

Landmarks are found on a downscaled copy; masks are built small and scaled up,
heavy filtering runs on a reduced face crop, so a full-resolution Canon photo
takes well under a second. No face (or no MediaPipe) -> the photo comes back
unchanged.
"""

import logging
import os
import sys
import threading

import cv2
import numpy as np

LOG = logging.getLogger("beauty")

MODEL_NAME = "face_landmarker.task"


def _model_path():
    """models/face_landmarker.task next to the app, else the copy built into the exe."""
    name = os.path.join("models", MODEL_NAME)
    dirs = []
    if getattr(sys, "frozen", False):
        dirs.append(os.path.dirname(sys.executable))
        if getattr(sys, "_MEIPASS", None):
            dirs.append(sys._MEIPASS)
    dirs.append(os.path.dirname(os.path.abspath(__file__)))
    for d in dirs:
        if os.path.exists(os.path.join(d, name)):
            return os.path.join(d, name)
    return os.path.join(dirs[0], name)


# Two MediaPipe APIs: the Tasks FaceLandmarker (every mediapipe version from
# 0.10 up, needs models/face_landmarker.task) and the old solutions.face_mesh
# (mediapipe <= 0.10.14 only). Both give the same 478 points.
_mp = _vision = _MESH = None
_MODEL = None
try:
    import mediapipe as _mp
    try:
        from mediapipe.tasks.python import vision as _vision
        from mediapipe.tasks.python.core import base_options as _base_options
        _MODEL = _model_path()
        if not os.path.exists(_MODEL):
            LOG.warning(f"[BEAUTY] model not found at {_MODEL}")
            _vision = None
    except Exception as _e:
        LOG.warning(f"[BEAUTY] MediaPipe Tasks not usable ({_e})")
        _vision = None
    if _vision is None:
        _MESH = getattr(getattr(_mp, "solutions", None), "face_mesh", None)
except Exception as _e:                      # app keeps working without it
    LOG.warning(f"[BEAUTY] MediaPipe not available ({_e}); beauty is off")

AVAILABLE = _vision is not None or _MESH is not None
if AVAILABLE:
    LOG.info(f"[BEAUTY] ready: mediapipe {getattr(_mp, '__version__', '?')}, "
             f"{'FaceLandmarker ' + _MODEL if _vision is not None else 'face_mesh'}")
elif _mp is not None:
    LOG.warning("[BEAUTY] MediaPipe has no face landmarker; beauty is off")

# MediaPipe Face Mesh landmark indices.
FACE_OVAL = [10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365, 379, 378,
             400, 377, 152, 148, 176, 149, 150, 136, 172, 58, 132, 93, 234, 127, 162, 21,
             54, 103, 67, 109]
EYE_A = [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246]
EYE_B = [362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398]
LOWER_LID_A = [33, 7, 163, 144, 145, 153, 154, 155, 133]
LOWER_LID_B = [362, 382, 381, 380, 374, 373, 390, 249, 263]
BROW_A = [70, 63, 105, 66, 107, 55, 65, 52, 53, 46]
BROW_B = [300, 293, 334, 296, 336, 285, 295, 282, 283, 276]
LIPS_OUT = [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 409, 270, 269, 267, 0, 37,
            39, 40, 185]
LIPS_IN = [78, 95, 88, 178, 87, 14, 317, 402, 318, 324, 308, 415, 310, 311, 312, 13, 82,
           81, 80, 191]
NOSTRILS = [98, 97, 2, 326, 327, 294, 278, 344, 440, 275, 4, 45, 220, 115, 48, 64]
GLOW_POINTS = [(117, 0.9), (346, 0.9), (6, 0.6), (197, 0.6), (195, 0.5), (199, 0.6)]
JAW_A = [172, 136, 150]
JAW_B = [397, 365, 379]

_lock = threading.Lock()
_engine = None                 # FaceLandmarker / FaceMesh, made on first use


def _get_engine():
    global _engine, AVAILABLE
    if _engine is None:
        try:
            if _vision is not None:
                with open(_MODEL, "rb") as f:          # buffer: works with any path
                    model = f.read()
                opts = _vision.FaceLandmarkerOptions(
                    base_options=_base_options.BaseOptions(
                        model_asset_buffer=model,
                        delegate=_base_options.BaseOptions.Delegate.CPU),
                    running_mode=_vision.RunningMode.IMAGE, num_faces=2,
                    min_face_detection_confidence=0.45, min_face_presence_confidence=0.45)
                _engine = _vision.FaceLandmarker.create_from_options(opts)
            else:
                _engine = _MESH.FaceMesh(static_image_mode=True, max_num_faces=2,
                                         refine_landmarks=True, min_detection_confidence=0.45)
        except Exception as e:
            LOG.warning(f"[BEAUTY] cannot start face landmarker: {e}; beauty is off")
            _engine = False
            AVAILABLE = False
    return _engine or None


def _mesh_points(rgb, live=False):
    """[478 x 2 point array in rgb's pixels] per face found in an RGB image."""
    h, w = rgb.shape[:2]
    with _lock:
        eng = _get_engine()
        if eng is None:
            return []
        try:
            if _vision is not None:
                img = _mp.Image(image_format=_mp.ImageFormat.SRGB,
                                data=np.ascontiguousarray(rgb))
                faces = eng.detect(img).face_landmarks or []
            else:
                faces = [lm.landmark for lm in (eng.process(rgb).multi_face_landmarks or [])]
        except Exception as e:
            LOG.warning(f"[BEAUTY] face landmarks failed: {e}")
            return []
    return [np.array([(p.x * w, p.y * h) for p in lm], np.float32) for lm in faces]


def find_faces(img_bgr, live=False, max_side=960):
    """[478 x 2 float array of points in the image's pixels] per face.
    Faces are found with YuNet (also small faces in a group shot), then the mesh
    runs on a crop around each one; without YuNet, on the whole picture."""
    if not AVAILABLE or img_bgr is None or img_bgr.size == 0:
        return []
    h, w = img_bgr.shape[:2]
    try:
        from effects import detect_faces
        boxes = [f["box"] for f in detect_faces(img_bgr)]
    except Exception:
        boxes = None
    if not boxes:
        s = min(1.0, max_side / float(max(h, w)))
        small = cv2.resize(img_bgr, (max(1, int(w * s)), max(1, int(h * s))),
                           interpolation=cv2.INTER_AREA) if s < 1 else img_bgr
        return [p / s for p in _mesh_points(cv2.cvtColor(small, cv2.COLOR_BGR2RGB), live)] \
            if boxes is None else []
    out = []
    for (bx, by, bw, bh) in boxes[:8]:
        side = 1.9 * max(bw, bh)
        cx, cy = bx + bw / 2.0, by + bh / 2.0
        x0, y0 = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
        x1, y1 = int(min(w, cx + side / 2)), int(min(h, cy + side / 2))
        if x1 - x0 < 24 or y1 - y0 < 24:
            continue
        crop = img_bgr[y0:y1, x0:x1]
        cs = min(1.0, 384.0 / max(crop.shape[:2]))
        if cs < 1:
            crop = cv2.resize(crop, (max(1, int(crop.shape[1] * cs)), max(1, int(crop.shape[0] * cs))),
                              interpolation=cv2.INTER_AREA)
        found = _mesh_points(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
        if found:
            # The face nearest the crop's centre is this box's face.
            c = np.array([crop.shape[1] / 2.0, crop.shape[0] / 2.0], np.float32)
            pts = min(found, key=lambda p: float(np.linalg.norm(p.mean(axis=0) - c)))
            out.append(pts / cs + np.array([x0, y0], np.float32))
    return out


# ------------------------------------------------------------------ helpers
def _poly(mask, pts, idx, value=255):
    cv2.fillPoly(mask, [np.round(pts[idx]).astype(np.int32)], value, lineType=cv2.LINE_AA)


def _feather(mask, sigma):
    if sigma <= 0.3:
        return mask
    return cv2.GaussianBlur(mask, (0, 0), sigma)


def _skin_mask(roi_small, pts, fw):
    """Skin of one face at the reduced crop's scale, 0..1 float, feathered."""
    h, w = roi_small.shape[:2]
    face = np.zeros((h, w), np.uint8)
    _poly(face, pts, FACE_OVAL)
    holes = np.zeros((h, w), np.uint8)
    for idx in (EYE_A, EYE_B, BROW_A, BROW_B, LIPS_OUT):
        _poly(holes, pts, idx)
    k = max(1, int(round(0.035 * fw)))
    holes = cv2.dilate(holes, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1)))
    core = cv2.erode(face, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1)))
    skin = cv2.subtract(face, holes)
    # Skin colour model from the face itself: hair, glasses frames, background stay out.
    lab = cv2.cvtColor(roi_small, cv2.COLOR_BGR2LAB).astype(np.float32)
    sample = (cv2.subtract(core, holes) > 0)
    if sample.sum() < 50:
        sample = skin > 0
    if sample.sum() < 20:
        return np.zeros((h, w), np.float32), None
    med = np.median(lab[sample], axis=0)
    d_chroma = (lab[..., 1] - med[1]) ** 2 + (lab[..., 2] - med[2]) ** 2
    d_light = np.clip(med[0] - lab[..., 0], 0, None)            # much darker = hair / shadow
    like = np.exp(-d_chroma / (2 * 11.0 ** 2)) * np.exp(-(d_light ** 2) / (2 * 55.0 ** 2))
    m = (skin.astype(np.float32) / 255.0) * like
    return np.clip(_feather(m, 0.045 * fw), 0, 1), med


def _spots(shape, centers, radius):
    """Soft round spots (0..1) at the given points."""
    h, w = shape
    m = np.zeros((h, w), np.float32)
    for (x, y), weight in centers:
        cv2.circle(m, (int(round(x)), int(round(y))), max(1, int(round(radius * 0.5))),
                   float(weight), -1, lineType=cv2.LINE_AA)
    return np.clip(_feather(m, radius * 0.6), 0, 1)


def _under_eye(shape, pts, fw):
    h, w = shape
    m = np.zeros((h, w), np.float32)
    for lid in (LOWER_LID_A, LOWER_LID_B):
        top = pts[lid]
        drop = np.array([0, 0.11 * fw], np.float32)
        poly = np.vstack([top + np.array([0, 0.015 * fw], np.float32), (top + drop)[::-1]])
        cv2.fillPoly(m, [np.round(poly).astype(np.int32)], 1.0, lineType=cv2.LINE_AA)
    return np.clip(_feather(m, 0.03 * fw), 0, 1)


def _warp_fields(shape, pts, fw, amount):
    """Backward-map offsets (dx, dy) for a slightly slimmer jaw and bigger eyes."""
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dx = np.zeros((h, w), np.float32)
    dy = np.zeros((h, w), np.float32)
    # Jaw: content near each lower-jaw side moves toward the face's centre line.
    centre_x = (pts[1, 0] + pts[152, 0]) / 2.0
    r = 0.2 * fw
    push = 0.035 * fw * amount
    for idx in (JAW_A, JAW_B):
        for i in idx:
            px, py = pts[i]
            out = np.sign(px - centre_x) or 1.0          # outward direction
            g = np.exp(-((xx - px) ** 2 + (yy - py) ** 2) / (2 * r * r))
            dx += out * push * g / len(idx) * 1.6
    # Eyes: magnify around each iris (~8% at full intensity).
    for iris in (468, 473):
        if iris >= len(pts):
            continue
        cx, cy = pts[iris]
        er = 0.13 * fw
        ddx, ddy = xx - cx, yy - cy
        dist = np.sqrt(ddx * ddx + ddy * ddy)
        k = 0.08 * amount * np.clip(1 - (dist / er) ** 2, 0, 1)
        dx -= ddx * k
        dy -= ddy * k
    return dx, dy


# ------------------------------------------------------------------ main
def beautify(img_bgr, intensity=0.6, faces=None, live=False):
    """Retouched copy of a BGR photo; the input is not changed. live=True: the
    camera preview (masks built smaller, so it keeps up with the frame rate)."""
    if img_bgr is None or img_bgr.size == 0:
        return img_bgr
    t = float(np.clip(intensity, 0.0, 1.0))
    if t <= 0.01:
        return img_bgr
    if faces is None:
        faces = find_faces(img_bgr, live=live)
    if not faces:
        return img_bgr
    out = img_bgr.copy()
    for pts in faces:
        try:
            _beautify_face(out, pts, t, 300.0 if live else 640.0)
        except Exception as e:
            LOG.warning(f"[BEAUTY] face skipped: {e}")
    return out


def _beautify_face(img, pts, t, work=640.0):
    H, W = img.shape[:2]
    oval = pts[FACE_OVAL]
    fw = float(np.linalg.norm(pts[234] - pts[454]))
    if fw < 24:
        return
    x0, y0 = oval.min(axis=0) - 0.25 * fw
    x1, y1 = oval.max(axis=0) + 0.25 * fw
    x0, y0 = int(max(0, x0)), int(max(0, y0))
    x1, y1 = int(min(W, x1)), int(min(H, y1))
    if x1 - x0 < 16 or y1 - y0 < 16:
        return
    roi = img[y0:y1, x0:x1]
    rh, rw = roi.shape[:2]
    s = min(1.0, work / max(rh, rw))                 # work size for masks / smoothing
    sw_, sh_ = max(1, int(rw * s)), max(1, int(rh * s))
    small = cv2.resize(roi, (sw_, sh_), interpolation=cv2.INTER_AREA) if s < 1 else roi.copy()
    p = (pts - np.array([x0, y0], np.float32)) * s
    fws = fw * s

    skin, med = _skin_mask(small, p, fws)
    if med is None:
        return

    def up(m):
        return cv2.resize(m, (rw, rh), interpolation=cv2.INTER_LINEAR) if s < 1 else m

    # 1. Skin smoothing (frequency separation).
    # Edge-preserving smoothing at ~220 px face width: plenty for skin, and fast.
    bs = min(1.0, 220.0 / max(1.0, fws))
    tiny = cv2.resize(small, (max(1, int(sw_ * bs)), max(1, int(sh_ * bs))),
                      interpolation=cv2.INTER_AREA) if bs < 1 else small
    base = cv2.bilateralFilter(tiny, d=9, sigmaColor=18 + 22 * t, sigmaSpace=max(2.0, 0.035 * fws * bs))
    base = cv2.GaussianBlur(base, (0, 0), max(0.6, 0.006 * fws * bs))
    if bs < 1:
        base = cv2.resize(base, (sw_, sh_), interpolation=cv2.INTER_LINEAR)
    roi_f = roi.astype(np.float32)
    base_up = cv2.resize(base, (rw, rh), interpolation=cv2.INTER_LINEAR).astype(np.float32) if s < 1 \
        else base.astype(np.float32)
    fine_sigma = max(0.8, 0.0025 * fw)
    fine = roi_f - cv2.GaussianBlur(roi_f, (0, 0), fine_sigma)
    smooth = base_up + fine * 0.55                     # keep micro texture: not plastic
    m_skin = up(skin)[..., None]
    res = roi_f + (smooth - roi_f) * (m_skin * (0.85 * t))

    # 2. Light & tone in LAB.
    lab = cv2.cvtColor(np.clip(res, 0, 255).astype(np.uint8), cv2.COLOR_BGR2LAB).astype(np.float32)
    ms = m_skin[..., 0]
    lab[..., 0] += ms * (7.0 * t)                                       # ring-light brightening
    red = np.clip(lab[..., 1] - med[1], 0, None)
    lab[..., 1] -= ms * red * (0.55 * t)                                # even out redness
    eye_lift = up(_under_eye((sh_, sw_), p, fws))
    lab[..., 0] += eye_lift * (9.0 * t)
    glow = up(_spots((sh_, sw_), [(p[i], wgt) for i, wgt in GLOW_POINTS], 0.09 * fws)) * ms
    lab[..., 0] += glow * (10.0 * t)
    # Teeth: open mouth -> less yellow, a touch brighter.
    if np.linalg.norm(pts[13] - pts[14]) > 0.035 * fw:
        mouth = np.zeros((sh_, sw_), np.uint8)
        _poly(mouth, p, LIPS_IN)
        mouth = up(_feather(mouth.astype(np.float32) / 255.0, 0.01 * fws))
        teeth = mouth * np.clip((lab[..., 0] - 110) / 50.0, 0, 1) * np.clip((lab[..., 2] - 128) / 20.0, 0, 1)
        lab[..., 2] -= teeth * (lab[..., 2] - 128) * (0.6 * t)
        lab[..., 0] += teeth * (5.0 * t)
    res = cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR).astype(np.float32)

    # 3. Eyes: local contrast on the eyes and lashes.
    eyes = np.zeros((sh_, sw_), np.uint8)
    _poly(eyes, p, EYE_A)
    _poly(eyes, p, EYE_B)
    k = max(1, int(round(0.02 * fws)))
    eyes = cv2.dilate(eyes, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1)))
    eyes = up(_feather(eyes.astype(np.float32) / 255.0, 0.012 * fws))[..., None]
    sharp = res + (res - cv2.GaussianBlur(res, (0, 0), max(1.0, 0.005 * fw))) * 0.9
    res = res + (sharp - res) * (eyes * t)

    # 4. Contour: small local warps (jaw in, eyes up to ~8% bigger).
    dx, dy = _warp_fields((sh_, sw_), p, fws, 0.6 * t)
    if s < 1:
        dx = cv2.resize(dx, (rw, rh), interpolation=cv2.INTER_LINEAR) / s
        dy = cv2.resize(dy, (rw, rh), interpolation=cv2.INTER_LINEAR) / s
    yy, xx = np.mgrid[0:rh, 0:rw].astype(np.float32)
    res = cv2.remap(np.clip(res, 0, 255).astype(np.uint8), xx + dx, yy + dy,
                    interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    img[y0:y1, x0:x1] = res

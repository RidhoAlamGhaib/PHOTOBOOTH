"""Photobooth Ceria — full 5-screen UI redesign.

Screens (held by a QStackedWidget):
  0. Homepage         — big title + START button
  1. Capture session  — REC indicator, framed live view, countdown, "Foto N/4"
  2. Frame picker     — 2-col grid of frame cards with a checkmark badge
  3. Printing         — fake progress bar (0→100%) while we save files
  4. Done             — final photo thumb + dummy QR + KEMBALI KE HOME

Icons are pulled from Twemoji on jsdelivr (free, predictable URLs, no key).
They load asynchronously; the UI is fully usable before icons land.
"""

import sys
import time
import datetime
import platform
import random
import zipfile
import json
import threading
import logging
import traceback
from pathlib import Path
from functools import partial

import cv2
import numpy as np
from PIL import Image
from effects import EFFECT_IDS, EFFECT_LABELS, apply_effect
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QGridLayout, QScrollArea, QSizePolicy, QMessageBox,
    QStackedWidget, QFrame, QProgressBar, QGraphicsDropShadowEffect, QScroller, QCheckBox
)
from PyQt5.QtCore import (
    Qt, QTimer, QThread, pyqtSignal, QSize, QUrl, QByteArray, QPointF, QRectF
)
from PyQt5.QtGui import (QPixmap, QImage, QIcon, QFont, QPainter, QColor,
                         QLinearGradient, QRadialGradient, QBrush, QFontDatabase)
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkRequest

# --- CONFIG ---
def _resolve_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent

BASE_DIR  = _resolve_base_dir()
FRAME_DIR = BASE_DIR / "frames"
SAVE_DIR  = BASE_DIR / "captures"
LOG_DIR   = BASE_DIR / "logs"
FRAME_DIR.mkdir(exist_ok=True)
SAVE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _setup_logging():
    log_file = LOG_DIR / f"photobooth_{datetime.datetime.now().strftime('%Y%m%d')}.log"
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    for h in list(logger.handlers):
        logger.removeHandler(h)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    if not getattr(sys, "frozen", False):
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(fmt)
        logger.addHandler(ch)
    def _excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logger.error("Uncaught exception",
                     exc_info=(exc_type, exc_value, exc_tb))
    sys.excepthook = _excepthook
    class _LogWriter:
        def __init__(self, level):
            self._level = level
            self._buf = ""
        def write(self, msg):
            self._buf += msg
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                if line.strip():
                    logger.log(self._level, line.rstrip())
        def flush(self):
            if self._buf.strip():
                logger.log(self._level, self._buf.rstrip())
            self._buf = ""
    if getattr(sys, "frozen", False):
        sys.stdout = _LogWriter(logging.INFO)
        sys.stderr = _LogWriter(logging.ERROR)
    logger.info("=" * 60)
    logger.info("Photobooth Ceria starting up")
    logger.info(f"  Python:    {sys.version.split()[0]}")
    logger.info(f"  Platform:  {platform.system()} {platform.release()}")
    logger.info(f"  Frozen:    {getattr(sys, 'frozen', False)}")
    logger.info(f"  BASE_DIR:  {BASE_DIR}")
    logger.info(f"  Log file:  {log_file}")
    logger.info("=" * 60)
    return logger

LOG = _setup_logging()

MAX_SLOTS = 12   # poses (max 8) + bonus shots

_FALLBACK_DEFAULTS = {
    "countdown_seconds":       10,
    # Seconds to let the camera start (fresh connection each session)
    # before the first shot's countdown begins.
    "camera_warmup_seconds":   5,
    # Mirror preview + photos (selfie view: customer's left stays on the left).
    "mirror":                  True,
    "max_retakes_per_shot":    2,
    "review_auto_confirm_seconds": 5,
    "shots_per_session":       4,
    # Bonus shots on top of the layout's poses. Guest shoots poses+extra,
    # then picks which ones go into the frame. 0 = no picking step.
    "extra_shots":             2,
    # Every shot (retakes + bonus) saved locally as its own JPG; only the
    # photos picked on the pick screen are uploaded to the session's Drive
    # folder (subfolder "foto-individu").
    "individual_photos": {
        "save":          True,
        "upload":        True,
        "apply_filter":  True,
        "jpeg_quality":  95,
    },
    # Auto-enhance every photo (brightness lift for low light + skin warmth +
    # gentle smoothing). 0 = off, 0.5 = subtle (default), 1.0 = medium,
    # up to ~2.0 = strong.
    "beautify_strength":       0.5,
    "slot_order":              "legacy",
    "camera": {
        # "opencv" = webcam via cv2 (default). "canon" = Canon DSLR via EDSDK.
        "source":     "canon",
        "index":      "auto",
        "backend":    "any",
        "width":      "auto",
        "height":     "auto",
        "fit_mode":   "cover",
        # Canon-only. Empty = look for EDSDK\Dll next to canon_edsdk.py.
        "edsdk_dll_dir":      "",
        # Cap the long side of the full-res DSLR shot. 4x6 @300dpi needs 1800px,
        # so 3000 is plenty and keeps beautify/filters fast. 0 = no cap.
        "canon_max_long_side": 3000,
        # true = autofocus each shot, falls back to NonAF if focus can't lock.
        # false = never AF (lens on MF, fixed-distance booth) - fastest/reliable.
        "canon_use_af":        True,
        # No live-view frame + no capture for this many seconds -> shut EDSDK
        # down and release the camera instead of keeping it stuck.
        "canon_fail_timeout_s": 30,
        # Give the full-res DSLR photo the colour/brightness of the live view.
        "match_liveview_color": True,
    },
    "camera_index":            None,
    "layouts": [
       {
      "id": "grid4",
      "name": "4 Foto — Grid 2×2",
      "paper": "4x6 portrait",
      "poses": 4,
      "frame_dir": "frames/a",
      "cut": "none"
    },
        {"id": "8dup", "name": "8 Foto — Strip dgn Duplikat (CUT)", "poses": 8,
         "frame_dir": "frames/ST8dup", "slot_to_pose": [0, 0, 1, 1, 2, 2, 3, 3],
         "cut": "2inch", "paper_name": "PR (2x6)*2"},
        {"id": "8poseCut", "name": "8 Foto — Strip dgn Duplikat (CUT)", "poses": 8,
         "frame_dir": "frames/ST8dup", "slot_to_pose": [0, 4, 1, 5, 2, 6, 3, 7],
         "cut": "2inch", "paper_name": "PR (2x6)*2"},
        {"id": "strip6", "name": "6 Foto Landscape (CUT)", "poses": 6,
         "frame_dir": "frames/ST6dup", "cut": "2inch", "paper_name": "PR (2x6)*2"},
        {"id": "test", "name": "3 Foto", "poses": 3,
         "frame_dir": "frames/test", "cut": "none"},
    ],
    "default_layout_id":       "grid4",
    "video_fps":               25,
    "bts_speed":               0.8,
    "bts_max_duration_s":      30.0,
    "moving_clip_s":           3.0,
    "moving_speed":            1.0,
    "video_short_side_px":     480,
    "print_bar_phase1_pct":    60,
    "print_bar_step":          2,
    "print_tick_ms":           40,
    "printing": {
        "enabled":            True,
        "mode":               "DSRX",
        "printer_cut":        "ds-rx1-1 (cut)",
        "printer_non_cut":    "ds-rx1-1 (non_cut)",
        "preferred_printer":  "",
        "paper_size":         "4x6",
        "dpi":                300,
        # Free "extra print" stepper on the frame screen: 0..max extra copies.
        "max_extra_prints":   3,
        "other": {
            "printer":     "",
            "paper_w_mm":  100,
            "paper_h_mm":  150,
            "quality":     "best",
            "media_type":  "matte",
            "paper_name":  "",
            "fit_mode":    "driver",
        },
    },
    "gdrive": {
        "enabled":              True,
        "event_name":           "Wedding-Event",
        "folder_id":            "PASTE_YOUR_FOLDER_ID_HERE",
        "client_secrets_path":  "client_secret.json",
        "token_path":           "oauth_token.json",
    },
    "stamp": {
        "qr_size_pct":          0.06,
        "qr_position":          "bottom-left",
        "password_position":    "bottom-right",
        "password_font_pct":    0.020,
        # How far each element is pushed IN from the edges, as a fraction of
        # the image dimension. Separate X (horizontal) + Y (vertical) control.
        # Bigger = further from the border. Try 0.02–0.06.
        "qr_inset_x_pct":           0.03,
        "qr_inset_y_pct":           0.035,
        "password_inset_x_pct":     0.03,
        "password_inset_y_pct":     0.035,
    },
    # Access code gate. "free" = anyone can start a session. "paid" = user
    # must enter a valid 9-digit code from codes.json before each session.
    "access_code": {
        "mode": "free",
    },
    "ui": {
        "studio_name":      "NEO PHOTO STUDIO",
        "app_title":        "Photobooth Ceria",
        "tagline":          "Abadikan momen seru kamu — empat foto, satu memori.",
        "start_button":     "Mulai Sesi  →",
    },  
    }

CONFIG = {}

def _deep_merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst

def _resolve_config_path():
    import os
    for i, a in enumerate(sys.argv[1:], start=1):
        if a == "--config" and i + 1 <= len(sys.argv) - 1:
            return Path(sys.argv[i + 1])
        if a.startswith("--config="):
            return Path(a.split("=", 1)[1])
    env_path = os.environ.get("PHOTOBOOTH_CONFIG")
    if env_path:
        return Path(env_path)
    return BASE_DIR / "config.json"


def _cfg_print(msg):
    """print() that can never kill startup on a legacy-codepage console."""
    try:
        print(msg)
    except Exception:
        try:
            sys.stdout.write(msg.encode("ascii", "replace").decode("ascii") + chr(10))
        except Exception:
            pass


def _load_app_config():
    import copy
    cfg = copy.deepcopy(_FALLBACK_DEFAULTS)
    cfg_file = _resolve_config_path()
    print(f"[CONFIG] looking for config at: {cfg_file}")
    print(f"[CONFIG] base dir (exe folder): {BASE_DIR}")
    print(f"[CONFIG] frozen mode: {getattr(sys, 'frozen', False)}")
    if cfg_file.exists():
        try:
            with open(cfg_file, "r", encoding="utf-8-sig") as f:
                user_cfg = json.load(f)
            _deep_merge(cfg, user_cfg)
            _cfg_print(f"[CONFIG] OK loaded from {cfg_file}")
        except Exception as e:
            _cfg_print(f"[CONFIG] FAILED to read {cfg_file}: {e}")
    else:
        try:
            cfg_file.parent.mkdir(parents=True, exist_ok=True)
            with open(cfg_file, "w", encoding="utf-8") as f:
                json.dump(_FALLBACK_DEFAULTS, f, indent=2, ensure_ascii=False)
            _cfg_print(f"[CONFIG] no config found - wrote defaults to {cfg_file}")
        except Exception as e:
            _cfg_print(f"[CONFIG] FAILED to write default {cfg_file}: {e}")
    return cfg

def _load_gdrive_config():
    return CONFIG.get("gdrive", {})

# ================= SESSION STATS =================
STATS_FILE = BASE_DIR / "session_stats.json"
CODES_FILE = BASE_DIR / "codes.json"


def _load_codes():
    """Load codes file. Format:
       {"codes": {"123456789": {"used": false, "used_at": null}, ...}}
       If file missing → create empty + warn admin to add codes."""
    if not CODES_FILE.exists():
        try:
            with open(CODES_FILE, "w", encoding="utf-8") as f:
                json.dump({"codes": {}}, f, indent=2, ensure_ascii=False)
            LOG.warning(f"[CODES] no codes.json — created empty at {CODES_FILE}")
        except Exception as e:
            LOG.error(f"[CODES] cannot create {CODES_FILE}: {e}")
        return {"codes": {}}
    try:
        with open(CODES_FILE, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        if "codes" not in data:
            data = {"codes": {}}
        return data
    except Exception as e:
        LOG.error(f"[CODES] read failed: {e}")
        return {"codes": {}}


def _check_and_use_code(code):
    """Returns:
       - 'ok'        : code valid + unused → marked used
       - 'used'      : code valid but already used
       - 'invalid'   : code not in file
       - 'error'     : file read/write failed"""
    code = str(code).strip()
    if not code:
        return "invalid"
    data = _load_codes()
    codes = data.get("codes", {})
    if code not in codes:
        return "invalid"
    entry = codes[code]
    # Normalize: entry can be bool, dict, or null.
    if isinstance(entry, dict):
        if entry.get("used"):
            return "used"
        entry["used"] = True
        entry["used_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    else:
        if entry is True:
            return "used"
        codes[code] = {"used": True,
                       "used_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    try:
        with open(CODES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        LOG.info(f"[CODES] code {code} consumed")
        return "ok"
    except Exception as e:
        LOG.error(f"[CODES] write failed: {e}")
        return "error"



def _record_session(extra=None):
    """Append a full per-session log entry. Append-only: every session adds
    a timestamped row, never overwrites. Returns the updated stats dict.
    Lives next to the .exe; survives restarts."""
    now = datetime.datetime.now()
    stats = {"total": 0, "first_session": None, "last_session": None, "sessions": []}
    if STATS_FILE.exists():
        try:
            with open(STATS_FILE, "r", encoding="utf-8-sig") as f:
                stats = json.load(f)
        except Exception as e:
            LOG.warning(f"[STATS] read failed, resetting: {e}")
    if "sessions" not in stats or not isinstance(stats["sessions"], list):
        stats["sessions"] = []
    entry = {
        "n": int(stats.get("total", 0)) + 1,
        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
    }
    if extra:
        entry.update(extra)
    stats["sessions"].append(entry)
    stats["total"] = entry["n"]
    if not stats.get("first_session"):
        stats["first_session"] = entry["timestamp"]
    stats["last_session"] = entry["timestamp"]
    try:
        with open(STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        LOG.info(f"[STATS] session #{entry['n']} logged @ {entry['timestamp']}")
    except Exception as e:
        LOG.error(f"[STATS] write failed: {e}")
    return stats


def _detect_available_cameras(max_index=10):
    available = []
    LOG.info(f"[CAMERA] Scanning for available cameras (indices 0-{max_index-1})...")
    for idx in range(max_index):
        try:
            if platform.system() == "Windows":
                backends = [
                    (cv2.CAP_DSHOW, "DSHOW"),
                    (getattr(cv2, 'CAP_MEDIAFOUNDATION', None), "MediaFoundation"),
                    (getattr(cv2, "CAP_VFW", None), "VFW"),
                    (cv2.CAP_ANY, "ANY"),
                ]
                backends = [(b, n) for b, n in backends if b is not None]
            else:
                backends = [(cv2.CAP_ANY, "ANY")]
            for backend, backend_name in backends:
                cap = cv2.VideoCapture(idx, backend)
                if cap.isOpened():
                    ret, _ = cap.read()
                    if ret:
                        width = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
                        height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
                        LOG.info(f"[CAMERA] Index {idx} ({backend_name}): OK [{int(width)}x{int(height)}]")
                        if idx not in available:
                            available.append(idx)
                        cap.release()
                        break
                    cap.release()
        except Exception:
            pass
    if available:
        LOG.info(f"[CAMERA] Found {len(available)} camera(s) at index(es): {available}")
    else:
        LOG.warning("[CAMERA] No cameras detected!")
    return available

CONFIG.update(_load_app_config())

# ---- Seoul Pastel Pop palette ----
# Legacy keys are kept so older call sites still resolve: "yellow" is now
# the primary accent (pink) and "yellow_dk" its pressed shade.
COLORS = {
    "bg_dark":   "#FFE4EC", "bg_mid": "#FFEFF4", "bg_light": "#FFF7F2",
    "yellow":    "#FF6FA3", "yellow_dk": "#F0508A", "white": "#FFFFFF",
    "ink":       "#3B2A4A", "muted": "#8C7A99", "danger": "#FF5A6E",
    "success":   "#4CC9A8", "border": "#F3D6E2",
    "pink":      "#FF6FA3", "pink_dk": "#F0508A", "pink_soft": "#FFD6E5",
    "lilac":     "#B9A6FF", "lilac_dk": "#9C86F5", "lilac_soft": "#ECE6FF",
    "mint":      "#8FE3CF", "mint_dk": "#4CC9A8", "mint_soft": "#DDF7EF",
    "butter":    "#FFE59A", "cream": "#FFF7F2", "blush": "#FFE4EC",
    "ink_soft":  "#7A6488", "card": "#FFFFFF",
}

# Resolved by _setup_fonts() once QApplication exists. Drop .ttf/.otf files
# (e.g. Poppins + Jua from Google Fonts) into fonts/ for the full look.
FONT_UI = "Segoe UI"
FONT_DISPLAY = "Segoe UI"


def _setup_fonts(app):
    global FONT_UI, FONT_DISPLAY
    fdir = BASE_DIR / "fonts"
    if fdir.exists():
        for f in sorted(list(fdir.glob("*.ttf")) + list(fdir.glob("*.otf"))):
            if QFontDatabase.addApplicationFont(str(f)) < 0:
                LOG.warning(f"[FONT] failed to load {f.name}")
    fams = set(QFontDatabase().families())

    def _pick(*names):
        for n in names:
            if n in fams:
                return n
        return "Segoe UI"
    FONT_UI = _pick("Poppins", "Nunito", "Quicksand", "Segoe UI")
    FONT_DISPLAY = _pick("Jua", "Gaegu", "Poppins", "Segoe UI Black", "Segoe UI")
    app.setFont(QFont(FONT_UI, 11))
    LOG.info(f"[FONT] ui={FONT_UI!r} display={FONT_DISPLAY!r}")


class PastelBackground(QWidget):
    """App backdrop: cream->blush gradient, soft colour blobs, faint polka
    dots and scattered stickers. Rendered once per size into a pixmap so the
    live preview repainting at 25 fps never redraws it."""
    STICKERS = "\u273f\u2606\u2661\u2726"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cache = None
        rnd = random.Random(20261001)
        palette = [COLORS["pink"], COLORS["lilac"], COLORS["mint"], COLORS["butter"]]
        # Stickers live in the side margins so they never sit behind titles.
        self._stickers = [(rnd.choice((rnd.uniform(0.02, 0.22), rnd.uniform(0.78, 0.97))),
                           rnd.random(), rnd.choice(self.STICKERS),
                           rnd.choice(palette), rnd.randint(16, 34))
                          for _ in range(0)]   # sticker glyphs disabled (clean look)

    def resizeEvent(self, e):
        self._cache = None
        super().resizeEvent(e)

    def _render(self):
        w, h = max(1, self.width()), max(1, self.height())
        pm = QPixmap(w, h)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        g = QLinearGradient(0, 0, w * 0.35, h)
        g.setColorAt(0.0, QColor(COLORS["cream"]))
        g.setColorAt(1.0, QColor(COLORS["blush"]))
        p.fillRect(0, 0, w, h, QBrush(g))
        big = max(w, h)
        p.setPen(Qt.NoPen)
        for cx, cy, r, col, a in ((0.08, 0.12, 0.30, COLORS["lilac"], 70),
                                  (0.92, 0.16, 0.26, COLORS["mint"], 60),
                                  (0.86, 0.94, 0.34, COLORS["pink"], 55),
                                  (0.10, 0.90, 0.24, COLORS["butter"], 85)):
            rg = QRadialGradient(QPointF(cx * w, cy * h), r * big)
            c0 = QColor(col); c0.setAlpha(a)
            c1 = QColor(col); c1.setAlpha(0)
            rg.setColorAt(0.0, c0)
            rg.setColorAt(1.0, c1)
            p.setBrush(QBrush(rg))
            p.drawEllipse(QPointF(cx * w, cy * h), r * big, r * big)
        dot = QColor(COLORS["pink"]); dot.setAlpha(30)
        p.setBrush(dot)
        step = 44
        for row, y in enumerate(range(step // 2, h, step)):
            off = step // 2 if row % 2 else 0
            for x in range(off, w, step):
                p.drawEllipse(QPointF(x, y), 2.2, 2.2)
        for fx, fy, glyph, col, size in self._stickers:
            c = QColor(col); c.setAlpha(130)
            p.setPen(c)
            f = QFont(FONT_DISPLAY)
            f.setPixelSize(size)
            f.setBold(True)
            p.setFont(f)
            p.drawText(QPointF(fx * w, fy * h), glyph)
        p.end()
        return pm

    def paintEvent(self, e):
        if self._cache is None or self._cache.size() != self.size():
            self._cache = self._render()
        p = QPainter(self)
        p.drawPixmap(e.rect(), self._cache, e.rect())


class PolaroidCluster(QWidget):
    """Three tilted pastel polaroids for the homepage hero."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(560, 300)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        k = self.height() / 300.0
        cw, ch = 180 * k, 220 * k
        specs = ((-12, 0.24, COLORS["lilac"], COLORS["lilac_soft"], "", "cheese!"),
                 (9, 0.76, COLORS["mint"], COLORS["mint_soft"], "", "bestie"),
                 (-2, 0.50, COLORS["pink"], COLORS["pink_soft"], "", "seoul"))
        for angle, fx, col, soft, glyph, cap in specs:
            p.save()
            p.translate(self.width() * fx, self.height() * 0.5)
            p.rotate(angle)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(190, 70, 120, 45))
            p.drawRoundedRect(QRectF(-cw / 2 + 4, -ch / 2 + 10, cw, ch), 10, 10)
            p.setBrush(QColor("white"))
            p.drawRoundedRect(QRectF(-cw / 2, -ch / 2, cw, ch), 10, 10)
            inner = QRectF(-cw / 2 + 12 * k, -ch / 2 + 12 * k, cw - 24 * k, ch - 62 * k)
            g = QLinearGradient(inner.topLeft(), inner.bottomRight())
            g.setColorAt(0.0, QColor(soft))
            g.setColorAt(1.0, QColor(col))
            p.setBrush(QBrush(g))
            p.drawRoundedRect(inner, 6, 6)
            f = QFont(FONT_DISPLAY); f.setPixelSize(int(58 * k)); p.setFont(f)
            p.setPen(QColor("white"))
            p.drawText(inner, Qt.AlignCenter, glyph)
            f2 = QFont(FONT_DISPLAY); f2.setPixelSize(int(20 * k)); p.setFont(f2)
            p.setPen(QColor(COLORS["ink"]))
            p.drawText(QRectF(-cw / 2, ch / 2 - 50 * k, cw, 44 * k), Qt.AlignCenter, cap)
            p.restore()

TWEMOJI = {
    "camera":   "1f4f7", "sparkle":  "2728", "star":     "2b50",
    "heart":    "2764",  "house":    "1f3e0", "printer":  "1f5a8",
    "check":    "2705",  "flash":    "26a1",  "image":    "1f5bc",
    "smile":    "1f60a", "rec":      "1f534", "party":    "1f389",
}
TWEMOJI_BASE = "https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72"


class IconLoader:
    def __init__(self, parent_qobject):
        self.nam = QNetworkAccessManager(parent_qobject)
        self.nam.finished.connect(self._on_finished)
        self.cache = {}
        self.pending = {}

    def request(self, key, size_px, callback):
        cp = TWEMOJI.get(key)
        if not cp:
            return
        if cp in self.cache:
            pix = self.cache[cp].scaled(size_px, size_px, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            callback(pix)
            return
        first_time = cp not in self.pending
        self.pending.setdefault(cp, []).append((callback, size_px))
        if first_time:
            url = f"{TWEMOJI_BASE}/{cp}.png"
            req = QNetworkRequest(QUrl(url))
            req.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.NoLessSafeRedirectPolicy)
            req.setAttribute(QNetworkRequest.User, cp)
            self.nam.get(req)

    def _on_finished(self, reply):
        cp = reply.request().attribute(QNetworkRequest.User)
        data = bytes(reply.readAll())
        reply.deleteLater()
        if not data:
            self.pending.pop(cp, None)
            return
        pix = QPixmap()
        if not pix.loadFromData(QByteArray(data)):
            self.pending.pop(cp, None)
            return
        self.cache[cp] = pix
        for cb, size_px in self.pending.pop(cp, []):
            try:
                cb(pix.scaled(size_px, size_px, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            except Exception:
                pass


class CameraThread(QThread):
    frame_ready = pyqtSignal(np.ndarray, np.ndarray, bool)

    def __init__(self, index=0):
        super().__init__()
        self.index = index
        self.running = True
        self.crop = False
        self.target_aspect = 3 / 4
        self.fit_mode = "cover"
        self.pad_color = (0, 0, 0)

    def run(self):
        cam_idx = self.index
        if cam_idx is None or cam_idx == "auto":
            available = _detect_available_cameras(max_index=10)
            if not available:
                LOG.error("[CAMERA] No cameras found during auto-detection")
                return
            cam_idx = available[0]
            LOG.info(f"[CAMERA] Auto-selected camera at index {cam_idx}")
        cap = None
        if platform.system() == "Windows":
            backends = [
                (cv2.CAP_DSHOW, "DSHOW"),
                (getattr(cv2, 'CAP_MEDIAFOUNDATION', None), "MediaFoundation"),
                (getattr(cv2, "CAP_VFW", None), "VFW"),
                (cv2.CAP_ANY, "ANY"),
            ]
            backends = [(b, n) for b, n in backends if b is not None]
        else:
            backends = [(cv2.CAP_ANY, "ANY")]
        for backend, backend_name in backends:
            cap = cv2.VideoCapture(cam_idx, backend)
            if cap.isOpened():
                ret, _ = cap.read()
                if ret:
                    LOG.info(f"[CAMERA] Opened index {cam_idx} with {backend_name} backend")
                    break
                cap.release()
        if cap is None or not cap.isOpened():
            LOG.error(f"[CAMERA] Failed to open camera at index {cam_idx}")
            return
        req_w = getattr(self, "req_width",  1280)
        req_h = getattr(self, "req_height", 720)
        if str(req_w).lower() == "auto" or str(req_h).lower() == "auto" or req_w in (0, None) or req_h in (0, None):
            probe = [(3840, 2160), (2560, 1440), (1920, 1080),
                     (1600, 900), (1280, 720), (640, 480)]
            best = (0, 0)
            for pw, ph in probe:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, pw)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, ph)
                aw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                ah = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                if aw >= pw * 0.95 and ah >= ph * 0.95:
                    best = (aw, ah)
                    break
                if aw * ah > best[0] * best[1]:
                    best = (aw, ah)
            if best[0] > 0:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, best[0])
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, best[1])
            req_w, req_h = best
            LOG.info(f"[CAMERA] auto-detect picked {best[0]}x{best[1]}")
        else:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH,  int(req_w))
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(req_h))
        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        LOG.info(f"[CAMERA] Active res: {actual_w}x{actual_h} (requested {req_w}x{req_h})")
        while self.running:
            ret, frame = cap.read()
            if ret:
                frame = cv2.flip(frame, 1)
                raw = np.ascontiguousarray(frame)
                if self.crop:
                    h, w = frame.shape[:2]
                    aspect = max(0.1, float(self.target_aspect))
                    src_aspect = w / h
                    fit = getattr(self, "fit_mode", "cover")
                    if fit == "contain":
                        if src_aspect > aspect:
                            new_w = w
                            new_h = int(round(w / aspect))
                            pad_total = new_h - h
                            pad_top = pad_total // 2
                            pad_bot = pad_total - pad_top
                            display = cv2.copyMakeBorder(
                                frame, pad_top, pad_bot, 0, 0,
                                cv2.BORDER_CONSTANT, value=self.pad_color)
                        else:
                            new_h = h
                            new_w = int(round(h * aspect))
                            pad_total = new_w - w
                            pad_left = pad_total // 2
                            pad_right = pad_total - pad_left
                            display = cv2.copyMakeBorder(
                                frame, 0, 0, pad_left, pad_right,
                                cv2.BORDER_CONSTANT, value=self.pad_color)
                        display = np.ascontiguousarray(display)
                    else:
                        if src_aspect > aspect:
                            new_w = int(round(h * aspect))
                            start_x = (w - new_w) // 2
                            display = np.ascontiguousarray(
                                frame[:, start_x:start_x + new_w])
                        else:
                            new_h = int(round(w / aspect))
                            start_y = (h - new_h) // 2
                            display = np.ascontiguousarray(
                                frame[start_y:start_y + new_h, :])
                else:
                    display = raw
                self.frame_ready.emit(display, raw, self.crop)
            time.sleep(0.02)
        cap.release()
        LOG.info("[CAMERA] Camera thread stopped")

    def stop(self):
        self.running = False
        self.wait()


SLOT_ALPHA_MAX = 128   # overlay alpha below this = photo hole


def fallback_slot_rects(ow, oh, n):
    n = max(1, n)
    if n == 1: cols = 1
    elif n == 2: cols = 2
    elif n == 3: cols = 3 if ow > oh else 1
    elif n == 4: cols = 2
    elif n in (5, 6): cols = 3 if ow > oh else 2
    else: cols = 2
    rows = (n + cols - 1) // cols
    mx = int(ow * 0.05); my = int(oh * 0.05)
    gap_x = int(ow * 0.02); gap_y = int(oh * 0.02)
    cell_w = (ow - 2 * mx - (cols - 1) * gap_x) // cols
    cell_h = (oh - 2 * my - (rows - 1) * gap_y) // rows
    rects = []
    for i in range(n):
        r, c = divmod(i, cols)
        x = mx + c * (cell_w + gap_x)
        y = my + r * (cell_h + gap_y)
        rects.append((x, y, cell_w, cell_h))
    return rects


def crop_center_to_aspect(img_bgr, target_w, target_h, fit_mode="cover", pad_color=(0, 0, 0)):
    h, w = img_bgr.shape[:2]
    if w <= 0 or h <= 0 or target_w <= 0 or target_h <= 0:
        return img_bgr
    src_ratio = w / h
    tgt_ratio = target_w / target_h
    if fit_mode == "contain":
        if src_ratio > tgt_ratio:
            new_w = target_w
            new_h = max(1, int(round(target_w / src_ratio)))
        else:
            new_h = target_h
            new_w = max(1, int(round(target_h * src_ratio)))
        resized = cv2.resize(img_bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)
        pad_top = (target_h - new_h) // 2
        pad_bot = target_h - new_h - pad_top
        pad_left = (target_w - new_w) // 2
        pad_right = target_w - new_w - pad_left
        return cv2.copyMakeBorder(resized, pad_top, pad_bot, pad_left, pad_right,
                                  cv2.BORDER_CONSTANT, value=pad_color)
    if src_ratio > tgt_ratio:
        new_w = int(round(h * tgt_ratio))
        x0 = (w - new_w) // 2
        cropped = img_bgr[:, x0:x0 + new_w]
    else:
        new_h = int(round(w / tgt_ratio))
        y0 = (h - new_h) // 2
        cropped = img_bgr[y0:y0 + new_h, :]
    return cv2.resize(cropped, (target_w, target_h), interpolation=cv2.INTER_AREA)


def _match_color_to(img_bgr, ref_bgr):
    """Make a DSLR still look like the live view the customer saw: per-channel
    histogram matching of brightness + mean/spread transfer of colour (LAB),
    stats taken from
    downscaled copies, applied to the full-res image as a LUT.
    If the still has no colour (camera Picture Style = Monochrome) but the
    live view does, only brightness is matched - colour can't be recovered."""
    if img_bgr is None or ref_bgr is None or img_bgr.size == 0 or ref_bgr.size == 0:
        return img_bgr

    def _small(x, n=640):
        h, w = x.shape[:2]
        s = min(1.0, n / float(max(h, w)))
        return cv2.resize(x, (max(1, int(w * s)), max(1, int(h * s))),
                          interpolation=cv2.INTER_AREA) if s < 1 else x

    src_lab = cv2.cvtColor(_small(img_bgr), cv2.COLOR_BGR2LAB)
    ref_lab = cv2.cvtColor(_small(ref_bgr), cv2.COLOR_BGR2LAB)

    def _chroma(lab):
        return float(np.abs(lab[..., 1:].astype(np.float32) - 128).mean())

    still_mono = _chroma(src_lab) < 1.5 and _chroma(ref_lab) > 4.0
    if still_mono:
        LOG.warning("[COLOR] DSLR photo is black & white but live view is colour - "
                    "set the camera's Picture Style to Standard (not Monochrome)")
    full_lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB)
    chans = cv2.split(full_lab)
    out = []
    for c in range(3):
        if still_mono and c > 0:
            out.append(chans[c])
            continue
        if c > 0:
            # Colour (a/b): shift + scale to the live view's mean/spread.
            # Linear, so no hue artifacts in dark or clipped areas.
            sm, ss = float(src_lab[..., c].mean()), float(src_lab[..., c].std())
            rm, rs = float(ref_lab[..., c].mean()), float(ref_lab[..., c].std())
            k = float(np.clip(rs / max(1e-3, ss), 0.5, 2.0))
            lut = np.clip((np.arange(256, dtype=np.float32) - sm) * k + rm, 0, 255)
            out.append(cv2.LUT(chans[c], lut.astype(np.uint8)))
            continue
        sh = np.bincount(src_lab[..., c].ravel(), minlength=256).astype(np.float64)
        rh = np.bincount(ref_lab[..., c].ravel(), minlength=256).astype(np.float64)
        scdf = np.cumsum(sh) / max(1.0, sh.sum())
        rcdf = np.cumsum(rh) / max(1.0, rh.sum())
        lut = np.clip(np.searchsorted(rcdf, scdf), 0, 255).astype(np.uint8)
        # Smooth the curve a little so banding can't appear.
        lut = cv2.GaussianBlur(lut.reshape(1, -1).astype(np.float32), (0, 0), 2.0)
        out.append(cv2.LUT(chans[c], np.clip(lut, 0, 255).astype(np.uint8).reshape(-1)))
    return cv2.cvtColor(cv2.merge(out), cv2.COLOR_LAB2BGR)


def _crop_to_aspect(img_bgr, aspect, max_long_side=0):
    """Center-crop a full-res DSLR frame to `aspect` (w/h) WITHOUT resampling,
    then optionally cap its long side. Keeps native resolution for print."""
    if img_bgr is None or img_bgr.size == 0:
        return img_bgr
    h, w = img_bgr.shape[:2]
    aspect = max(0.1, float(aspect))
    if w / h > aspect:
        new_w = int(round(h * aspect))
        x0 = max(0, (w - new_w) // 2)
        out = img_bgr[:, x0:x0 + new_w]
    else:
        new_h = int(round(w / aspect))
        y0 = max(0, (h - new_h) // 2)
        out = img_bgr[y0:y0 + new_h, :]
    out = np.ascontiguousarray(out)
    try:
        cap = int(max_long_side or 0)
    except Exception:
        cap = 0
    if cap > 0:
        oh, ow = out.shape[:2]
        long_side = max(oh, ow)
        if long_side > cap:
            sc = cap / float(long_side)
            out = cv2.resize(out, (max(1, int(ow * sc)), max(1, int(oh * sc))),
                             interpolation=cv2.INTER_AREA)
    return out


FILTER_IDS = [
    "none", "bw", "noir", "sepia", "warm",
    "cool", "vivid", "soft", "fade",
]
FILTER_LABELS = {
    "none":  "Original", "bw": "B&W", "noir": "Noir", "sepia": "Sepia",
    "warm": "Warm", "cool": "Cool", "vivid": "Vivid", "soft": "Soft", "fade": "Fade",
}
# Lens / lighting / face-aware / artistic effects (effects.py).
FILTER_IDS += EFFECT_IDS
FILTER_LABELS.update(EFFECT_LABELS)

def beautify(img_bgr, strength=1.0):
    """Light auto-enhance for low-light / dull photos:
      - auto brightness lift (only when the frame is dark)
      - gentle white balance toward neutral
      - skin-tone warmth + subtle smoothing
      - mild contrast & saturation pop
    `strength` 0.0 = off, 1.0 = default (medium), up to ~2.0 = strong.
    Returns a new BGR uint8 array."""
    if img_bgr is None or img_bgr.size == 0 or strength <= 0:
        return img_bgr
    s = float(strength)
    out = img_bgr.astype(np.float32)

    # 1) Auto brightness — measure mean luma; lift only if dark.
    luma = 0.114 * out[..., 0] + 0.587 * out[..., 1] + 0.299 * out[..., 2]
    mean_luma = float(luma.mean())
    # Target a comfortable mid-brightness (~135). Lift scales with how dark
    # it is, capped so bright photos aren't blown out.
    target = 135.0
    if mean_luma < target:
        lift = 1.0 + min(0.6, (target - mean_luma) / target) * s
        out = out * lift

    # 2) Gentle gray-world white balance (reduce color cast from bad lighting).
    b_mean = out[..., 0].mean() + 1e-5
    g_mean = out[..., 1].mean() + 1e-5
    r_mean = out[..., 2].mean() + 1e-5
    gray = (b_mean + g_mean + r_mean) / 3.0
    wb_amt = 0.5 * s  # partial correction so it stays natural
    out[..., 0] *= (1.0 + wb_amt * (gray / b_mean - 1.0))
    out[..., 1] *= (1.0 + wb_amt * (gray / g_mean - 1.0))
    out[..., 2] *= (1.0 + wb_amt * (gray / r_mean - 1.0))

    # 3) Skin warmth — small boost to red, tiny drop to blue.
    out[..., 2] = out[..., 2] * (1.0 + 0.05 * s) + 4 * s   # R
    out[..., 0] = out[..., 0] * (1.0 - 0.03 * s)            # B
    out = np.clip(out, 0, 255).astype(np.uint8)

    # 4) Mild contrast (S-curve) + saturation pop in HSV.
    hsv = cv2.cvtColor(out, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[..., 1] = np.clip(hsv[..., 1] * (1.0 + 0.12 * s), 0, 255)  # saturation
    hsv[..., 2] = np.clip(hsv[..., 2] * (1.0 + 0.04 * s), 0, 255)  # brightness
    out = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)

    # 5) Subtle skin smoothing — bilateral blur blended back so it's not plastic.
    smooth_amt = min(1.0, 0.5 * s)
    if smooth_amt > 0.01:
        smoothed = cv2.bilateralFilter(out, d=7, sigmaColor=40, sigmaSpace=40)
        out = cv2.addWeighted(out, 1.0 - smooth_amt * 0.6,
                              smoothed, smooth_amt * 0.6, 0)
    return out


def apply_filter(img_bgr, name):
    if name in (None, "", "none"):
        return img_bgr
    img = img_bgr
    if img is None or img.size == 0:
        return img
    if name == "bw":
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
    if name == "noir":
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        x = np.arange(256, dtype=np.float32) / 255.0
        y = np.clip(0.5 + 1.4 * (x - 0.5) + 0.05 * np.sin((x - 0.5) * 6.0), 0, 1)
        lut = (y * 255).astype(np.uint8)
        g = cv2.LUT(g, lut)
        return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
    if name == "sepia":
        b, g, r = cv2.split(img.astype(np.float32))
        tr = 0.393 * r + 0.769 * g + 0.189 * b
        tg = 0.349 * r + 0.686 * g + 0.168 * b
        tb = 0.272 * r + 0.534 * g + 0.131 * b
        out = cv2.merge([tb, tg, tr])
        return np.clip(out, 0, 255).astype(np.uint8)
    if name == "warm":
        b, g, r = cv2.split(img.astype(np.float32))
        r = np.clip(r * 1.12 + 8,  0, 255)
        g = np.clip(g * 1.04,       0, 255)
        b = np.clip(b * 0.88 - 4,   0, 255)
        return cv2.merge([b, g, r]).astype(np.uint8)
    if name == "cool":
        b, g, r = cv2.split(img.astype(np.float32))
        r = np.clip(r * 0.88 - 4,   0, 255)
        g = np.clip(g * 0.98,       0, 255)
        b = np.clip(b * 1.18 + 10,  0, 255)
        return cv2.merge([b, g, r]).astype(np.uint8)
    if name == "vivid":
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
        hsv[..., 1] = np.clip(hsv[..., 1] * 1.45, 0, 255)
        hsv[..., 2] = np.clip(hsv[..., 2] * 1.05, 0, 255)
        out = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
        blur = cv2.GaussianBlur(out, (0, 0), 1.5)
        return cv2.addWeighted(out, 1.25, blur, -0.25, 0)
    if name == "soft":
        return cv2.bilateralFilter(img, d=9, sigmaColor=55, sigmaSpace=55)
    if name == "fade":
        out = img.astype(np.float32)
        out = out * 0.88 + 24
        hsv = cv2.cvtColor(np.clip(out, 0, 255).astype(np.uint8), cv2.COLOR_BGR2HSV).astype(np.float32)
        hsv[..., 1] *= 0.78
        out = cv2.cvtColor(np.clip(hsv, 0, 255).astype(np.uint8), cv2.COLOR_HSV2BGR).astype(np.float32)
        b, g, r = cv2.split(out)
        r = np.clip(r + 6, 0, 255); b = np.clip(b - 4, 0, 255)
        return cv2.merge([b, g, r]).astype(np.uint8)
    if name in EFFECT_IDS:
        return apply_effect(img, name)
    return img


def stamp_password_on_image(pil_img, password, qr_url=None):
    """Stamp password + QR onto an in-memory PIL image. Returns a NEW image,
    leaves the original untouched."""
    try:
        from PIL import Image as PILImage, ImageDraw, ImageFont
        stamp_cfg = CONFIG.get("stamp", {})
        qr_pos       = stamp_cfg.get("qr_position", "bottom-left")
        pwd_pos      = stamp_cfg.get("password_position", "bottom-right")
        qr_size_pct  = float(stamp_cfg.get("qr_size_pct", 0.06))
        pwd_font_pct = float(stamp_cfg.get("password_font_pct", 0.020))
        im = pil_img.convert("RGBA").copy()
        w, h = im.size
        draw = ImageDraw.Draw(im)
        font_size = max(28, int(h * pwd_font_pct))

        # Insets as fraction of the relevant dimension. Each corner element
        # (QR + password) can be pushed "in" from the edges independently on
        # X and Y axes. Back-compat: bottom_inset_pct still works as a
        # vertical inset for bottom-* positions if the new keys are absent.
        legacy_bottom = float(stamp_cfg.get("bottom_inset_pct", 0.0))
        qr_inset_x  = float(stamp_cfg.get("qr_inset_x_pct",  stamp_cfg.get("qr_inset_pct", 0.02)))
        qr_inset_y  = float(stamp_cfg.get("qr_inset_y_pct",  stamp_cfg.get("qr_inset_pct", legacy_bottom or 0.02)))
        pwd_inset_x = float(stamp_cfg.get("password_inset_x_pct", 0.02))
        pwd_inset_y = float(stamp_cfg.get("password_inset_y_pct", legacy_bottom or 0.02))

        font = None
        for candidate in ("DejaVuSans-Bold.ttf", "Arial.ttf", "arial.ttf",
                          "Helvetica.ttf", "LiberationSans-Bold.ttf"):
            try:
                font = ImageFont.truetype(candidate, font_size)
                break
            except Exception:
                continue
        if font is None:
            font = ImageFont.load_default()
        text = str(password)
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        except Exception:
            tw, th = (font.getsize(text) if hasattr(font, "getsize")
                      else (len(text) * font_size // 2, font_size))

        def _xy_for(pos, w_obj, h_obj, inset_x_pct, inset_y_pct):
            """Position w_obj×h_obj inside the image at `pos`, pushed in from
            the edges by inset_x_pct (of width) and inset_y_pct (of height)."""
            ix = int(w * inset_x_pct)
            iy = int(h * inset_y_pct)
            pos = (pos or "bottom-left").lower()
            top = "top" in pos
            left = "left" in pos
            x = ix if left else (w - w_obj - ix)
            y = iy if top  else (h - h_obj - iy)
            return x, y

        # Password
        x, y = _xy_for(pwd_pos, tw, th, pwd_inset_x, pwd_inset_y)
        draw.text((x + 1, y + 1), text, fill=(0, 0, 0, 160), font=font)
        draw.text((x,     y    ), text, fill=(255, 255, 255, 230), font=font)
        # QR
        if qr_url:
            try:
                import qrcode
                qr = qrcode.QRCode(
                    version=None,
                    error_correction=qrcode.constants.ERROR_CORRECT_M,
                    box_size=10, border=2,
                )
                qr.add_data(qr_url)
                qr.make(fit=True)
                qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")
                qr_size = max(70, int(h * qr_size_pct))
                qr_img = qr_img.resize((qr_size, qr_size), PILImage.NEAREST)
                qx, qy = _xy_for(qr_pos, qr_size, qr_size, qr_inset_x, qr_inset_y)
                bg = PILImage.new("RGBA", (qr_size + 8, qr_size + 8), (255, 255, 255, 255))
                im.paste(bg, (qx - 4, qy - 4))
                im.paste(qr_img, (qx, qy))
            except Exception as e:
                LOG.warning(f"[STAMP] QR failed: {e}")
        return im
    except Exception as e:
        LOG.warning(f"[STAMP] failed: {e}")
        return pil_img


class GDriveUploader:
    SCOPES = ["https://www.googleapis.com/auth/drive.file"]
    def __init__(self, client_secrets_path, folder_id, token_path="oauth_token.json"):
        cs = Path(client_secrets_path)
        if not cs.is_absolute():
            cs = BASE_DIR / cs
        self.client_secrets_path = str(cs)
        self.folder_id           = folder_id
        tp = Path(token_path)
        if not tp.is_absolute():
            tp = BASE_DIR / tp
        self.token_path          = tp
        self._service            = None
        self._error              = None

    def _ensure_service(self):
        if self._service is not None or self._error is not None:
            return
        try:
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build
            creds = None
            if self.token_path.exists():
                try:
                    creds = Credentials.from_authorized_user_file(
                        str(self.token_path), self.SCOPES)
                except Exception:
                    creds = None
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    try:
                        creds.refresh(Request())
                    except Exception:
                        creds = None
                if not creds:
                    if not Path(self.client_secrets_path).exists():
                        raise FileNotFoundError(
                            f"OAuth client secrets not found: {self.client_secrets_path}")
                    flow = InstalledAppFlow.from_client_secrets_file(
                        self.client_secrets_path, self.SCOPES)
                    creds = flow.run_local_server(port=0)
                try:
                    with open(self.token_path, "w", encoding="utf-8") as f:
                        f.write(creds.to_json())
                except Exception:
                    pass
            self._service = build("drive", "v3", credentials=creds, cache_discovery=False)
        except Exception as e:
            self._error = f"GDrive OAuth failed: {e}"
            LOG.error(f"[GDRIVE] {self._error}")

    def upload_file(self, local_path, remote_name=None, make_public=True, parent_id=None):
        self._ensure_service()
        if self._service is None:
            return None
        try:
            from googleapiclient.http import MediaFileUpload
            metadata = {
                "name":    remote_name or Path(local_path).name,
                "parents": [parent_id or self.folder_id],
            }
            media = MediaFileUpload(str(local_path), resumable=False)
            f = self._service.files().create(
                body=metadata, media_body=media,
                fields="id, webViewLink, webContentLink",
                supportsAllDrives=True,
            ).execute()
            file_id = f.get("id")
            if not file_id:
                return None
            if make_public:
                self._make_public(file_id)
            url = f.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view"
            return {"id": file_id, "url": url}
        except Exception as e:
            LOG.error(f"[GDRIVE] upload failed for {local_path}: {e}")
            return None

    def create_folder(self, name, parent_id=None, make_public=True):
        self._ensure_service()
        if self._service is None:
            return None
        try:
            metadata = {
                "name":     name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents":  [parent_id or self.folder_id],
            }
            f = self._service.files().create(
                body=metadata, fields="id, webViewLink",
                supportsAllDrives=True,
            ).execute()
            fid = f.get("id")
            if not fid:
                return None
            if make_public:
                self._make_public(fid)
            url = f.get("webViewLink") or f"https://drive.google.com/drive/folders/{fid}"
            return {"id": fid, "url": url}
        except Exception as e:
            LOG.error(f"[GDRIVE] create_folder failed: {e}")
            return None

    def _make_public(self, file_id):
        try:
            self._service.permissions().create(
                fileId=file_id, body={"type": "anyone", "role": "reader"},
                supportsAllDrives=True,
            ).execute()
        except Exception:
            pass


def _list_printers_win():
    if platform.system() != "Windows":
        return []
    try:
        import win32print
        return [p[2] for p in win32print.EnumPrinters(
            win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)]
    except Exception:
        return []


def _find_preferred_printer(preferred):
    if not preferred:
        return None
    pref_lower = preferred.strip().lower()
    for name in _list_printers_win():
        if pref_lower in name.lower():
            return name
    return None


def _send_pil_image_to_printer_win(pil_img, printer_name=None, paper_size="4x6",
                                    dpi=300, paper_name="", cut="none",
                                    quality="default", media_type="default",
                                    paper_w_mm=None, paper_h_mm=None):
    """Send an in-memory PIL image directly to a Windows printer. No file
    is written — the stamped copy lives only inside this function."""
    if platform.system() != "Windows":
        LOG.info("[PRINT] not Windows — skipping physical print")
        return False
    try:
        import win32print, win32ui, win32con
        from PIL import Image as PILImage, ImageWin
    except Exception as e:
        LOG.warning(f"[PRINT] pywin32 not available: {e}")
        return False
    if printer_name:
        target = printer_name
    else:
        try:
            target = win32print.GetDefaultPrinter()
        except Exception as e:
            LOG.error(f"[PRINT] no default printer: {e}")
            return False
    LOG.info(f"[PRINT] target={target}")
    custom_devmode = None
    try:
        hPrinter = win32print.OpenPrinter(target)
        try:
            info = win32print.GetPrinter(hPrinter, 2)
            devmode = info["pDevMode"]
            if devmode is not None:
                try:
                    devmode.Scale = 100
                except Exception:
                    pass
                devmode = win32print.DocumentProperties(
                    0, hPrinter, target, devmode, devmode,
                    win32con.DM_IN_BUFFER | win32con.DM_OUT_BUFFER)
                custom_devmode = devmode
        finally:
            win32print.ClosePrinter(hPrinter)
    except Exception as e:
        LOG.warning(f"[PRINT] DEVMODE setup failed: {e}")
    hDC = None
    try:
        hDC = win32ui.CreateDC()
        if custom_devmode is not None:
            try:
                hDC.CreatePrinterDC(target, custom_devmode)
            except TypeError:
                hDC.CreatePrinterDC(target)
        else:
            hDC.CreatePrinterDC(target)
        printable_w = hDC.GetDeviceCaps(win32con.HORZRES)
        printable_h = hDC.GetDeviceCaps(win32con.VERTRES)
        physical_w  = hDC.GetDeviceCaps(110)
        physical_h  = hDC.GetDeviceCaps(111)
        offset_x    = hDC.GetDeviceCaps(112)
        offset_y    = hDC.GetDeviceCaps(113)
        LOG.info(f"[PRINT] printable: {printable_w}x{printable_h}px  "
                 f"physical: {physical_w}x{physical_h}px  "
                 f"offset: ({offset_x},{offset_y})px")
        img = pil_img.convert("RGB")
        img_is_portrait      = img.height > img.width
        printer_is_portrait  = printable_h > printable_w
        if img_is_portrait != printer_is_portrait:
            img = img.rotate(90, expand=True)
        fit_mode_print = getattr(_send_pil_image_to_printer_win, "_fit_mode", "cover")
        if fit_mode_print == "driver":
            src_ratio = img.width / img.height
            dst_ratio = printable_w / printable_h
            if src_ratio > dst_ratio:
                new_w = printable_w
                new_h = int(round(printable_w / src_ratio))
            else:
                new_h = printable_h
                new_w = int(round(printable_h * src_ratio))
            img_resized = img.resize((new_w, new_h), PILImage.LANCZOS)
            canvas = PILImage.new("RGB", (printable_w, printable_h), (255, 255, 255))
            paste_x = (printable_w - new_w) // 2
            paste_y = (printable_h - new_h) // 2
            canvas.paste(img_resized, (paste_x, paste_y))
            img = canvas
        elif fit_mode_print == "contain":
            src_ratio = img.width / img.height
            phys_ratio = physical_w / physical_h
            if src_ratio > phys_ratio:
                new_w = physical_w
                new_h = int(round(physical_w / src_ratio))
            else:
                new_h = physical_h
                new_w = int(round(physical_h * src_ratio))
            img_resized = img.resize((new_w, new_h), PILImage.LANCZOS)
            canvas = PILImage.new("RGB", (printable_w, printable_h), (255, 255, 255))
            phys_paste_x = (physical_w - new_w) // 2
            phys_paste_y = (physical_h - new_h) // 2
            paste_x = phys_paste_x - offset_x
            paste_y = phys_paste_y - offset_y
            canvas.paste(img_resized, (paste_x, paste_y))
            img = canvas
        else:
            src_ratio = img.width / img.height
            dst_ratio = printable_w / printable_h
            if src_ratio > dst_ratio:
                new_w = int(img.height * dst_ratio)
                x0 = (img.width - new_w) // 2
                img = img.crop((x0, 0, x0 + new_w, img.height))
            else:
                new_h = int(img.width / dst_ratio)
                y0 = (img.height - new_h) // 2
                img = img.crop((0, y0, img.width, y0 + new_h))
            img = img.resize((printable_w, printable_h), PILImage.LANCZOS)
        hDC.StartDoc("photobooth_print")
        hDC.StartPage()
        dib = ImageWin.Dib(img)
        dib.draw(hDC.GetHandleOutput(), (0, 0, printable_w, printable_h))
        hDC.EndPage()
        hDC.EndDoc()
        hDC.DeleteDC()
        LOG.info(f"[PRINT] sent to {target}")
        return True
    except Exception as e:
        LOG.error(f"[PRINT] failed: {e}")
        try:
            if hDC is not None:
                hDC.DeleteDC()
        except Exception:
            pass
        return False


def print_session_image(pil_img, layout=None, password=None, qr_url=None):
    """Stamp the image in-memory + send to printer. Stamped copy is NEVER
    saved to disk — it exists only for the duration of this call."""
    pcfg = CONFIG.get("printing", {})
    if not pcfg.get("enabled", True):
        LOG.info("[PRINT] disabled in config — skipping")
        return False
    # Stamp now (in memory only). Original pil_img untouched.
    stamped = stamp_password_on_image(pil_img, password, qr_url=qr_url) if password else pil_img
    mode = str(pcfg.get("mode", "DSRX")).upper()
    if mode == "OTHER":
        other = pcfg.get("other", {}) or {}
        preferred = (other.get("printer") or pcfg.get("preferred_printer") or "").strip()
        printer = _find_preferred_printer(preferred) if preferred else None
        _send_pil_image_to_printer_win._fit_mode = str(other.get("fit_mode", "driver"))
        return _send_pil_image_to_printer_win(
            stamped, printer_name=printer,
            paper_size=pcfg.get("paper_size", "4x6"),
            dpi=int(pcfg.get("dpi", 300)),
            paper_name=other.get("paper_name", "") or "",
            cut="none",
            quality=str(other.get("quality", "best")),
            media_type=str(other.get("media_type", "matte")),
            paper_w_mm=other.get("paper_w_mm", 100),
            paper_h_mm=other.get("paper_h_mm", 150),
        )
    _send_pil_image_to_printer_win._fit_mode = "cover"
    cut_mode = (layout.get("cut") if layout else None) or "none"
    name_cut     = (pcfg.get("printer_cut")     or "").strip()
    name_non_cut = (pcfg.get("printer_non_cut") or "").strip()
    legacy       = (pcfg.get("preferred_printer") or "").strip()
    if cut_mode == "none":
        preferred = name_non_cut or legacy
    else:
        preferred = name_cut or legacy
    printer = _find_preferred_printer(preferred) if preferred else None
    paper_name = ""
    cut = "none"
    if printer and layout:
        paper_name = layout.get("paper_name", "") or ""
        cut        = layout.get("cut", "none")
        if cut == "2inch" and not paper_name:
            paper_name = "PR (2x6)*2"
    return _send_pil_image_to_printer_win(
        stamped, printer_name=printer,
        paper_size=pcfg.get("paper_size", "4x6"),
        dpi=int(pcfg.get("dpi", 300)),
        paper_name=paper_name, cut=cut,
    )


# ================= BACKGROUND SAVER =================
class BackgroundSaverThread(QThread):
    """Encodes BTS + Moving-picture videos off the UI thread, then triggers
    upload. GIF generation removed (too heavy at high cam res)."""
    finished_save = pyqtSignal(list, list)

    def __init__(self, parent, bts_mp4_path, mov_mp4_path,
                 bts_buffer, moving_clips, captured_frames,
                 video_fps, bts_speed, bts_max_duration_s, moving_speed,
                 overlay_path, slot_rects, slot_to_pose=None, fit_mode="cover",
                 moving_clip_s=3.0):
        super().__init__(parent)
        self._bts_path    = bts_mp4_path
        self._mov_path    = mov_mp4_path
        self._bts_buffer  = list(bts_buffer)
        self._moving_clips = [list(c) if c else None for c in moving_clips]
        self._captured_frames = [f.copy() for f in captured_frames]
        self._video_fps   = video_fps
        self._bts_speed   = bts_speed
        self._bts_max_d   = bts_max_duration_s
        self._moving_speed = moving_speed
        self._moving_clip_s = moving_clip_s
        self._overlay_path = overlay_path
        self._slot_rects  = slot_rects
        self._slot_to_pose = list(slot_to_pose) if slot_to_pose else None
        self._fit_mode    = fit_mode

    def run(self):
        paths, errors = [], []
        if self._bts_buffer:
            try:
                self._write_bts(self._bts_path)
                if Path(self._bts_path).exists():
                    paths.append(str(self._bts_path))
            except Exception as e:
                errors.append(f"BTS: {e}")
        if any(c for c in self._moving_clips):
            try:
                self._write_moving(self._mov_path)
                if Path(self._mov_path).exists():
                    paths.append(str(self._mov_path))
            except Exception as e:
                errors.append(f"Moving-pic: {e}")
        self.finished_save.emit(paths, errors)

    def _write_bts(self, mp4_path):
        src = self._bts_buffer
        n_src = len(src)
        if n_src == 0:
            return
        fps = self._video_fps
        real_duration = n_src / float(fps)
        target_duration = min(real_duration / self._bts_speed, self._bts_max_d)
        target_duration = max(target_duration, 1.0)
        n_out = max(int(round(target_duration * fps)), 1)
        if n_out == 1:
            picked_idx = [0]
        else:
            picked_idx = [int(round(i * (n_src - 1) / (n_out - 1))) for i in range(n_out)]
        h, w = src[0].shape[:2]
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(mp4_path), fourcc, fps, (w, h))
        if not writer.isOpened():
            raise RuntimeError("Cannot open VideoWriter")
        for i in picked_idx:
            writer.write(src[i])
        writer.release()

    def _write_moving(self, mp4_path):
        if not self._overlay_path:
            raise RuntimeError("No overlay path")
        overlay = Image.open(self._overlay_path).convert("RGBA")
        ow, oh = overlay.size
        rects = self._slot_rects
        if rects is None:
            rects = fallback_slot_rects(ow, oh, len(self._moving_clips))
        n_slots = len(rects)
        clips_src = []
        for slot_idx in range(n_slots):
            if self._slot_to_pose and slot_idx < len(self._slot_to_pose):
                pose_idx = self._slot_to_pose[slot_idx]
            else:
                pose_idx = slot_idx
            if 0 <= pose_idx < len(self._moving_clips) and self._moving_clips[pose_idx]:
                frames_src = self._moving_clips[pose_idx]
            elif 0 <= pose_idx < len(self._captured_frames):
                frames_src = [self._captured_frames[pose_idx]]
            else:
                frames_src = [np.zeros((rects[slot_idx][3], rects[slot_idx][2], 3), dtype=np.uint8)]
            clips_src.append(frames_src)
        clip_len = min(len(c) for c in clips_src)
        if clip_len < 1:
            clip_len = 1
        # The rolling buffer was filled over ~moving_clip_s seconds of real
        # time. To play back at REAL-TIME speed, the output fps must equal
        # (frames actually captured) / (seconds they span), NOT video_fps —
        # because the capture loop usually lands fewer frames than video_fps.
        clip_seconds = max(0.1, float(self._moving_clip_s))
        real_fps = clip_len / clip_seconds
        fps = max(1, int(round(real_fps * self._moving_speed)))
        out_w = 720
        out_h = int(round(oh * (out_w / ow)))
        if out_w % 2: out_w -= 1
        if out_h % 2: out_h -= 1
        ov = np.array(overlay)
        alpha_arr = ov[:, :, 3]
        has_transparent_slots = bool((alpha_arr < SLOT_ALPHA_MAX).any())
        if has_transparent_slots:   # clear half-opaque holes (see _build_final_canvas)
            ov[:, :, 3] = np.where(alpha_arr < SLOT_ALPHA_MAX, 0, alpha_arr)
            overlay = Image.fromarray(ov, "RGBA")
        overlay_small = overlay.resize((out_w, out_h), Image.BILINEAR)
        sx = out_w / ow
        sy = out_h / oh
        scaled_rects = []
        for (rx, ry, rw, rh) in rects:
            scaled_rects.append((
                int(round(rx * sx)),
                int(round(ry * sy)),
                max(1, int(round(rw * sx))),
                max(1, int(round(rh * sy))),
            ))
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(mp4_path), fourcc, fps, (out_w, out_h))
        if not writer.isOpened():
            raise RuntimeError("Cannot open VideoWriter")
        for t in range(clip_len):
            canvas = Image.new("RGBA", (out_w, out_h), (255, 255, 255, 255))
            if has_transparent_slots:
                for slot_idx in range(min(len(clips_src), len(scaled_rects))):
                    cell_frame_bgr = clips_src[slot_idx][t]
                    sxp, syp, swp, shp = scaled_rects[slot_idx]
                    cell_resized = crop_center_to_aspect(cell_frame_bgr, swp, shp, fit_mode=self._fit_mode)
                    cell_rgb = cv2.cvtColor(cell_resized, cv2.COLOR_BGR2RGB)
                    cell_pil = Image.fromarray(cell_rgb)
                    canvas.paste(cell_pil, (sxp, syp))
                canvas.paste(overlay_small, (0, 0), overlay_small)
            else:
                canvas.paste(overlay_small, (0, 0), overlay_small)
                for slot_idx in range(min(len(clips_src), len(scaled_rects))):
                    cell_frame_bgr = clips_src[slot_idx][t]
                    sxp, syp, swp, shp = scaled_rects[slot_idx]
                    cell_resized = crop_center_to_aspect(cell_frame_bgr, swp, shp, fit_mode=self._fit_mode)
                    cell_rgb = cv2.cvtColor(cell_resized, cv2.COLOR_BGR2RGB)
                    cell_pil = Image.fromarray(cell_rgb)
                    canvas.paste(cell_pil, (sxp, syp))
            rgb_arr = np.array(canvas.convert("RGB"))
            bgr_arr = cv2.cvtColor(rgb_arr, cv2.COLOR_RGB2BGR)
            writer.write(bgr_arr)
        writer.release()


# ================= MAIN WINDOW =================
class MainWindow(QMainWindow):
    # Worker threads post UI updates through this (queued to the UI thread).
    # QTimer.singleShot(0, lambda) from a plain Python thread never fires.
    _ui_sig = pyqtSignal(object)

    SCREEN_HOME    = 0
    SCREEN_LAYOUTS = 1
    SCREEN_CODE    = 6   # 9-digit code input (between layout pick and capture)
    SCREEN_CAPTURE = 2
    SCREEN_FRAMES  = 3
    SCREEN_PRINT   = 4
    SCREEN_DONE    = 5

    def __init__(self):
        super().__init__()
        self._ui_sig.connect(self._run_ui_fn)
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.showFullScreen()
        self.setStyleSheet(f"""
            QMainWindow {{
                background: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0 {COLORS['cream']}, stop:1 {COLORS['blush']});
            }}
        """)
        self.icons = IconLoader(self)
        self.overlay_path        = None
        self.frame_paths         = []
        self.frame_buttons       = []
        self.captured_frames     = []
        self.last_raw_frame      = None
        self.is_reviewing        = False
        self.is_reviewing_final  = False
        self.temp_canvas         = None
        self.current_step        = 1
        self.session_started     = False
        self.thumbnail_cache     = {}
        self.slot_widgets        = []
        self.layouts             = list(CONFIG.get("layouts", []))
        self.current_layout      = self._resolve_default_layout()
        self.shots_per_session   = self.current_layout.get("poses", 4)
        self.pose_aspects        = [3 / 4] * self.shots_per_session
        self.video_fps           = CONFIG["video_fps"]
        self.bts_speed           = CONFIG["bts_speed"]
        self.bts_max_duration_s  = CONFIG["bts_max_duration_s"]
        self.moving_clip_s       = CONFIG["moving_clip_s"]
        self.moving_speed        = CONFIG["moving_speed"]
        self.video_short_side    = CONFIG["video_short_side_px"]
        self.video_record_size   = (640, 480)
        self.bts_buffer          = []
        self.rolling_buffer      = []
        self.moving_clips        = [None] * self.shots_per_session
        self._last_record_ts     = 0.0
        self._pending_moving_clip = None
        self.max_retakes_per_shot = CONFIG["max_retakes_per_shot"]
        self.retakes_used         = 0
        self.extra_shots          = max(0, int(CONFIG.get("extra_shots", 0) or 0))
        self.all_shots            = []   # every shot this session (kept + bonus)
        self.all_clips            = []
        self.picked_indices       = []   # all_shots index per layout pose
        self.pick_pool            = []   # picked all_shots indices, frame order first
        self.retake_shots         = []   # (frame, clip, step) of takes replaced by a retake
        self.shot_labels          = []
        self.kept_shot_idx        = []
        self._fp_sel              = None # frame-picker slot tapped for a swap
        self._fp_thumb_cache      = {}
        self.extra_prints         = 0
        self._slot_rect_cache     = {}
        self.current_filter   = "none"
        self._filter_cache    = {}
        self._save_done   = False
        self._save_paths  = []
        self._save_errors = []
        self._final_png_path = None
        # Painted pastel backdrop; every screen is transparent on top of it.
        self._bg = PastelBackground()
        self.stack = QStackedWidget(self._bg)
        self.stack.setStyleSheet("QStackedWidget { background: transparent; }")
        _bg_l = QVBoxLayout(self._bg)
        _bg_l.setContentsMargins(0, 0, 0, 0)
        _bg_l.addWidget(self.stack)
        self.setCentralWidget(self._bg)
        self._top_bar_widget = None
        self._build_homepage()
        self._build_layout_picker()
        self._build_capture_screen()
        self._build_frame_picker()
        self._build_print_screen()
        self._build_done_screen()
        self._build_code_screen()
        self._build_pick_screen()
        self.stack.setCurrentIndex(self.SCREEN_HOME)
        self._load_frames()
        self.canon_mode = False
        self._canon_waiting = False
        self._cam_warming = False
        self._cam_frame_seen = False
        self._warm_token = 0
        self.fit_mode = str((CONFIG.get("camera", {}) or {}).get("fit_mode", "cover")).lower()
        if self.fit_mode not in ("cover", "contain"):
            self.fit_mode = "cover"
        # The camera is opened per session (_start_session) and released when
        # the session goes to print, so every session gets a fresh EDSDK/OpenCV
        # connection instead of one long-lived handle that goes stale.
        self.camera = None
        QTimer.singleShot(0, self._fit_capture_layout)

    # ================= CAMERA LIFECYCLE (one connection per session) =================
    def _create_camera(self):
        """Open a fresh camera thread (Canon via EDSDK, or OpenCV webcam)."""
        cam_cfg = CONFIG.get("camera", {}) or {}
        cam_index = cam_cfg.get("index", "auto")
        cam_source = str(cam_cfg.get("source", "opencv")).lower()
        LOG.info(f"[CAMERA] config camera.source={cam_source!r} index={cam_index!r}")
        self.canon_mode = False
        self._canon_max_long = cam_cfg.get("canon_max_long_side", 3000)
        if cam_source == "canon":
            try:
                from canon_edsdk import CanonCameraThread
                self.camera = CanonCameraThread(
                    dll_dir=(cam_cfg.get("edsdk_dll_dir") or None),
                    use_af=bool(cam_cfg.get("canon_use_af", True)),
                    fail_timeout_s=float(cam_cfg.get("canon_fail_timeout_s", 30)))
                self.canon_mode = True
                LOG.info("[CAMERA] source=canon (Canon DSLR via EDSDK)")
            except Exception as e:
                LOG.error(f"[CAMERA] Canon EDSDK init FAILED: {e!r}")
                LOG.error("[CAMERA] canon traceback:" + chr(10) + traceback.format_exc())
                LOG.error("[CAMERA] falling back to OpenCV webcam")
                self.camera = CameraThread(cam_index)
        else:
            self.camera = CameraThread(cam_index)
        try:
            self.camera.backend_pref = cam_cfg.get("backend", "any")
            w_cfg = cam_cfg.get("width", "auto")
            h_cfg = cam_cfg.get("height", "auto")
            self.camera.req_width  = w_cfg if isinstance(w_cfg, str) else int(w_cfg)
            self.camera.req_height = h_cfg if isinstance(h_cfg, str) else int(h_cfg)
            fit_mode = str(cam_cfg.get("fit_mode", "cover")).lower()
            if fit_mode not in ("cover", "contain"):
                fit_mode = "cover"
            self.camera.fit_mode = fit_mode
            self.fit_mode = fit_mode
        except Exception:
            self.fit_mode = "cover"
        self.camera.frame_ready.connect(self._update_preview)
        if self.canon_mode:
            self.camera.still_ready.connect(self._on_canon_still)
            self.camera.capture_failed.connect(self._on_canon_capture_failed)
            self.camera.camera_lost.connect(self._on_canon_lost)
        self._cam_frame_seen = False
        self.camera.start()
        LOG.info("[CAMERA] opened for new session")

    def _release_camera(self):
        """Stop the camera thread so it closes its session (EDSDK
        CloseSession + TerminateSDK / cv2 release). Non-blocking: the thread
        finishes in the background; _create_camera waits for it if needed."""
        cam = self.camera
        if cam is None:
            return
        self.camera = None
        self._warm_token += 1
        self._cam_warming = False
        for sig in ("frame_ready", "still_ready", "capture_failed", "camera_lost"):
            try:
                getattr(cam, sig).disconnect()
            except Exception:
                pass
        cam.running = False
        self._old_cameras = [c for c in getattr(self, "_old_cameras", []) if c.isRunning()]
        self._old_cameras.append(cam)
        LOG.info("[CAMERA] released (session ended)")

    def _wait_old_cameras(self, timeout_ms=15000):
        for c in getattr(self, "_old_cameras", []):
            if c.isRunning() and not c.wait(timeout_ms):
                LOG.warning("[CAMERA] previous camera thread still running")
        # Keep refs to any still-running thread: destroying a running QThread crashes.
        self._old_cameras = [c for c in getattr(self, "_old_cameras", []) if c.isRunning()]

    def _ui(self, fn):
        """Run fn on the UI thread; safe to call from worker threads."""
        self._ui_sig.emit(fn)

    def _run_ui_fn(self, fn):
        try:
            fn()
        except Exception as e:
            LOG.warning(f"[UI] deferred call failed: {e}")

    def _build_homepage(self):
        screen = QWidget()
        screen.setStyleSheet("background: transparent;")
        root = QVBoxLayout(screen)
        root.setContentsMargins(80, 40, 80, 40)
        root.setSpacing(0)
        root.addStretch(1)
        eyebrow = QLabel(f"{CONFIG['ui']['studio_name']}")
        eyebrow.setStyleSheet(f"""
            color: {COLORS['pink_dk']}; background: white;
            border: 2px solid {COLORS['pink_soft']}; border-radius: 22px;
            font-size: 16px; font-weight: 800; letter-spacing: 6px;
            padding: 10px 28px;
        """)
        root.addWidget(eyebrow, 0, Qt.AlignHCenter)
        root.addSpacing(26)
        root.addWidget(PolaroidCluster(), 0, Qt.AlignHCenter)
        root.addSpacing(14)
        title = QLabel(CONFIG["ui"]["app_title"])
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"""
            color: {COLORS['ink']}; font-size: 104px; font-weight: 800;
            font-family: '{FONT_DISPLAY}'; background: transparent;
        """)
        root.addWidget(title)
        root.addSpacing(6)
        tagline = QLabel(CONFIG["ui"]["tagline"])
        tagline.setAlignment(Qt.AlignCenter)
        tagline.setStyleSheet(f"""
            color: {COLORS['ink_soft']}; font-size: 26px;
            font-weight: 500; background: transparent;
        """)
        root.addWidget(tagline)
        root.addSpacing(40)
        self.btn_start = QPushButton(f"{CONFIG['ui']['start_button']}")
        self.btn_start.setFixedHeight(104)
        self.btn_start.setMinimumWidth(440)
        self.btn_start.setCursor(Qt.PointingHandCursor)
        self.btn_start.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {COLORS['pink']}, stop:1 {COLORS['lilac']});
                color: white; border: 4px solid white;
                font-size: 32px; font-weight: 800;
                border-radius: 52px; padding: 0 56px;
            }}
            QPushButton:hover {{ background: {COLORS['pink_dk']}; }}
        """)
        self.btn_start.clicked.connect(self._goto_layout_picker)
        self._add_shadow(self.btn_start, blur=40, y_offset=14, alpha=160)
        root.addWidget(self.btn_start, 0, Qt.AlignHCenter)
        root.addSpacing(20)
        extra = max(0, int(CONFIG.get("extra_shots", 0) or 0))
        hint_txt = "tap tombol di atas untuk mulai"
        if extra > 0:
            hint_txt += f"   \u00b7   +{extra} foto bonus, pilih favoritmu"
        hint = QLabel(hint_txt)
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet(f"color: {COLORS['ink_soft']}; font-size: 18px; "
                           f"font-weight: 600; background: transparent;")
        root.addWidget(hint)
        root.addStretch(1)
        self.stack.addWidget(screen)

    @staticmethod
    def _set_label_pixmap(label, pix):
        label.setPixmap(pix)

    @staticmethod
    def _add_shadow(widget, blur=24, x_offset=0, y_offset=6, alpha=120):
        eff = QGraphicsDropShadowEffect()
        eff.setBlurRadius(blur)
        eff.setOffset(x_offset, y_offset)
        eff.setColor(QColor(190, 70, 120, max(20, int(alpha * 0.45))))
        widget.setGraphicsEffect(eff)

    def _resolve_default_layout(self):
        layouts = list(CONFIG.get("layouts", []))
        default_id = CONFIG.get("default_layout_id")
        if default_id:
            for L in layouts:
                if L.get("id") == default_id:
                    return L
        if layouts:
            return layouts[0]
        return {"id": "grid4", "name": "4 Foto", "poses": 4, "frame_dir": "frames"}

    def _build_layout_picker(self):
        screen = QWidget()
        screen.setStyleSheet("background: transparent;")
        self.layout_screen = screen
        eyebrow = QLabel("LANGKAH 1 DARI 2", screen)
        eyebrow.setAlignment(Qt.AlignCenter)
        eyebrow.setStyleSheet(f"""
            color: {COLORS['yellow']}; font-size: 16px; font-weight: 700;
            letter-spacing: 10px; background: transparent;
        """)
        self._lp_eyebrow = eyebrow
        title = QLabel("Pilih Layout", screen)
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"""
            color: {COLORS['ink']}; font-size: 56px; font-weight: 800;
            font-family: '{FONT_DISPLAY}'; background: transparent;
        """)
        self._lp_title = title
        sub = QLabel("Pilih ukuran kertas & jumlah foto sesuai sesi kamu", screen)
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet(f"""
            color: {COLORS['ink_soft']}; font-size: 18px; font-weight: 500;
            background: transparent;
        """)
        self._lp_sub = sub
        self.lp_preview_container = QWidget(screen)
        self.lp_preview_container.setStyleSheet("background: transparent;")
        self.lp_preview = QLabel("", self.lp_preview_container)
        self.lp_preview.setAlignment(Qt.AlignCenter)
        self.lp_preview.setStyleSheet(f"background: transparent; border: none; color: {COLORS['ink_soft']}; font-size: 18px;")
        self._add_shadow(self.lp_preview, blur=40, y_offset=12, alpha=140)
        self.lp_caption = QLabel("", screen)
        self.lp_caption.setAlignment(Qt.AlignCenter)
        self.lp_caption.setStyleSheet(f"""
            color: {COLORS['ink']}; font-size: 22px; font-weight: 800;
            background: transparent;
        """)
        self.lp_caption_meta = QLabel("", screen)
        self.lp_caption_meta.setAlignment(Qt.AlignCenter)
        self.lp_caption_meta.setStyleSheet(f"""
            color: {COLORS['yellow']}; font-size: 14px; font-weight: 600;
            letter-spacing: 2px; background: transparent;
        """)
        self.layout_strip_scroll = QScrollArea(screen)
        self.layout_strip_scroll.setWidgetResizable(True)
        self.layout_strip_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.layout_strip_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.layout_strip_scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QScrollBar:horizontal { height: 10px; background: #FFE1EC; border-radius: 5px; }"
            "QScrollBar::handle:horizontal { background: #FF9CC0; border-radius: 5px; min-width: 50px; }"
            "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }"
        )
        strip_host = QWidget()
        strip_host.setStyleSheet("background: transparent;")
        self.layout_strip = QHBoxLayout(strip_host)
        self.layout_strip.setContentsMargins(20, 8, 20, 8)
        self.layout_strip.setSpacing(16)
        self.layout_strip.addStretch()
        self.layout_buttons = []
        self._lp_selected_layout = None
        for layout_def in self.layouts:
            card = self._make_layout_card(layout_def)
            self.layout_strip.addWidget(card)
            self.layout_buttons.append((card, layout_def))
        self.layout_strip.addStretch()
        self.layout_strip_scroll.setWidget(strip_host)
        self.btn_layout_back = QPushButton("← KEMBALI", screen)
        self.btn_layout_back.setFixedHeight(64)
        self.btn_layout_back.setMinimumWidth(180)
        self.btn_layout_back.setCursor(Qt.PointingHandCursor)
        self.btn_layout_back.setStyleSheet(f"""
            QPushButton {{
                background: white; color: {COLORS['ink']};
                border: 2px solid {COLORS['pink_soft']};
                border-radius: 32px; font-size: 18px; font-weight: 800;
                padding: 0 24px;
            }}
            QPushButton:hover {{ background: {COLORS['pink_soft']}; }}
        """)
        self.btn_layout_back.clicked.connect(
            lambda: self.stack.setCurrentIndex(self.SCREEN_HOME))
        self._add_shadow(self.btn_layout_back, blur=18, y_offset=4, alpha=110)
        self.btn_layout_next = QPushButton("LANJUT →", screen)
        self.btn_layout_next.setFixedHeight(64)
        self.btn_layout_next.setMinimumWidth(180)
        self.btn_layout_next.setCursor(Qt.PointingHandCursor)
        self.btn_layout_next.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {COLORS['pink']}, stop:1 {COLORS['lilac']}); color: white;
                border-radius: 32px; font-size: 18px; font-weight: 900;
                padding: 0 24px;
            }}
            QPushButton:hover {{ background: {COLORS['pink_dk']}; }}
        """)
        self.btn_layout_next.clicked.connect(self._confirm_layout_selection)
        self._add_shadow(self.btn_layout_next, blur=18, y_offset=4, alpha=110)
        self.stack.addWidget(screen)

    def _make_layout_card(self, layout_def):
        card = QPushButton()
        card.setCursor(Qt.PointingHandCursor)
        card.setFixedSize(QSize(170, 220))
        card.setStyleSheet(self._layout_card_style(selected=False))
        card.clicked.connect(partial(self._on_layout_card_clicked, layout_def, card))
        wrap = QVBoxLayout(card)
        wrap.setContentsMargins(10, 10, 10, 10)
        wrap.setSpacing(8)
        thumb = QLabel()
        thumb.setAlignment(Qt.AlignCenter)
        thumb.setFixedHeight(140)
        thumb.setStyleSheet("background: transparent; border: none;")
        frame_dir = BASE_DIR / layout_def.get("frame_dir", "frames")
        thumbnails = sorted(frame_dir.glob("*.png")) if frame_dir.exists() else []
        if thumbnails:
            pix = QPixmap(str(thumbnails[0])).scaled(150, 140, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            thumb.setPixmap(pix)
        else:
            thumb.setText("(no frame)")
            thumb.setStyleSheet("""
                color: #7A6488; background: #FFF0F5;
                border: 2px dashed #FFC2D8; border-radius: 10px; font-size: 12px;
            """)
        wrap.addWidget(thumb)
        name = QLabel(layout_def.get("name", layout_def.get("id", "Layout")))
        name.setAlignment(Qt.AlignCenter)
        name.setStyleSheet(f"color: {COLORS['ink']}; font-size: 12px; font-weight: 800; background: transparent;")
        name.setWordWrap(True)
        wrap.addWidget(name)
        check = QLabel("✓", card)
        check.setAlignment(Qt.AlignCenter)
        check.setFixedSize(34, 34)
        check.setStyleSheet(f"""
            background: {COLORS['success']}; color: white; font-size: 20px;
            font-weight: 900; border-radius: 17px; border: 2px solid white;
        """)
        check.move(card.width() - 40, 6)
        check.hide()
        card.check_label = check
        return card

    @staticmethod
    def _layout_card_style(selected):
        if selected:
            return f"""
                QPushButton {{
                    background: white; border: 3px solid {COLORS['yellow']};
                    border-radius: 14px; padding: 0;
                }}
            """
        return """
            QPushButton {
                background: rgba(255,255,255,0.85); border: 3px solid transparent;
                border-radius: 18px; padding: 0;
            }
            QPushButton:hover { border-color: #FFC2D8; }
        """

    def _on_layout_card_clicked(self, layout_def, button):
        self._lp_selected_layout = layout_def
        for card, ldef in self.layout_buttons:
            sel = (ldef is layout_def)
            card.setStyleSheet(self._layout_card_style(selected=sel))
            check = getattr(card, "check_label", None)
            if check is not None:
                if sel:
                    check.move(card.width() - check.width() - 6, 6)
                    check.show()
                    check.raise_()
                else:
                    check.hide()
        self._render_lp_preview(layout_def)

    def _render_lp_preview(self, layout_def):
        frame_dir = BASE_DIR / layout_def.get("frame_dir", "frames")
        thumbs = sorted(frame_dir.glob("*.png")) if frame_dir.exists() else []
        cw = self.lp_preview_container.width()
        ch = self.lp_preview_container.height()
        if cw < 10 or ch < 10:
            return
        if thumbs:
            pix = QPixmap(str(thumbs[0]))
            if not pix.isNull():
                scaled = pix.scaled(cw, ch, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.lp_preview.setGeometry(
                    (cw - scaled.width()) // 2, (ch - scaled.height()) // 2,
                    scaled.width(), scaled.height())
                self.lp_preview.setPixmap(scaled)
            else:
                self.lp_preview.setGeometry(0, 0, cw, ch)
                self.lp_preview.setPixmap(QPixmap())
        else:
            self.lp_preview.setGeometry(0, 0, cw, ch)
            self.lp_preview.setText("(tidak ada contoh frame)")
        self.lp_caption.setText(layout_def.get("name", layout_def.get("id", "Layout")))
        meta_parts = []
        poses = layout_def.get("poses")
        if poses is not None:
            meta_parts.append(f"{poses} FOTO")
        if layout_def.get("paper"):
            meta_parts.append(layout_def["paper"].upper())
        self.lp_caption_meta.setText("  ·  ".join(meta_parts))

    def _confirm_layout_selection(self):
        chosen = self._lp_selected_layout
        if chosen is None:
            chosen = self.layouts[0] if self.layouts else self.current_layout
        self._pick_layout(chosen)

    def _fit_lp_layout(self):
        if not hasattr(self, "layout_screen") or self.layout_screen is None:
            return
        sw = self.layout_screen.width()
        sh = self.layout_screen.height()
        if sw < 10 or sh < 10:
            return
        if sh < 900:
            self._lp_eyebrow.setGeometry(0, 16, sw, 18)
            self._lp_title.setGeometry(0, 36, sw, 52)
            self._lp_title.setStyleSheet(f"color: {COLORS['ink']}; font-size: 38px; font-weight: 800; font-family: '{FONT_DISPLAY}'; background: transparent;")
            self._lp_sub.setGeometry(0, 96, sw, 22)
            self._lp_sub.setStyleSheet(f"color: {COLORS['ink_soft']}; font-size: 14px; font-weight: 500; background: transparent;")
        else:
            self._lp_eyebrow.setGeometry(0, 36, sw, 24)
            self._lp_title.setGeometry(0, 70, sw, 72)
            self._lp_sub.setGeometry(0, 152, sw, 28)
        short_screen = sh < 900
        nav_h = 56 if short_screen else 64
        bottom_pad = 16 if short_screen else 24
        strip_h = 180 if short_screen else 220
        nav_strip_gap = 8 if short_screen else 12
        cap_h_main = 26 if short_screen else 30
        cap_h_meta = 18 if short_screen else 20
        cap_gap = 2 if short_screen else 4
        cap_strip_gap = 8 if short_screen else 14
        nav_y = sh - nav_h - bottom_pad
        strip_y = nav_y - strip_h - nav_strip_gap
        cap_meta_y = strip_y - cap_h_meta - cap_strip_gap
        cap_main_y = cap_meta_y - cap_h_main - cap_gap
        self.btn_layout_back.setFixedHeight(nav_h)
        self.btn_layout_next.setFixedHeight(nav_h)
        self.btn_layout_back.move(sw // 2 - self.btn_layout_back.width() - 16,
            nav_y + (nav_h - self.btn_layout_back.height()) // 2)
        self.btn_layout_next.move(sw // 2 + 16,
            nav_y + (nav_h - self.btn_layout_next.height()) // 2)
        self.btn_layout_back.raise_()
        self.btn_layout_next.raise_()
        self.layout_strip_scroll.setGeometry(40, strip_y, sw - 80, strip_h)
        target_card_h = strip_h - 22
        for card, _ldef in self.layout_buttons:
            if card.height() != target_card_h:
                card.setFixedSize(QSize(140 if short_screen else 170, target_card_h))
                if hasattr(card, "check_label"):
                    card.check_label.move(card.width() - 40, 6)
        self.lp_caption.setGeometry(0, cap_main_y, sw, cap_h_main)
        self.lp_caption_meta.setGeometry(0, cap_meta_y, sw, cap_h_meta)
        top_y = 170 if short_screen else 200
        avail_h = cap_main_y - top_y - 16
        avail_w = int(sw * 0.6)
        pc_x = (sw - avail_w) // 2
        pc_y = top_y
        pc_w = avail_w
        pc_h = max(60, avail_h)
        self.lp_preview_container.setGeometry(pc_x, pc_y, pc_w, pc_h)
        chosen = self._lp_selected_layout
        if chosen is None and self.layouts:
            chosen = self.current_layout if self.current_layout in self.layouts else self.layouts[0]
            self._on_layout_card_clicked(chosen, self.layout_buttons[
                next((i for i, (_, l) in enumerate(self.layout_buttons) if l is chosen), 0)
            ][0])
        elif chosen is not None:
            self._render_lp_preview(chosen)

    def _goto_layout_picker(self):
        if not self.layouts:
            self._pick_layout(self.current_layout)
            return
        if len(self.layouts) == 1:
            self._pick_layout(self.layouts[0])
            return
        self.stack.setCurrentIndex(self.SCREEN_LAYOUTS)
        QTimer.singleShot(0, self._fit_lp_layout)

    def _pick_layout(self, layout_def):
        self.current_layout    = layout_def
        self.shots_per_session = max(1, int(layout_def.get("poses", 4)))
        global FRAME_DIR
        new_frame_dir = BASE_DIR / layout_def.get("frame_dir", "frames")
        if new_frame_dir.exists():
            FRAME_DIR = new_frame_dir
        self._load_frames()
        self._detect_layout_slot_aspects()
        # Free mode → skip code. Paid mode → require 9-digit code.
        ac = CONFIG.get("access_code", {}) or {}
        mode = str(ac.get("mode", "free")).lower()
        if mode == "paid":
            self._goto_code_input()
        else:
            self._session_access_code = None
            self._start_session()

    def _detect_layout_slot_aspects(self):
        N = self.shots_per_session
        default_aspect = 3 / 4
        self.pose_aspects = [default_aspect] * N
        if not self.frame_paths:
            return
        first_frame = self.frame_paths[0]
        try:
            overlay = Image.open(first_frame).convert("RGBA")
        except Exception:
            return
        slot_to_pose = self._slot_to_pose_mapping()
        slot_count = len(slot_to_pose) if slot_to_pose else N
        rects = self._detect_slot_rects(overlay, target_count=slot_count, path=first_frame)
        if rects is None:
            return
        for pose_idx in range(N):
            slot_idx_for_pose = pose_idx
            if slot_to_pose:
                matches = [si for si, pi in enumerate(slot_to_pose) if pi == pose_idx]
                if matches:
                    slot_idx_for_pose = matches[0]
            if 0 <= slot_idx_for_pose < len(rects):
                _, _, sw, sh = rects[slot_idx_for_pose]
                if sw > 0 and sh > 0:
                    self.pose_aspects[pose_idx] = sw / sh

    def _build_capture_screen(self):
        screen = QWidget()
        screen.setStyleSheet("background: transparent;")
        self.capture_screen = screen
        top_bar = QWidget(screen)
        top_bar.setStyleSheet("background: transparent;")
        self._top_bar_widget = top_bar
        tb_l = QHBoxLayout(top_bar)
        tb_l.setContentsMargins(40, 20, 40, 0)
        tb_l.setSpacing(0)
        rec_pill = QWidget()
        rec_pill.setStyleSheet("background: transparent;")
        rec_l = QHBoxLayout(rec_pill)
        rec_l.setContentsMargins(0, 0, 0, 0)
        rec_l.setSpacing(8)
        self.rec_dot = QLabel()
        self.rec_dot.setFixedSize(20, 20)
        self.rec_dot.setStyleSheet(f"background: {COLORS['danger']}; border-radius: 10px;")
        rec_lbl = QLabel("REC")
        rec_lbl.setStyleSheet(f"color: {COLORS['ink']}; font-size: 22px; font-weight: 800; background: transparent;")
        rec_l.addWidget(self.rec_dot)
        rec_l.addWidget(rec_lbl)
        tb_l.addWidget(rec_pill, 0, Qt.AlignLeft)
        tb_l.addStretch()
        self.flash_icon = QLabel()
        self.flash_icon.setFixedSize(48, 48)
        self.flash_icon.setStyleSheet("background: white; border-radius: 24px;")
        self.flash_icon.hide()
        tb_l.addWidget(self.flash_icon, 0, Qt.AlignRight)
        self._rec_blink_timer = QTimer(self)
        self._rec_blink_timer.setInterval(600)
        self._rec_blink_timer.timeout.connect(self._toggle_rec_blink)
        self._rec_visible = True
        self.preview_container = QWidget(screen)
        self.preview_container.setStyleSheet("background: transparent;")
        self.preview_label = QLabel("", self.preview_container)
        self.preview_label.setAlignment(Qt.AlignCenter)
        self._preview_style_live  = "background: #241A2C; border: 10px solid white; border-radius: 32px;"
        self._preview_style_final = "background: transparent; border: none; border-radius: 0px;"
        self.preview_label.setStyleSheet(self._preview_style_live)
        self._add_shadow(self.preview_label, blur=40, y_offset=12, alpha=140)
        self.countdown_label = QLabel(self.preview_container)
        self.countdown_label.setAlignment(Qt.AlignCenter)
        self.countdown_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.countdown_label.setStyleSheet("color: white; background: transparent; font-weight: 900;")
        self.countdown_label.hide()
        self._add_shadow(self.countdown_label, blur=40, y_offset=4, alpha=180)
        self.ready_banner = QLabel("Siap-siap ya!", self.preview_container)
        self.ready_banner.setAlignment(Qt.AlignCenter)
        self.ready_banner.setStyleSheet(f"""
            background: white; color: {COLORS['ink']}; border: 3px solid {COLORS['pink_soft']};
            font-size: 22px; font-weight: 800; border-radius: 22px; padding: 8px 24px;
        """)
        self.ready_banner.hide()
        self._add_shadow(self.ready_banner, blur=20, y_offset=4, alpha=120)
        self.slot_widgets = []
        for i in range(MAX_SLOTS):
            slot = QLabel(screen)
            slot.setStyleSheet("""
                background: rgba(255, 255, 255, 0.85);
                border: 3px dashed #FFB3CD;
                border-radius: 14px;
            """)
            slot.setAlignment(Qt.AlignCenter)
            ph = QLabel(f"{i+1}", slot)
            ph.setAlignment(Qt.AlignCenter)
            ph.setStyleSheet(f"""
                color: {COLORS['pink']};
                font-size: 30px; font-weight: 800;
                font-family: '{FONT_DISPLAY}'; background: transparent;
            """)
            slot.placeholder_text = ph
            slot.image_label = None
            self._add_shadow(slot, blur=18, y_offset=4, alpha=80)
            self.slot_widgets.append(slot)
        self.progress_pill = QWidget(screen)
        self.progress_pill.setStyleSheet("background: white; border-radius: 30px;")
        self._add_shadow(self.progress_pill, blur=24, y_offset=6, alpha=130)
        pp_l = QHBoxLayout(self.progress_pill)
        pp_l.setContentsMargins(28, 14, 28, 14)
        pp_l.setSpacing(14)
        self.progress_text = QLabel("Foto 1 / 4")
        self.progress_text.setStyleSheet(f"color: {COLORS['ink']}; font-size: 20px; font-weight: 800; background: transparent;")
        pp_l.addWidget(self.progress_text)
        self.progress_dots = []
        for _ in range(MAX_SLOTS):
            dot = QLabel()
            dot.setFixedSize(20, 20)
            dot.setStyleSheet(f"background: {COLORS['border']}; border-radius: 10px;")
            self.progress_dots.append(dot)
            pp_l.addWidget(dot)
        self.review_widget = QWidget(screen)
        self.review_widget.setStyleSheet("background: transparent;")
        rv_outer = QVBoxLayout(self.review_widget)
        rv_outer.setContentsMargins(0, 0, 0, 0)
        rv_outer.setSpacing(10)
        rv_btn_row = QWidget(self.review_widget)
        rv_btn_row.setStyleSheet("background: transparent;")
        rv_l = QHBoxLayout(rv_btn_row)
        rv_l.setContentsMargins(0, 0, 0, 0)
        rv_l.setSpacing(24)
        rv_l.addStretch()
        self.btn_retake = QPushButton("Retry (2)")
        self.btn_next   = QPushButton("Next")
        for btn in (self.btn_retake, self.btn_next):
            btn.setFixedSize(220, 80)
            btn.setCursor(Qt.PointingHandCursor)
        self._style_retake_button(enabled=True, remaining=2)
        self.btn_next.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {COLORS['pink']}, stop:1 {COLORS['lilac']});
                color: white; border: 3px solid white;
                border-radius: 28px; font-size: 24px; font-weight: 800;
            }}
            QPushButton:hover {{ background: {COLORS['pink_dk']}; }}
        """)
        self.btn_retake.clicked.connect(self._retake_photo)
        self.btn_next.clicked.connect(self._next_photo)
        self._add_shadow(self.btn_retake, blur=20, y_offset=4, alpha=110)
        self._add_shadow(self.btn_next, blur=20, y_offset=4, alpha=110)
        rv_l.addWidget(self.btn_retake)
        rv_l.addWidget(self.btn_next)
        rv_l.addStretch()
        rv_outer.addWidget(rv_btn_row)
        self.review_widget.hide()
        self.stack.addWidget(screen)

    def _toggle_rec_blink(self):
        self._rec_visible = not self._rec_visible
        bg = COLORS['danger'] if self._rec_visible else "transparent"
        self.rec_dot.setStyleSheet(f"background: {bg}; border-radius: 10px;")

    def _style_retake_button(self, enabled, remaining):
        if enabled:
            self.btn_retake.setEnabled(True)
            self.btn_retake.setText(f"Retry ({remaining})")
            self.btn_retake.setStyleSheet(f"""
                QPushButton {{
                    background: {COLORS['lilac']}; color: white; border: 3px solid white;
                    border-radius: 28px; font-size: 24px; font-weight: 800;
                }}
                QPushButton:hover {{ background: {COLORS['lilac_dk']}; }}
            """)
        else:
            self.btn_retake.setEnabled(False)
            self.btn_retake.setText("Retry (0)")
            self.btn_retake.setStyleSheet("""
                QPushButton {
                    background: #EFE3EA; color: #C4B2C0;
                    border-radius: 24px; font-size: 24px; font-weight: 800;
                }
            """)

    def _build_frame_picker(self):
        screen = QWidget()
        screen.setStyleSheet("background: transparent;")
        self.frames_screen = screen
        title = QLabel("Pilih Frame", screen)
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"color: {COLORS['ink']}; font-size: 48px; font-weight: 800; font-family: '{FONT_DISPLAY}'; background: transparent;")
        self._fp_title = title
        sub = QLabel("Pilih desain yang paling cocok untuk momen kamu", screen)
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet(f"color: {COLORS['ink_soft']}; font-size: 20px; font-weight: 500; background: transparent;")
        self._fp_sub = sub
        # Left column: every picked photo, frame slots first then spares.
        # Tap one then another to swap them (re-orders the frame).
        self.fp_slot_hint = QLabel("Ketuk 2 foto untuk tukar posisi", screen)
        self.fp_slot_hint.setAlignment(Qt.AlignCenter)
        self.fp_slot_hint.setStyleSheet(
            f"color: {COLORS['ink_soft']}; font-size: 15px; font-weight: 700; background: transparent;")
        self.fp_slot_scroll = QScrollArea(screen)
        self.fp_slot_scroll.setWidgetResizable(False)
        self.fp_slot_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.fp_slot_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.fp_slot_scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
            "QScrollBar:vertical { width: 8px; background: #FFE1EC; border-radius: 4px; }"
            "QScrollBar::handle:vertical { background: #FF9CC0; border-radius: 4px; min-height: 30px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        )
        self.fp_slot_host = QWidget()
        self.fp_slot_host.setStyleSheet("background: transparent;")
        self.fp_slot_scroll.setWidget(self.fp_slot_host)
        QScroller.grabGesture(self.fp_slot_scroll.viewport(), QScroller.LeftMouseButtonGesture)
        self.fp_slot_widgets = []
        for i in range(MAX_SLOTS):
            slot = QPushButton(self.fp_slot_host)
            slot.setCursor(Qt.PointingHandCursor)
            slot.setStyleSheet(self._fp_slot_style("frame"))
            slot.clicked.connect(partial(self._on_fp_slot_clicked, i))
            ph = QLabel(f"{i+1}", slot)
            ph.setAlignment(Qt.AlignCenter)
            ph.setAttribute(Qt.WA_TransparentForMouseEvents)
            ph.setStyleSheet(f"color: {COLORS['pink']}; font-size: 26px; font-weight: 800; font-family: '{FONT_DISPLAY}'; background: transparent; border: none;")
            slot.placeholder_text = ph
            slot.image_label = None
            badge = QLabel("", slot)
            badge.setAlignment(Qt.AlignCenter)
            badge.setAttribute(Qt.WA_TransparentForMouseEvents)
            slot.badge = badge
            ring = QLabel(slot)
            ring.setAttribute(Qt.WA_TransparentForMouseEvents)
            ring.setStyleSheet(f"background: transparent; border: 6px solid {COLORS['yellow']}; "
                               f"border-radius: 14px;")
            ring.hide()
            slot.sel_ring = ring
            self._add_shadow(slot, blur=14, y_offset=3, alpha=80)
            slot.hide()
            self.fp_slot_widgets.append(slot)
        self.fp_preview_container = QWidget(screen)
        self.fp_preview_container.setStyleSheet("background: transparent;")
        self.fp_preview = QLabel("", self.fp_preview_container)
        self.fp_preview.setAlignment(Qt.AlignCenter)
        self.fp_preview.setStyleSheet("background: transparent; border: none;")
        self._add_shadow(self.fp_preview, blur=40, y_offset=12, alpha=140)
        self.frame_strip_scroll = QScrollArea(screen)
        self.frame_strip_scroll.setWidgetResizable(True)
        self.frame_strip_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.frame_strip_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.frame_strip_scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QScrollBar:horizontal { height: 10px; background: #FFE1EC; border-radius: 5px; }"
            "QScrollBar::handle:horizontal { background: #FF9CC0; border-radius: 5px; min-width: 50px; }"
            "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }"
        )
        self.frame_strip_host = QWidget()
        self.frame_strip_host.setStyleSheet("background: transparent;")
        self.frame_strip = QHBoxLayout(self.frame_strip_host)
        self.frame_strip.setContentsMargins(8, 8, 8, 8)
        self.frame_strip.setSpacing(14)
        self.frame_strip.addStretch()
        self.frame_strip_scroll.setWidget(self.frame_strip_host)
        self.btn_back_picker = QPushButton("← KEMBALI", screen)
        self.btn_back_picker.setFixedHeight(64)
        self.btn_back_picker.setMinimumWidth(180)
        self.btn_back_picker.setCursor(Qt.PointingHandCursor)
        self.btn_back_picker.setStyleSheet(f"""
            QPushButton {{
                background: white; color: {COLORS['ink']};
                border: 2px solid {COLORS['pink_soft']};
                border-radius: 32px; font-size: 18px; font-weight: 800;
                padding: 0 24px;
            }}
            QPushButton:hover {{ background: {COLORS['pink_soft']}; }}
        """)
        self.btn_back_picker.clicked.connect(self._reset_to_home)
        self._add_shadow(self.btn_back_picker, blur=18, y_offset=4, alpha=110)
        self.btn_continue = QPushButton("LANJUT →", screen)
        self.btn_continue.setFixedHeight(64)
        self.btn_continue.setMinimumWidth(180)
        self.btn_continue.setCursor(Qt.PointingHandCursor)
        self.btn_continue.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {COLORS['pink']}, stop:1 {COLORS['lilac']}); color: white;
                border-radius: 32px; font-size: 18px; font-weight: 900;
                padding: 0 24px;
            }}
            QPushButton:hover {{ background: {COLORS['pink_dk']}; }}
        """)
        self.btn_continue.clicked.connect(self._goto_print)
        self._add_shadow(self.btn_continue, blur=18, y_offset=4, alpha=110)
        self.filter_panel_scroll = QScrollArea(screen)
        self.filter_panel_scroll.setWidgetResizable(True)
        self.filter_panel_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.filter_panel_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.filter_panel_scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QScrollBar:vertical { width: 8px; background: #FFE1EC; border-radius: 4px; }"
            "QScrollBar::handle:vertical { background: #FF9CC0; border-radius: 4px; min-height: 30px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        )
        self.filter_panel_host = QWidget()
        self.filter_panel_host.setStyleSheet("background: transparent;")
        self.filter_panel_layout = QVBoxLayout(self.filter_panel_host)
        self.filter_panel_layout.setContentsMargins(8, 8, 8, 8)
        self.filter_panel_layout.setSpacing(10)
        self.filter_panel_title = QLabel("FILTER")
        self.filter_panel_title.setStyleSheet("color: #7A6488; font-size: 13px; font-weight: 800; letter-spacing: 3px; background: transparent; padding: 4px 4px 0 4px;")
        self.filter_panel_layout.addWidget(self.filter_panel_title)
        self.filter_cards = {}
        for fid in FILTER_IDS:
            label = FILTER_LABELS.get(fid, fid)
            btn = QPushButton(label.replace("&", "&&"))  # '&' = mnemonic in Qt
            btn.setFixedHeight(54)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setCheckable(True)
            self._style_filter_card(btn, selected=(fid == "none"))
            btn.clicked.connect(lambda _=False, f=fid: self._set_filter(f))
            self.filter_panel_layout.addWidget(btn)
            self.filter_cards[fid] = btn
        self.filter_panel_layout.addStretch(1)
        self.filter_panel_scroll.setWidget(self.filter_panel_host)
        # ---- Extra print stepper (free, 0..printing.max_extra_prints) ----
        self.extra_card = QWidget(screen)
        self.extra_card.setObjectName("extraCard")
        self.extra_card.setAttribute(Qt.WA_StyledBackground, True)
        self.extra_card.setStyleSheet(f"""
            #extraCard {{ background: white; border: 2px solid {COLORS['pink_soft']};
                          border-radius: 22px; }}
        """)
        self._add_shadow(self.extra_card, blur=22, y_offset=6, alpha=90)
        ec = QVBoxLayout(self.extra_card)
        ec.setContentsMargins(16, 12, 16, 12)
        ec.setSpacing(6)
        ec_title = QLabel("CETAK TAMBAHAN")
        ec_title.setAlignment(Qt.AlignCenter)
        ec_title.setStyleSheet(f"color: {COLORS['ink_soft']}; font-size: 13px; font-weight: 800; "
                               f"letter-spacing: 3px; background: transparent; border: none;")
        ec.addWidget(ec_title)
        ec_row = QHBoxLayout()
        ec_row.setSpacing(12)
        self.btn_extra_minus = QPushButton("\u2212")
        self.btn_extra_plus = QPushButton("+")
        for b in (self.btn_extra_minus, self.btn_extra_plus):
            b.setFixedSize(56, 56)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(f"""
                QPushButton {{ background: {COLORS['pink_soft']}; color: {COLORS['pink_dk']};
                               border-radius: 28px; font-size: 30px; font-weight: 900; border: none; }}
                QPushButton:hover {{ background: {COLORS['pink']}; color: white; }}
                QPushButton:disabled {{ background: #F4ECF0; color: #D9C9D3; }}
            """)
        self.extra_value = QLabel("0")
        self.extra_value.setAlignment(Qt.AlignCenter)
        self.extra_value.setStyleSheet(f"color: {COLORS['ink']}; font-size: 40px; font-weight: 800; "
                                       f"font-family: '{FONT_DISPLAY}'; background: transparent; border: none;")
        ec_row.addWidget(self.btn_extra_minus)
        ec_row.addWidget(self.extra_value, 1)
        ec_row.addWidget(self.btn_extra_plus)
        ec.addLayout(ec_row)
        self.extra_hint = QLabel("")
        self.extra_hint.setAlignment(Qt.AlignCenter)
        self.extra_hint.setStyleSheet(f"color: {COLORS['ink_soft']}; font-size: 14px; font-weight: 600; "
                                      f"background: transparent; border: none;")
        ec.addWidget(self.extra_hint)
        self.btn_extra_minus.clicked.connect(partial(self._change_extra_prints, -1))
        self.btn_extra_plus.clicked.connect(partial(self._change_extra_prints, 1))
        self.extra_card.hide()
        # Customer choice: individual photos (soft files) get the filter too?
        self.chk_indiv_fx = QCheckBox("Efek di foto satuan", screen)
        self.chk_indiv_fx.setCursor(Qt.PointingHandCursor)
        self.chk_indiv_fx.setAttribute(Qt.WA_StyledBackground, True)
        self.chk_indiv_fx.setStyleSheet(f"""
            QCheckBox {{ background: white; border: 2px solid {COLORS['pink_soft']};
                         border-radius: 18px; padding: 0 14px; color: {COLORS['ink']};
                         font-size: 16px; font-weight: 800; spacing: 12px; }}
            QCheckBox::indicator {{ width: 28px; height: 28px; border-radius: 8px;
                                    border: 2px solid {COLORS['pink']}; background: white; }}
            QCheckBox::indicator:checked {{ background: {COLORS['pink']}; }}
        """)
        self._add_shadow(self.chk_indiv_fx, blur=18, y_offset=4, alpha=80)
        self.stack.addWidget(screen)

    def _max_extra_prints(self):
        pcfg = CONFIG.get("printing", {}) or {}
        if not pcfg.get("enabled", True):
            return 0
        try:
            return max(0, int(pcfg.get("max_extra_prints", 0)))
        except (TypeError, ValueError):
            return 0

    def _change_extra_prints(self, delta):
        self.extra_prints = max(0, min(self._max_extra_prints(), self.extra_prints + delta))
        self._refresh_extra_card()

    def _refresh_extra_card(self):
        if not hasattr(self, "extra_card"):
            return
        mx = self._max_extra_prints()
        self.extra_prints = max(0, min(mx, int(getattr(self, "extra_prints", 0) or 0)))
        self.extra_value.setText(str(self.extra_prints))
        self.btn_extra_minus.setEnabled(self.extra_prints > 0)
        self.btn_extra_plus.setEnabled(self.extra_prints < mx)
        self.extra_hint.setText(f"total {1 + self.extra_prints} lembar  \u00b7  maks +{mx}")

    def _style_filter_card(self, btn, selected):
        if selected:
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {COLORS['pink']}, stop:1 {COLORS['lilac']}); color: white;
                    border-radius: 18px; font-size: 22px; font-weight: 800;
                    text-align: center; padding: 0 14px;
                    border: 2px solid {COLORS['yellow_dk']};
                }}
            """)
        else:
            btn.setStyleSheet("""
                QPushButton {
                    background: white; color: #3B2A4A;
                    border-radius: 18px; font-size: 20px; font-weight: 700;
                    text-align: center; padding: 0 14px;
                    border: 2px solid #FFD6E5;
                }
                QPushButton:hover { background: #FFF0F5; }
            """)

    def _set_filter(self, fid):
        if fid not in FILTER_IDS:
            return
        self.current_filter = fid
        for k, btn in (self.filter_cards or {}).items():
            self._style_filter_card(btn, selected=(k == fid))
            btn.setChecked(k == fid)
        if self.stack.currentIndex() == self.SCREEN_FRAMES:
            self._populate_fp_slots()
            self._render_fp_preview()

    def _build_print_screen(self):
        screen = QWidget()
        screen.setStyleSheet("background: transparent;")
        root = QVBoxLayout(screen)
        root.setContentsMargins(80, 80, 80, 80)
        root.setSpacing(0)
        root.addStretch(1)
        eyebrow = QLabel("MEMPROSES")
        eyebrow.setAlignment(Qt.AlignCenter)
        eyebrow.setStyleSheet(f"color: {COLORS['yellow']}; font-size: 18px; font-weight: 700; letter-spacing: 12px; background: transparent;")
        root.addWidget(eyebrow)
        self.print_title = QLabel("Mencetak foto…")
        self.print_title.setAlignment(Qt.AlignCenter)
        self.print_title.setStyleSheet(f"color: {COLORS['ink']}; font-size: 64px; font-weight: 800; font-family: '{FONT_DISPLAY}'; background: transparent;")
        root.addWidget(self.print_title)
        self.print_status = QLabel("Foto sedang dicetak — siap diambil sebentar lagi")
        self.print_status.setAlignment(Qt.AlignCenter)
        self.print_status.setStyleSheet(f"color: {COLORS['ink_soft']}; font-size: 24px; font-weight: 500; background: transparent;")
        root.addWidget(self.print_status)
        root.addSpacing(56)
        self.print_progress = QProgressBar()
        self.print_progress.setMinimum(0)
        self.print_progress.setMaximum(100)
        self.print_progress.setValue(0)
        self.print_progress.setTextVisible(False)
        self.print_progress.setFixedHeight(18)
        self.print_progress.setMaximumWidth(900)
        self.print_progress.setStyleSheet(f"""
            QProgressBar {{
                background: {COLORS['pink_soft']};
                border-radius: 9px;
            }}
            QProgressBar::chunk {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {COLORS['pink']}, stop:1 {COLORS['lilac']});
                border-radius: 9px;
            }}
        """)
        wrap_p = QHBoxLayout()
        wrap_p.addStretch()
        wrap_p.addWidget(self.print_progress)
        wrap_p.addStretch()
        root.addLayout(wrap_p)
        root.addSpacing(12)
        self.print_pct = QLabel("0%")
        self.print_pct.setAlignment(Qt.AlignCenter)
        self.print_pct.setStyleSheet(f"color: {COLORS['ink_soft']}; font-size: 18px; font-weight: 700; letter-spacing: 2px; background: transparent;")
        root.addWidget(self.print_pct)
        root.addStretch(1)
        self.print_timer = QTimer(self)
        self.print_timer.setInterval(CONFIG["print_tick_ms"])
        self.print_timer.timeout.connect(self._tick_print)
        self.stack.addWidget(screen)

    def _build_done_screen(self):
        screen = QWidget()
        screen.setStyleSheet("background: transparent;")
        root = QVBoxLayout(screen)
        root.setContentsMargins(80, 80, 80, 80)
        root.setSpacing(0)
        root.addStretch(1)
        eyebrow = QLabel("SELESAI")
        eyebrow.setAlignment(Qt.AlignCenter)
        eyebrow.setStyleSheet(f"color: {COLORS['yellow']}; font-size: 18px; font-weight: 700; letter-spacing: 12px; background: transparent;")
        root.addWidget(eyebrow)
        root.addSpacing(24)
        title = QLabel("Yay! Fotomu sudah jadi")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"color: {COLORS['ink']}; font-size: 56px; font-weight: 800; font-family: '{FONT_DISPLAY}'; background: transparent;")
        root.addWidget(title)
        root.addSpacing(12)
        sub = QLabel("Hasil cetak menanti — scan QR di pojok foto untuk soft file")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet(f"color: {COLORS['ink_soft']}; font-size: 22px; font-weight: 500; background: transparent;")
        sub.setWordWrap(True)
        root.addWidget(sub)
        root.addSpacing(20)
        # Status text — shows BTS/upload progress.
        self.done_status = QLabel("Video & upload sedang diproses…")
        self.done_status.setAlignment(Qt.AlignCenter)
        self.done_status.setStyleSheet(f"color: {COLORS['yellow']}; font-size: 16px; font-weight: 600; letter-spacing: 3px; background: transparent;")
        root.addWidget(self.done_status)
        root.addSpacing(40)
        body = QHBoxLayout()
        body.setSpacing(48)
        body.addStretch()
        self.done_thumb = QLabel()
        self.done_thumb.setFixedSize(440, 600)
        self.done_thumb.setAlignment(Qt.AlignCenter)
        self.done_thumb.setStyleSheet("background: white; border-top: 16px solid white; border-left: 16px solid white; border-right: 16px solid white; border-bottom: 64px solid white; border-radius: 8px;")
        self._add_shadow(self.done_thumb, blur=40, y_offset=14, alpha=130)
        body.addWidget(self.done_thumb)

        # Right side: QR code + password card.
        qr_card = QWidget()
        qr_card.setFixedWidth(400)
        qr_card.setStyleSheet("background: transparent;")
        qr_l = QVBoxLayout(qr_card)
        qr_l.setContentsMargins(0, 0, 0, 0)
        qr_l.setSpacing(20)
        qr_l.addStretch()

        qr_eyebrow = QLabel("SCAN UNTUK SOFT FILE")
        qr_eyebrow.setAlignment(Qt.AlignCenter)
        qr_eyebrow.setStyleSheet(f"color: {COLORS['yellow']}; font-size: 14px; font-weight: 700; letter-spacing: 6px; background: transparent;")
        qr_l.addWidget(qr_eyebrow)

        self.done_qr_label = QLabel()
        self.done_qr_label.setFixedSize(340, 340)
        self.done_qr_label.setAlignment(Qt.AlignCenter)
        self.done_qr_label.setStyleSheet(f"background: white; border: 4px solid {COLORS['pink_soft']}; border-radius: 22px; padding: 16px;")
        self._add_shadow(self.done_qr_label, blur=24, y_offset=8, alpha=120)
        qr_wrap = QHBoxLayout()
        qr_wrap.addStretch()
        qr_wrap.addWidget(self.done_qr_label)
        qr_wrap.addStretch()
        qr_l.addLayout(qr_wrap)

        # Status under QR (folder URL not ready / uploading / done).
        self.done_qr_status = QLabel("Menyiapkan QR…")
        self.done_qr_status.setAlignment(Qt.AlignCenter)
        self.done_qr_status.setStyleSheet(f"color: {COLORS['ink_soft']}; font-size: 14px; font-weight: 600; background: transparent;")
        self.done_qr_status.setWordWrap(True)
        qr_l.addWidget(self.done_qr_status)

        # Password block.
        qr_l.addSpacing(8)
        pwd_eyebrow = QLabel("KODE SESI")
        pwd_eyebrow.setAlignment(Qt.AlignCenter)
        pwd_eyebrow.setStyleSheet(f"color: {COLORS['ink_soft']}; font-size: 12px; font-weight: 800; letter-spacing: 4px; background: transparent;")
        qr_l.addWidget(pwd_eyebrow)
        self.done_password_label = QLabel("------")
        self.done_password_label.setAlignment(Qt.AlignCenter)
        self.done_password_label.setStyleSheet(f"color: {COLORS['lilac_dk']}; font-size: 40px; font-weight: 900; letter-spacing: 8px; background: {COLORS['lilac_soft']}; border-radius: 20px; padding: 6px 18px; font-family: '{FONT_DISPLAY}';")
        qr_l.addWidget(self.done_password_label)
        qr_l.addStretch()

        body.addWidget(qr_card)
        body.addStretch()
        root.addLayout(body)
        root.addSpacing(56)
        self.btn_home = QPushButton("Sesi Berikutnya")
        self.btn_home.setFixedHeight(80)
        self.btn_home.setMinimumWidth(320)
        self.btn_home.setCursor(Qt.PointingHandCursor)
        self.btn_home.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {COLORS['pink']}, stop:1 {COLORS['lilac']}); color: white;
                border-radius: 40px;
                font-size: 24px; font-weight: 700;
                padding: 0 44px;
            }}
            QPushButton:hover {{ background: {COLORS['pink_dk']}; }}
        """)
        self.btn_home.clicked.connect(self._reset_to_home)
        self._add_shadow(self.btn_home, blur=30, y_offset=10, alpha=110)
        wrap = QHBoxLayout()
        wrap.addStretch()
        wrap.addWidget(self.btn_home)
        wrap.addStretch()
        root.addLayout(wrap)
        root.addStretch(1)
        self.stack.addWidget(screen)

    # =============== CODE INPUT SCREEN (9-digit numeric) ===============
    def _build_code_screen(self):
        screen = QWidget()
        screen.setStyleSheet("background: transparent;")
        root = QVBoxLayout(screen)
        root.setContentsMargins(80, 60, 80, 60)
        root.setSpacing(0)
        root.addStretch(1)

        eyebrow = QLabel("LANGKAH 2 DARI 2")
        eyebrow.setAlignment(Qt.AlignCenter)
        eyebrow.setStyleSheet(f"color: {COLORS['yellow']}; font-size: 16px; font-weight: 700; letter-spacing: 10px; background: transparent;")
        root.addWidget(eyebrow)
        root.addSpacing(20)

        title = QLabel("Masukkan Kode Akses")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"color: {COLORS['ink']}; font-size: 56px; font-weight: 800; font-family: '{FONT_DISPLAY}'; background: transparent;")
        root.addWidget(title)
        root.addSpacing(10)

        sub = QLabel("Masukkan 9 digit kode yang kamu terima")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet(f"color: {COLORS['ink_soft']}; font-size: 18px; background: transparent;")
        root.addWidget(sub)
        root.addSpacing(40)

        # Code display — 9 boxes showing digits typed so far.
        self._code_buffer = ""
        self.code_boxes = []
        boxes_wrap = QHBoxLayout()
        boxes_wrap.setSpacing(10)
        boxes_wrap.addStretch()
        for i in range(9):
            b = QLabel("•")
            b.setFixedSize(70, 90)
            b.setAlignment(Qt.AlignCenter)
            b.setStyleSheet(self._code_box_style(False))
            self.code_boxes.append(b)
            boxes_wrap.addWidget(b)
            if i == 2 or i == 5:
                sep = QLabel("-")
                sep.setStyleSheet(f"color: {COLORS['pink']}; font-size: 36px; font-weight: 700; background: transparent;")
                boxes_wrap.addWidget(sep)
        boxes_wrap.addStretch()
        root.addLayout(boxes_wrap)
        root.addSpacing(20)

        self.code_status = QLabel(" ")
        self.code_status.setAlignment(Qt.AlignCenter)
        self.code_status.setStyleSheet(f"color: {COLORS['danger']}; font-size: 18px; font-weight: 700; background: transparent;")
        root.addWidget(self.code_status)
        root.addSpacing(20)

        # Numeric keypad (3x4 grid + backspace + clear).
        keypad_wrap = QHBoxLayout()
        keypad_wrap.addStretch()
        keypad = QGridLayout()
        keypad.setSpacing(12)
        rows = [["1","2","3"],["4","5","6"],["7","8","9"],["⌫","0","✓"]]
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                btn = QPushButton(val)
                btn.setFixedSize(110, 90)
                btn.setCursor(Qt.PointingHandCursor)
                if val == "✓":
                    btn.setStyleSheet(f"""
                        QPushButton {{
                            background: {COLORS['success']}; color: white;
                            border-radius: 22px; font-size: 32px; font-weight: 900;
                        }}
                        QPushButton:hover {{ background: #3BB394; }}
                    """)
                    btn.clicked.connect(self._code_submit)
                elif val == "⌫":
                    btn.setStyleSheet(f"""
                        QPushButton {{
                            background: {COLORS['pink_soft']}; color: {COLORS['pink_dk']};
                            border-radius: 22px; font-size: 28px; font-weight: 800;
                        }}
                        QPushButton:hover {{ background: {COLORS['pink']}; color: white; }}
                    """)
                    btn.clicked.connect(self._code_backspace)
                else:
                    btn.setStyleSheet("""
                        QPushButton {
                            background: white; color: #3B2A4A;
                            border-radius: 22px; font-size: 36px; font-weight: 800;
                        }
                        QPushButton:hover { background: #FFF0F5; }
                    """)
                    btn.clicked.connect(partial(self._code_digit, val))
                self._add_shadow(btn, blur=14, y_offset=3, alpha=100)
                keypad.addWidget(btn, r, c)
        keypad_wrap.addLayout(keypad)
        keypad_wrap.addStretch()
        root.addLayout(keypad_wrap)
        root.addSpacing(20)

        # Bottom: KEMBALI ke layout picker.
        bot = QHBoxLayout()
        bot.addStretch()
        btn_back = QPushButton("← KEMBALI")
        btn_back.setFixedHeight(56)
        btn_back.setMinimumWidth(180)
        btn_back.setCursor(Qt.PointingHandCursor)
        btn_back.setStyleSheet(f"""
            QPushButton {{
                background: white; color: {COLORS['ink']};
                border: 2px solid {COLORS['pink_soft']};
                border-radius: 28px; font-size: 16px; font-weight: 700;
                padding: 0 24px;
            }}
            QPushButton:hover {{ background: {COLORS['pink_soft']}; }}
        """)
        btn_back.clicked.connect(lambda: self.stack.setCurrentIndex(self.SCREEN_LAYOUTS))
        bot.addWidget(btn_back)
        bot.addStretch()
        root.addLayout(bot)
        root.addStretch(1)

        self.stack.addWidget(screen)

    @staticmethod
    def _code_box_style(filled):
        if filled:
            return f"""
                background: white; color: #3B2A4A;
                border-radius: 14px; font-size: 44px; font-weight: 900;
                border: 3px solid {COLORS['yellow']};
            """
        return """
            background: rgba(255,255,255,0.7); color: #FFB3CD;
            border-radius: 14px; font-size: 44px; font-weight: 900;
            border: 2px dashed #FFC2D8;
        """

    def _refresh_code_boxes(self):
        for i, b in enumerate(self.code_boxes):
            if i < len(self._code_buffer):
                b.setText(self._code_buffer[i])
                b.setStyleSheet(self._code_box_style(True))
            else:
                b.setText("•")
                b.setStyleSheet(self._code_box_style(False))

    def _code_digit(self, d):
        if len(self._code_buffer) < 9:
            self._code_buffer += d
            self.code_status.setText(" ")
            self._refresh_code_boxes()

    def _code_backspace(self):
        self._code_buffer = self._code_buffer[:-1]
        self.code_status.setText(" ")
        self._refresh_code_boxes()

    def _code_submit(self):
        if len(self._code_buffer) != 9:
            self.code_status.setText("Masukkan 9 digit lengkap dulu")
            return
        result = _check_and_use_code(self._code_buffer)
        if result == "ok":
            self.code_status.setText("✓ Kode valid")
            self.code_status.setStyleSheet(f"color: {COLORS['success']}; font-size: 18px; font-weight: 700; background: transparent;")
            # Reset buffer for next session.
            entered = self._code_buffer
            self._code_buffer = ""
            self._refresh_code_boxes()
            self.code_status.setStyleSheet(f"color: {COLORS['danger']}; font-size: 18px; font-weight: 700; background: transparent;")
            self.code_status.setText(" ")
            # Save which code was used for the session log.
            self._session_access_code = entered
            QTimer.singleShot(200, self._start_session)
        elif result == "used":
            self.code_status.setText("Kode sudah pernah dipakai")
        elif result == "invalid":
            self.code_status.setText("Kode tidak valid")
        else:
            self.code_status.setText("Error baca file kode")

    def _goto_code_input(self):
        """Show the code input screen. Called after layout pick."""
        self._code_buffer = ""
        self.code_status.setText(" ")
        self._refresh_code_boxes()
        self.stack.setCurrentIndex(self.SCREEN_CODE)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_capture_layout()
        self._fit_fp_layout()
        self._fit_lp_layout()
        self._fit_pick_layout()

    def _fit_capture_layout(self):
        if not hasattr(self, "capture_screen") or self.capture_screen is None:
            return
        if not self.preview_container or not self.preview_label:
            return
        sw = self.capture_screen.width()
        sh = self.capture_screen.height()
        if sw < 10 or sh < 10:
            return
        if self._top_bar_widget is not None:
            self._top_bar_widget.setGeometry(0, 0, sw, 80)
        pose_idx = len(self.captured_frames) % max(1, self.shots_per_session)
        if pose_idx < len(self.pose_aspects):
            preview_aspect = self.pose_aspects[pose_idx]
        else:
            preview_aspect = 3 / 4
        N = max(1, min(MAX_SLOTS, self.total_shots))
        thumb_strip_h = 130 if N > 0 else 0
        bottom_pad = 30
        progress_pill_h = 60
        progress_pill_gap = 18
        avail_top = 100
        avail_bot = bottom_pad + progress_pill_h + progress_pill_gap + thumb_strip_h + 16
        avail_h = sh - avail_top - avail_bot
        avail_w = int(sw * 0.86)
        fit_h = avail_h
        fit_w = int(round(fit_h * preview_aspect))
        if fit_w > avail_w:
            fit_w = avail_w
            fit_h = int(round(fit_w / preview_aspect))
        pc_w, pc_h = max(120, fit_w), max(120, fit_h)
        pc_x = (sw - pc_w) // 2
        pc_y = avail_top + (avail_h - pc_h) // 2
        self.preview_container.setGeometry(pc_x, pc_y, pc_w, pc_h)
        if not self.is_reviewing_final:
            self.preview_label.setGeometry(0, 0, pc_w, pc_h)
        cd_h = int(min(pc_w, pc_h) * 0.45)
        cd_w = max(cd_h, int(pc_w * 0.55))
        cd_font = max(60, int(cd_h * 0.78))
        self.countdown_label.setStyleSheet(f"color: white; background: transparent; font-weight: 900; font-size: {cd_font}px;")
        self.countdown_label.setFixedSize(cd_w, cd_h)
        self.countdown_label.setGeometry((pc_w - cd_w) // 2, (pc_h - cd_h) // 2, cd_w, cd_h)
        rb_w = int(pc_w * 0.55)
        self.ready_banner.setFixedSize(rb_w, 50)
        self.ready_banner.move((pc_w - rb_w) // 2, int(pc_h * 0.80))
        strip_y = pc_y + pc_h + 20
        slot_h = thumb_strip_h
        slot_widths = []
        for i in range(N):
            a = self.pose_aspects[i % len(self.pose_aspects)] if self.pose_aspects else 3 / 4
            slot_widths.append(max(40, int(round(slot_h * a))))
        slot_gap = 10
        total_w = sum(slot_widths) + slot_gap * (N - 1)
        max_strip_w = sw - 80
        if total_w > max_strip_w:
            scale = max_strip_w / total_w
            slot_widths = [max(30, int(round(w * scale))) for w in slot_widths]
            slot_h_scaled = max(60, int(round(slot_h * scale)))
            total_w = sum(slot_widths) + slot_gap * (N - 1)
        else:
            slot_h_scaled = slot_h
        x_start = (sw - total_w) // 2
        for s in self.slot_widgets:
            s.setVisible(False)
        x = x_start
        for i in range(N):
            self.slot_widgets[i].setGeometry(x, strip_y, slot_widths[i], slot_h_scaled)
            self.slot_widgets[i].setVisible(True)
            x += slot_widths[i] + slot_gap
        for slot in self.slot_widgets:
            if not slot.isVisible():
                continue
            sw_s = slot.width(); sh_s = slot.height()
            slot.placeholder_text.setGeometry(0, 0, sw_s, sh_s)
            if slot.image_label is not None:
                slot.image_label.setGeometry(2, 2, sw_s - 4, sh_s - 4)
        pp_w = 270 + 34 * self.total_shots
        pp_h = progress_pill_h
        self.progress_pill.setGeometry((sw - pp_w) // 2, sh - pp_h - bottom_pad, pp_w, pp_h)
        rv_w = 540
        rv_h = 100
        self.review_widget.setGeometry((sw - rv_w) // 2, sh - pp_h - bottom_pad - rv_h - 12, rv_w, rv_h)

    def _update_preview(self, frame, raw_frame, is_cropped):
        if CONFIG.get("mirror", True):
            # Selfie view: customer's left on the left. Applied here so the
            # preview, webcam stills, BTS and moving clips all match.
            frame = cv2.flip(frame, 1)
            raw_frame = cv2.flip(raw_frame, 1)
        self.last_raw_frame = frame
        self._last_uncropped_frame = raw_frame
        self._cam_frame_seen = True
        if (self.session_started and not self._cam_warming
                and not self.is_reviewing and not self.is_reviewing_final):
            now = time.perf_counter()
            min_dt = 1.0 / max(self.video_fps, 1)
            if now - self._last_record_ts >= min_dt:
                self._last_record_ts = now
                rh, rw = raw_frame.shape[:2]
                target_short = self.video_short_side
                if rh <= rw:
                    new_h = target_short
                    new_w = int(round(rw * (target_short / rh)))
                else:
                    new_w = target_short
                    new_h = int(round(rh * (target_short / rw)))
                if new_w % 2: new_w -= 1
                if new_h % 2: new_h -= 1
                small = cv2.resize(raw_frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
                bts_cap = int(self.video_fps * 60.0)
                if len(self.bts_buffer) < bts_cap:
                    self.bts_buffer.append(small)
                self.rolling_buffer.append(small)
                max_rolling = int(self.video_fps * self.moving_clip_s) + 2
                if len(self.rolling_buffer) > max_rolling:
                    self.rolling_buffer = self.rolling_buffer[-max_rolling:]
        if self.is_reviewing or self.is_reviewing_final:
            return
        if self.stack.currentIndex() != self.SCREEN_CAPTURE:
            return
        w = self.preview_label.width()
        h = self.preview_label.height()
        if w < 10 or h < 10:
            self._fit_capture_layout()
            w = self.preview_label.width()
            h = self.preview_label.height()
        if w < 10 or h < 10:
            return
        rgb  = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        qimg = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format_RGB888)
        pixmap = QPixmap.fromImage(qimg).scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.preview_label.setPixmap(pixmap)

    def _load_frames(self):
        paths = sorted(FRAME_DIR.glob("*.png"))
        self.frame_paths = list(paths)
        while self.frame_strip.count():
            item = self.frame_strip.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.frame_buttons = []
        self.frame_strip.addStretch()
        for i, path in enumerate(self.frame_paths):
            card = QPushButton()
            card.setFixedSize(QSize(140, 200))
            card.setCursor(Qt.PointingHandCursor)
            pix = QPixmap(str(path)).scaled(124, 184, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            card.setIcon(QIcon(pix))
            card.setIconSize(QSize(124, 184))
            card.setStyleSheet(self._frame_card_style(selected=False))
            card.clicked.connect(partial(self._on_frame_selected, path, card))
            check = QLabel("✓", card)
            check.setAlignment(Qt.AlignCenter)
            check.setFixedSize(34, 34)
            check.setStyleSheet(f"background: {COLORS['success']}; color: white; font-size: 20px; font-weight: 900; border-radius: 17px; border: 2px solid white;")
            check.move(card.width() - 40, 6)
            check.hide()
            card.check_label = check
            self.frame_strip.insertWidget(self.frame_strip.count() - 1, card)
            self.frame_buttons.append(card)
        self.frame_strip.addStretch()
        if self.frame_paths:
            self.overlay_path = self.frame_paths[0]
            if self.frame_buttons:
                QTimer.singleShot(0, lambda: self._set_selected_frame_button(0))

    @staticmethod
    def _frame_card_style(selected):
        if selected:
            return f"""
                QPushButton {{
                    background: white;
                    border: 3px solid {COLORS['yellow']};
                    border-radius: 12px;
                    padding: 8px;
                }}
            """
        return """
            QPushButton {
                background: white;
                border: 3px solid transparent;
                border-radius: 12px;
                padding: 8px;
            }
            QPushButton:hover { border-color: #FFC2D8; }
        """

    def _set_selected_frame_button(self, idx):
        for i, b in enumerate(self.frame_buttons):
            b.setStyleSheet(self._frame_card_style(selected=(i == idx)))
            check = getattr(b, "check_label", None)
            if check is not None:
                if i == idx:
                    check.move(b.width() - check.width() - 6, 6)
                    check.show()
                    check.raise_()
                else:
                    check.hide()

    def _on_frame_selected(self, path, button):
        self.overlay_path = path
        try:
            idx = self.frame_buttons.index(button)
        except ValueError:
            idx = -1
        if idx >= 0:
            self._set_selected_frame_button(idx)
        if self.stack.currentIndex() == self.SCREEN_FRAMES:
            self._render_fp_preview()

    def _fp_items(self):
        """Photos for the frame-picker column, in order: the first
        shots_per_session go into the frame, the rest are spares."""
        if self.pick_pool and self.all_shots:
            return [self.all_shots[i] for i in self.pick_pool if 0 <= i < len(self.all_shots)]
        return list(self.captured_frames)

    def _apply_pool_order(self):
        """Derive the frame's photos/clips from pick_pool (frame order first)."""
        N = self.shots_per_session
        pool = list(self.pick_pool)
        self.picked_indices = pool
        self.captured_frames = [self.all_shots[i] for i in pool[:N]]
        self.moving_clips = [self.all_clips[i] if i < len(self.all_clips) else None
                             for i in pool[:N]]

    @staticmethod
    def _fp_slot_style(kind):
        border = "white" if kind == "frame" else "transparent"
        bg = "rgba(255,255,255,0.75)" if kind == "spare" else "white"
        return (f"QPushButton {{ background: {bg}; border: 2px solid {border}; "
                f"border-radius: 14px; }} QPushButton:hover {{ border-color: #FFC2D8; }}")

    def _style_fp_slots(self):
        N = self.shots_per_session
        sel = getattr(self, "_fp_sel", None)
        for idx, slot in enumerate(self.fp_slot_widgets):
            if not slot.isVisible():
                continue
            kind = "frame" if idx < N else "spare"
            slot.setStyleSheet(self._fp_slot_style(kind))
            slot.sel_ring.setGeometry(0, 0, slot.width(), slot.height())
            slot.sel_ring.setVisible(idx == sel)
            slot.sel_ring.raise_()
            b = slot.badge
            if idx < N:
                b.setText(str(idx + 1))
                b.setStyleSheet(f"background: {COLORS['pink']}; color: white; font-size: 16px; "
                                f"font-weight: 900; border-radius: 15px; border: 2px solid white;")
                b.resize(30, 30)
            else:
                b.setText("cadangan")
                b.setStyleSheet(f"background: white; color: {COLORS['ink_soft']}; font-size: 12px; "
                                f"font-weight: 800; border-radius: 11px; border: 1px solid {COLORS['pink_soft']};")
                b.resize(84, 22)
            b.move(6, 6)
            b.raise_()

    def _on_fp_slot_clicked(self, idx):
        items = self._fp_items()
        if idx >= len(items) or not self.pick_pool:
            return
        sel = self._fp_sel
        if sel is None:
            self._fp_sel = idx
            self._style_fp_slots()
            return
        self._fp_sel = None
        if sel != idx and sel < len(self.pick_pool):
            p = self.pick_pool
            p[sel], p[idx] = p[idx], p[sel]
            self._apply_pool_order()
            LOG.info(f"[FRAME] reorder -> {[i + 1 for i in p]}")
            self._populate_fp_slots()
            N = self.shots_per_session
            if sel < N or idx < N:
                self._render_fp_preview()
        self._style_fp_slots()

    def _fp_thumb(self, shot_key, frame, w, h):
        flt = getattr(self, "current_filter", "none")
        key = (shot_key, flt, w, h)
        pix = self._fp_thumb_cache.get(key)
        if pix is not None:
            return pix
        # Downscale before beautify/filter: thumbnails don't need full res.
        fh, fw = frame.shape[:2]
        sc = min(1.0, 2.0 * max(w / max(1, fw), h / max(1, fh)))
        src = frame
        if sc < 1.0:
            src = cv2.resize(frame, (max(1, int(fw * sc)), max(1, int(fh * sc))),
                             interpolation=cv2.INTER_AREA)
        bty = float(CONFIG.get("beautify_strength", 0.0))
        if bty > 0:
            src = beautify(src, bty)
        if flt and flt != "none":
            src = apply_filter(src, flt)
        rgb = np.ascontiguousarray(cv2.cvtColor(src, cv2.COLOR_BGR2RGB))
        qimg = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format_RGB888)
        pix = QPixmap.fromImage(qimg).scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        if pix.width() > w or pix.height() > h:
            pix = pix.copy((pix.width() - w) // 2, (pix.height() - h) // 2, w, h)
        self._fp_thumb_cache[key] = pix
        return pix

    def _populate_fp_slots(self):
        items = self._fp_items()
        keys = list(self.pick_pool) if (self.pick_pool and self.all_shots) else \
            [f"c{i}" for i in range(len(items))]
        for idx, slot in enumerate(self.fp_slot_widgets):
            if slot.image_label is not None:
                slot.image_label.deleteLater()
                slot.image_label = None
            if idx < len(items):
                slot.placeholder_text.hide()
                sw_s = max(slot.width() - 4, 10)
                sh_s = max(slot.height() - 4, 10)
                if sw_s < 20 or sh_s < 20:
                    continue
                pix = self._fp_thumb(keys[idx], items[idx], sw_s, sh_s)
                img_lbl = QLabel(slot)
                img_lbl.setAlignment(Qt.AlignCenter)
                img_lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
                img_lbl.setStyleSheet("background: transparent; border: none; border-radius: 12px;")
                img_lbl.setPixmap(pix)
                img_lbl.setGeometry(2, 2, sw_s, sh_s)
                img_lbl.show()
                slot.image_label = img_lbl
        self._style_fp_slots()

    def _render_fp_preview(self):
        try:
            canvas = self._build_final_canvas()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Gagal render: {e}")
            return
        if canvas is None:
            return
        self.temp_canvas = canvas
        ow, oh = canvas.size
        cw = self.fp_preview_container.width()
        ch = self.fp_preview_container.height()
        if cw < 10 or ch < 10:
            return
        target = ow / oh
        new_h = ch
        new_w = int(new_h * target)
        if new_w > cw:
            new_w = cw
            new_h = int(new_w / target)
        nx = (cw - new_w) // 2
        ny = (ch - new_h) // 2
        self.fp_preview.setGeometry(nx, ny, new_w, new_h)
        rgb = canvas.convert("RGB")
        data = rgb.tobytes("raw", "RGB")
        qimg = QImage(data, rgb.width, rgb.height, QImage.Format_RGB888)
        pix = QPixmap.fromImage(qimg).scaled(new_w, new_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.fp_preview.setPixmap(pix)

    def _fit_fp_layout(self):
        if not hasattr(self, "frames_screen") or self.frames_screen is None:
            return
        sw = self.frames_screen.width()
        sh = self.frames_screen.height()
        if sw < 10 or sh < 10:
            return
        self._fp_title.setGeometry(0, 36, sw, 64)
        self._fp_sub.setGeometry(0, 104, sw, 40)
        strip_h = 220
        bottom_pad = 24
        strip_y = sh - strip_h - bottom_pad
        nav_h = 56
        btn_w_back = max(180, self.btn_back_picker.sizeHint().width())
        btn_w_next = max(180, self.btn_continue.sizeHint().width())
        self.btn_back_picker.setFixedHeight(nav_h)
        self.btn_continue.setFixedHeight(nav_h)
        self.btn_back_picker.resize(btn_w_back, nav_h)
        self.btn_continue.resize(btn_w_next, nav_h)
        self.btn_back_picker.move(24, 24)
        self.btn_continue.move(sw - btn_w_next - 24, 24)
        self.btn_back_picker.raise_()
        self.btn_continue.raise_()
        self.frame_strip_scroll.setGeometry(40, strip_y, sw - 80, strip_h)
        top_y = max(150, nav_h + 48)
        avail_h = strip_y - top_y - 16
        filter_w = 280
        filter_x = sw - filter_w - 24
        filter_y = top_y
        filter_h = strip_y - filter_y - 16
        if hasattr(self, "chk_indiv_fx"):
            chk_h = 64
            filter_h -= chk_h + 14
            self.chk_indiv_fx.setGeometry(filter_x, filter_y + filter_h + 14, filter_w, chk_h)
            self.chk_indiv_fx.raise_()
        if hasattr(self, "extra_card"):
            if self._max_extra_prints() > 0:
                card_h = 176
                filter_h -= card_h + 14
                self.extra_card.setGeometry(filter_x, filter_y + filter_h + 14, filter_w, card_h)
                self.extra_card.show()
                self.extra_card.raise_()
            else:
                self.extra_card.hide()
        self.filter_panel_scroll.setGeometry(filter_x, filter_y, filter_w, filter_h)
        self.filter_panel_scroll.raise_()
        for _fid, _btn in (getattr(self, "filter_cards", {}) or {}).items():
            _btn.setFixedHeight(72)
        if hasattr(self, "filter_panel_title"):
            self.filter_panel_title.setStyleSheet(
                "color: #7A6488; font-size: 18px; font-weight: 800; "
                "letter-spacing: 4px; background: transparent; padding: 4px 4px 8px 4px;"
            )
        self.btn_back_picker.hide()
        target_ratio = 3 / 4
        pc_h = int(avail_h * 0.95)
        pc_w = int(pc_h * target_ratio)
        max_pc_w = int(sw * 0.30)
        if pc_w > max_pc_w:
            pc_w = max_pc_w
            pc_h = int(pc_w / target_ratio)
        pc_x = (sw - pc_w) // 2
        pc_y = top_y + (avail_h - pc_h) // 2
        self.fp_preview_container.setGeometry(pc_x, pc_y, pc_w, pc_h)
        N = max(1, min(MAX_SLOTS, self.shots_per_session))
        items = self._fp_items()
        P = max(N, min(MAX_SLOTS, len(items)))
        side_margin = 32
        slot_gap_y = 14
        pad = 12   # room for the drop shadows inside the scroll area
        l_area_x = side_margin
        l_area_w = pc_x - 30 - l_area_x
        hint_h = 32
        col_y = pc_y + hint_h
        col_h = pc_h - hint_h
        self.fp_slot_hint.setGeometry(l_area_x, pc_y, l_area_w, hint_h - 4)
        self.fp_slot_hint.setVisible(len(items) > 1)
        slot_w_cap = 300
        max_l_slot_w = min(slot_w_cap, max(140, l_area_w - 2 * pad - 10))

        def _aspect(i):
            if i < N:
                return self.pose_aspects[i] if i < len(self.pose_aspects) else 3 / 4
            if i < len(items):
                fh, fw = items[i].shape[:2]
                return fw / max(1, fh)
            return 3 / 4
        # Size so the N frame slots fit without scrolling; spares scroll.
        max_slot_h = (col_h - 2 * pad - slot_gap_y * (N - 1)) // max(1, N)
        max_aspect = max((_aspect(i) for i in range(N)), default=3/4)
        h_from_w = int(max_l_slot_w / max(0.1, max_aspect))
        slot_h = max(72, min(max_slot_h, h_from_w))
        for s in self.fp_slot_widgets:
            s.setVisible(False)
        stack_h = P * slot_h + (P - 1) * slot_gap_y + 2 * pad
        widest = max(min(max_l_slot_w, max(40, int(round(slot_h * _aspect(i)))))
                     for i in range(P))
        bar_w = 14 if stack_h > col_h else 0
        host_w = widest + 2 * pad
        col_w = min(l_area_w, host_w + bar_w)
        self.fp_slot_scroll.setGeometry(l_area_x + (l_area_w - col_w) // 2, col_y, col_w, col_h)
        self.fp_slot_host.resize(host_w, max(stack_h, col_h))
        y_start = pad + max(0, (col_h - stack_h) // 2)
        for i in range(P):
            w_for_this = max(40, int(round(slot_h * _aspect(i))))
            w_for_this = min(w_for_this, max_l_slot_w)
            x = (host_w - w_for_this) // 2
            self.fp_slot_widgets[i].setGeometry(
                x, y_start + i * (slot_h + slot_gap_y), w_for_this, slot_h)
            self.fp_slot_widgets[i].setVisible(True)
        for slot in self.fp_slot_widgets:
            sw_s = slot.width(); sh_s = slot.height()
            slot.placeholder_text.setGeometry(0, 0, sw_s, sh_s)
            if slot.image_label is not None:
                slot.image_label.setGeometry(2, 2, sw_s - 4, sh_s - 4)
        self._populate_fp_slots()
        self._render_fp_preview()

    @property
    def total_shots(self):
        """Layout poses + bonus shots (capped by the slot widgets we have)."""
        n = max(1, int(self.shots_per_session))
        return min(MAX_SLOTS, n + max(0, int(getattr(self, "extra_shots", 0) or 0)))

    def _start_session(self):
        self.extra_shots = max(0, int(CONFIG.get("extra_shots", 0) or 0))
        self.all_shots = []
        self.all_clips = []
        self.picked_indices = []
        self.pick_pool = []
        self.retake_shots = []
        self.shot_labels = []
        self.kept_shot_idx = []
        self.extra_prints = 0
        self._refresh_extra_card()
        self.captured_frames = []
        self.current_step = 1
        self.session_started = True
        self._release_camera()       # never reuse a handle from an earlier session
        self._wait_old_cameras()
        self._create_camera()
        self.camera.crop = True
        self.current_filter = "none"
        self.bts_buffer = []
        self.rolling_buffer = []
        self.moving_clips = [None] * self.total_shots
        self._pending_moving_clip = None
        self._last_record_ts = 0.0
        self.retakes_used = 0
        self.temp_canvas = None
        self.is_reviewing = False
        self.is_reviewing_final = False
        self._reset_slots()
        self._update_progress_dots()
        self.preview_label.setStyleSheet(self._preview_style_live)
        self.stack.setCurrentIndex(self.SCREEN_CAPTURE)
        self._rec_blink_timer.start()
        QTimer.singleShot(0, self._fit_capture_layout)
        self._start_camera_warmup()

    def _start_camera_warmup(self):
        """Give the camera time to start before the first shot's countdown."""
        self._warm_token += 1
        token = self._warm_token
        self._cam_warming = True
        self.current_count = max(0, int(CONFIG.get("camera_warmup_seconds", 5)))
        self._warm_deadline = time.time() + self.current_count + 15
        self.ready_banner.setText("Menyiapkan kamera\u2026")
        self.ready_banner.show()
        self.ready_banner.raise_()
        self.countdown_label.show()
        self.countdown_label.raise_()
        self._warmup_tick(token)

    def _warmup_tick(self, token):
        if token != self._warm_token or not self.session_started:
            return
        if self.current_count > 0:
            self.countdown_label.setText(str(self.current_count))
            self.current_count -= 1
            QTimer.singleShot(1000, lambda: self._warmup_tick(token))
            return
        # Timer done; if the camera hasn't produced a frame yet, wait a bit
        # longer (up to 15s) rather than shooting into a dead camera.
        if not self._cam_frame_seen and time.time() < self._warm_deadline:
            self.countdown_label.setText("\u2026")
            QTimer.singleShot(250, lambda: self._warmup_tick(token))
            return
        if not self._cam_frame_seen:
            LOG.warning("[CAMERA] no live frame after warm-up; starting anyway")
        self._cam_warming = False
        self.ready_banner.setText("Siap-siap ya!")
        self._start_capture_cycle()

    def _reset_slots(self):
        for i, slot in enumerate(self.slot_widgets):
            if slot.image_label is not None:
                slot.image_label.deleteLater()
                slot.image_label = None
            slot.setStyleSheet("""
                background: rgba(255, 255, 255, 0.85);
                border: 3px dashed #FFB3CD;
                border-radius: 14px;
            """)
            slot.placeholder_text.setText(f"{i+1}")
            slot.placeholder_text.show()
        self.thumbnail_cache.clear()

    def _update_progress_dots(self):
        N = self.shots_per_session
        T = self.total_shots
        for i, dot in enumerate(self.progress_dots):
            if i >= T:
                dot.setVisible(False)
                continue
            dot.setVisible(True)
            if i < len(self.captured_frames):
                dot.setStyleSheet(f"background: {COLORS['mint_dk']}; border-radius: 10px;")
            elif i == len(self.captured_frames):
                dot.setStyleSheet(f"background: {COLORS['pink']}; border-radius: 10px;")
            elif i >= N:
                dot.setStyleSheet(f"background: {COLORS['lilac_soft']}; border-radius: 10px;")
            else:
                dot.setStyleSheet(f"background: {COLORS['border']}; border-radius: 10px;")
        step = min(self.current_step, T)
        txt = f"Foto {step} / {T}"
        if step > N:
            txt += "  \u00b7  bonus"
        self.progress_text.setText(txt)

    def _start_capture_cycle(self):
        self.is_reviewing = False
        self._canon_waiting = False
        self.review_widget.hide()
        self.rolling_buffer = []
        pose_idx = (self.current_step - 1) % max(1, self.shots_per_session)
        if pose_idx < len(self.pose_aspects):
            self.camera.target_aspect = self.pose_aspects[pose_idx]
        self._fit_capture_layout()
        self._update_progress_dots()
        self.ready_banner.show()
        self.ready_banner.raise_()
        self.countdown_label.show()
        self.countdown_label.raise_()
        self.current_count = CONFIG["countdown_seconds"]
        self._run_countdown()

    def _run_countdown(self):
        if self.current_count > 0:
            self.countdown_label.setText(str(self.current_count))
            self.current_count -= 1
            QTimer.singleShot(1000, self._run_countdown)
        else:
            self.countdown_label.hide()
            self.ready_banner.hide()
            self._take_shot()

    def _take_shot(self):
        # Canon path fires the real shutter; review opens when the full-res
        # photo finishes downloading (_on_canon_still).
        if getattr(self, "canon_mode", False):
            self._take_shot_canon()
            return
        if self.last_raw_frame is None:
            return
        self._pending_moving_clip = list(self.rolling_buffer)
        self._show_shot_review(self.last_raw_frame.copy())

    def _take_shot_canon(self):
        """Ask the DSLR for a full-res shutter capture (non-blocking)."""
        self._pending_moving_clip = list(self.rolling_buffer)
        self._canon_waiting = True
        # Live-view frame at the moment of the shot: the still gets its colour.
        ref = getattr(self, "_last_uncropped_frame", None)
        self._live_color_ref = ref.copy() if ref is not None else None
        try:
            self.countdown_label.setText("")
            self.countdown_label.show()
            self.countdown_label.raise_()
        except Exception:
            pass
        self.camera.request_capture()

    def _on_canon_still(self, img):
        """Full-res DSLR photo arrived. Crop to this pose's aspect, then review."""
        if not self._canon_waiting:
            return
        self._canon_waiting = False
        try:
            self.countdown_label.hide()
        except Exception:
            pass
        aspect = float(getattr(self.camera, "target_aspect", 3 / 4) or 3 / 4)
        if CONFIG.get("mirror", True):
            img = cv2.flip(img, 1)   # match the mirrored live view
        if (CONFIG.get("camera", {}) or {}).get("match_liveview_color", True):
            try:
                img = _match_color_to(img, getattr(self, "_live_color_ref", None))
            except Exception as e:
                LOG.warning(f"[COLOR] live-view colour match failed: {e}")
        self._show_shot_review(
            _crop_to_aspect(img, aspect, self._canon_max_long))

    def _on_canon_capture_failed(self, msg):
        """Shutter/download failed — fall back to the live frame so the
        session never dead-ends mid-shoot."""
        if not self._canon_waiting:
            return
        self._canon_waiting = False
        LOG.error(f"[CANON] capture failed ({msg}) — using live-view frame")
        try:
            self.countdown_label.hide()
        except Exception:
            pass
        if self.last_raw_frame is not None:
            self._show_shot_review(self.last_raw_frame.copy())

    def _on_canon_lost(self, reason):
        """Watchdog stopped EDSDK (camera released). Unblock any pending shot;
        later shots use the last live frame until the app is restarted."""
        LOG.error(f"[CANON] camera lost: {reason} - EDSDK stopped")
        if self._canon_waiting:
            self._on_canon_capture_failed(reason)
        self.canon_mode = False

    def _show_shot_review(self, frame):
        self.current_frame_data = frame
        self.is_reviewing = True
        rgb = cv2.cvtColor(self.current_frame_data, cv2.COLOR_BGR2RGB)
        qimg = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format_RGB888)
        w = self.preview_label.width()
        h = self.preview_label.height()
        if w >= 10 and h >= 10:
            self.preview_label.setPixmap(QPixmap.fromImage(qimg).scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        remaining = self.max_retakes_per_shot - self.retakes_used
        self._style_retake_button(enabled=(remaining > 0), remaining=remaining)
        self.review_widget.show()
        self.review_widget.raise_()
        self._start_review_auto_confirm()

    def _start_review_auto_confirm(self):
        secs = int(CONFIG.get("review_auto_confirm_seconds", 5))
        if secs <= 0:
            return
        self._review_auto_secs = secs
        if not hasattr(self, "_review_auto_timer") or self._review_auto_timer is None:
            self._review_auto_timer = QTimer(self)
            self._review_auto_timer.setInterval(1000)
            self._review_auto_timer.timeout.connect(self._on_review_auto_tick)
        self.btn_next.setText(f"Next ({self._review_auto_secs})")
        self._review_auto_timer.start()

    def _stop_review_auto_confirm(self):
        if hasattr(self, "_review_auto_timer") and self._review_auto_timer:
            self._review_auto_timer.stop()
        try:
            self.btn_next.setText("Next")
        except Exception:
            pass

    def _on_review_auto_tick(self):
        self._review_auto_secs -= 1
        if self._review_auto_secs <= 0:
            self._stop_review_auto_confirm()
            self._next_photo()
        else:
            self.btn_next.setText(f"Next ({self._review_auto_secs})")

    def _retake_photo(self):
        if self.retakes_used >= self.max_retakes_per_shot:
            return
        self._stop_review_auto_confirm()
        self.retakes_used += 1
        self.review_widget.hide()
        self.is_reviewing = False
        if self.current_frame_data is not None:
            # Keep the rejected take: the pick screen shows every photo.
            self.retake_shots.append((self.current_frame_data,
                                      self._pending_moving_clip, self.current_step))
        self.current_frame_data = None
        self._pending_moving_clip = None
        self._start_capture_cycle()

    def _next_photo(self):
        self._stop_review_auto_confirm()
        self.review_widget.hide()
        self.is_reviewing = False
        if self.current_frame_data is not None:
            slot_idx = len(self.captured_frames)
            self.captured_frames.append(self.current_frame_data)
            if (self._pending_moving_clip is not None
                    and 0 <= slot_idx < len(self.moving_clips)):
                self.moving_clips[slot_idx] = self._pending_moving_clip
            self._pending_moving_clip = None
            self.current_frame_data = None
            self._update_captured_previews()
        if self.current_step < self.total_shots:
            self.current_step += 1
            self.retakes_used = 0
            self._update_progress_dots()
            self._start_capture_cycle()
        else:
            self._update_progress_dots()
            self.is_reviewing_final = True
            self.camera.crop = False
            self._rec_blink_timer.stop()
            self._collect_all_shots()
            if len(self.all_shots) > self.shots_per_session:
                self._goto_pick_screen()
            else:
                self.pick_pool = list(range(len(self.all_shots)))
                self._apply_pool_order()
                self._goto_frame_picker()

    def _collect_all_shots(self):
        """all_shots = every photo of the session in shooting order, retakes
        included (each retake right before the take that replaced it)."""
        N = self.shots_per_session
        shots, clips, labels, kept = [], [], [], []
        for i, frame in enumerate(self.captured_frames):
            step = i + 1
            tag = "   bonus" if step > N else ""
            for k, (rf, rc, rs) in enumerate(r for r in self.retake_shots if r[2] == step):
                shots.append(rf); clips.append(rc)
                labels.append(f"#{step}  ulang {k + 1}{tag}")
            kept.append(len(shots))
            shots.append(frame)
            clips.append(self.moving_clips[i] if i < len(self.moving_clips) else None)
            labels.append(f"#{step}{tag}")
        self.all_shots, self.all_clips = shots, clips
        self.shot_labels, self.kept_shot_idx = labels, kept

    def _goto_frame_picker(self):
        self.current_filter = "none"
        icfg = CONFIG.get("individual_photos", {}) or {}
        self.chk_indiv_fx.setChecked(bool(icfg.get("apply_filter", True)))
        self._fp_sel = None
        self._fp_thumb_cache = {}
        self.fp_slot_scroll.verticalScrollBar().setValue(0)
        if hasattr(self, "filter_cards"):
            for k, btn in self.filter_cards.items():
                self._style_filter_card(btn, selected=(k == "none"))
                btn.setChecked(k == "none")
        if self.frame_paths:
            self.overlay_path = self.frame_paths[0]
            QTimer.singleShot(0, lambda: self._set_selected_frame_button(0))
        self._refresh_extra_card()
        self.stack.setCurrentIndex(self.SCREEN_FRAMES)
        QTimer.singleShot(0, self._fit_fp_layout)

    # =============== PICK SCREEN (keep N of N+bonus shots) ===============
    def _build_pick_screen(self):
        screen = QWidget()
        screen.setStyleSheet("background: transparent;")
        self.pick_screen = screen
        self._pick_title = QLabel("Pilih Foto Favoritmu", screen)
        self._pick_title.setAlignment(Qt.AlignCenter)
        self._pick_title.setStyleSheet(
            f"color: {COLORS['ink']}; font-size: 52px; font-weight: 800; "
            f"font-family: '{FONT_DISPLAY}'; background: transparent;")
        self._pick_sub = QLabel("", screen)
        self._pick_sub.setAlignment(Qt.AlignCenter)
        self._pick_sub.setStyleSheet(
            f"color: {COLORS['ink_soft']}; font-size: 20px; font-weight: 500; background: transparent;")
        self._pick_counter = QLabel("", screen)
        self._pick_counter.setAlignment(Qt.AlignCenter)
        self._pick_counter.setStyleSheet(
            f"color: {COLORS['pink_dk']}; background: white; border: 2px solid {COLORS['pink_soft']}; "
            f"border-radius: 22px; font-size: 18px; font-weight: 800;")
        self.pick_scroll = QScrollArea(screen)
        self.pick_scroll.setWidgetResizable(False)
        self.pick_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.pick_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.pick_scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
            "QScrollBar:vertical { width: 12px; background: #FFE1EC; border-radius: 6px; }"
            "QScrollBar::handle:vertical { background: #FF9CC0; border-radius: 6px; min-height: 40px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        )
        self.pick_host = QWidget()
        self.pick_host.setStyleSheet("background: transparent;")
        self.pick_scroll.setWidget(self.pick_host)
        QScroller.grabGesture(self.pick_scroll.viewport(), QScroller.LeftMouseButtonGesture)
        self.pick_cards = []
        self._pick_order = []
        self._pick_pix = []
        self.btn_pick_reset = QPushButton("Ulang Pilih", screen)
        self.btn_pick_reset.setCursor(Qt.PointingHandCursor)
        self.btn_pick_reset.setStyleSheet(f"""
            QPushButton {{
                background: white; color: {COLORS['ink']};
                border: 2px solid {COLORS['pink_soft']};
                border-radius: 34px; font-size: 20px; font-weight: 800; padding: 0 26px;
            }}
            QPushButton:hover {{ background: {COLORS['pink_soft']}; }}
        """)
        self.btn_pick_reset.clicked.connect(self._pick_reset)
        self._add_shadow(self.btn_pick_reset, blur=18, y_offset=4, alpha=110)
        self.btn_pick_next = QPushButton("Lanjut  \u2192", screen)
        self.btn_pick_next.setCursor(Qt.PointingHandCursor)
        self.btn_pick_next.clicked.connect(self._confirm_pick)
        self._add_shadow(self.btn_pick_next, blur=18, y_offset=4, alpha=110)
        self.stack.addWidget(screen)
        self.SCREEN_PICK = self.stack.indexOf(screen)

    @staticmethod
    def _pick_card_style(selected):
        if selected:
            return (f"QPushButton {{ background: white; border: 5px solid {COLORS['pink']}; "
                    f"border-radius: 18px; }}")
        return ("QPushButton { background: rgba(255,255,255,0.92); border: 5px solid transparent; "
                "border-radius: 18px; } QPushButton:hover { border-color: #FFC2D8; }")

    def _pick_max(self):
        """Most photos the user may keep: layout poses + bonus shots."""
        N = self.shots_per_session
        return min(len(self.all_shots), N + max(0, int(getattr(self, "extra_shots", 0) or 0)))

    def _goto_pick_screen(self):
        N = self.shots_per_session
        M = self._pick_max()
        kept = list(getattr(self, "kept_shot_idx", []) or range(len(self.all_shots)))
        self._pick_order = kept[:N]
        self._populate_pick_grid()
        rng = f"{N}" if M <= N else f"{N}\u2013{M}"
        self._pick_sub.setText(
            f"Pilih {rng} dari {len(self.all_shots)} foto  \u00b7  "
            f"{N} pertama masuk frame, urutan bisa diatur lagi nanti")
        self.pick_scroll.verticalScrollBar().setValue(0)
        self.stack.setCurrentIndex(self.SCREEN_PICK)
        QTimer.singleShot(0, self._fit_pick_layout)

    def _populate_pick_grid(self):
        for c in self.pick_cards:
            c.deleteLater()
        self.pick_cards = []
        self._pick_pix = []
        N = self.shots_per_session
        for i, frame in enumerate(self.all_shots):
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w = rgb.shape[:2]
            sc = 560.0 / max(h, w)
            if sc < 1:
                rgb = cv2.resize(rgb, (max(1, int(w * sc)), max(1, int(h * sc))),
                                 interpolation=cv2.INTER_AREA)
            rgb = np.ascontiguousarray(rgb)
            qimg = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0],
                          QImage.Format_RGB888).copy()
            self._pick_pix.append(QPixmap.fromImage(qimg))
            card = QPushButton(self.pick_host)
            card.setCursor(Qt.PointingHandCursor)
            card.clicked.connect(partial(self._on_pick_card, i))
            img = QLabel(card)
            img.setAlignment(Qt.AlignCenter)
            img.setAttribute(Qt.WA_TransparentForMouseEvents)
            img.setStyleSheet("background: transparent; border: none;")
            labels = getattr(self, "shot_labels", None) or []
            cap = QLabel(labels[i] if i < len(labels) else f"#{i + 1}", card)
            cap.setAlignment(Qt.AlignCenter)
            cap.setAttribute(Qt.WA_TransparentForMouseEvents)
            cap.setStyleSheet(f"color: {COLORS['ink_soft']}; font-size: 18px; font-weight: 800; "
                              f"font-family: '{FONT_DISPLAY}'; background: transparent; border: none;")
            badge = QLabel("", card)
            badge.setAlignment(Qt.AlignCenter)
            badge.setAttribute(Qt.WA_TransparentForMouseEvents)
            badge.setFixedSize(48, 48)
            badge.setStyleSheet(f"background: {COLORS['pink']}; color: white; font-size: 24px; "
                                f"font-weight: 900; border-radius: 24px; border: 3px solid white;")
            card.img_label, card.cap_label, card.badge = img, cap, badge
            self._add_shadow(card, blur=22, y_offset=6, alpha=110)
            card.show()
            self.pick_cards.append(card)

    def _fit_pick_layout(self):
        if not hasattr(self, "pick_screen"):
            return
        sw, sh = self.pick_screen.width(), self.pick_screen.height()
        if sw < 10 or sh < 10:
            return
        self._pick_title.setGeometry(0, 30, sw, 68)
        self._pick_sub.setGeometry(0, 100, sw, 30)
        cnt_w = 380
        self._pick_counter.setGeometry((sw - cnt_w) // 2, 140, cnt_w, 44)
        nav_h, bottom = 68, 30
        nav_y = sh - nav_h - bottom
        self.btn_pick_reset.resize(240, nav_h)
        self.btn_pick_next.resize(280, nav_h)
        self.btn_pick_reset.move(sw // 2 - 240 - 14, nav_y)
        self.btn_pick_next.move(sw // 2 + 14, nav_y)
        n = len(self.pick_cards)
        if n == 0:
            return
        top = 200
        view_h = nav_y - top - 16
        self.pick_scroll.setGeometry(40, top, sw - 80, view_h)
        bar_w = 16
        avail_w = sw - 80 - bar_w
        f0 = self.all_shots[0]
        aspect = f0.shape[1] / max(1, f0.shape[0])
        gap, pad, cap_h, margin = 26, 14, 46, 18
        # Big cards in a fixed column count; extra rows scroll vertically.
        # A card is capped a bit shorter than the viewport so the next row
        # peeks in and hints that the list scrolls.
        cols = min(n, 3 if n <= 6 else 4)
        rows = -(-n // cols)
        cw = (avail_w - 2 * margin - gap * (cols - 1)) / cols
        max_card_h = view_h - 2 * margin - (60 if rows > 1 else 0)
        pw = min(cw - 2 * pad, (max_card_h - pad - cap_h) * aspect)
        if pw <= 20:
            return
        pw = int(pw)
        ph = int(pw / aspect)
        card_w, card_h = pw + 2 * pad, ph + pad + cap_h
        grid_h = rows * card_h + (rows - 1) * gap + 2 * margin
        self.pick_host.resize(avail_w, max(grid_h, view_h))
        y0 = margin + max(0, (view_h - grid_h) // 2)
        for i, card in enumerate(self.pick_cards):
            r, c = divmod(i, cols)
            in_row = min(cols, n - r * cols)
            row_w = in_row * card_w + (in_row - 1) * gap
            x = (avail_w - row_w) // 2 + c * (card_w + gap)
            y = y0 + r * (card_h + gap)
            card.setGeometry(x, y, card_w, card_h)
            pix = self._pick_pix[i].scaled(pw, ph, Qt.KeepAspectRatioByExpanding,
                                           Qt.SmoothTransformation)
            if pix.width() > pw or pix.height() > ph:
                pix = pix.copy((pix.width() - pw) // 2, (pix.height() - ph) // 2, pw, ph)
            # Unselected = photo washed toward white. A pre-dimmed pixmap, not
            # QGraphicsOpacityEffect: effects nested inside the card's drop
            # shadow get mispositioned by Qt.
            dim = QPixmap(pix)
            dp = QPainter(dim)
            dp.fillRect(dim.rect(), QColor(255, 255, 255, 140))
            dp.end()
            card.pix_on, card.pix_off = pix, dim
            card.img_label.setGeometry(pad, pad, pw, ph)
            card.img_label.setPixmap(pix)
            card.cap_label.setGeometry(0, pad + ph + 2, card_w, cap_h - 4)
            card.badge.move(card_w - card.badge.width() - 10, 10)
        self._refresh_pick_cards()

    def _refresh_pick_cards(self):
        N = self.shots_per_session
        for i, card in enumerate(self.pick_cards):
            sel = i in self._pick_order
            card.setStyleSheet(self._pick_card_style(sel))
            if sel:
                card.badge.setText(str(self._pick_order.index(i) + 1))
                card.badge.show()
                card.badge.raise_()
            else:
                card.badge.hide()
            on = getattr(card, "pix_on", None)
            if on is not None:
                card.img_label.setPixmap(on if sel else card.pix_off)
        k = len(self._pick_order)
        M = self._pick_max()
        extra = f"  (maks {M})" if M > N else ""
        self._pick_counter.setText(f"{k} dipilih  \u00b7  min {N}{extra}")
        ready = (N <= k <= M)
        self.btn_pick_next.setEnabled(ready)
        if ready:
            self.btn_pick_next.setStyleSheet(f"""
                QPushButton {{
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                        stop:0 {COLORS['pink']}, stop:1 {COLORS['lilac']});
                    color: white; border: 3px solid white;
                    border-radius: 34px; font-size: 22px; font-weight: 900;
                }}
                QPushButton:hover {{ background: {COLORS['pink_dk']}; }}
            """)
        else:
            self.btn_pick_next.setStyleSheet("""
                QPushButton { background: #EFE3EA; color: #C4B2C0; border: 3px solid white;
                              border-radius: 34px; font-size: 22px; font-weight: 900; }
            """)

    def _on_pick_card(self, i):
        M = self._pick_max()
        if i in self._pick_order:
            self._pick_order.remove(i)
        elif len(self._pick_order) < M:
            self._pick_order.append(i)
        else:
            # Already full: swap out the most recent pick so a tap always
            # does something visible.
            self._pick_order[-1] = i
        self._refresh_pick_cards()

    def _pick_reset(self):
        self._pick_order = []
        self._refresh_pick_cards()

    def _confirm_pick(self):
        if not (self.shots_per_session <= len(self._pick_order) <= self._pick_max()):
            return
        self.pick_pool = list(self._pick_order)
        self._apply_pool_order()
        LOG.info(f"[PICK] kept shots {[i + 1 for i in self.pick_pool]} of {len(self.all_shots)}")
        self._goto_frame_picker()

    def _update_captured_previews(self):
        for idx, slot in enumerate(self.slot_widgets):
            if slot.image_label is not None:
                slot.image_label.deleteLater()
                slot.image_label = None
            if idx < len(self.captured_frames):
                frame = self.captured_frames[idx]
                slot.placeholder_text.hide()
                slot.setStyleSheet("background: white; border: 2px solid white; border-radius: 14px;")
                sw_s = max(slot.width() - 4, 10)
                sh_s = max(slot.height() - 4, 10)
                cache_key = f"{idx}_{sw_s}x{sh_s}"
                if cache_key not in self.thumbnail_cache:
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    qimg = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format_RGB888)
                    pix = QPixmap.fromImage(qimg).scaled(sw_s, sh_s, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                    if pix.width() > sw_s or pix.height() > sh_s:
                        cx = (pix.width() - sw_s) // 2
                        cy = (pix.height() - sh_s) // 2
                        pix = pix.copy(cx, cy, sw_s, sh_s)
                    self.thumbnail_cache[cache_key] = pix
                img_lbl = QLabel(slot)
                img_lbl.setAlignment(Qt.AlignCenter)
                img_lbl.setStyleSheet("background: transparent; border-radius: 12px;")
                img_lbl.setPixmap(self.thumbnail_cache[cache_key])
                img_lbl.setGeometry(2, 2, sw_s, sh_s)
                img_lbl.setScaledContents(False)
                img_lbl.show()
                slot.image_label = img_lbl
            else:
                slot.setStyleSheet("""
                    background: rgba(255, 255, 255, 0.85);
                    border: 3px dashed #FFB3CD;
                    border-radius: 14px;
                """)
                slot.placeholder_text.setText(f"{idx+1}")
                slot.placeholder_text.show()

    def _detect_slot_rects(self, overlay_pil, target_count=None, path=None):
        if target_count is None:
            target_count = self.shots_per_session
        try:
            order_key = (self.current_layout or {}).get("slot_order") or CONFIG.get("slot_order", "legacy")
        except Exception:
            order_key = CONFIG.get("slot_order", "legacy")
        # Cache by the frame actually measured (not whichever frame is selected).
        src = path or self.overlay_path or ""
        path_key = f"{src}|{target_count}|{order_key}"
        if path_key in self._slot_rect_cache:
            return self._slot_rect_cache[path_key]
        arr = np.array(overlay_pil)
        h, w = arr.shape[:2]
        alpha = arr[:, :, 3]
        rgb = arr[:, :, :3]
        # Photo holes: mostly transparent (alpha < 128). Frames exported from
        # Canva/Photoshop often leave holes slightly opaque instead of 0.
        transparent_mask = (alpha < SLOT_ALPHA_MAX)
        if transparent_mask.any():
            slot_mask = transparent_mask
        else:
            brightness = rgb.mean(axis=2)
            slot_mask = brightness < 25
        if not slot_mask.any():
            self._slot_rect_cache[path_key] = None
            return None
        mask_u8 = slot_mask.astype(np.uint8) * 255
        num_labels, _, stats, _ = cv2.connectedComponentsWithStats(mask_u8, connectivity=4)
        if num_labels <= 1:
            self._slot_rect_cache[path_key] = None
            return None
        min_area = 0.01 * w * h
        candidates = []
        for lbl in range(1, num_labels):
            x, y, cw, ch, area = stats[lbl]
            if area < min_area: continue
            if cw > 0.95 * w and ch > 0.95 * h: continue
            candidates.append((x, y, cw, ch, area))
        candidates.sort(key=lambda r: r[4], reverse=True)
        fname = Path(str(src)).name
        if len(candidates) < target_count:
            if not candidates:
                self._slot_rect_cache[path_key] = None
                return None
            # Use the frame's own holes rather than a generic grid that ignores it.
            LOG.warning(f"[FRAME] {fname}: {len(candidates)} photo slots but layout "
                        f"has {target_count} poses - extra photos are left out")
        elif len(candidates) > target_count:
            # Extra holes the same size as real slots are slots too (frame made
            # for more poses than the layout): fill them instead of leaving
            # them empty. Much smaller holes stay decoration.
            nth = candidates[target_count - 1][4]
            extra = [c for c in candidates[target_count:] if c[4] >= 0.8 * nth]
            if extra:
                LOG.warning(f"[FRAME] {fname}: {target_count + len(extra)} photo slots "
                            f"but layout has {target_count} poses - repeating photos "
                            f"(set \"poses\": {target_count + len(extra)} for this layout)")
            candidates = candidates[:target_count] + extra
        rects = [(x, y, cw, ch) for (x, y, cw, ch, _) in candidates]
        order = None
        try:
            order = (self.current_layout or {}).get("slot_order")
        except Exception:
            order = None
        if not order:
            order = CONFIG.get("slot_order", "legacy")
        ordered = self._order_slots(rects, order, w, h, target_count)
        self._slot_rect_cache[path_key] = ordered
        return ordered

    @staticmethod
    def _order_slots(rects, order, w, h, target_count):
        rects_sorted = sorted(rects, key=lambda r: (r[1] + r[3] / 2))
        rows = []
        row_threshold = h * 0.05
        for r in rects_sorted:
            cy = r[1] + r[3] / 2
            placed = False
            for row in rows:
                row_cy = sum(rr[1] + rr[3] / 2 for rr in row) / len(row)
                if abs(cy - row_cy) < row_threshold:
                    row.append(r)
                    placed = True
                    break
            if not placed:
                rows.append([r])
        if order == "row_lr_tb":
            out = []
            for row in rows:
                row.sort(key=lambda r: r[0])
                out.extend(row)
            return out
        if order == "row_rl_tb":
            out = []
            for row in rows:
                row.sort(key=lambda r: r[0], reverse=True)
                out.extend(row)
            return out
        if order in ("col_tb_lr", "col_tb_rl"):
            rects_x = sorted(rects, key=lambda r: (r[0] + r[2] / 2))
            cols = []
            col_threshold = w * 0.05
            for r in rects_x:
                cx = r[0] + r[2] / 2
                placed = False
                for col in cols:
                    col_cx = sum(rr[0] + rr[2] / 2 for rr in col) / len(col)
                    if abs(cx - col_cx) < col_threshold:
                        col.append(r)
                        placed = True
                        break
                if not placed:
                    cols.append([r])
            if order == "col_tb_rl":
                cols = list(reversed(cols))
            out = []
            for col in cols:
                col.sort(key=lambda r: r[1])
                out.extend(col)
            return out
        out = []
        for row in rows:
            row.sort(key=lambda r: r[0])
            out.extend(row)
        if target_count == 4 and len(out) == 4:
            x_median = sorted(r[0] + r[2] / 2 for r in out)[len(out) // 2]
            left  = sorted([r for r in out if r[0] + r[2] / 2 <= x_median], key=lambda r: r[1])
            right = sorted([r for r in out if r[0] + r[2] / 2 >  x_median], key=lambda r: r[1])
            if len(left) == 2 and len(right) == 2:
                out = left + right
        return out

    def _build_final_canvas(self):
        if not self.overlay_path:
            return None
        overlay = Image.open(self.overlay_path).convert("RGBA")
        ow, oh = overlay.size
        slot_to_pose = self._slot_to_pose_mapping()
        slot_count = len(slot_to_pose) if slot_to_pose else self.shots_per_session
        rects = self._detect_slot_rects(overlay, target_count=slot_count, path=self.overlay_path)
        if rects is None:
            LOG.warning(f"[FRAME] {Path(str(self.overlay_path)).name}: no photo holes "
                        f"found - using a plain grid")
            rects = fallback_slot_rects(ow, oh, slot_count)
        ov = np.array(overlay)
        alpha_arr = ov[:, :, 3]
        has_transparent_slots = bool((alpha_arr < SLOT_ALPHA_MAX).any())
        if has_transparent_slots:
            # Make the holes fully clear so a half-opaque hole can't wash the
            # photo out with white; anti-aliased edges (alpha >= 128) stay.
            ov[:, :, 3] = np.where(alpha_arr < SLOT_ALPHA_MAX, 0, alpha_arr)
            overlay = Image.fromarray(ov, "RGBA")
        slot_pose_pairs = []
        for slot_idx, rect in enumerate(rects):
            if slot_to_pose:
                pose_idx = slot_to_pose[slot_idx] if slot_idx < len(slot_to_pose) else slot_idx
            else:
                pose_idx = slot_idx
            if pose_idx >= len(self.captured_frames) and self.captured_frames:
                pose_idx %= len(self.captured_frames)   # more slots than photos
            if 0 <= pose_idx < len(self.captured_frames):
                slot_pose_pairs.append((rect, pose_idx))
        canvas = Image.new("RGBA", (ow, oh), (255, 255, 255, 255))
        flt = getattr(self, "current_filter", "none")
        bty = float(CONFIG.get("beautify_strength", 0.0))
        if has_transparent_slots:
            for (rect, pose_idx) in slot_pose_pairs:
                x, y, cw, ch = rect
                if cw < 1 or ch < 1: continue
                f = self.captured_frames[pose_idx]
                img_bgr = crop_center_to_aspect(f, cw, ch, fit_mode=getattr(self, "fit_mode", "cover"))
                if bty > 0: img_bgr = beautify(img_bgr, bty)
                img_bgr = apply_filter(img_bgr, flt)
                img = Image.fromarray(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB))
                canvas.paste(img, (x, y))
            canvas.paste(overlay, (0, 0), overlay)
        else:
            canvas.paste(overlay, (0, 0), overlay)
            for (rect, pose_idx) in slot_pose_pairs:
                x, y, cw, ch = rect
                if cw < 1 or ch < 1: continue
                f = self.captured_frames[pose_idx]
                img_bgr = crop_center_to_aspect(f, cw, ch, fit_mode=getattr(self, "fit_mode", "cover"))
                if bty > 0: img_bgr = beautify(img_bgr, bty)
                img_bgr = apply_filter(img_bgr, flt)
                img = Image.fromarray(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB))
                canvas.paste(img, (x, y))
        return canvas

    def _slot_to_pose_mapping(self):
        m = self.current_layout.get("slot_to_pose") if self.current_layout else None
        if not m or not isinstance(m, list):
            return None
        N = self.shots_per_session
        try:
            m = [int(x) for x in m]
        except (TypeError, ValueError):
            return None
        if not all(0 <= x < N for x in m):
            return None
        return m

    # ============= NEW FLOW: PRINT → DONE → BG (BTS + LIVE + UPLOAD) =============
    def _goto_print(self):
        """LANJUT from frame picker. New flow:
          1. Build final canvas (in memory)
          2. Show 'Menyiapkan folder...' screen
          3. Create GDrive subfolder + upload thank-you message (SYNC)
          4. Get folder URL → use for QR stamp
          5. Print STAMPED copy (in-memory, never saved)
          6. Save CLEAN PNG to disk
          7. Show Done screen
          8. Background: gen BTS + LIVE videos → upload photo + videos
        """
        try:
            self.temp_canvas = self._build_final_canvas()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Gagal komposit: {e}")
            self._reset_to_home()
            return
        if self.temp_canvas is None:
            QMessageBox.critical(self, "Error", "Canvas final kosong")
            self._reset_to_home()
            return

        self._release_camera()   # end the camera session; next session reopens it
        self.stack.setCurrentIndex(self.SCREEN_PRINT)
        self.print_progress.setValue(0)
        self.print_pct.setText("0%")
        self.print_title.setText("Menyiapkan folder…")
        self.print_status.setText("Membuat folder GDrive & upload pesan ucapan…")

        # Per-session identity.
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self._session_password = f"{random.randint(0, 999999):06d}"
        rand_suffix = f"{random.randint(0, 0xFFFF):04x}"
        gd_cfg = _load_gdrive_config()
        event_name = (gd_cfg.get("event_name") or "Session").strip().replace(" ", "-")
        self._session_id = f"{event_name}-{ts}-{rand_suffix}"

        self._pending_ts = ts
        self._pending_layout = dict(self.current_layout) if self.current_layout else None
        self._pending_folder_id = None
        self._pending_folder_url = None
        self._pending_uploader = None
        self._pending_upload_reason = None
        self._pending_all_shots = list(self.all_shots or self.captured_frames)
        self._pending_picked = list(self.picked_indices or range(len(self._pending_all_shots)))
        self._pending_frame_n = self.shots_per_session
        self._pending_filter = getattr(self, "current_filter", "none")
        self._pending_indiv_fx = self.chk_indiv_fx.isChecked()
        self._pending_copies = 1 + max(0, int(getattr(self, "extra_prints", 0) or 0))

        # Run folder-prep in BG thread; when done, continue to print step.
        threading.Thread(target=self._prep_folder_then_print, daemon=True).start()

    def _prep_folder_then_print(self):
        """BG step 1: Create GDrive subfolder + upload thank-you.
        Then trigger print on UI thread."""
        cfg = _load_gdrive_config()
        folder_url = None
        folder_id  = None
        uploader   = None
        reason     = None

        if cfg.get("enabled"):
            folder_cfg_id = cfg.get("folder_id", "").strip()
            if folder_cfg_id and folder_cfg_id != "PASTE_YOUR_FOLDER_ID_HERE":
                try:
                    uploader = GDriveUploader(
                        cfg["client_secrets_path"], folder_cfg_id,
                        token_path=cfg.get("token_path", "oauth_token.json"))
                    sub = uploader.create_folder(self._session_id)
                    if sub:
                        folder_id  = sub["id"]
                        folder_url = sub["url"]
                        LOG.info(f"[GDRIVE] subfolder created: {folder_url}")
                        # Upload thank-you message (PNG preferred, else TXT).
                        thanks_path = self._find_thank_you_file()
                        if thanks_path:
                            try:
                                uploader.upload_file(
                                    thanks_path,
                                    remote_name=thanks_path.name,
                                    parent_id=folder_id)
                                LOG.info(f"[GDRIVE] thank-you uploaded: {thanks_path.name}")
                            except Exception as e:
                                LOG.warning(f"[GDRIVE] thank-you upload failed: {e}")
                        else:
                            LOG.info("[GDRIVE] no thank_you.png / thank_you.txt found")
                    else:
                        reason = (getattr(uploader, "_error", None)
                                  or "gagal membuat folder Drive")
                        LOG.warning(f"[GDRIVE] subfolder creation returned None: {reason}")
                except Exception as e:
                    reason = f"Drive error: {e}"
                    LOG.error(f"[GDRIVE] folder prep failed: {e}")
            else:
                reason = "Drive belum diatur (isi gdrive.folder_id di config.json)"
                LOG.warning("[GDRIVE] folder_id not configured")
        else:
            reason = "upload Drive dimatikan di config"
            LOG.info("[GDRIVE] disabled in config")

        # Store results (BG thread → main thread access ok since we only
        # read after this thread signals via QTimer).
        self._pending_folder_id = folder_id
        self._pending_folder_url = folder_url
        self._pending_uploader = uploader
        self._pending_upload_reason = reason

        # Continue on UI thread.
        QTimer.singleShot(0, self._after_folder_prep)

    def _find_thank_you_file(self):
        """Look for thank_you.png first, then thank_you.txt, in BASE_DIR."""
        png = BASE_DIR / "thank_you.png"
        if png.exists():
            return png
        txt = BASE_DIR / "thank_you.txt"
        if txt.exists():
            return txt
        return None

    def _after_folder_prep(self):
        """UI step: now we have (maybe) folder_url. Stamp + print + save."""
        ts = self._pending_ts
        folder_url = self._pending_folder_url
        canvas_for_print = self.temp_canvas
        password = self._session_password
        layout_snap = self._pending_layout
        print_enabled = bool(CONFIG.get("printing", {}).get("enabled", True))
        copies = max(1, int(getattr(self, "_pending_copies", 1) or 1))
        up_reason = getattr(self, "_pending_upload_reason", None) or "periksa koneksi internet"

        # Update print screen.
        if print_enabled:
            self.print_title.setText(f"Mencetak {copies} lembar…" if copies > 1 else "Mencetak foto…")
            if folder_url:
                self.print_status.setText("Foto sedang dicetak — QR siap discan setelah cetak")
            else:
                self.print_status.setText(f"Soft file tidak ter-upload ({up_reason}). Foto tetap dicetak & disimpan.")
        else:
            self.print_title.setText("Menyimpan foto…")
            self.print_status.setText("Print dimatikan — foto disimpan dengan QR + kode sesi")

        # ---- Print STAMPED in-memory copy (only when print enabled) ----
        if print_enabled:
            def _do_print(copies=copies):
                for n in range(copies):
                    try:
                        ok = print_session_image(canvas_for_print, layout=layout_snap,
                                                 password=password, qr_url=folder_url)
                        LOG.info(f"[PRINT] copy {n + 1}/{copies} sent={ok}")
                    except Exception as e:
                        LOG.error(f"[PRINT] copy {n + 1}/{copies} uncaught: {e}")
            threading.Thread(target=_do_print, daemon=True).start()

        # ---- Save PNG ----
        # When printing is ENABLED  → save CLEAN (printed copy carries the stamp).
        # When printing is DISABLED → save the STAMPED copy locally (QR + password),
        #   since there's no printout to carry it.
        try:
            png_path = SAVE_DIR / f"FINAL_{ts}.png"
            if print_enabled:
                self.temp_canvas.convert("RGB").save(png_path)
                LOG.info(f"[SAVE] clean PNG: {png_path}")
            else:
                stamped = stamp_password_on_image(self.temp_canvas, password, qr_url=folder_url)
                stamped.convert("RGB").save(png_path)
                LOG.info(f"[SAVE] stamped PNG (print disabled): {png_path}")
            self._final_png_path = png_path
            self._pending_png_path = png_path
        except Exception as e:
            LOG.error(f"[SAVE] PNG failed: {e}")
            self._pending_png_path = None

        # Every shot as its own JPG (BG); upload step waits on this job.
        self._indiv_job = self._start_individuals_job(ts)

        # Start progress bar → Done screen.
        self._print_done_target = 100
        self.print_timer.start()

    def _start_individuals_job(self, ts):
        """Save every shot of the session (kept + bonus) as its own JPG under
        captures/SESSION_<ts>/ off the UI thread. Returns a job the upload
        step waits on: {"done": Event, "files": [(name, path)]}."""
        # files: every shot saved locally (backup). upload: only the photos the
        # customer picked on the pick screen go to Google Drive.
        job = {"done": threading.Event(), "files": [], "upload": []}
        icfg = CONFIG.get("individual_photos", {}) or {}
        shots = list(getattr(self, "_pending_all_shots", []) or [])
        if not icfg.get("save", True) or not shots:
            job["done"].set()
            return job
        picked = list(getattr(self, "_pending_picked", []) or [])
        frame_n = int(getattr(self, "_pending_frame_n", len(picked)) or len(picked))
        # Customer's checkbox on the frame screen (default: config apply_filter).
        styled = bool(getattr(self, "_pending_indiv_fx", icfg.get("apply_filter", True)))
        flt = getattr(self, "_pending_filter", "none") if styled else "none"
        bty = float(CONFIG.get("beautify_strength", 0.0))
        quality = int(icfg.get("jpeg_quality", 95))
        out_dir = SAVE_DIR / f"SESSION_{ts}"

        def _work():
            try:
                out_dir.mkdir(parents=True, exist_ok=True)
                for i, frame in enumerate(shots):
                    try:
                        img = beautify(frame, bty) if bty > 0 else frame
                        img = apply_filter(img, flt)
                        name = f"foto_{i + 1:02d}"
                        if i in picked:
                            rank = picked.index(i)
                            name += (f"_dipilih-{rank + 1}" if rank < frame_n else "_favorit")
                        name += ".jpg"
                        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
                        if not ok:
                            LOG.warning(f"[INDIV] encode failed for shot {i + 1}")
                            continue
                        path = out_dir / name
                        path.write_bytes(buf.tobytes())
                        job["files"].append((name, path))
                        if i in picked:
                            job["upload"].append((name, path))
                    except Exception as e:
                        LOG.error(f"[INDIV] shot {i + 1} failed: {e}")
                LOG.info(f"[INDIV] saved {len(job['files'])}/{len(shots)} to {out_dir}")
            finally:
                job["done"].set()

        threading.Thread(target=_work, daemon=True).start()
        return job

    def _tick_print(self):
        cur = self.print_progress.value()
        if cur < self._print_done_target:
            cur = min(cur + CONFIG["print_bar_step"], self._print_done_target)
            self.print_progress.setValue(cur)
            self.print_pct.setText(f"{cur}%")
        else:
            self.print_timer.stop()
            QTimer.singleShot(300, self._goto_done)

    def _render_done_qr(self, folder_url):
        """Render the GDrive folder URL as a QR code into done_qr_label.
        If url is None (upload failed), show a placeholder + status text."""
        if not folder_url:
            self.done_qr_label.clear()
            self.done_qr_label.setText("")
            self.done_qr_label.setStyleSheet(
                "background: rgba(255,255,255,0.7); color: #FFB3CD; "
                "border: 3px dashed #FFC2D8; border-radius: 22px; font-size: 72px; font-weight: 900;")
            reason = getattr(self, "_pending_upload_reason", None) or "periksa koneksi internet"
            self.done_qr_status.setText(f"Soft file belum tersedia ({reason}). "
                                        "Foto tetap dicetak & disimpan di komputer.")
            return
        try:
            import qrcode
            qr = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=10, border=2,
            )
            qr.add_data(folder_url)
            qr.make(fit=True)
            qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
            qsize = self.done_qr_label.width() - 32
            qr_img = qr_img.resize((qsize, qsize), Image.NEAREST)
            # Bypass PIL.ImageQt (broken on Python 3.13) — convert via raw bytes.
            data = qr_img.tobytes("raw", "RGB")
            qimg = QImage(data, qr_img.width, qr_img.height, qr_img.width * 3, QImage.Format_RGB888)
            qpix = QPixmap.fromImage(qimg.copy())
            self.done_qr_label.setStyleSheet(f"background: white; border: 4px solid {COLORS['pink_soft']}; border-radius: 22px; padding: 16px;")
            self.done_qr_label.setPixmap(qpix)
            self.done_qr_status.setText("Scan QR ini untuk download foto & video. "
                                        "Video mungkin masih diupload — tunggu sebentar lalu refresh.")
        except Exception as e:
            LOG.error(f"[DONE] QR render failed: {e}")
            self.done_qr_label.clear()
            self.done_qr_label.setText("QR")
            self.done_qr_status.setText(f"QR gagal dirender: {e}")

    def _goto_done(self):
        # Foto thumb.
        if self.temp_canvas is not None:
            rgb = self.temp_canvas.convert("RGB")
            data = rgb.tobytes("raw", "RGB")
            qimg = QImage(data, rgb.width, rgb.height, QImage.Format_RGB888)
            pix = QPixmap.fromImage(qimg).scaled(
                self.done_thumb.contentsRect().width(), self.done_thumb.contentsRect().height(),
                Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.done_thumb.setPixmap(pix)
        # Password.
        self.done_password_label.setText(getattr(self, "_session_password", "------"))
        # QR — only if folder URL was successfully created during prep.
        folder_url = getattr(self, "_pending_folder_url", None)
        self._render_done_qr(folder_url)

        self.done_status.setText("GENERATE VIDEO…")
        self.stack.setCurrentIndex(self.SCREEN_DONE)

        # Record the session count (local persistent append-only log).
        try:
            stats = _record_session(extra={
                "session_id": getattr(self, "_session_id", None),
                "layout": (self._pending_layout or {}).get("id") if self._pending_layout else None,
                "folder_url": getattr(self, "_pending_folder_url", None),
                "shots_total": len(getattr(self, "_pending_all_shots", []) or []),
                "picked": [i + 1 for i in (getattr(self, "_pending_picked", []) or [])],
                "copies": getattr(self, "_pending_copies", 1),
            })
            self._last_stats = stats
        except Exception as e:
            LOG.error(f"[STATS] record failed: {e}")
            stats = None

        # Upload the stats file to the GDrive ROOT folder (overwrite each time)
        # so the owner can check session count remotely from a phone.
        uploader = getattr(self, "_pending_uploader", None)
        if uploader is not None and STATS_FILE.exists():
            def _upload_stats():
                try:
                    uploader.upload_file(
                        STATS_FILE,
                        remote_name="_SESSION_STATS.json",
                        parent_id=None)  # None = root configured folder
                    LOG.info("[STATS] uploaded to GDrive root")
                except Exception as e:
                    LOG.warning(f"[STATS] upload failed: {e}")
            threading.Thread(target=_upload_stats, daemon=True).start()

        # ---- Kick off background: BTS + LIVE → upload ----
        ts = self._pending_ts
        png_path = self._pending_png_path
        layout_snap = self._pending_layout
        has_bts = bool(self.bts_buffer)
        has_moving = any(c is not None and len(c) > 0 for c in self.moving_clips)
        bts_mp4 = SAVE_DIR / f"BTS_{ts}.mp4"
        mov_mp4 = SAVE_DIR / f"LIVE_{ts}.mp4"
        slot_to_pose = self._slot_to_pose_mapping()
        slot_count = len(slot_to_pose) if slot_to_pose else self.shots_per_session
        slot_rects = None
        if self.overlay_path:
            try:
                ov = Image.open(self.overlay_path).convert("RGBA")
                slot_rects = self._detect_slot_rects(ov, target_count=slot_count, path=self.overlay_path)
            except Exception:
                slot_rects = None

        if has_bts or has_moving:
            self._bg_saver = BackgroundSaverThread(
                self, bts_mp4, mov_mp4,
                self.bts_buffer, self.moving_clips, self.captured_frames,
                self.video_fps, self.bts_speed, self.bts_max_duration_s,
                self.moving_speed, self.overlay_path, slot_rects,
                slot_to_pose=slot_to_pose,
                fit_mode=getattr(self, "fit_mode", "cover"),
                moving_clip_s=self.moving_clip_s,
            )
            self._bg_saver.finished_save.connect(
                partial(self._on_videos_done, png_path=png_path,
                        bts_mp4=bts_mp4 if has_bts else None,
                        mov_mp4=mov_mp4 if has_moving else None))
            self._bg_saver.start()
        else:
            # No videos to encode — go straight to upload.
            self._on_videos_done([], [], png_path=png_path, bts_mp4=None, mov_mp4=None)

    def _on_videos_done(self, paths, errors, png_path=None, bts_mp4=None, mov_mp4=None):
        if errors:
            LOG.warning(f"[SAVE] video errors: {errors}")
        self.done_status.setText("UPLOADING TO DRIVE…")
        files_to_upload = []
        if png_path is not None and Path(png_path).exists():
            files_to_upload.append(("foto.png", Path(png_path)))
        if bts_mp4 is not None and Path(bts_mp4).exists():
            files_to_upload.append(("behind-the-scene.mp4", Path(bts_mp4)))
        if mov_mp4 is not None and Path(mov_mp4).exists():
            files_to_upload.append(("moving-picture.mp4", Path(mov_mp4)))
        indiv_job = getattr(self, "_indiv_job", None)
        upload_indiv = bool((CONFIG.get("individual_photos", {}) or {}).get("upload", True))
        if not files_to_upload and not (indiv_job and upload_indiv):
            self.done_status.setText("DONE")
            return

        # Reuse the subfolder + uploader created during the prep step.
        # If prep failed (no creds / no internet), skip upload entirely; the
        # files are still saved locally under captures/.
        folder_id = getattr(self, "_pending_folder_id", None)
        uploader  = getattr(self, "_pending_uploader", None)
        if not folder_id or uploader is None:
            reason = getattr(self, "_pending_upload_reason", None) or "periksa koneksi internet"
            LOG.warning(f"[GDRIVE] no folder/uploader from prep \u2014 skipping upload ({reason})")
            self.done_status.setText(f"SOFT FILE TIDAK TER-UPLOAD \u2014 {reason}")
            return
        folder_url = self._pending_folder_url

        def _upload_session():
            queue = [(n, p, folder_id) for n, p in files_to_upload]
            if indiv_job is not None and upload_indiv:
                self._ui(lambda: self.done_status.setText(
                    "MENYIAPKAN FOTO INDIVIDU\u2026"))
                indiv_job["done"].wait(timeout=180)
                if indiv_job["upload"]:
                    sub = uploader.create_folder("foto-individu", parent_id=folder_id)
                    target = sub["id"] if sub else folder_id
                    queue += [(n, p, target) for n, p in indiv_job["upload"]]
            total = len(queue)
            failed = 0
            for i, (remote_name, local, parent) in enumerate(queue, 1):
                self._ui(lambda i=i, t=total, n=remote_name:
                    self.done_status.setText(f"UPLOADING {n}  ({i}/{t})"))
                if uploader.upload_file(local, remote_name=remote_name, parent_id=parent) is None:
                    failed += 1
            if failed:
                self._ui(lambda f=failed, t=total: self.done_status.setText(
                    f"{f}/{t} FILE GAGAL UPLOAD"))
            else:
                self._ui(lambda: self.done_status.setText("ALL FILES UPLOADED"))
            LOG.info(f"[GDRIVE] session done: {folder_url} ({total - failed}/{total} ok)")

        threading.Thread(target=_upload_session, daemon=True).start()

    def _reset_to_home(self):
        self.is_reviewing = False
        self.is_reviewing_final = False
        self.session_started = False
        self.temp_canvas = None
        self.captured_frames = []
        self._release_camera()
        self.bts_buffer = []
        self.rolling_buffer = []
        self.moving_clips = [None] * self.shots_per_session
        self._pending_moving_clip = None
        self.all_shots = []
        self.all_clips = []
        self.picked_indices = []
        self.pick_pool = []
        self.retake_shots = []
        self.shot_labels = []
        self.kept_shot_idx = []
        self.extra_prints = 0
        if self.print_timer.isActive():
            self.print_timer.stop()
        if self._rec_blink_timer.isActive():
            self._rec_blink_timer.stop()
        self.preview_label.setStyleSheet(self._preview_style_live)
        self.preview_label.setPixmap(QPixmap())
        self._reset_slots()
        self.stack.setCurrentIndex(self.SCREEN_HOME)

    def closeEvent(self, event):
        self._release_camera()
        self._wait_old_cameras()
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
        elif event.key() == Qt.Key_F12:
            # Hidden admin: show session stats popup.
            self._show_session_stats()

    def _show_session_stats(self):
        stats = {"total": 0, "first_session": "-", "last_session": "-", "sessions": []}
        if STATS_FILE.exists():
            try:
                with open(STATS_FILE, "r", encoding="utf-8-sig") as f:
                    stats = json.load(f)
            except Exception as e:
                QMessageBox.warning(self, "Stats", f"Gagal baca stats: {e}")
                return
        sessions = stats.get("sessions", [])
        # Last 15 sessions, newest first.
        recent = list(reversed(sessions))[:15]
        lines = []
        for s in recent:
            layout = s.get("layout") or "?"
            lines.append(f"   #{s.get('n')}  {s.get('timestamp')}  [{layout}]")
        log_block = "\n".join(lines) or "   (belum ada sesi)"
        msg = (
            f"TOTAL SESI: {stats.get('total', 0)}\n\n"
            f"Sesi pertama: {stats.get('first_session', '-')}\n"
            f"Sesi terakhir: {stats.get('last_session', '-')}\n\n"
            f"15 sesi terakhir:\n{log_block}\n\n"
            f"File lengkap: {STATS_FILE}\n"
            f"(juga ke-upload ke GDrive sebagai _SESSION_STATS.json)"
        )
        QMessageBox.information(self, "Log Sesi (Admin)", msg)


def _apply_ui_scaling(app):
    """Auto-scale the UI for laptops with screens smaller than the 1920x1080
    design target. Monkey-patches QWidget.setStyleSheet (regex-scales every
    `Npx` value in CSS) plus the common fixed-size setters so existing
    hardcoded sizes shrink proportionally. Called once before MainWindow
    is built. On full-HD+ screens it's a no-op."""
    import re
    screen = app.primaryScreen()
    if screen is None:
        return
    geom = screen.availableGeometry()
    sw, sh = geom.width(), geom.height()
    # Reference: design was sized for 1920x1080. Pick the tighter axis.
    scale = min(sw / 1920.0, sh / 1080.0)
    # Don't upscale on big screens; don't shrink below 0.55 (gets unreadable).
    if scale >= 0.98:
        LOG.info(f"[UI] screen {sw}x{sh} — no scaling needed")
        return
    scale = max(0.55, scale)
    LOG.info(f"[UI] screen {sw}x{sh} — applying UI scale {scale:.3f}")

    _px_re = re.compile(r'(\d+)px')
    def _scale_css(css):
        if not css:
            return css
        return _px_re.sub(
            lambda m: f"{max(1, int(round(int(m.group(1)) * scale)))}px",
            css)

    # Patch setStyleSheet so every CSS string passing through gets scaled.
    _orig_setStyleSheet = QWidget.setStyleSheet
    def _patched_setStyleSheet(self, css):
        _orig_setStyleSheet(self, _scale_css(css))
    QWidget.setStyleSheet = _patched_setStyleSheet

    # Scale all fixed/min/max size setters.
    def _scale_int(n):
        try:
            return max(1, int(round(float(n) * scale)))
        except (TypeError, ValueError):
            return n

    for method_name in ("setFixedHeight", "setFixedWidth",
                        "setMinimumHeight", "setMinimumWidth",
                        "setMaximumHeight", "setMaximumWidth"):
        orig = getattr(QWidget, method_name)
        def _make(orig_fn):
            def _patched(self, n):
                return orig_fn(self, _scale_int(n))
            return _patched
        setattr(QWidget, method_name, _make(orig))

    _orig_setFixedSize = QWidget.setFixedSize
    def _patched_setFixedSize(self, *args):
        if len(args) == 1:
            sz = args[0]
            if isinstance(sz, QSize):
                sz = QSize(_scale_int(sz.width()), _scale_int(sz.height()))
            _orig_setFixedSize(self, sz)
        else:
            w, h = args
            _orig_setFixedSize(self, _scale_int(w), _scale_int(h))
    QWidget.setFixedSize = _patched_setFixedSize

    # Spacing + content margins on layouts.
    from PyQt5.QtWidgets import QLayout
    _orig_setSpacing = QLayout.setSpacing
    QLayout.setSpacing = lambda self, n, _o=_orig_setSpacing: _o(self, _scale_int(n))
    _orig_setContentsMargins = QLayout.setContentsMargins
    def _patched_setContentsMargins(self, *args):
        if len(args) == 4:
            l, t, r, b = args
            _orig_setContentsMargins(self,
                _scale_int(l), _scale_int(t), _scale_int(r), _scale_int(b))
        else:
            _orig_setContentsMargins(self, *args)
    QLayout.setContentsMargins = _patched_setContentsMargins


def _install_exit_camera_cleanup(app, win):
    """Close the camera session cleanly on every way out of the app, like
    test_canon.py's try/finally: normal quit, console window closed, Ctrl+C,
    logoff/shutdown. A session left open keeps the Canon body busy
    (EDS_ERR 0x81) until it is power-cycled."""
    def _on_quit():
        try:
            win._release_camera()
            win._wait_old_cameras()
        except Exception as e:
            LOG.warning(f"[EXIT] camera release failed: {e}")
    app.aboutToQuit.connect(_on_quit)
    if platform.system() != "Windows":
        return
    try:
        import ctypes
        from ctypes import wintypes
        HandlerRoutine = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

        def _console_handler(ctrl_type):
            # Runs on its own thread; Windows allows a few seconds here.
            LOG.info(f"[EXIT] console event {ctrl_type} - closing camera")
            try:
                from canon_edsdk import CanonCameraThread
                CanonCameraThread.stop_all(timeout_ms=4000)
            except Exception as e:
                LOG.warning(f"[EXIT] canon stop failed: {e}")
            return False   # continue with default handling (process exit)
        win._console_handler_ref = HandlerRoutine(_console_handler)  # keep alive
        ctypes.windll.kernel32.SetConsoleCtrlHandler(win._console_handler_ref, True)
    except Exception as e:
        LOG.warning(f"[EXIT] console handler not installed: {e}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    _setup_fonts(app)
    _apply_ui_scaling(app)
    win = MainWindow()
    _install_exit_camera_cleanup(app, win)
    sys.exit(app.exec_())
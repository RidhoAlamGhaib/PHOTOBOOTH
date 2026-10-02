"""Canon EDSDK bridge for the photobooth.

Talks to a Canon EOS body (tested target: EOS 200D Mark II / 250D / Rebel SL3,
DIGIC 8) through Canon's EDSDK.dll using ctypes. Gives you two things the rest
of the app already understands — plain BGR numpy arrays:

  * live view         → CanonCamera.grab_live_frame()  (for preview/countdown)
  * full-res capture  → CanonCamera.capture_still()    (the real shutter shot)

Two layers:
  * CanonCamera        — low-level, single-threaded, synchronous. Easy to test.
  * CanonCameraThread  — a QThread drop-in for the app's own CameraThread:
                         emits frame_ready(display, raw, crop) for live view,
                         and still_ready(raw) after a full-res capture that you
                         kick off with request_capture().

------------------------------------------------------------------------------
SETUP (one-time)
------------------------------------------------------------------------------
1. Get EDSDK from Canon's developer site (you said you already have it).
2. Use the DLL build that matches your Python bitness. 64-bit Python needs the
   64-bit EDSDK.dll. Check with:  python -c "import struct;print(struct.calcsize('P')*8)"
3. Put the whole EDSDK `Dll` folder (EDSDK.dll + EdsImage.dll + the rest — they
   must stay together) somewhere, then point this module at it, either:
     - env var:   set EDSDK_DLL_DIR=C:\\path\\to\\EDSDK\\Dll
     - or a folder named  EDSDK\\Dll  next to this file (the default), or
     - pass dll_dir=... to CanonCamera().
4. Install OpenCV + numpy (the app already has them).

Quick hardware check (no app, no Qt):   python test_canon.py
------------------------------------------------------------------------------
"""

import os
import sys
import time
import ctypes
import logging
import threading
import atexit
from ctypes import c_void_p, c_uint, c_int, c_uint32, c_uint64, byref, POINTER

import numpy as np
import cv2

LOG = logging.getLogger("canon_edsdk")

# ============================================================================
# EDSDK constants (from EDSDKErrors.h / EDSDKTypes.h)
# ============================================================================
EDS_ERR_OK               = 0x00000000
EDS_ERR_DEVICE_BUSY      = 0x00000081
EDS_ERR_OBJECT_NOTREADY  = 0x0000A102   # live view not streaming yet / retry

# Property IDs
kEdsPropID_SaveTo            = 0x0000000B
kEdsPropID_Evf_OutputDevice  = 0x00000500
kEdsPropID_Evf_Mode          = 0x00000501

# SaveTo
kEdsSaveTo_Camera = 1
kEdsSaveTo_Host   = 2
kEdsSaveTo_Both   = 3

# Evf output device (bit flags)
kEdsEvfOutputDevice_TFT = 1
kEdsEvfOutputDevice_PC  = 2

# Camera commands
kEdsCameraCommand_TakePicture          = 0x00000000
kEdsCameraCommand_ExtendShutDownTimer  = 0x00000001
kEdsCameraCommand_PressShutterButton   = 0x00000004
kEdsCameraCommand_DoEvfAf              = 0x00000102

# Shutter-button params
kEdsCameraCommand_ShutterButton_OFF                 = 0x00000000
kEdsCameraCommand_ShutterButton_Halfway             = 0x00000001
kEdsCameraCommand_ShutterButton_Completely          = 0x00000003
kEdsCameraCommand_ShutterButton_Halfway_NonAF       = 0x00010001
kEdsCameraCommand_ShutterButton_Completely_NonAF    = 0x00010003

# Object events
kEdsObjectEvent_All                  = 0x00000200
kEdsObjectEvent_DirItemCreated       = 0x00000204
kEdsObjectEvent_DirItemRequestTransfer = 0x00000208

# State events
kEdsStateEvent_All       = 0x00000300
kEdsStateEvent_Shutdown  = 0x00000301

EDS_MAX_NAME = 256


# ============================================================================
# ctypes structs
# ============================================================================
class EdsCapacity(ctypes.Structure):
    _fields_ = [
        ("numberOfFreeClusters", c_int),
        ("bytesPerSector",       c_int),
        ("reset",                c_int),
    ]


class EdsDirectoryItemInfo(ctypes.Structure):
    # Modern SDK (13.x): size is 64-bit. Matches EDSDKTypes.h.
    _fields_ = [
        ("size",       c_uint64),
        ("isFolder",   c_int),
        ("groupID",    c_uint32),
        ("option",     c_uint32),
        ("szFileName", ctypes.c_char * EDS_MAX_NAME),
        ("format",     c_uint32),
        ("dateTime",   c_uint32),
    ]


# Callback type: EdsError __stdcall (EdsUInt32 event, EdsBaseRef ref, EdsVoid* ctx)
# WINFUNCTYPE => __stdcall, required on Windows.
if sys.platform == "win32":
    _CALLBACK = ctypes.WINFUNCTYPE(c_uint, c_uint, c_void_p, c_void_p)
else:  # pragma: no cover - EDSDK is Windows/Mac; this keeps import working
    _CALLBACK = ctypes.CFUNCTYPE(c_uint, c_uint, c_void_p, c_void_p)


class EdsError(RuntimeError):
    def __init__(self, func, code):
        self.code = code
        super().__init__(f"{func} failed: EDS_ERR 0x{code & 0xFFFFFFFF:08X}")


def _app_dir():
    """The folder the user sees: next to the .exe when frozen (PyInstaller),
    otherwise next to this file."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _dll_dir_candidates():
    """Folders searched for EDSDK.dll, in order; the first hit wins.
    __file__ alone isn't enough: in a --onefile build it points into the
    temporary _MEIxxxx unpack folder, not the folder holding the .exe."""
    env = os.environ.get("EDSDK_DLL_DIR")
    roots = (_app_dir(),                                  # next to exe / script
             getattr(sys, "_MEIPASS", None),              # PyInstaller bundle
             os.path.dirname(os.path.abspath(__file__)),  # next to this module
             os.getcwd())
    dirs = [env] if env else []
    for r in roots:
        if r:
            dirs += [os.path.join(r, "EDSDK", "Dll"),
                     os.path.join(r, "EDSDK_64", "Dll"),
                     r]
    out = []
    for d in dirs:
        d = os.path.normpath(d)
        if d not in out:
            out.append(d)
    return out


def _default_dll_dir():
    cands = _dll_dir_candidates()
    for d in cands:
        if os.path.exists(os.path.join(d, "EDSDK.dll")):
            return d
    return cands[0]


class CanonCamera:
    """Single-threaded synchronous EDSDK wrapper.

    Call all methods from ONE thread. EDSDK is not fully thread-safe, and on
    Windows it needs its event queue pumped (we call EdsGetEvent for you).

    Typical use:
        cam = CanonCamera(); cam.open(); cam.start_live_view()
        frame = cam.grab_live_frame()      # BGR ndarray or None
        shot  = cam.capture_still()        # full-res BGR ndarray
        cam.close()
    """

    def __init__(self, dll_dir=None):
        # A relative dll_dir (e.g. from config.json) is taken from the app folder.
        if dll_dir and not os.path.isabs(dll_dir):
            dll_dir = os.path.join(_app_dir(), dll_dir)
        self._dll_dir = dll_dir or _default_dll_dir()
        self._dll = None
        self._camera = None            # EdsCameraRef
        self._sdk_inited = False
        self._live = False
        # Full-res capture plumbing (filled by the object-event callback).
        self._pending_still = None     # bytes of the downloaded JPEG
        self._still_lock = threading.Lock()
        # Keep callback alive so it isn't garbage-collected while registered.
        self._obj_cb = _CALLBACK(self._on_object_event)

    # ---- low-level helpers ---------------------------------------------------
    def _load_dll(self):
        dll_path = os.path.join(self._dll_dir, "EDSDK.dll")
        if not os.path.exists(dll_path):
            searched = "\n  ".join([self._dll_dir] + [d for d in _dll_dir_candidates()
                                                      if d != self._dll_dir])
            raise FileNotFoundError(
                f"EDSDK.dll not found. Put EDSDK.dll + EdsImage.dll next to the "
                f"app (or in EDSDK\\Dll next to it), set EDSDK_DLL_DIR, or pass "
                f"dll_dir=... Searched:\n  {searched}")
        # Make the sibling DLLs (EdsImage.dll etc.) discoverable.
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(self._dll_dir)
            except Exception:
                pass
        os.environ["PATH"] = self._dll_dir + os.pathsep + os.environ.get("PATH", "")
        self._dll = ctypes.WinDLL(dll_path)   # stdcall

        d = self._dll
        # Declare the argtypes/restype we rely on (keeps 64-bit pointers sane).
        d.EdsGetChildCount.argtypes   = [c_void_p, POINTER(c_uint32)]
        d.EdsGetChildAtIndex.argtypes = [c_void_p, c_int, POINTER(c_void_p)]
        d.EdsSetPropertyData.argtypes = [c_void_p, c_uint32, c_int, c_uint32, c_void_p]
        d.EdsSetCapacity.argtypes     = [c_void_p, EdsCapacity]
        d.EdsSendCommand.argtypes     = [c_void_p, c_uint32, c_int]
        d.EdsSetObjectEventHandler.argtypes = [c_void_p, c_uint32, _CALLBACK, c_void_p]
        d.EdsCreateMemoryStream.argtypes = [c_uint64, POINTER(c_void_p)]
        d.EdsCreateEvfImageRef.argtypes  = [c_void_p, POINTER(c_void_p)]
        d.EdsDownloadEvfImage.argtypes   = [c_void_p, c_void_p]
        d.EdsGetLength.argtypes  = [c_void_p, POINTER(c_uint64)]
        d.EdsGetPointer.argtypes = [c_void_p, POINTER(c_void_p)]
        d.EdsGetDirectoryItemInfo.argtypes = [c_void_p, POINTER(EdsDirectoryItemInfo)]
        d.EdsDownload.argtypes         = [c_void_p, c_uint64, c_void_p]
        d.EdsDownloadComplete.argtypes = [c_void_p]
        d.EdsRelease.argtypes = [c_void_p]

    def _check(self, code, func):
        if code != EDS_ERR_OK:
            raise EdsError(func, code)

    def _pump_events(self):
        # On Windows, drives EDSDK's internal event queue (fires our callback).
        try:
            self._dll.EdsGetEvent()
        except Exception:
            pass

    # ---- lifecycle -----------------------------------------------------------
    def open(self):
        if self._dll is None:
            self._load_dll()
        self._check(self._dll.EdsInitializeSDK(), "EdsInitializeSDK")
        self._sdk_inited = True

        cam_list = c_void_p()
        self._check(self._dll.EdsGetCameraList(byref(cam_list)), "EdsGetCameraList")
        try:
            count = c_uint32(0)
            self._check(self._dll.EdsGetChildCount(cam_list, byref(count)),
                        "EdsGetChildCount")
            if count.value == 0:
                raise RuntimeError("No Canon camera found. Is it on, in a photo "
                                   "mode (not video/playback), and USB-connected?")
            camera = c_void_p()
            self._check(self._dll.EdsGetChildAtIndex(cam_list, 0, byref(camera)),
                        "EdsGetChildAtIndex")
            self._camera = camera
        finally:
            self._dll.EdsRelease(cam_list)

        self._check(self._dll.EdsOpenSession(self._camera), "EdsOpenSession")
        LOG.info("[CANON] session opened")
        # Safety net: if the process exits without close() (crash, killed
        # window), still release the session so the body doesn't stay busy.
        atexit.register(self.close)

        # Download to host (PC), not the SD card.
        self._set_prop_u32(kEdsPropID_SaveTo, kEdsSaveTo_Host)
        # Tell the camera the host has "space" so it will release the shutter.
        cap = EdsCapacity(0x7FFFFFFF, 0x1000, 1)
        self._check(self._dll.EdsSetCapacity(self._camera, cap), "EdsSetCapacity")
        # Register the handler that downloads each new full-res image.
        self._check(self._dll.EdsSetObjectEventHandler(
            self._camera, kEdsObjectEvent_All, self._obj_cb, None),
            "EdsSetObjectEventHandler")
        return self

    def close(self):
        """Release everything: live view off, close session, TerminateSDK.
        Safe to call more than once."""
        try:
            atexit.unregister(self.close)
        except Exception:
            pass
        if self._dll is None:
            return
        try:
            if self._live:
                self.stop_live_view()
        except Exception:
            pass
        try:
            if self._camera is not None:
                self._dll.EdsCloseSession(self._camera)
                self._dll.EdsRelease(self._camera)
                self._camera = None
                LOG.info("[CANON] session closed")
        except Exception as e:
            LOG.warning(f"[CANON] close session error: {e}")
        try:
            if self._sdk_inited:
                self._dll.EdsTerminateSDK()
                self._sdk_inited = False
        except Exception as e:
            LOG.warning(f"[CANON] terminate SDK error: {e}")

    # ---- properties ----------------------------------------------------------
    def _set_prop_u32(self, prop_id, value):
        data = c_uint32(value)
        self._check(self._dll.EdsSetPropertyData(
            self._camera, prop_id, 0, 4, byref(data)),
            f"EdsSetPropertyData(0x{prop_id:X})")

    # ---- live view -----------------------------------------------------------
    def start_live_view(self):
        # Route live view to the PC.
        self._set_prop_u32(kEdsPropID_Evf_OutputDevice, kEdsEvfOutputDevice_PC)
        self._live = True
        LOG.info("[CANON] live view -> PC")
        # The first frames aren't ready immediately; caller should tolerate None.

    def stop_live_view(self):
        if not self._live:
            return
        try:
            self._set_prop_u32(kEdsPropID_Evf_OutputDevice, 0)
        except Exception as e:
            LOG.warning(f"[CANON] stop live view: {e}")
        self._live = False

    def grab_live_frame(self):
        """Return the current live-view frame as a BGR ndarray, or None if the
        stream isn't ready yet (call again shortly)."""
        if not self._live:
            return None
        stream = c_void_p()
        evf = c_void_p()
        try:
            self._check(self._dll.EdsCreateMemoryStream(0, byref(stream)),
                        "EdsCreateMemoryStream")
            self._check(self._dll.EdsCreateEvfImageRef(stream, byref(evf)),
                        "EdsCreateEvfImageRef")
            code = self._dll.EdsDownloadEvfImage(self._camera, evf)
            if code == EDS_ERR_OBJECT_NOTREADY:
                return None
            if code != EDS_ERR_OK:
                # Transient device-busy etc. — skip this frame.
                return None
            return self._stream_to_bgr(stream)
        finally:
            if evf:
                self._dll.EdsRelease(evf)
            if stream:
                self._dll.EdsRelease(stream)

    def _stream_to_bgr(self, stream):
        length = c_uint64(0)
        self._check(self._dll.EdsGetLength(stream, byref(length)), "EdsGetLength")
        if length.value == 0:
            return None
        ptr = c_void_p()
        self._check(self._dll.EdsGetPointer(stream, byref(ptr)), "EdsGetPointer")
        buf = ctypes.string_at(ptr, length.value)
        arr = np.frombuffer(buf, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)   # JPEG -> BGR
        return img

    # ---- full-res capture ----------------------------------------------------
    def _on_object_event(self, event, obj_ref, context):
        """EDSDK callback (fired during _pump_events). Downloads a new image."""
        try:
            if event in (kEdsObjectEvent_DirItemRequestTransfer,
                         kEdsObjectEvent_DirItemCreated):
                data = self._download_dir_item(obj_ref)
                if data is not None:
                    with self._still_lock:
                        self._pending_still = data
        except Exception as e:
            LOG.error(f"[CANON] object event download failed: {e}")
        finally:
            # Release the directory-item ref the SDK handed us.
            if obj_ref:
                try:
                    self._dll.EdsRelease(obj_ref)
                except Exception:
                    pass
        return EDS_ERR_OK

    def _download_dir_item(self, dir_item):
        info = EdsDirectoryItemInfo()
        self._check(self._dll.EdsGetDirectoryItemInfo(dir_item, byref(info)),
                    "EdsGetDirectoryItemInfo")
        stream = c_void_p()
        self._check(self._dll.EdsCreateMemoryStream(info.size, byref(stream)),
                    "EdsCreateMemoryStream(download)")
        try:
            self._check(self._dll.EdsDownload(dir_item, info.size, stream),
                        "EdsDownload")
            self._check(self._dll.EdsDownloadComplete(dir_item),
                        "EdsDownloadComplete")
            name = info.szFileName.decode(errors="replace")
            LOG.info(f"[CANON] downloaded {name} ({info.size} bytes)")
            return self._stream_to_bgr(stream)
        finally:
            self._dll.EdsRelease(stream)

    def capture_still(self, timeout=12.0, use_af=True):
        """Fire the shutter and return the full-res photo as a BGR ndarray.
        Blocks until the image is downloaded or `timeout` elapses (raises
        TimeoutError). Must be called on the same thread as open()."""
        with self._still_lock:
            self._pending_still = None

        self._trigger_shutter(use_af=use_af)

        deadline = time.time() + timeout
        while time.time() < deadline:
            self._pump_events()
            with self._still_lock:
                if self._pending_still is not None:
                    img = self._pending_still
                    self._pending_still = None
                    if img is None:
                        raise RuntimeError("captured image failed to decode")
                    return img
            time.sleep(0.03)
        raise TimeoutError("Timed out waiting for the captured photo to download")

    def _send(self, command, param=0):
        return self._dll.EdsSendCommand(self._camera, command, param)

    def _trigger_shutter(self, use_af=True):
        """Prefer the press-shutter sequence (works well with live view AF);
        fall back to the one-shot TakePicture command."""
        if self._live and use_af:
            try:
                self._send(kEdsCameraCommand_DoEvfAf, 1)
                time.sleep(0.2)
            except Exception:
                pass
        completely = (kEdsCameraCommand_ShutterButton_Completely if use_af
                      else kEdsCameraCommand_ShutterButton_Completely_NonAF)
        code = self._send(kEdsCameraCommand_PressShutterButton, completely)
        # Always release the button.
        self._send(kEdsCameraCommand_PressShutterButton,
                   kEdsCameraCommand_ShutterButton_OFF)
        if code == EDS_ERR_OK:
            return
        LOG.warning(f"[CANON] PressShutterButton returned 0x{code & 0xFFFFFFFF:08X}; "
                    f"falling back to TakePicture")
        code = self._send(kEdsCameraCommand_TakePicture, 0)
        if code != EDS_ERR_OK:
            raise EdsError("TakePicture", code)


# ============================================================================
# Qt drop-in for the app's CameraThread
# ============================================================================
try:
    from PyQt5.QtCore import QThread, pyqtSignal
    _HAVE_QT = True
except Exception:  # allow importing this module without Qt (e.g. test script)
    _HAVE_QT = False


if _HAVE_QT:
    class CanonCameraThread(QThread):
        """Drop-in replacement for the app's CameraThread, backed by a Canon DSLR.

        Mirrors CameraThread's interface:
          * emits frame_ready(display, raw, crop)  — live view, ~continuously
          * attributes: crop, target_aspect, fit_mode, pad_color  (same cropping)
        Adds, for full-res stills:
          * request_capture()            — ask for a shutter shot (non-blocking)
          * signal still_ready(ndarray)  — emitted with the full-res BGR photo
          * signal capture_failed(str)   — emitted if the capture errors out
        """
        frame_ready    = pyqtSignal(np.ndarray, np.ndarray, bool)
        still_ready    = pyqtSignal(np.ndarray)
        capture_failed = pyqtSignal(str)
        camera_lost    = pyqtSignal(str)   # watchdog gave up; EDSDK shut down

        def __init__(self, index=0, dll_dir=None, use_af=True,
                     fail_timeout_s=30.0):
            super().__init__()
            self.use_af = use_af
            # No good live frame AND no successful capture for this long ->
            # shut EDSDK down cleanly instead of hammering a stuck camera.
            self.fail_timeout_s = float(fail_timeout_s)
            self.index = index          # unused (USB), kept for interface parity
            self.running = True
            self.crop = False
            self.target_aspect = 3 / 4
            self.fit_mode = "cover"
            self.pad_color = (0, 0, 0)
            self._dll_dir = dll_dir
            self._capture_requested = False
            self._lock = threading.Lock()

        def request_capture(self):
            """Call from the GUI thread. The next loop iteration fires the
            shutter and emits still_ready with the full-res photo."""
            with self._lock:
                self._capture_requested = True

        def run(self):
            cam = CanonCamera(dll_dir=self._dll_dir)
            try:
                cam.open()
                cam.start_live_view()
            except Exception as e:
                LOG.error(f"[CANON] startup failed: {e}")
                self._shutdown_edsdk(cam, f"Camera startup failed: {e}")
                return

            last_ok = time.time()
            lost_reason = None
            while self.running:
                # Handle a pending full-res capture first.
                with self._lock:
                    want = self._capture_requested
                    self._capture_requested = False
                if want:
                    try:
                        shot = cam.capture_still(use_af=self.use_af)
                        last_ok = time.time()
                        self.still_ready.emit(np.ascontiguousarray(shot))
                    except Exception as e:
                        LOG.error(f"[CANON] capture failed: {e}")
                        self.capture_failed.emit(str(e))

                frame = None
                try:
                    frame = cam.grab_live_frame()
                    cam._pump_events()      # keep events flowing for downloads
                except Exception as e:
                    LOG.warning(f"[CANON] live view error: {e}")

                if frame is not None:
                    last_ok = time.time()
                    raw = np.ascontiguousarray(frame)
                    display = self._apply_crop(frame) if self.crop else raw
                    self.frame_ready.emit(display, raw, self.crop)
                elif time.time() - last_ok > self.fail_timeout_s:
                    lost_reason = (f"no live-view frame or capture for "
                                   f"{self.fail_timeout_s:.0f}s")
                    break
                time.sleep(0.02)

            if lost_reason:
                LOG.error(f"[CANON] watchdog: {lost_reason} - stopping EDSDK")
                self._shutdown_edsdk(cam, lost_reason)
            else:
                try:
                    cam.close()
                except Exception:
                    pass
            LOG.info("[CANON] camera thread stopped")

        def _shutdown_edsdk(self, cam, reason):
            """Close session + TerminateSDK so the camera is released, then
            tell the app the DSLR is gone."""
            try:
                cam.close()
                LOG.info("[CANON] EDSDK stopped, camera released")
            except Exception as e:
                LOG.warning(f"[CANON] EDSDK shutdown error: {e}")
            self.running = False
            self.camera_lost.emit(reason)

        def _apply_crop(self, frame):
            """Same cover/contain crop CameraThread does, so the preview matches."""
            h, w = frame.shape[:2]
            aspect = max(0.1, float(self.target_aspect))
            src_aspect = w / h
            fit = getattr(self, "fit_mode", "cover")
            if fit == "contain":
                if src_aspect > aspect:
                    new_h = int(round(w / aspect))
                    pad = new_h - h
                    top = pad // 2
                    disp = cv2.copyMakeBorder(frame, top, pad - top, 0, 0,
                                              cv2.BORDER_CONSTANT, value=self.pad_color)
                else:
                    new_w = int(round(h * aspect))
                    pad = new_w - w
                    left = pad // 2
                    disp = cv2.copyMakeBorder(frame, 0, 0, left, pad - left,
                                              cv2.BORDER_CONSTANT, value=self.pad_color)
                return np.ascontiguousarray(disp)
            if src_aspect > aspect:
                new_w = int(round(h * aspect))
                x0 = (w - new_w) // 2
                return np.ascontiguousarray(frame[:, x0:x0 + new_w])
            new_h = int(round(w / aspect))
            y0 = (h - new_h) // 2
            return np.ascontiguousarray(frame[y0:y0 + new_h, :])

        def stop(self):
            self.running = False
            self.wait()

"""Read paper (media) remaining and status straight from a DNP DS-RX1.

Uses DNP's CyStat64.dll / CyStat.dll (DS-RX1 SDK; 64-bit Python needs
CyStat64.dll, shipped next to the app), the same
library DNP's Rx1Lib wraps:

    PortInitialize(wchar_t* portName) -> int portNum  e.g. L"USB001" (1-based)
    GetMediaCounter(int portNum)   -> int  prints left on the roll
    GetInitialMediaCount(int)      -> int  prints on a full roll
    GetStatus(int)                 -> int  status code (see STATUS_TEXT)

All calls are made from one background thread that polls every few seconds;
the app reads the cached values. If CyStat.dll can't be found or loaded the
monitor stays "unavailable" and the app works as before.
"""

import os
import sys
import ctypes
import logging
import threading
import time

LOG = logging.getLogger("dnp_status")

# Status codes (from Rx1Lib constants). Groups: 0x0001xxxx normal,
# 0x0002xxxx operator fixable, 0x0004xxxx hardware, 0x0008xxxx system.
STATUS_TEXT = {
    0x00010001: "Siap",
    0x00010002: "Sedang mencetak",
    0x00010004: "Standby",
    0x00010008: "Kertas habis",
    0x00010010: "Ribbon habis",
    0x00010020: "Head pendinginan",
    0x00010040: "Motor pendinginan",
    0x00020001: "Tutup printer terbuka",
    0x00020002: "Kertas macet",
    0x00020004: "Ribbon error",
    0x00020008: "Kertas tidak cocok",
    0x00020010: "Data error",
    0x00020020: "Kotak sisa potongan penuh",
    0x00080001: "System error",
}
READY_CODES = {0x00010001, 0x00010002, 0x00010004, 0x00010020, 0x00010040}
GROUP_HARDWARE = 0x00040000


def status_text(code):
    if code is None:
        return "-"
    if code in STATUS_TEXT:
        return STATUS_TEXT[code]
    if code & GROUP_HARDWARE:
        return f"Hardware error (0x{code & 0xFFFFFFFF:08X})"
    return f"Status 0x{code & 0xFFFFFFFF:08X}"


def _app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _dll_candidates(extra_dir=None):
    dirs = []
    if extra_dir:
        dirs.append(extra_dir if os.path.isabs(extra_dir) else os.path.join(_app_dir(), extra_dir))
    dirs += [_app_dir(), os.path.join(_app_dir(), "dnp")]
    win = os.environ.get("SystemRoot", r"C:\Windows")
    dirs += [os.path.join(win, "System32"), os.path.join(win, "SysWOW64")]
    # DNP ships the 64-bit build as CyStat64.dll; prefer the one matching Python.
    is64 = ctypes.sizeof(ctypes.c_void_p) == 8
    names = ["CyStat64.dll", "CyStat.dll"] if is64 else ["CyStat.dll"]
    return [os.path.join(d, n) for d in dirs for n in names]


def _printer_port(printer_name):
    """Windows printer name -> its port name (e.g. 'USB001')."""
    import win32print
    h = win32print.OpenPrinter(printer_name)
    try:
        info = win32print.GetPrinter(h, 2)
        return (info.get("pPortName") or "").split(",")[0].strip()
    finally:
        win32print.ClosePrinter(h)


def _language_monitor_check(printer_name):
    """CyStat talks to the printer through the DS-RX1 driver's language
    monitor CSJCYLM.DLL (LoadLibrary + MonitorIoControl). Report whether that
    DLL loads and whether the printer queue is set up to use it."""
    notes = []
    try:
        ctypes.WinDLL("CSJCYLM.DLL")
        notes.append("CSJCYLM.DLL loads OK")
    except OSError as e:
        win = os.environ.get("SystemRoot", r"C:\Windows")
        here = [p for p in (os.path.join(win, "System32", "CSJCYLM.DLL"),
                            os.path.join(win, "SysWOW64", "CSJCYLM.DLL"))
                if os.path.exists(p)]
        notes.append(f"CSJCYLM.DLL (DNP language monitor) NOT loadable: {e}; "
                     f"found at: {here or 'nowhere'}")
    try:
        import win32print
        h = win32print.OpenPrinter(printer_name)
        try:
            info = win32print.GetPrinter(h, 2)
            try:
                mon = win32print.GetPrinterDriver(h, None, 3).get("MonitorName") or "-"
            except Exception:
                mon = "?"
        finally:
            win32print.ClosePrinter(h)
        notes.append(f"queue driver={info.get('pDriverName')!r} port={info.get('pPortName')!r} "
                     f"language monitor={mon!r}")
    except Exception as e:
        notes.append(f"queue info unavailable: {e}")
    return " | ".join(notes)


class DnpMonitor:
    def __init__(self, printer_name, dll_dir=None, poll_s=15.0):
        self.printer_name = printer_name
        self.dll_dir = dll_dir
        self.poll_s = max(3.0, float(poll_s))
        self.available = False
        self.error = None
        self.remaining = None     # prints left on roll
        self.initial = None       # prints on a full roll
        self.status = None        # raw status code
        self.updated_at = None
        self._dll = None
        self._port = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = False
        self._listeners = []

    # ---- public -------------------------------------------------------------
    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="dnp-monitor").start()

    def refresh_soon(self):
        """Ask for a fresh read (e.g. right after a print)."""
        self._wake.set()

    def on_update(self, fn):
        self._listeners.append(fn)

    def stop(self):
        self._stop = True
        self._wake.set()

    @property
    def status_text(self):
        return status_text(self.status)

    @property
    def ready(self):
        return self.status in READY_CODES

    def snapshot(self):
        with self._lock:
            return dict(available=self.available, error=self.error,
                        remaining=self.remaining, initial=self.initial,
                        status=self.status, status_text=status_text(self.status),
                        updated_at=self.updated_at)

    # ---- internals ----------------------------------------------------------
    def _load(self):
        if sys.platform != "win32":
            raise RuntimeError("DNP status needs Windows")
        tried = []
        wrong_bits = []
        for path in _dll_candidates(self.dll_dir):
            if not os.path.exists(path):
                tried.append(path)
                continue
            try:
                if hasattr(os, "add_dll_directory"):
                    try:
                        os.add_dll_directory(os.path.dirname(path))
                    except Exception:
                        pass
                dll = ctypes.WinDLL(path)
            except OSError as e:
                if getattr(e, "winerror", None) == 193:
                    wrong_bits.append(path)      # e.g. 32-bit CyStat.dll; keep looking
                    continue
                raise
            # PortInitialize takes a WIDE (UTF-16) port name (disassembly:
            # word-wise compare, copied into the port table and later passed
            # to the language monitor's MonitorIoControl). Rx1Lib's ANSI
            # declaration is wrong for this DLL. Returns a 1-based port number.
            dll.PortInitialize.argtypes = [ctypes.c_wchar_p]
            dll.PortInitialize.restype = ctypes.c_int
            for fn in ("GetMediaCounter", "GetInitialMediaCount", "GetStatus"):
                getattr(dll, fn).argtypes = [ctypes.c_int]
                getattr(dll, fn).restype = ctypes.c_int
            LOG.info(f"[DNP] loaded {path}")
            return dll
        if wrong_bits:
            bits = 64 if ctypes.sizeof(ctypes.c_void_p) == 8 else 32
            raise RuntimeError(f"only wrong-bitness DLLs found ({', '.join(wrong_bits)}); "
                               f"Python is {bits}-bit - put CyStat64.dll next to the app")
        raise FileNotFoundError("CyStat64.dll / CyStat.dll not found. Looked in:\n  " + "\n  ".join(tried))

    def _connect(self):
        if self._dll is None:
            self._dll = self._load()
        port_name = _printer_port(self.printer_name)
        if not port_name:
            raise RuntimeError(f"no port for printer {self.printer_name!r}")
        port = self._dll.PortInitialize(port_name)
        if port < 0:
            raise RuntimeError(f"PortInitialize({port_name}) returned {port}")
        self._port = port
        LOG.info(f"[DNP] {self.printer_name} on {port_name} -> port {port}")

    def _read(self):
        d, p = self._dll, self._port
        status = d.GetStatus(p)
        remaining = d.GetMediaCounter(p)
        initial = d.GetInitialMediaCount(p)
        if remaining < 0 and (status & 0x80000000):
            raise RuntimeError(f"printer not responding (status 0x{status & 0xFFFFFFFF:08X}); "
                               + _language_monitor_check(self.printer_name))
        with self._lock:
            self.status = status & 0xFFFFFFFF
            self.remaining = remaining if remaining >= 0 else None
            self.initial = initial if initial > 0 else None
            self.available = True
            self.error = None
            self.updated_at = time.time()

    def _notify(self):
        for fn in list(self._listeners):
            try:
                fn(self.snapshot())
            except Exception as e:
                LOG.warning(f"[DNP] listener failed: {e}")

    def _loop(self):
        last_logged = None
        while not self._stop:
            try:
                if self._port is None:
                    self._connect()
                self._read()
                key = (self.remaining, self.status)
                if key != last_logged:
                    LOG.info(f"[DNP] media {self.remaining}/{self.initial}  status: {self.status_text}")
                    last_logged = key
            except Exception as e:
                msg = str(e)
                if msg != self.error:
                    LOG.warning(f"[DNP] status unavailable: {msg}")
                with self._lock:
                    self.available = False
                    self.error = msg
                self._port = None          # reconnect next round (USB replug)
            self._notify()
            self._wake.wait(self.poll_s if self.available else max(self.poll_s, 30.0))
            self._wake.clear()

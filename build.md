# Building SeoulBox.exe

How to turn `SeoulBox.py` into a Windows app folder you can copy to the booth PC.
Uses **PyInstaller** in *one-folder* mode.

---

## 1. Before you start

- **64-bit Python.** The bundled Canon `EDSDK\Dll\EDSDK.dll` is x64, so the exe must be 64-bit too.
  Check: `py -c "import struct; print(struct.calcsize('P') * 8)"` → must print `64`.
- **Python version.** You run on Python 3.14. Use the newest PyInstaller, because 3.14 support only
  exists in recent releases. If PyInstaller errors out on 3.14, build from a Python **3.12 or 3.13**
  virtual env instead. The app itself doesn't need 3.14.
- Build on Windows (same OS family as the booth PC). PyInstaller can't cross-compile.

## 2. Make a clean build environment

A fresh venv keeps the exe small and proves every dependency is really installed.
From the `photobooth` folder:

```bat
py -m venv .venv-build
.venv-build\Scripts\activate
python -m pip install --upgrade pip
pip install pyinstaller pyqt5 opencv-python numpy pillow qrcode google-api-python-client google-auth google-auth-oauthlib pywin32
```

What each one is for:

| Package | Used by |
|---|---|
| `pyqt5` | the whole UI |
| `opencv-python`, `numpy` | camera frames, filters, beautify, video |
| `pillow` | frame compositing, stamping, printing |
| `qrcode` | QR on the print + done screen |
| `google-api-python-client`, `google-auth`, `google-auth-oauthlib` | Drive upload |
| `pywin32` | printing (`win32print`, `win32ui`) |

Check that the app runs from this venv **before** building:

```bat
python SeoulBox.py
```

## 3. Build

Run from the `photobooth` folder with the venv active:

```bat
pyinstaller --noconfirm --onefile --clean --windowed --name SeoulBox --add-data "EDSDK\Dll;EDSDK\Dll" --hidden-import canon_edsdk --hidden-import win32print --hidden-import win32ui --hidden-import win32con --collect-data googleapiclient --exclude-module tkinter SeoulBox.py
```

Camera-only test exe (keeps the console so you can read its output):

```bat
pyinstaller --noconfirm --onefile --clean --add-data "EDSDK\Dll;EDSDK\Dll" test_canon.py
```

Why each flag:

| Flag | Reason |
|---|---|
| `--windowed` | No black console window behind the booth UI. Output goes to `logs\` instead. |
| `--add-data "EDSDK\Dll;EDSDK\Dll"` | Ships the 64-bit Canon DLLs inside the exe. Optional: without it, put `EDSDK.dll` + `EdsImage.dll` next to the exe (or in an `EDSDK\Dll` folder next to it) instead. |
| `--hidden-import canon_edsdk` | It's imported lazily (only when `camera.source = "canon"`), so force it into the bundle. |
| `--hidden-import win32*` | The printing modules are imported inside functions. |
| `--collect-data googleapiclient` | The Drive client needs its bundled API discovery files. Without this, upload fails with `UnknownApiNameOrVersion: drive v3`. |
| `--exclude-module tkinter` | Not used; smaller build. |

Optional: add `--icon app.ico` for a custom exe icon.

Output with `--onefile`: a single `dist\SeoulBox.exe`. Put it in its own folder on the booth PC
(e.g. `C:\SeoulBox\`) together with the runtime files from step 4.
Without `--onefile` you get `dist\SeoulBox\SeoulBox.exe` + an `_internal\` folder; ship that whole folder.

> `--onefile` trade-off: the exe unpacks itself to a temp `_MEIxxxx` folder on every launch, so it
> starts a few seconds slower. Leave out `--onefile` if start-up time matters on the kiosk.

**Where the app looks for `EDSDK.dll`** (first hit wins; inside each it tries `EDSDK\Dll`,
`EDSDK_64\Dll`, then the folder itself):
1. `EDSDK_DLL_DIR` environment variable, if set
2. the folder holding the `.exe`
3. the bundled files (`_internal\`, or the `_MEIxxxx` temp folder for `--onefile`)
4. the working directory

`camera.edsdk_dll_dir` in `config.json` overrides all of this. A relative path there is taken from
the exe's folder. If nothing is found, the error lists every folder it searched.

## 4. Copy the runtime files next to the exe

When frozen, the app reads and writes everything **next to `SeoulBox.exe`**, not inside the bundle.
Don't bundle these with `--add-data`; copy them after building. For `--onefile` (exe lands in `dist\`):

```bat
mkdir dist\SeoulBox
move /Y dist\SeoulBox.exe dist\SeoulBox\
xcopy /E /I /Y frames dist\SeoulBox\frames
xcopy /E /I /Y models dist\SeoulBox\models
copy /Y config.json dist\SeoulBox\
```

(Without `--onefile` the exe is already in `dist\SeoulBox\`; skip the first two lines.)

Copy these too **if you have them**:

| File / folder | What it does | Needed when |
|---|---|---|
| `frames\` | Frame overlays per layout | Always |
| `models\` | Face detector for Spotlight / Big Eyes / Big Head effects | Always (without it those effects fall back to a weaker detector) |
| `config.json` | All settings | Always (if missing, the app writes defaults on first run) |
| `client_secret.json` | Google OAuth client | Drive upload |
| `oauth_token.json` | Saved Google login | Optional: skips the first-run browser login |
| `codes.json` | Access codes | `access_code.mode = "paid"` |
| `thank_you.png` / `thank_you.txt` | Uploaded into each guest's folder | Optional |
| `fonts\` | Poppins / Jua `.ttf` for the full pastel look | Optional |
| `session_stats.json` | Session counter | Only if you want to keep the old count |

`captures\` and `logs\` are created automatically.

Final layout on the booth PC:

```
SeoulBox\
├─ SeoulBox.exe
├─ _internal\            <- only without --onefile (don't touch)
├─ EDSDK.dll, EdsImage.dll  <- only if you didn't bundle them with --add-data
├─ config.json
├─ client_secret.json    (optional)
├─ frames\
├─ models\
├─ fonts\                (optional)
├─ captures\             (auto)
└─ logs\                 (auto)
```

## 5. One-click rebuild (optional)

Save this as `build.bat` in the `photobooth` folder and double-click it:

```bat
@echo off
cd /d "%~dp0"
if not exist .venv-build\Scripts\activate.bat (
  echo Create .venv-build first - see build.md
  pause
  exit /b 1
)
call .venv-build\Scripts\activate.bat
pyinstaller --noconfirm --onefile --clean --windowed --name SeoulBox ^
  --add-data "EDSDK\Dll;EDSDK\Dll" ^
  --hidden-import canon_edsdk ^
  --hidden-import win32print --hidden-import win32ui --hidden-import win32con ^
  --collect-data googleapiclient ^
  --exclude-module tkinter ^
  SeoulBox.py
if errorlevel 1 (
  pause
  exit /b 1
)
if not exist dist\SeoulBox mkdir dist\SeoulBox
move /Y dist\SeoulBox.exe dist\SeoulBox\ >nul
xcopy /E /I /Y frames dist\SeoulBox\frames >nul
xcopy /E /I /Y models dist\SeoulBox\models >nul
if exist config.json         copy /Y config.json         dist\SeoulBox\ >nul
if exist client_secret.json  copy /Y client_secret.json  dist\SeoulBox\ >nul
if exist oauth_token.json    copy /Y oauth_token.json    dist\SeoulBox\ >nul
if exist codes.json          copy /Y codes.json          dist\SeoulBox\ >nul
if exist thank_you.png       copy /Y thank_you.png       dist\SeoulBox\ >nul
if exist thank_you.txt       copy /Y thank_you.txt       dist\SeoulBox\ >nul
if exist fonts               xcopy /E /I /Y fonts dist\SeoulBox\fonts >nul
echo.
echo Done: dist\SeoulBox\SeoulBox.exe
pause
```

Note: each run replaces `dist\SeoulBox\SeoulBox.exe` and re-copies the files above.
`captures\` and `logs\` inside `dist\SeoulBox\` are kept.

## 6. Test the build (before the event)

Run `dist\SeoulBox\SeoulBox.exe`, then open the newest `logs\photobooth_YYYYMMDD.log`:

| Check | Log line you want |
|---|---|
| Frozen mode + right folder | `Frozen: True`, `BASE_DIR: ...\SeoulBox` |
| Config loaded | `[CONFIG] OK loaded from ...\config.json` |
| Canon | `[CAMERA] source=canon`, `[CANON] session opened`, `[CANON] live view -> PC` |
| Fonts (if added) | `[FONT] ui='Poppins' display='Jua'` |
| Drive | `[GDRIVE] subfolder created: https://drive.google.com/...` |
| Print | `[PRINT] copy 1/1 sent=True` |
| Individual photos | `[INDIV] saved 6/6 to ...\captures\SESSION_...` |

Do one full session: shoot → pick → frame → extra print → print → scan the QR with your phone.
The first Drive upload opens a browser for Google login. **Do this once on the booth PC before
the event**; after that `oauth_token.json` is reused.

Keys: `Esc` closes the app, `F12` shows session stats.

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| `EDSDK.dll not found. ... Searched: ...` | Put `EDSDK.dll` + `EdsImage.dll` next to the exe (or in `EDSDK\Dll` next to it), or rebuild with `--add-data "EDSDK\Dll;EDSDK\Dll"`, or set `camera.edsdk_dll_dir` in `config.json`. |
| `[WinError 193] %1 is not a valid Win32 application` | 32-bit DLL in a 64-bit exe (or the reverse). Use the x64 EDSDK (`EDSDK_64\Dll` in Canon's download). |
| `DLL load failed` for EDSDK | Install the **Microsoft Visual C++ 2015-2022 Redistributable (x64)** on the booth PC. |
| `[CAMERA] Canon EDSDK init FAILED`, then webcam fallback | Read the traceback in the log. Also close EOS Utility / EOS Webcam Utility. |
| `UnknownApiNameOrVersion: drive v3` | Rebuild with `--collect-data googleapiclient`. |
| `OAuth client secrets not found` | Copy `client_secret.json` next to `SeoulBox.exe`. |
| `[PRINT] pywin32 not available` | `pip install pywin32` in the build venv, then rebuild. |
| App starts then closes instantly, no window | Check `logs\` for `Uncaught exception`. For a visible console while debugging, rebuild without `--windowed`. |
| Antivirus / SmartScreen blocks the exe | Common false positive for PyInstaller apps. Allow the `SeoulBox` folder in Windows Security, or code-sign the exe. |
| Frames missing / "(no frame)" | `frames\` (and each layout's `frame_dir`) must sit next to `SeoulBox.exe`. |

## 8. Updating later

- Code change (`SeoulBox.py`, `canon_edsdk.py`): rebuild, then replace `SeoulBox.exe` (plus
  `_internal\` if you build without `--onefile`) on the booth PC. Keep its `config.json`, `captures\`, `logs\` and `oauth_token.json`.
- Settings, frames or fonts only: no rebuild needed. Edit or copy the files next to the exe.

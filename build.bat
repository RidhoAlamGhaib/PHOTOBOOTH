@echo off
cd /d "%~dp0"
if not exist .venv-build\Scripts\python.exe (
  echo Create .venv-build first - see build.md
  pause
  exit /b 1
)
".venv-build\Scripts\python.exe" -c "import PyInstaller, truststore, certifi, qrcode, googleapiclient, win32print, PyQt5.QtWebEngineWidgets" 2>nul
if errorlevel 1 (
  echo Missing packages in .venv-build. Run:
  echo   .venv-build\Scripts\python.exe -m pip install pyinstaller pyqt5 opencv-python numpy pillow qrcode google-api-python-client google-auth google-auth-oauthlib pywin32 truststore certifi PyQtWebEngine
  pause
  exit /b 1
)
".venv-build\Scripts\python.exe" -m PyInstaller --noconfirm --onefile --clean --windowed --name NeoPhoto ^
  --add-data "EDSDK\Dll;EDSDK\Dll" ^
  --hidden-import canon_edsdk --hidden-import effects --hidden-import dnp_status --hidden-import web_bridge --hidden-import PyQt5.QtWebEngineWidgets ^
  --hidden-import win32print --hidden-import win32ui --hidden-import win32con ^
  --hidden-import qrcode --hidden-import truststore --hidden-import certifi ^
  --hidden-import google.oauth2.credentials --hidden-import google_auth_oauthlib.flow ^
  --hidden-import google.auth.transport.requests ^
  --hidden-import googleapiclient.discovery --hidden-import googleapiclient.http ^
  --collect-data googleapiclient --collect-data certifi ^
  --exclude-module tkinter ^
  NeoPhoto.py
if errorlevel 1 (
  pause
  exit /b 1
)
if not exist dist\NeoPhoto mkdir dist\NeoPhoto
move /Y dist\NeoPhoto.exe dist\NeoPhoto\ >nul
xcopy /E /I /Y frames dist\NeoPhoto\frames >nul
xcopy /E /I /Y models dist\NeoPhoto\models >nul
copy /Y CyStat64.dll dist\NeoPhoto\ >nul
rem config.json: only the first time, so your settings in dist\NeoPhoto are kept
if not exist dist\NeoPhoto\config.json copy /Y config.json dist\NeoPhoto\ >nul
if exist client_secret.json  copy /Y client_secret.json  dist\NeoPhoto\ >nul
if exist oauth_token.json    if not exist dist\NeoPhoto\oauth_token.json copy /Y oauth_token.json dist\NeoPhoto\ >nul
if exist codes.json          copy /Y codes.json          dist\NeoPhoto\ >nul
if exist thank_you.png       copy /Y thank_you.png       dist\NeoPhoto\ >nul
if exist thank_you.txt       copy /Y thank_you.txt       dist\NeoPhoto\ >nul
if exist fonts               xcopy /E /I /Y fonts dist\NeoPhoto\fonts >nul
echo.
echo Done: dist\NeoPhoto\NeoPhoto.exe
pause

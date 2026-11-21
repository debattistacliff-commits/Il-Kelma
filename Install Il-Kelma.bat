@echo off
setlocal
title Install Il-Kelma
cd /d "%~dp0"
echo.
echo   Il-Kelma - Tkellem. Isma'. Ikteb.
echo   Setting everything up. This can take a while the first time.
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo   Python 3.13 is needed. Please install it from python.org, tick "Add python.exe to PATH", then run this again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo   [1/4] Creating a private Python space...
  python -m venv .venv || goto :failed
)

echo   [2/4] Installing the parts Il-Kelma needs...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :failed

echo   [3/4] Downloading the speech models (several GB)...
".venv\Scripts\python.exe" download_models.py || goto :failed

echo   [4/4] Downloading the Maltese word data...
".venv\Scripts\python.exe" download_data.py || goto :failed

echo   Making the Il-Kelma shortcut on your desktop...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'Il-Kelma.lnk')); $s.TargetPath='%~dp0Start Il-Kelma.bat'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%~dp0assets\il-kelma.ico'; $s.WindowStyle=7; $s.Description='Il-Kelma - Tkellem. Isma''. Ikteb.'; $s.Save()"

echo.
echo   All done. Double-click "Il-Kelma" on your desktop to begin.
pause
exit /b 0

:failed
echo.
echo   Something went wrong. Please check your internet connection and run this again.
pause
exit /b 1

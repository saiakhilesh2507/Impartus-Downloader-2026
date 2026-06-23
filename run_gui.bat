@echo off
REM Launch the Impartus Downloader GUI on Windows.
REM Double-click this file, or run it from a terminal.

setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python is not on PATH. Install Python 3.10+ from python.org.
  pause
  exit /b 1
)

REM Install deps on first run.
python -c "import flask, cryptography" 2>nul
if errorlevel 1 (
  echo Installing requirements...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo pip install failed.
    pause
    exit /b 1
  )
)

python gui.py %*
endlocal

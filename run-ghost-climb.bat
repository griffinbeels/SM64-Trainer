@echo off
setlocal
cd /d "%~dp0"
REM User-owned launch: a fresh practice profile, preserving the real history.
REM Optional arguments pass to ghost_climb.py, for example --player-name Griff.
if not exist ".venv\Scripts\python.exe" (
  echo Run run-test-server.bat once to prepare this checkout's dependencies.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" tools\build_natives.py
if errorlevel 1 (
  echo Native build failed; the climb did not start.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" tools\ghost_climb.py --seed --prepare --launch %*
if errorlevel 1 (
  echo Close the other trainer and check the message above before trying again.
  pause
  exit /b 1
)

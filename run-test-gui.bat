@echo off
REM ============================================================
REM  Run the DESKTOP WINDOW from this checkout (the shell the
REM  published app uses), on the test port, with no console.
REM
REM  run-test-server.bat keeps a console open, and closing a
REM  console kills the server with no shutdown at all. Closing
REM  THIS window is the path a published user takes, so it is
REM  the one to use when checking what happens at close.
REM
REM  Usage:
REM    run-test-gui.bat            uses port 8066
REM    run-test-gui.bat 8070       uses port 8070
REM ============================================================
setlocal
cd /d "%~dp0"

set "SM64_PORT=%~1"
if "%SM64_PORT%"=="" set "SM64_PORT=8066"

where uv >nul 2>nul
if errorlevel 1 (
  echo ERROR: 'uv' is not on your PATH.
  pause
  exit /b 1
)

uv sync
if errorlevel 1 (
  echo ERROR: dependency sync failed. The window was not started.
  pause
  exit /b 1
)

REM pythonw + start: the window owns its own process, and this console closes.
start "" uv run pythonw -m sm64_events.desktop
endlocal

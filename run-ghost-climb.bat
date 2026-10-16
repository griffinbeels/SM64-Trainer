@echo off
setlocal
cd /d "%~dp0"
REM User-owned launch: zero practice, no PB, four rivals from renamed saved ghosts.
REM Actual timings and original practice history stay unchanged.
REM Resume an existing tester: run-ghost-climb.bat --profile "C:\absolute\profile"
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
if /i "%~1"=="--profile" (
  ".venv\Scripts\python.exe" tools\ghost_climb.py --launch %*
) else (
  ".venv\Scripts\python.exe" tools\ghost_climb.py --simulate-fresh --launch %*
)
if errorlevel 1 (
  echo Close the other trainer and check the message above before trying again.
  pause
  exit /b 1
)

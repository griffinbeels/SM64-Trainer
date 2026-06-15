@echo off
REM ============================================================
REM  One-click build for sm64_tracker.
REM  Double-click this file to produce dist\sm64_tracker.exe.
REM  ffmpeg is bundled automatically from your PATH; to use a
REM  specific ffmpeg.exe, drag it onto this .bat (or pass its
REM  path as the first argument).
REM ============================================================
setlocal
cd /d "%~dp0"

echo.
echo ============================================================
echo   Building sm64_tracker.exe  --  takes a couple of minutes
echo ============================================================
echo.

where uv >nul 2>nul
if errorlevel 1 (
  echo ERROR: 'uv' is not on your PATH.
  echo Install it from https://docs.astral.sh/uv/ then run this again.
  echo.
  pause
  exit /b 1
)

if "%~1"=="" (
  uv run python tools\build_exe.py
) else (
  uv run python tools\build_exe.py --ffmpeg "%~1"
)
if errorlevel 1 goto failed

echo.
echo ============================================================
echo   DONE.  Your exe is here:
echo   %~dp0dist\sm64_tracker.exe
echo ============================================================
echo.
pause
exit /b 0

:failed
echo.
echo ============================================================
echo   BUILD FAILED.  Scroll up to see the error, then close.
echo ============================================================
echo.
pause
exit /b 1

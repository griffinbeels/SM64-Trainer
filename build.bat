@echo off
REM ============================================================
REM  One-click build for SM64 Trainer.
REM  Double-click this file to build BOTH artifacts:
REM    dist\SM64Trainer\SM64Trainer.exe   the app (onedir)
REM    dist\SM64TrainerSetup.exe          the bootstrap installer
REM  ffmpeg is bundled automatically from your PATH; to use a
REM  specific ffmpeg.exe, drag it onto this .bat (or pass its
REM  path as the first argument).
REM
REM  This is `tools\build_exe.py --mode all`. For just one, run
REM  that script with --mode app or --mode bootstrap. To PUBLISH,
REM  use tools\release.py instead -- it builds, checksums, tags
REM  and uploads in one command.
REM ============================================================
setlocal
cd /d "%~dp0"

echo.
echo ============================================================
echo   Building SM64 Trainer  --  takes a couple of minutes
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
echo   DONE.  Your build is here:
echo     %~dp0dist\SM64Trainer\SM64Trainer.exe    (the app)
echo     %~dp0dist\SM64TrainerSetup.exe           (the installer)
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

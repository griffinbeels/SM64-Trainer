@echo off
REM ============================================================
REM  Run a TEST server from the latest committed `main`.
REM
REM  Starts the SM64 Trainer server FROM SOURCE on a DIFFERENT
REM  port (default 8066) than the packaged trainer exe (8064),
REM  so you can run this alongside the real trainer to test
REM  main's committed changes live without a port collision.
REM
REM  Usage:
REM    run-test-server.bat            ->  http://127.0.0.1:8066
REM    run-test-server.bat 8070       ->  http://127.0.0.1:8070
REM ============================================================
setlocal
cd /d "%~dp0"

REM --- pick the port (first arg, else 8066) ---
set "SM64_PORT=%~1"
if "%SM64_PORT%"=="" set "SM64_PORT=8066"

REM --- uv is required ---
where uv >nul 2>nul
if errorlevel 1 (
  echo ERROR: 'uv' is not on your PATH.
  echo Install it from https://docs.astral.sh/uv/ then run this again.
  echo.
  pause
  exit /b 1
)

REM --- update to the latest committed main (non-fatal) ---
REM  Name the branch in the warning: this checkout is sometimes parked on a
REM  feature branch by a parallel session, and the old wording ("could not
REM  fast-forward") read like a git problem while the real message is "you
REM  are NOT testing main" — which silently cost a live test run (2026-07-24).
for /f "delims=" %%B in ('git rev-parse --abbrev-ref HEAD') do set "SM64_BRANCH=%%B"
echo Fetching the latest committed main...
git pull --ff-only
if errorlevel 1 (
  echo.
  if /i not "%SM64_BRANCH%"=="main" (
    echo WARNING: this checkout is on branch '%SM64_BRANCH%', NOT main.
    echo          The test server below runs THAT branch's code — anything
    echo          merged to main since is not included. Switch with
    echo          'git checkout main' if you meant to test main.
  ) else (
    echo WARNING: could not fast-forward ^(uncommitted local changes or a
    echo          diverged branch^). Running the CURRENT checkout instead of
    echo          the very latest main.
  )
  echo.
)

REM --- make sure dependencies match the lockfile ---
echo Syncing dependencies...
uv sync

echo.
echo ============================================================
echo   SM64 Trainer  --  TEST server (from source, main)
echo   URL:    http://127.0.0.1:%SM64_PORT%
REM  Every dev/demo page this server hosts gets printed HERE, with the port
REM  already filled in. The tuning page existed for a day behind a hand-typed
REM  path and was unreachable the moment this script picked a different port
REM  (2026-07-27) -- if you add another one, add its line here too.
echo   Tuning: http://127.0.0.1:%SM64_PORT%/ui/tune.html   (rank-up climb feel)
echo   Tuning: http://127.0.0.1:%SM64_PORT%/ui/tunemarelo.html   (overall rank-up feel)
echo   The real trainer exe keeps its own port (8064) untouched.
echo   Press CTRL+C in this window to stop the test server.
echo ============================================================
echo.

REM canonical launch (binds the CTRL+C graceful-shutdown deadline)
uv run python -m sm64_events.main

echo.
echo Test server stopped.
pause
endlocal

@echo off
REM ============================================================
REM  Run a TEST server from this checkout.
REM
REM  Starts the SM64 Trainer server FROM SOURCE on a DIFFERENT
REM  port (default 8066) than the packaged trainer exe (8064),
REM  so you can run this alongside the real trainer to test
REM  this checkout's changes live without a port collision.
REM
REM  Usage:
REM    run-test-server.bat            ->  http://127.0.0.1:8066
REM    run-test-server.bat 8070       ->  http://127.0.0.1:8070
REM ============================================================
setlocal
cd /d "%~dp0"

REM Segment definition saves author the defaults for the next release.
REM The UI also offers Save only for me; packaged builds ignore this flag.
set "SM64_MAINTAINER=1"

REM --- pick the port (first arg, else 8066) ---
set "SM64_PORT=%~1"
if "%SM64_PORT%"=="" set "SM64_PORT=8066"

REM --- the lab Scene viewer turns on by itself where this machine has a lab config ---
REM  Its ghost button on each Practice log row, the Scene viewer and its recording
REM  switch exist only when SM64_SCENE_LAB names a lab config. Linked to this file
REM  instead of run-scene-lab.bat, his test server came up without them, more than
REM  once ("this build doesn't have any ghosts and such set up so i can't test that.
REM  this has happened a few times", 2026-11-07). A machine without the config is
REM  unchanged.
if not defined SM64_SCENE_LAB if exist "%LOCALAPPDATA%\SM64Trainer\scene-lab\lab.json" set "SM64_SCENE_LAB=%LOCALAPPDATA%\SM64Trainer\scene-lab\lab.json"

REM --- uv is required ---
where uv >nul 2>nul
if errorlevel 1 (
  echo ERROR: 'uv' is not on your PATH.
  echo Install it from https://docs.astral.sh/uv/ then run this again.
  echo.
  pause
  exit /b 1
)

REM --- update main only; preserve a worktree's reviewed candidate ---
for /f "delims=" %%B in ('git rev-parse --abbrev-ref HEAD') do set "SM64_BRANCH=%%B"
if /i "%SM64_BRANCH%"=="main" (
  echo Fetching the latest committed main...
  git pull --ff-only
  if errorlevel 1 (
    echo WARNING: could not fast-forward ^(uncommitted local changes or a
    echo          diverged branch^). Running the CURRENT checkout instead of
    echo          the very latest main.
  )
) else (
  echo Running branch '%SM64_BRANCH%' from this checkout; skipping git pull.
)

REM --- say WHICH CODE this is, every run, success or not ---
REM  The warning above only fires when `git pull` FAILS, so the common bad case
REM  is silent: a window opened before the fix landed, or a browser serving
REM  cached files, looks exactly like a working checkout. That cost a whole
REM  round on 2026-07-28 -- a fix already on main was reported as "still
REM  broken", twice, and proving otherwise took a live re-measure. One line
REM  makes "am I running the fix?" answerable at a glance.
for /f "delims=" %%C in ('git log -1 --format^="%%h %%ad %%s" --date^=short') do set "SM64_HEAD=%%C"

REM --- make sure dependencies match the lockfile ---
echo Syncing dependencies...
uv sync
if errorlevel 1 (
  echo ERROR: dependency sync failed. The test server was not started.
  pause
  exit /b 1
)

echo.
echo ============================================================
echo   SM64 Trainer  --  TEST server (from this source checkout)
echo   Folder: %CD%
echo   Code:   %SM64_BRANCH% @ %SM64_HEAD%
echo   Segment saves: shared defaults for the next release
echo   URL:    http://127.0.0.1:%SM64_PORT%
echo           (hard-refresh with CTRL+SHIFT+R -- the browser caches ui/)
REM  Every dev/demo page this server hosts gets printed HERE, with the port
REM  already filled in. The tuning page existed for a day behind a hand-typed
REM  path and was unreachable the moment this script picked a different port
REM  (2026-07-27) -- if you add another one, add its line here too.
echo   Tuning: http://127.0.0.1:%SM64_PORT%/ui/tune.html   (rank-up climb feel)
echo   Tuning: http://127.0.0.1:%SM64_PORT%/ui/tunemarelo.html   (overall rank-up feel)
echo   Tuning: http://127.0.0.1:%SM64_PORT%/ui/tunelog.html   (practice log card layout)
echo   Tuning: http://127.0.0.1:%SM64_PORT%/ui/tuneselector.html   (selector card exchange)
echo   Tuning: http://127.0.0.1:%SM64_PORT%/ui/tunefeed.html   (feed arrival + every dropdown)
if defined SM64_SCENE_LAB echo   Lab:    Scene viewer in the sidebar, under Lab   (%SM64_SCENE_LAB%)
echo   The real trainer exe keeps its own port (8064) untouched.
echo   Press CTRL+C in this window to stop the test server.
echo ============================================================
echo.

REM --- the lab Scene viewer (run-scene-lab.bat) runs the game's code in Unicorn;
REM     tools/scene_lab/cpu_hooks.c reads Unicorn 2.1.4's internals, so pin it ---
set "SM64_UV_WITH="
if defined SM64_SCENE_LAB set "SM64_UV_WITH=--with unicorn==2.1.4"

REM canonical launch (binds the CTRL+C graceful-shutdown deadline)
uv run %SM64_UV_WITH% python -m sm64_events.main

echo.
echo Test server stopped.
pause
endlocal

@echo off
REM ============================================================
REM  Run a TEST server with the lab Scene viewer turned on.
REM
REM  The same test server as run-test-server.bat (port 8066
REM  unless one is given), with SM64_SCENE_LAB naming the lab
REM  config in %LOCALAPPDATA%\SM64Trainer\scene-lab\lab.json:
REM  the ROM, the scene packages folder, the Project64 Plugin
REM  folder (read only) and the symbol map. The viewer appears
REM  in the sidebar under Lab; without this, nothing loads.
REM
REM  Usage:
REM    run-scene-lab.bat            ->  http://127.0.0.1:8066
REM    run-scene-lab.bat 8070       ->  http://127.0.0.1:8070
REM ============================================================
setlocal
set "SM64_SCENE_LAB=%LOCALAPPDATA%\SM64Trainer\scene-lab\lab.json"
if not exist "%SM64_SCENE_LAB%" (
  echo ERROR: no lab config at %SM64_SCENE_LAB%
  echo The Scene viewer is a lab feature and has not been set up on this machine.
  echo.
  pause
  exit /b 1
)
call "%~dp0run-test-server.bat" %*
endlocal

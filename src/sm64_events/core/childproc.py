# SPDX-License-Identifier: GPL-2.0-or-later
# Runs in the simulation program (SM64TrainerSim.exe); licenses/README.md lists its GPL-2.0-or-later code.
"""One door for how a BACKGROUND child process is spawned on Windows.

Two flags, both load-bearing and both invisible when missing:

- CREATE_NO_WINDOW — without it, Windows 11's default-terminal handoff opens a
  fully activated Windows Terminal window for any child that needs a console.
- STARTF_FORCEOFFFEEDBACK — without it, every CreateProcess from the shipped
  windowed exe flips the user's POINTER to the "working in background" spinner
  for up to 2 s, even though no window ever appears. One spawn is a blink; the
  AV sink respawning a dying ffmpeg turned it into a permanently flashing
  cursor on a user's machine (report 2026-08-07: "when I open it my mouse
  stars flashing"). Measured the same day from a windowed parent spawning a
  short-lived child 3x/s: the pointer was the busy spinner in 219/290 samples
  with default flags, 1/290 with this flag.

User-GESTURE spawns keep their own arrangements on purpose — the Explorer
reveal, the bootstrap installer, the update relaunch. Their window or busy
cursor is feedback the user's own click earned (the focus rule: who asked for
it decides). tests/test_single_source.py pins that every other spawn site in
the package comes through here.

A PYTHON child has a third trap: the venv's python.exe is a launcher that
starts the real interpreter as ITS child, and it does not pass
STARTF_FORCEOFFFEEDBACK on (measured 2026-09-27: the interpreter behind the
launcher reads startup flags 0x100, one started directly 0x180). The launcher
also costs ~106 ms per start and hides the real process, and its memory, one
generation down. `python_child` starts the interpreter itself.
"""
import os
import subprocess
import sys
from pathlib import Path

# src/ of THIS checkout: a shared editable venv may belong to another worktree.
_SOURCE_ROOT = Path(__file__).resolve().parents[2]


def quiet_spawn_kwargs() -> dict:
    """Popen/run kwargs for a child that must touch neither the screen nor the
    pointer. Empty off Windows, so call sites need no platform guard."""
    if not hasattr(subprocess, "STARTUPINFO"):
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= getattr(subprocess, "STARTF_FORCEOFFFEEDBACK", 0x80)
    # The literal fallbacks are the documented Win32 values — this line runs
    # only where STARTUPINFO exists (Windows), so they are always right.
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
            "startupinfo": startupinfo}


def isolated_python_child(script: Path, frozen_flag: str, args=()) -> list[str]:
    """Arguments for a user-requested privileged helper with no site/PYTHONPATH.

    The caller owns UAC. Interpreter choice stays here, alongside background
    Python children; the venv launcher must not add another process or window.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, frozen_flag, *args]
    interpreter = Path(getattr(sys, "_base_executable", sys.executable))
    windowless = interpreter.with_name("pythonw.exe")
    if not windowless.is_file():
        raise OSError("This Python installation has no windowless administrator helper")
    return [str(windowless), "-I", "-S", str(script), *args]


#: The most lines one child's error output may put in the log; past it one line says the rest was not logged.
CHILD_LOG_LINES = 200


def log_child_stderr(process, name: str, *, limit: int = CHILD_LOG_LINES):
    """Copies a child's error output (its stderr, opened as a pipe) into this process's log, one WARNING per line
    under the logger `sm64.child.<name>`, on a daemon thread that also keeps the pipe drained so the child never
    blocks on it. A Ghost Lab child's traceback (a crashed cutter, resolver or evaluator) used to go to DEVNULL or
    a console window; the installed app has neither, so the app's own log is the one place it can be read. A
    chatty child is capped at `limit` lines. Returns the thread, or None when the child has no stderr pipe."""
    import logging
    import threading
    stream = getattr(process, "stderr", None)
    if stream is None:
        return None
    log = logging.getLogger(f"sm64.child.{name}")

    def drain() -> None:
        written = 0
        try:
            while raw := stream.readline():                # bytes or text; empty at the pipe's end
                line = (raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw).rstrip()
                if not line:
                    continue
                if written < limit:
                    log.warning("%s", line)
                elif written == limit:
                    log.warning("(more output from this child is not logged)")
                written += 1
        except (OSError, ValueError):
            pass

    thread = threading.Thread(target=drain, name=f"stderr-{name}", daemon=True)
    thread.start()
    return thread


def other_program_env(env: dict) -> dict:
    """`env` for another PyInstaller program than this one (the simulation program beside the Trainer): without
    this process's PyInstaller variables (`_MEI*`, `_PYI_*`), which name THIS program's contents folder and would
    make the other one load from it (core/relaunch.py drops them for the same reason)."""
    return {key: value for key, value in env.items() if not key.startswith(("_MEI", "_PYI"))}


def python_child(module: str, frozen_flag: str, args=(), *,
                 site_packages: bool = False) -> tuple[list[str], dict]:
    """(argv, env) that run `module` as a direct child interpreter.

    Frozen: the exe re-enters itself through `frozen_flag` (gui_entry.py
    dispatches it), except a Ghost Lab child that sm64_events/scenelab_code.py
    assigns to the simulation program, which starts that program beside this
    exe instead (sim_entry.py dispatches it). env is a copy of this process's
    environment, so a spawner may extend it. Source: the base interpreter,
    never the venv launcher, with this checkout's src/ first on the path.
    `site_packages` is for a child that imports third-party packages (the
    ghost overlay needs pywebview): `__PYVENV_LAUNCHER__` is how CPython's
    own launcher tells the base interpreter which venv it is running, so the
    child gets that venv's site-packages without the launcher in between."""
    env = os.environ.copy()
    if getattr(sys, "frozen", False):
        from sm64_events.scenelab_code import SIM_PROGRAM, child_program
        program = sys.executable
        if child_program(frozen_flag) == SIM_PROGRAM:
            program = str(Path(sys.executable).with_name(SIM_PROGRAM))
            env = other_program_env(env)
        env.pop("__PYVENV_LAUNCHER__", None)
        return [program, frozen_flag, *args], env
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (
        str(_SOURCE_ROOT), env.get("PYTHONPATH"))))
    if site_packages and sys.prefix != sys.base_prefix:
        env["__PYVENV_LAUNCHER__"] = sys.executable
    else:
        env.pop("__PYVENV_LAUNCHER__", None)
    interpreter = getattr(sys, "_base_executable", None) or sys.executable
    return [interpreter, "-m", module, *args], env

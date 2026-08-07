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
"""
import subprocess


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

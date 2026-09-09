"""Report development server processes without changing their lifetime.

SessionStart is not authorization to stop a server. A socketless PID may be
a venv launcher whose child owns the listener, a booting server, or a valid
broadcast-only recorder. The former socket-based cleanup killed a launcher
during live practice. Neither sockets nor command-line matching prove ownership.

This hook is always read-only, including its legacy --report invocation.
An agent must clean up its own explicitly tracked test children separately;
the human owns the running trainer and its launcher.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

_TARGET_MARKERS = ("-m http.server", "-m sm64_events.main")

# This tool runs from a SessionStart hook, and the hook runner has no console
# of its own — so Windows allocates one for every console child it spawns,
# which flashes a window on screen and takes the keyboard from whatever the
# user is typing into ("something pops up and i end up messing with it",
# 2026-07-25). Every PowerShell call below is invisible.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_PS_ENUM = (
    "$procs = Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | "
    "Select-Object ProcessId, CommandLine, CreationDate; "
    "$listens = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | "
    "Select-Object -ExpandProperty OwningProcess -Unique; "
    "@{procs = @($procs | ForEach-Object { @{pid = $_.ProcessId; cmd = [string]$_.CommandLine} }); "
    "listening = @($listens)} | ConvertTo-Json -Depth 4 -Compress"
)


def _enumerate() -> tuple[list[dict], set[int]]:
    """Return (python processes, set of pids holding a listening socket)."""
    raw = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", _PS_ENUM],
        capture_output=True, text=True, timeout=30,
        creationflags=_NO_WINDOW,
    )
    data = json.loads(raw.stdout)
    procs = data.get("procs") or []
    if isinstance(procs, dict):  # ConvertTo-Json collapses single-item arrays
        procs = [procs]
    listening = data.get("listening") or []
    if isinstance(listening, int):
        listening = [listening]
    return procs, {int(p) for p in listening}


def sweep() -> int:
    procs, listening = _enumerate()
    self_pid = os.getpid()

    for proc in procs:
        proc_pid = int(proc["pid"])
        cmd = proc.get("cmd") or ""
        if proc_pid == self_pid or not any(m in cmd for m in _TARGET_MARKERS):
            continue
        state = "listening" if proc_pid in listening else "no listener on this PID"
        print(f"dev_cleanup: observed ({state}) pid {proc_pid}: {cmd[:100]}")
    return 0


def main() -> int:
    try:
        return sweep()
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
        print(f"dev_cleanup: skipped ({exc})", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())

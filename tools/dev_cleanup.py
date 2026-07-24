"""Sweep orphaned dev processes left behind by finished Claude sessions.

WHY: sessions start throwaway `python -m http.server` harness servers (UI
verification) and `python -m sm64_events.main` dev servers, and sometimes die
or forget to kill them. A 2026-07-24 system audit found FIVE leaked
http.servers plus a duplicate broadcast-only main server still running hours
later, contributing to user-visible system lag (they poll, hold handles, and
pile up across a working day). This tool makes "orphans never happen"
enforceable: it runs from a SessionStart hook (see .claude/settings.json) and
kills only processes that are PROVABLY dead weight.

Kill criteria (deliberately conservative — a concurrent session's live server
must never be touched):
  * python process whose command line contains `-m http.server` or
    `-m sm64_events.main` AND which holds NO listening TCP socket. A server
    with no listening socket serves nobody: either its bind failed (port
    already taken by a sibling) or it's wedged. Live servers always listen.

Anything that DOES hold a socket is only reported (stdout), never killed —
use --report to see the survivors and judge staleness yourself.

Windows-only (matches this project's runtime); enumeration via PowerShell
CIM + Get-NetTCPConnection because that needs no extra dependencies.

Usage:
    uv run python tools/dev_cleanup.py            # kill provable orphans, report the rest
    uv run python tools/dev_cleanup.py --report   # report only, kill nothing
"""
from __future__ import annotations

import json
import subprocess
import sys

_TARGET_MARKERS = ("-m http.server", "-m sm64_events.main")

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
    )
    data = json.loads(raw.stdout)
    procs = data.get("procs") or []
    if isinstance(procs, dict):  # ConvertTo-Json collapses single-item arrays
        procs = [procs]
    listening = data.get("listening") or []
    if isinstance(listening, int):
        listening = [listening]
    return procs, {int(p) for p in listening}


def sweep(kill: bool = True) -> int:
    procs, listening = _enumerate()
    self_pid = None  # this tool runs under python too; never self-target
    import os
    self_pid = os.getpid()

    killed, kept = [], []
    for proc in procs:
        proc_pid = int(proc["pid"])
        cmd = proc.get("cmd") or ""
        if proc_pid == self_pid or not any(m in cmd for m in _TARGET_MARKERS):
            continue
        if proc_pid in listening:
            kept.append((proc_pid, cmd))
            continue
        if kill:
            subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                            f"Stop-Process -Id {proc_pid} -Force -Confirm:$false"],
                           capture_output=True, timeout=15)
        killed.append((proc_pid, cmd))

    verb = "killed" if kill else "would kill"
    for proc_pid, cmd in killed:
        print(f"dev_cleanup: {verb} socketless orphan pid {proc_pid}: {cmd[:100]}")
    for proc_pid, cmd in kept:
        print(f"dev_cleanup: kept (listening) pid {proc_pid}: {cmd[:100]}")
    if not killed and not kept:
        # stay silent when there is nothing to say — this runs from a
        # SessionStart hook and its stdout lands in session context
        pass
    return 0


def main() -> int:
    kill = "--report" not in sys.argv
    try:
        return sweep(kill=kill)
    except Exception as exc:  # fail open: a cleanup tool must never block a session
        print(f"dev_cleanup: skipped ({exc})", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())

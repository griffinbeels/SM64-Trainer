"""Identify uncoordinated test controllers, regardless of Claude/Codex parent.

Updated controllers register before queueing. The process birth time prevents
stale tickets/PID reuse from hiding an older runner. We observe other runs;
we never stop them. A competing run invalidates a performance comparison.

A process's command line, ancestry and directory are read ONCE per process
(keyed by PID and birth time) and remembered while it lives. Reading them
for every Python process on every call cost 1.0-1.2 s with 36 of them on
this desktop, and the runner's watcher calls this every two seconds on the
controller, whose test thread then ran at ~65-75% of its solo speed under
the GIL (2026-09-22). Which of them count as ours is still decided on every
call, from the registrations as they are now.
"""
from pathlib import Path

import psutil

PYTHON_NAMES = {"python.exe", "pythonw.exe", "pytest.exe", "uv.exe"}
CONTROLLER_ARGS = {"run_tests.py", "pytest.exe", "pytest-script.py", "measure_run_load.py"}
# (pid, birth time) -> None for a process that is not a test controller, else
# what a report needs; pruned to the living on every call.
_SEEN: dict[tuple[int, float], dict | None] = {}
# (pid, birth time) -> its ancestors' PIDs. parents() walks the tree through
# a whole-system snapshot per step: ~60 ms each here.
_ANCESTRY: dict[tuple[int, float], set[int]] = {}


def _ancestors(proc) -> set[int]:
    key = (proc.pid, proc.create_time())
    if key not in _ANCESTRY:
        _ANCESTRY[key] = {parent.pid for parent in proc.parents()}
    return _ANCESTRY[key]


def _controller_facts(proc: psutil.Process) -> dict | None:
    key = (proc.pid, proc.create_time())
    if key not in _SEEN:
        try:
            argv = proc.cmdline()
            if not any(arg == "pytest" or Path(arg).name in CONTROLLER_ARGS for arg in argv):
                _SEEN[key] = None
            else:
                _SEEN[key] = {"pid": proc.pid, "started": key[1], "checkout": proc.cwd(),
                              "parents": {parent.pid for parent in proc.parents()}}
        except psutil.AccessDenied:
            # Do not advertise an all-clear if a plausible test controller
            # cannot be inspected. Its PID is enough to explain the wait.
            _SEEN[key] = {"pid": proc.pid, "started": None,
                          "checkout": "unreadable Python process", "parents": set()}
    return _SEEN[key]


def live_registrations(directory: Path) -> set[int]:
    live = set()
    for ticket in directory.glob("*.runner"):
        try:
            pid, born = ticket.stem.split("-", 1)
            proc = psutil.Process(int(pid))
            if proc.create_time() == float(born):
                live.add(proc.pid)
            else:
                ticket.unlink(missing_ok=True)
        except (ValueError, psutil.NoSuchProcess):
            ticket.unlink(missing_ok=True)
            continue  # A crash leaves a harmless stale ticket, never a lock.
    return live


def competing_runs(directory: Path) -> list[dict]:
    """One row per outside controller tree; never count our own workers."""
    current = psutil.Process()
    known = live_registrations(directory) | {current.pid}
    ancestors = set(_ancestors(current))
    # uv/venv launchers remain alive while their registered Python child is
    # queued. Treating those wrappers as legacy creates a circular wait.
    # Exclude the ancestors THEMSELVES, never all their sibling descendants.
    for pid in known:
        try:
            ancestors |= _ancestors(psutil.Process(pid))
        except psutil.NoSuchProcess:
            continue
    for key in [key for key in _ANCESTRY if key[0] not in known]:
        del _ANCESTRY[key]
    found, living = {}, set()
    for proc in psutil.process_iter(["name"]):
        if (proc.info["name"] or "").lower() not in PYTHON_NAMES:
            continue
        try:
            facts = _controller_facts(proc)
            living.add((proc.pid, proc.create_time()))
        except psutil.NoSuchProcess:
            continue
        if facts is None or proc.pid in ancestors | known or facts["parents"] & known:
            continue
        found[proc.pid] = facts
    for key in _SEEN.keys() - living:
        del _SEEN[key]
    return [{key: value for key, value in row.items() if key != "parents"}
            for row in found.values() if not row["parents"] & found.keys()]

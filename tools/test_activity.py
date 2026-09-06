"""Identify uncoordinated test controllers, regardless of Claude/Codex parent.

Updated controllers register before queueing. The process birth time prevents
stale tickets/PID reuse from hiding an older runner. We observe other runs;
we never stop them. A competing run invalidates a performance comparison.
"""
from pathlib import Path

import psutil


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
    ancestors = {proc.pid for proc in current.parents()}
    # uv/venv launchers remain alive while their registered Python child is
    # queued. Treating those wrappers as legacy creates a circular wait.
    # Exclude the ancestors THEMSELVES, never all their sibling descendants.
    for pid in known:
        try:
            ancestors.update(proc.pid for proc in psutil.Process(pid).parents())
        except psutil.NoSuchProcess:
            continue
    found = {}
    for proc in psutil.process_iter(["name"]):
        if (proc.info["name"] or "").lower() not in {"python.exe", "pythonw.exe", "pytest.exe", "uv.exe"}:
            continue
        try:
            argv = proc.cmdline()
            if not any(arg == "pytest" or Path(arg).name in
                       {"run_tests.py", "pytest.exe", "pytest-script.py", "measure_run_load.py"}
                       for arg in argv):
                continue
            parents = {parent.pid for parent in proc.parents()}
            if proc.pid in ancestors | known or parents & known:
                continue
            found[proc.pid] = {"pid": proc.pid, "started": proc.create_time(),
                               "checkout": proc.cwd(), "parents": parents}
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            # Do not advertise an all-clear if a plausible test controller
            # cannot be inspected. Its PID is enough to explain the wait.
            found[proc.pid] = {"pid": proc.pid, "started": None,
                               "checkout": "unreadable Python process", "parents": set()}
    return [{key: value for key, value in row.items() if key != "parents"}
            for row in found.values() if not row["parents"] & found.keys()]

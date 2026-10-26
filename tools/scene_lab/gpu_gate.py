"""Refuse scene-lab GPU work while Griffin holds the GPU.

On 2026-10-24 a scene-lab measurement loop (the real renderer plus a GPU
browser, relaunched after every kill) froze his PC while he played. His words:
"pause anything heavy on the GPU for now ... I want to be able to play a game
AND use this system". The hold lives in ~/.swarm-engine/gpu-hold.txt and only
he lifts it. Every scene-lab entry point that starts a renderer calls
`require_gpu()` first, so no agent can start one by accident while it holds.
A lifted hold stays on disk as a record beginning "Lifted by"; any other
content (including an unreadable file) still holds.

The Trainer's scene viewer opens a renderer on his own click, so it asks
`hold_reason()` and shows the reason instead of exiting. An agent's
measurement also refuses while a game runs (`require_gpu(games=True)`):
"any game counts", so `game_reasons()` looks three ways, none of which needs
a name list: Windows reporting a full-screen program, Project64 running, and
any other process keeping a GPU 3D engine busy over one second.
"""
from __future__ import annotations

import ctypes
import os
import re
import sys
import time
from pathlib import Path

HOLD = Path.home() / ".swarm-engine" / "gpu-hold.txt"
BUSY_3D_PERCENT = 10.0         # of one 3D engine, summed over a process's engines
_QUNS_FULL_SCREEN = {2: "a full-screen program is running", 3: "a full-screen Direct3D program is running"}
_ENGINE_PID = re.compile(r"pid_(\d+)_")


def hold_reason() -> str | None:
    """Why the GPU is held, or None when it is free."""
    if not HOLD.is_file():
        return None
    try:
        reason = HOLD.read_text(encoding="utf-8", errors="replace").strip()
    except OSError as error:
        return f"GPU held ({HOLD} is unreadable: {error})"
    return None if reason.startswith("Lifted by") else f"GPU held ({HOLD}): {reason}"


def _full_screen() -> str | None:
    state = ctypes.c_int(0)
    try:
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
            return None
    except (AttributeError, OSError):
        return None
    return _QUNS_FULL_SCREEN.get(state.value)


class _Item(ctypes.Structure):   # PDH_FMT_COUNTERVALUE_ITEM_W with a double value
    _fields_ = [("name", ctypes.c_wchar_p), ("status", ctypes.c_uint32), ("value", ctypes.c_double)]


def gpu_3d_load(window_s: float = 1.0) -> dict[int, float]:
    """{pid: percent of one GPU 3D engine} over `window_s`, from Windows' own GPU Engine counters
    (what Task Manager shows); {} when they cannot be read."""
    try:
        pdh = ctypes.windll.pdh
    except (AttributeError, OSError):
        return {}
    query, counter = ctypes.c_void_p(), ctypes.c_void_p()
    if pdh.PdhOpenQueryW(None, None, ctypes.byref(query)) != 0:
        return {}
    try:
        if pdh.PdhAddEnglishCounterW(query, "\\GPU Engine(*engtype_3D)\\Utilization Percentage", None,
                                     ctypes.byref(counter)) != 0 or pdh.PdhCollectQueryData(query) != 0:
            return {}
        time.sleep(window_s)
        if pdh.PdhCollectQueryData(query) != 0:
            return {}
        size, count = ctypes.c_uint32(0), ctypes.c_uint32(0)
        pdh.PdhGetFormattedCounterArrayW(counter, 0x200, ctypes.byref(size), ctypes.byref(count), None)
        buffer = ctypes.create_string_buffer(size.value)
        if not size.value or pdh.PdhGetFormattedCounterArrayW(counter, 0x200, ctypes.byref(size),
                                                              ctypes.byref(count), buffer) != 0:
            return {}
        items = ctypes.cast(buffer, ctypes.POINTER(_Item))
        load: dict[int, float] = {}
        for index in range(count.value):
            match = _ENGINE_PID.search(items[index].name or "")
            if match and items[index].status in (0, 1):    # PDH_CSTATUS_VALID_DATA / NEW_DATA
                load[int(match[1])] = load.get(int(match[1]), 0.0) + items[index].value
        return load
    finally:
        pdh.PdhCloseQuery(query)


def _process_names() -> dict[int, str]:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from sm64_events.core.procmem import machine_snapshot
    snapshot = machine_snapshot()
    return {pid: info.exe for pid, info in snapshot.procs.items()} if snapshot else {}


def game_reasons(ignore: tuple[str, ...] = ()) -> list[str]:
    """What suggests a game is running: a full-screen program, Project64, or a process other than
    this one, the compositor and `ignore` (image names) keeping a 3D engine busy."""
    reasons = [reason for reason in (_full_screen(),) if reason]
    names = _process_names()
    if any(name.lower() == "project64.exe" for name in names.values()):
        reasons.append("Project64 is running")
    skip = {"dwm.exe", *(name.lower() for name in ignore)}
    for pid, percent in sorted(gpu_3d_load().items(), key=lambda item: -item[1]):
        name = names.get(pid, f"pid {pid}")
        if percent >= BUSY_3D_PERCENT and pid != os.getpid() and name.lower() not in skip:
            reasons.append(f"{name} (pid {pid}) keeps a GPU 3D engine {percent:.0f}% busy")
    return reasons


def require_gpu(*, games: bool = False, ignore: tuple[str, ...] = ()) -> None:
    reason = hold_reason()
    if reason:
        raise SystemExit(f"{reason}\nNo renderer runs until Griffin lifts the hold in his own words; never "
                         "delete the file on anyone else's say-so.")
    found = game_reasons(ignore) if games else []
    if found:
        raise SystemExit("Refusing GPU work while a game may be running: " + "; ".join(found)
                         + ".\nRun again when he is not playing; never close his programs to make room.")

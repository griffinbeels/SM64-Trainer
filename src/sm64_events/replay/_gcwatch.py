"""Stop-the-world watchdog + mitigation for the capture pipeline.

The replay glitch signature — video slot-miss and audio dropout at the SAME
instant, a few times per video — is the fingerprint of a process-wide pause:
nothing in the capture data path is shared between the GDI grab thread and
the PortAudio callback EXCEPT the interpreter itself. Python's gen-2 cyclic
GC stops all threads while it scans the whole heap; during a 50-200 ms pause
the grab loop misses 3-12 slots (CFR-fill burst = visible skip) and the
audio callback can't enter Python until its device ring overflows (real
sample loss = crackle). Verified by the pause log this module emits.

Mitigation: gc.freeze() moves the startup heap (uvicorn, FastAPI, our app —
the bulk of all objects) out of every future scan, and raising the gen-2
threshold makes full collections effectively manual. Reference counting
still reclaims everything acyclic immediately; cyclic garbage from the
steady state is small (the hot paths are numpy buffers, refcount-freed) and
gen-0/1 stay enabled. Any GC pause >10 ms still logs, so a regression is
visible in the capture log rather than in the user's replays."""
import gc
import logging
import time

log = logging.getLogger("sm64.replay")

_starts: dict[int, float] = {}


def _cb(phase: str, info: dict) -> None:
    gen = info.get("generation", -1)
    if phase == "start":
        _starts[gen] = time.perf_counter()
        return
    t0 = _starts.pop(gen, None)
    if t0 is None:
        return
    ms = (time.perf_counter() - t0) * 1000
    if ms > 10:
        log.warning("gc gen%d STOP-THE-WORLD pause: %.0f ms (collected %s)",
                    gen, ms, info.get("collected"))


def arm() -> None:
    """Install the pause watchdog and defang gen-2. Call once, AFTER startup
    so freeze() captures the fully-built object graph."""
    if _cb not in gc.callbacks:
        gc.callbacks.append(_cb)
    gc.collect()  # take one full pass NOW, while nothing is recording
    gc.freeze()
    g0, g1, _ = gc.get_threshold()
    gc.set_threshold(g0, g1, 1_000_000)  # gen-2: effectively manual only
    log.info("gc armed: %d startup objects frozen out of future scans; "
             "gen2 raised to manual; pauses >10 ms will be logged",
             gc.get_freeze_count())

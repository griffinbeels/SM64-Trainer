"""Stop-the-world watchdog + GC policy for the capture pipeline.

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
threshold makes automatic full collections effectively never fire. Reference
counting still reclaims everything acyclic immediately; gen-0/gen-1 still run
and still collect short-lived cycles.

THE LEAK THIS USED TO HAVE: raising the gen-2 threshold to ~manual without
EVER running a manual gen-2 collection meant any cyclic object that survived
into gen-2 was never reclaimed for the process lifetime — an unbounded leak
across a long session (the exact "machine ran out of memory after hours"
report on 2026-06-13). The fix is _Gen2Collector: it runs gc.collect(2)
OPPORTUNISTICALLY WHILE THE RECORDER IS IDLE — footage is discarded then, so
a stop-the-world pause costs nothing and never lands in a clip — with a
long-interval force backstop so a never-idle session still bounds gen-2
garbage (at one predictable, logged glitch every force_after_s). Any GC pause
>10 ms still logs, so a regression stays visible in the capture log.
"""
import gc
import logging
import threading
import time

log = logging.getLogger("sm64.replay")

_starts: dict[int, float] = {}

_COLLECT_POLL_S = 30.0     # idle check cadence (also the idle collect rate)
_FORCE_COLLECT_S = 300.0   # never-idle backstop: bound gen-2 garbage to ~5 min

_collector: "_Gen2Collector | None" = None  # process singleton (start once)


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


def should_collect(idle: bool, secs_since_collect: float,
                   force_after_s: float) -> bool:
    """Run a gen-2 collection now? Yes while idle (the pause is invisible —
    discarded footage), or when it's been too long regardless (bounds gen-2
    garbage in a never-idle session). Pure — unit-tested."""
    return idle or secs_since_collect >= force_after_s


class _Gen2Collector:
    """Daemon thread that performs the 'manual' gen-2 collection the
    threshold bump defers. collect_fn is injectable for tests."""

    def __init__(self, is_idle, *, poll_s: float = _COLLECT_POLL_S,
                 force_after_s: float = _FORCE_COLLECT_S, collect_fn=None):
        self._is_idle = is_idle
        self._poll_s = poll_s
        self._force_after_s = force_after_s
        self._collect = collect_fn or (lambda: gc.collect(2))
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="gc-gen2",
                                        daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        """Tests/clean teardown. Production never calls this — the daemon
        thread dies with the process."""
        self._stop.set()
        self._thread.join(timeout=2)

    def _loop(self) -> None:
        last = time.monotonic()
        while not self._stop.wait(self._poll_s):
            since = time.monotonic() - last
            try:
                if should_collect(self._is_idle(), since, self._force_after_s):
                    reclaimed = self._collect()
                    last = time.monotonic()
                    if reclaimed:
                        log.info("gc gen2 manual collect reclaimed %d objects",
                                 reclaimed)
            except Exception:
                log.exception("gen-2 collector iteration failed")


def arm(is_idle=None) -> None:
    """Install the pause watchdog, defang automatic gen-2, freeze the startup
    heap, and (when is_idle is given) start the idle-time gen-2 collector.
    Call once, AFTER startup so freeze() captures the fully-built graph.

    is_idle: zero-arg callable returning True while footage is discarded
    (recorder.is_idle). Without it the collector is NOT started and gen-2
    garbage would accumulate — callers that disable gen-2 MUST pass it."""
    global _collector
    if _cb not in gc.callbacks:
        gc.callbacks.append(_cb)
    gc.collect()  # take one full pass NOW, while nothing is recording
    gc.freeze()
    g0, g1, _ = gc.get_threshold()
    gc.set_threshold(g0, g1, 1_000_000)  # gen-2: automatic collection ~off
    if is_idle is not None and _collector is None:
        _collector = _Gen2Collector(is_idle)
        _collector.start()
    log.info("gc armed: %d startup objects frozen out of future scans; "
             "gen2 auto-collection disabled, manual collector %s; "
             "pauses >10 ms will be logged", gc.get_freeze_count(),
             "started (idle-driven)" if _collector else "NOT started")

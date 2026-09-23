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
OPPORTUNISTICALLY WHEN THE RECORDER ALLOWS IT — paired GPU automatic idle
retains lead-in, so only actual capture retirement qualifies — with a
long-interval force backstop so a never-idle session still bounds gen-2
garbage. Any GC pause >10 ms still logs, so a regression stays visible.

THE BACKSTOP'S COST (2026-09-23): the forced collection walked every
container allocated since startup and still alive, so it paused 54-177 ms
(mean 81 ms over 447 logged collections) every 5 minutes, and each pause
stopped the audio callback (see docs/replay-gpu-runtime.md). The walk, not
the garbage, is the cost: each collection freed only 1.3k-3.8k objects.
So every manual collection now FREEZES its survivors, and the next
"survivors" backstop walks only what was allocated since (measured: about
1 ms, flat as the heap grows, against 27-65 ms for 340k-674k containers).
Cycles that die among frozen objects wait for a FULL collection: unfreeze,
collect everything, freeze again. That runs whenever capture is idle (the
free moment) and at least every _FULL_COLLECT_S in a session that never
goes idle, so the leak stays bounded at one predictable full pause an hour
instead of twelve. Rejected: gen-1 only (frees nothing that reached gen 2,
the 2026-06-13 leak again) and skipping while capturing (the cost is heap
size; skipping only defers it). Every manual collection logs its kind,
the containers it walked and its pause, so a live session measures the
real heap rather than the synthetic numbers above.
"""
import gc
import logging
import threading
import time

log = logging.getLogger("sm64.replay")

_starts: dict[int, float] = {}

_COLLECT_POLL_S = 30.0     # idle check cadence (also the idle collect rate)
_FORCE_COLLECT_S = 300.0   # never-idle backstop: bound gen-2 garbage to ~5 min
_FULL_COLLECT_S = 3600.0   # never-idle full backstop: bound frozen-cycle garbage to ~1 h

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
    """Run a gen-2 collection now? Yes when capture permits, or when it has
    been too long regardless (bounds gen-2
    garbage in a never-idle session). Pure — unit-tested."""
    return idle or secs_since_collect >= force_after_s


def plan(idle: bool, secs_since_collect: float, secs_since_full: float,
         force_after_s: float, full_after_s: float) -> str | None:
    """Which manual collection to run now: "full" (unfreeze everything and
    collect it: capture is idle, or the full backstop is due), "survivors"
    (walk only what was allocated since the last freeze: the 5-minute
    backstop) or None. Pure — unit-tested."""
    if idle or secs_since_full >= full_after_s:
        return "full"
    if should_collect(False, secs_since_collect, force_after_s):
        return "survivors"
    return None


class _Gen2Collector:
    """Daemon thread that performs the 'manual' gen-2 collection the
    threshold bump defers. collect_fn and gc_ops (freeze/unfreeze/count) are
    injectable for tests: the real ones mutate the whole process's GC state."""

    def __init__(self, is_idle, *, poll_s: float = _COLLECT_POLL_S,
                 force_after_s: float = _FORCE_COLLECT_S,
                 full_after_s: float = _FULL_COLLECT_S, collect_fn=None, gc_ops=gc,
                 clock=time.monotonic):
        self._is_idle = is_idle
        self._poll_s = poll_s
        self._force_after_s = force_after_s
        self._full_after_s = full_after_s
        self._collect = collect_fn or (lambda: gc.collect(2))
        self._gc = gc_ops
        self._clock = clock
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="gc-gen2",
                                        daemon=True)
        self.last = None  # (kind, walked, pause_ms, reclaimed) of the latest collection

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        """Tests/clean teardown. Production never calls this — the daemon
        thread dies with the process."""
        self._stop.set()
        self._thread.join(timeout=2)

    def run(self, kind: str) -> int:
        """One manual collection of `kind`, then freeze its survivors."""
        walked = len(self._gc.get_objects())  # frozen objects are not listed
        if kind == "full":
            walked += self._gc.get_freeze_count()
            self._gc.unfreeze()
        started = time.perf_counter()
        reclaimed = self._collect()
        pause_ms = (time.perf_counter() - started) * 1000
        self._gc.freeze()  # survivors leave every later "survivors" walk
        self.last = (kind, walked, pause_ms, reclaimed)
        log.info("gc gen2 %s collect: walked %d containers in %.1f ms, reclaimed %s",
                 kind, walked, pause_ms, reclaimed)
        return reclaimed

    def _loop(self) -> None:
        last = last_full = self._clock()
        while not self._stop.wait(self._poll_s):
            now = self._clock()
            try:
                kind = plan(self._is_idle(), now - last, now - last_full,
                            self._force_after_s, self._full_after_s)
                if kind is not None:
                    self.run(kind)
                    last = self._clock()
                    if kind == "full":
                        last_full = last
            except Exception:
                log.exception("gen-2 collector iteration failed")


def arm(is_idle=None) -> None:
    """Install the pause watchdog, defang automatic gen-2, freeze the startup
    heap, and (when is_idle is given) start the idle-time gen-2 collector.
    Call once, AFTER startup so freeze() captures the fully-built graph.

    is_idle: zero-arg callable returning True when capture permits opportunistic
    collection (recorder.can_collect). Without it the collector is NOT started and gen-2
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

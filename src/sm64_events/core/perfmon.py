"""Periodic resource sampler — turns core/procmem.py probes into a session
time-series that survives the process.

WHY A FILE, NOT JUST LOGS: the leak only shows "after hours", and the prior
monitor's evidence (a scrolling `mem:` log line) was aggregate and ephemeral —
you could see RSS climb but not WHAT, and a crash took the trail with it. This
writes one JSON object per sample to data/perf_log.jsonl, so a long session
leaves a machine-readable trail that attributes growth to a specific
process/handle-class/Python-type/subsystem AFTER the fact. Self-analysing:
each interval logs the fastest-growing Python types vs the startup baseline and
fires a one-shot warning per resource class that breaches a leak threshold —
so even an unattended session names its own culprit.

The log is size-capped (rotates to .prev) so the diagnostic can never be the
thing that fills the disk. All sampling is best-effort: a probe failure logs
and the loop continues; the server is never put at risk by its own monitor.

STALLS: each minute records this server's busiest threads, its helper tree,
Project64's address-space headroom and aggregate machine busy/DPC/interrupt
load. No unrelated application names or process rankings enter retained
snapshots, logs or /health. The OS snapshot is reduced immediately to our
process family and the configured emulator. A StallWatch heartbeat records
the same limited evidence across a stall and just after it."""
import json
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from sm64_events.core.procmem import (MachineSnapshot, address_space,
                                      descendants, machine_snapshot,
                                      machine_summary, named_processes,
                                      native_thread_label, resource_alarms,
                                      sample, system_memory, thread_cpu,
                                      top_type_growth, tree_summary)

from sm64_events.core.diagnostic_retention import PERF_LOG_MAX_BYTES

log = logging.getLogger("sm64.procmem")

_MiB = 1024 ** 2
DEFAULT_LOG_MAX_BYTES = PERF_LOG_MAX_BYTES  # one current file and one .prev
_DEFAULT_LOG = Path("data") / "perf_log.jsonl"
_USE_DEFAULT = object()  # sentinel: resolve _DEFAULT_LOG at call time, so tests
                         # (conftest) can redirect it and never write the real
                         # diagnostic file — a test sample would corrupt the
                         # human's session data the analyzer reads.
_MACHINE_FIELDS = ("window_s", "busy_pct", "dpc_pct", "interrupt_pct")
_KNOWN_HELPERS = frozenset({"sm64trainer.exe", "python.exe", "pythonw.exe", "ffmpeg.exe",
                            "ffprobe.exe", "msedgewebview2.exe", "conhost.exe"})


def _machine_fields(value) -> dict | None:
    """Only anonymous aggregate load enters a diagnostic record."""
    if not isinstance(value, dict):
        return None
    return {key: value[key] for key in _MACHINE_FIELDS
            if isinstance(value.get(key), (int, float))}


def _child_fields(value) -> dict | None:
    """Keep owned child resources while hiding unexpected executable names."""
    if not isinstance(value, dict):
        return None
    result = {key: value[key] for key in ("count", "rss_bytes", "private_bytes", "threads")
              if isinstance(value.get(key), (int, float))}
    named = {}
    for name, amount in (value.get("by_name") or {}).items():
        label = name if name.lower() in _KNOWN_HELPERS else "app-child"
        if isinstance(amount, (int, float)):
            named[label] = named.get(label, 0) + amount
    result["by_name"] = named
    members = []
    for member in (value.get("members") or [])[:12]:
        row = {key: member[key] for key in ("pid", "ppid", "private_mib", "rss_mib", "threads", "cpu_pct")
               if isinstance(member.get(key), (int, float))}
        name = member.get("exe", "")
        row["exe"] = name if name.lower() in _KNOWN_HELPERS else "app-child"
        members.append(row)
    result["members"] = members
    return result


def _scope_snapshot(machine: MachineSnapshot, pid: int, watch, sticky):
    """Discard unrelated process names before retaining a sample or baseline."""
    members = descendants(machine, pid, sticky)
    watched = {name.lower() for name in watch}
    child_ids = {child.pid for child in members}
    allowed = child_ids | {pid} | {p.pid for p in machine.procs.values() if p.exe.lower() in watched}
    procs = {}
    for identity in allowed:
        process = machine.procs.get(identity)
        if process is None:
            continue
        if process.exe.lower() not in _KNOWN_HELPERS | watched:
            process = process._replace(exe="Trainer" if identity == pid else "app-child")
        procs[identity] = process
    scoped = MachineSnapshot(machine.t, procs, {pid: machine.threads[pid]} if pid in machine.threads else {}, machine.cpus)
    return scoped, [procs[child.pid] for child in members]


def _machine_totals(previous: MachineSnapshot, current: MachineSnapshot) -> dict:
    return _machine_fields(machine_summary(previous, current, n=0)) or {}


def _mib_reading(value) -> str:
    return "unmeasured" if value is None else f"{value / _MiB:.0f} MiB"


async def _off_loop(operation, *args):
    """Cancellation drains this owned job before lifespan teardown proceeds."""
    import asyncio
    task = asyncio.create_task(asyncio.to_thread(operation, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        # Further cancellation requests must not cancel the asyncio wrapper:
        # a cancelled wrapper cannot establish that its OS thread has stopped.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                log.exception("perfmon worker failed during shutdown")
                raise cancelled from None
        try:
            task.result()
        except Exception:
            log.exception("perfmon worker failed during shutdown")
        raise


def perf_record(snap: dict, gauges: dict, *, uptime_s: float, t_utc: str,
                top_growers: list[dict], top_n_types: int = 30) -> dict:
    """Build the JSON-serialisable line persisted per sample. Trims the full
    type histogram to its top-N (the rest is noise) and folds bytes to MiB for
    readability. Pure — unit-tested; the exact shape IS the analysis contract
    for whatever reads perf_log.jsonl later."""
    types = snap.get("types") or {}
    top_types = dict(sorted(types.items(), key=lambda kv: kv[1],
                            reverse=True)[:top_n_types])
    return {
        "t_utc": t_utc,
        "uptime_s": round(uptime_s, 1),
        "rss_mib": round(snap.get("rss_bytes", 0) / _MiB, 1),
        "private_mib": round(snap.get("private_bytes", 0) / _MiB, 1),
        "objects": snap.get("objects"),
        "handles": snap.get("handles"),
        "gdi": snap.get("gdi_objects"),
        "user": snap.get("user_objects"),
        "threads": snap.get("threads"),
        "os_threads": snap.get("os_threads"),
        "cpu": snap.get("cpu"),
        "gc_counts": snap.get("gc", {}).get("counts"),
        "scratch_mib": (round(snap["scratch_bytes"] / _MiB, 1)
                        if "scratch_bytes" in snap else None),
        "system": snap.get("system"),
        "children": _child_fields(snap.get("children")),
        "gpu": snap.get("gpu"),
        "processes": snap.get("processes"),
        "machine": _machine_fields(snap.get("machine")),
        "gauges": gauges or {},
        "top_growers": top_growers,
        "top_types": top_types,
    }


def start_new_session_log(path: Path | None) -> None:
    """Rotate an existing perf log to .prev at server startup, so each run's
    perf_log.jsonl is SELF-CONTAINED. uptime_s resets per process, so mixing
    runs in one file would break the analyzer's duration math; one run = one log
    matches the recorder's wipe-scratch-on-start model. Prior run is preserved
    in .prev. Best-effort — a failure logs and is swallowed."""
    if path is None:
        return
    try:
        if path.exists():
            os.replace(path, path.with_suffix(path.suffix + ".prev"))
    except Exception:
        log.exception("perf log rotate-on-start failed (continuing)")


def write_perf_record(path: Path, record: dict, *,
                      max_bytes: int = DEFAULT_LOG_MAX_BYTES) -> None:
    """Append one JSONL line, rotating to .prev when the file passes max_bytes
    (bounds disk to ~2x the cap — the diagnostic must never fill the volume).
    Best-effort: a write failure logs and is swallowed."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > max_bytes:
            os.replace(path, path.with_suffix(path.suffix + ".prev"))
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        log.exception("perf log write failed (continuing)")


class StallWatch:
    """A heartbeat that notices when this server could not run on time.

    A thread asks to wake every `period_s`. Waking `threshold_s` or more late
    means nothing here ran for that long: the machine was starved (CPU,
    paging, a driver) or one of our threads held the interpreter. Either way
    `on_stall(late_s)` runs, at most once per `cooldown_s`, to record who was
    busy. A stall at least period + threshold long (1.5 s) is always seen; a
    shorter one only if it covers a wake. A freeze confined to Project64 (its
    GPU work, say) does not delay this thread, so only the minute record
    covers it.

    Cost, measured 2026-09-27: 2.7 ms of CPU per minute at a 1 s period,
    against 10.3 ms at 0.25 s (each wake is ~45 us of thread wake-up and
    interpreter re-entry), which is why the period is not shorter."""

    def __init__(self, on_stall: Callable[[float], object],
                 stop: threading.Event, *, period_s: float = 1.0,
                 threshold_s: float = 0.5, cooldown_s: float = 30.0,
                 clock: Callable[[], float] = time.perf_counter):
        self._on_stall = on_stall
        self._stop = stop
        self._period = period_s
        self._threshold = threshold_s
        self._cooldown = cooldown_s
        self._clock = clock
        self._lock = threading.Lock()
        self._count = 0
        self._worst_s = 0.0
        self._last_capture: float | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if not self.running:
            self._thread = threading.Thread(target=self._run, daemon=True,
                                            name="perf-stall-watch")
            self._thread.start()

    def join(self, timeout: float) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        due = self._clock() + self._period
        while not self._stop.wait(max(0.0, due - self._clock())):
            now = self._clock()
            self.observe(now, now - due)
            # From after the capture: its own seconds are not the next stall.
            due = self._clock() + self._period

    def observe(self, now: float, late_s: float) -> bool:
        """Account one wake that came `late_s` after it was due; True when it
        ran a capture."""
        if late_s < self._threshold or self._stop.is_set():
            return False
        with self._lock:
            self._count += 1
            self._worst_s = max(self._worst_s, late_s)
            if self._last_capture is not None \
                    and now - self._last_capture < self._cooldown:
                return False
            self._last_capture = now
        try:
            self._on_stall(late_s)
        except Exception:
            log.exception("stall capture failed (continuing)")
        return True

    def take_counts(self) -> dict:
        """Stalls seen since the last call, captured or not: {count,
        worst_late_ms}. The minute record carries these."""
        with self._lock:
            out = {"count": self._count,
                   "worst_late_ms": round(self._worst_s * 1000)}
            self._count, self._worst_s = 0, 0.0
        return out


class PerfMonitor:
    """Samples every `interval_s`: logs an expanded `mem:` line + a top-growers
    line, fires each resource alarm once, and appends a perf_log.jsonl record.
    `latest` (trimmed) backs /health. Slow probes/persistence run on a worker;
    application gauges stay on the event loop that owns them. Heap, DXGI and
    scratch scans are opt-in (SM64_PERFMON_DEEP=1), never routine play work."""

    def __init__(self, scratch_dir: Path | None = None,
                 interval_s: float = 60.0,
                 perf_log_path=_USE_DEFAULT,
                 gauges: Callable[[], dict] | None = None,
                 self_pid: int | None = None,
                 max_log_bytes: int = DEFAULT_LOG_MAX_BYTES,
                 watch_processes=("Project64.exe",),
                 enabled: bool = True, deep: bool | None = None,
                 stall_watch: bool = True, stall_followup_s: float = 2.0):
        self._enabled = enabled    # SM64_PERFMON=0 -> run() is a no-op (zero
                                   # per-60s cost: no heap walk, no probes, no
                                   # log line) for audio-sensitive sessions
        self._scratch_dir = scratch_dir
        self._deep = (os.environ.get("SM64_PERFMON_DEEP") == "1"
                      if deep is None else deep)
        self._interval_s = interval_s
        # exe names whose memory we sample alongside ours — PJ64 is NOT our
        # child, so a PJ64 leak (suspected from the 69-min capture's +5 GiB
        # system commit that wasn't us) only shows via this by-name probe.
        self._watch = tuple(watch_processes or ())
        # Keep the raw value; resolve _DEFAULT_LOG LAZILY (at write time) via
        # _logpath() so a conftest patch applies even when the monitor was
        # constructed at import time (main.py builds `app` before tests patch
        # the path) — that's what keeps a server-in-thread test from clobbering
        # the human's real perf_log. _USE_DEFAULT -> module default; None -> no
        # file; explicit Path -> that.
        self._perf_log_path = perf_log_path
        self._gauges = gauges
        self._pid = os.getpid() if self_pid is None else self_pid
        self._max_log_bytes = max_log_bytes
        self._baseline: dict = {}        # first full sample (alarm reference)
        self._baseline_types: dict = {}  # first histogram (growth reference)
        self._fired: set[str] = set()    # alarm messages already warned
        self._t0 = time.monotonic()
        self.latest: dict = {}
        # Snapshot state is shared by the minute worker and the stall watch.
        self._state_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._minute_prev: MachineSnapshot | None = None
        self._recent: deque = deque(maxlen=3)   # stall baselines, oldest first
        self._tree_seen: frozenset = frozenset()  # (pid, created) of helpers
        self._native_labels: dict = {}          # (tid, created) -> label
        self._stop = threading.Event()
        self._followup_s = stall_followup_s
        self._stalls = (StallWatch(self._capture_stall, self._stop)
                       if stall_watch else None)

    def _logpath(self) -> Path | None:
        """Resolve the perf-log path at use time (see __init__)."""
        return (_DEFAULT_LOG if self._perf_log_path is _USE_DEFAULT
                else self._perf_log_path)

    @property
    def log_path(self) -> Path | None:
        """Where samples actually land (None = persistence disabled) — the
        public door the debug report reads."""
        return self._logpath()

    def _collect(self) -> dict:
        """Routine sampling does not grow with heap size or retained files."""
        machine = machine_snapshot(thread_pids=(self._pid,))
        if machine is None:  # off Windows: the stateless probes alone
            snap = sample(self._scratch_dir if self._deep else None,
                          count_objects=self._deep, resources=True,
                          children_of=self._pid, histogram=self._deep,
                          gpu=self._deep, processes=self._watch)
            snap["children"] = _child_fields(snap.get("children"))
            return snap
        snap = sample(self._scratch_dir if self._deep else None,
                      count_objects=self._deep, resources=True,
                      histogram=self._deep, gpu=self._deep)
        snap.update(self._machine_view(machine))
        return snap

    def _machine_view(self, machine: MachineSnapshot) -> dict:
        """What one snapshot answers against the previous minute's: this
        server's threads, its whole helper tree, the watched processes (with
        Project64's address-space headroom) and aggregate machine load."""
        with self._state_lock:
            machine, members = _scope_snapshot(machine, self._pid, self._watch, self._tree_seen)
            prev, self._minute_prev = self._minute_prev, machine
            self._recent.append(machine)
            self._tree_seen = frozenset((p.pid, p.created) for p in members)
        watched = named_processes(machine, self._watch, prev)
        for exe, slot in watched.items():
            spaces = [address_space(p.pid, only_32bit=True)
                      for p in machine.procs.values() if p.exe == exe]
            spaces = [s for s in spaces if s]
            if spaces:  # several instances: report the one closest to full
                slot.update(min(spaces, key=lambda s: s["vm_largest_free_mib"]))
        me = machine.procs.get(self._pid)
        view = {"children": tree_summary(members, prev, machine),
                "processes": watched,
                "os_threads": me.threads if me is not None else None}
        if prev is not None:
            view["cpu"] = thread_cpu(prev, machine, self._pid, self._labeler())
            view["machine"] = _machine_totals(prev, machine)
        return view

    def _labeler(self) -> Callable[[int, int], str]:
        """Thread names for thread_cpu: Python's own name for threads it
        started, else a cached native label."""
        python = {t.native_id: t.name for t in threading.enumerate()
                  if t.native_id is not None}
        cache = self._native_labels
        if len(cache) > 256:  # pools churn native threads; bound the cache
            cache.clear()

        def label(tid: int, created: int) -> str:
            if tid in python:
                return python[tid]
            name = cache.get((tid, created))  # the other thread may clear it
            if name is None:
                name = cache[(tid, created)] = native_thread_label(tid)
            return name
        return label

    def _stall_baseline(self, stall_began: float) -> MachineSnapshot | None:
        """The newest snapshot taken before the stall began (so the window
        covers all of it), else the oldest one kept."""
        with self._state_lock:
            before = [s for s in self._recent if s.t <= stall_began]
            return before[-1] if before else (self._recent[0]
                                              if self._recent else None)

    def _capture_stall(self, late_s: float) -> dict | None:
        """Runs on the stall watch: who was busy across the stall (since the
        last snapshot before it) and in the `stall_followup_s` right after."""
        started = time.perf_counter()
        now = machine_snapshot(thread_pids=(self._pid,))
        if now is None:
            return None
        with self._state_lock:
            now, _ = _scope_snapshot(now, self._pid, self._watch, self._tree_seen)
        base = self._stall_baseline(now.t - late_s)
        with self._state_lock:
            self._recent.append(now)
        record: dict = {"kind": "stall",
                        "t_utc": datetime.now(timezone.utc).isoformat(),
                        "uptime_s": round(time.monotonic() - self._t0, 1),
                        "late_ms": round(late_s * 1000)}
        if base is not None:
            record["before"] = _machine_totals(base, now)
            record["before"]["self"] = thread_cpu(base, now, self._pid,
                                                  self._labeler())
        record["system"] = system_memory()
        cost = time.perf_counter() - started
        if self._followup_s > 0 and not self._stop.wait(self._followup_s):
            resumed = time.perf_counter()
            after = machine_snapshot(thread_pids=(self._pid,))
            if after is not None:
                with self._state_lock:
                    after, _ = _scope_snapshot(after, self._pid, self._watch, self._tree_seen)
                    self._recent.append(after)
                record["after"] = _machine_totals(now, after)
            cost += time.perf_counter() - resumed
        record["capture_ms"] = round(cost * 1000, 1)
        before = record.get("before") or {}
        log.warning("stall: this server could not run for %d ms; machine busy %s%% "
                    "over %s s; server %s%%", record["late_ms"], before.get("busy_pct", "?"),
                    before.get("window_s", "?"), (before.get("self") or {}).get("process_pct", "?"))
        self._persist(record)
        return record

    def _persist(self, record: dict) -> None:
        path = self._logpath()
        if path is not None:
            with self._write_lock:  # the minute worker and the watch both append
                write_perf_record(path, record, max_bytes=self._max_log_bytes)

    def _read_gauges(self) -> dict:
        try:
            return self._gauges() if self._gauges is not None else {}
        except Exception:
            log.exception("perfmon gauges failed")
            return {}

    def _tick(self, gauges=None) -> dict:
        """Collect and persist; run() supplies gauges read on their owner loop."""
        started = time.perf_counter()  # monotonic() ticks in 15.6 ms steps here
        snap = self._collect()
        gvals = self._read_gauges() if gauges is None else gauges

        if not self._baseline:
            self._baseline = snap
            self._baseline_types = snap.get("types", {})

        growers = top_type_growth(self._baseline_types, snap.get("types", {}),
                                  n=10)
        _log_sample(snap, growers)

        for msg in resource_alarms(self._baseline, snap):
            if msg not in self._fired:
                log.warning("resource growth alarm: %s", msg)
                self._fired.add(msg)

        record = perf_record(
            snap, gvals, uptime_s=time.monotonic() - self._t0,
            t_utc=datetime.now(timezone.utc).isoformat(), top_growers=growers)
        record["sampling"] = {"mode": "deep" if self._deep else "light",
                              "collect_ms": round((time.perf_counter() - started) * 1000, 3)}
        record["stalls"] = (self._stalls.take_counts()
                            if self._stalls is not None and self._stalls.running
                            else None)
        self._persist(record)

        # /health surface: everything but the full top_types blob (kept small)
        self.latest = {k: v for k, v in record.items() if k != "top_types"}
        return record

    async def run(self) -> None:
        import asyncio
        if not self._enabled:
            log.info("perf monitor DISABLED (SM64_PERFMON=0) — no sampling, "
                     "no perf_log.jsonl, zero overhead")
            return
        await _off_loop(start_new_session_log, self._logpath())
        self._stop.clear()
        if self._stalls is not None:
            self._stalls.start()
        try:
            while True:
                try:
                    # Only one sample can run at a time. The next iteration waits
                    # for completion; a slow disk cannot build a queue of probes.
                    await _off_loop(self._tick, self._read_gauges())
                except Exception:
                    log.exception("perfmon tick failed (continuing)")
                await asyncio.sleep(self._interval_s)
        finally:
            # The watch wakes on the event within a period, and a capture's
            # follow-up wait ends with it; only an in-flight snapshot (~10 ms)
            # can hold this join.
            self._stop.set()
            if self._stalls is not None:
                self._stalls.join(1.0)


def _log_sample(snap: dict, growers: list) -> None:
    """The minute's human-readable log lines."""
    child = snap.get("children") or {}
    sysd = snap.get("system") or {}
    gpu = snap.get("gpu") or {}
    procs = (snap.get("processes") or {}).values()
    pj64 = sum(p.get("rss_bytes", 0) for p in procs)
    largest = [p["vm_largest_free_mib"] for p in procs
               if "vm_largest_free_mib" in p]
    log.info(
        "mem: rss=%.0f MiB priv=%.0f MiB obj=%s thr=%d/%s handles=%s gdi=%s "
        "user=%s child=%.0f MiB(%d) gpu=%s pj64=%.0f MiB pj64_vm_largest_free=%s "
        "sys_load=%s%% scratch=%s",
        snap.get("rss_bytes", 0) / _MiB, snap.get("private_bytes", 0) / _MiB,
        snap.get("objects", "unmeasured"), snap.get("threads", -1),
        snap.get("os_threads", "?"),
        snap.get("handles", "?"), snap.get("gdi_objects", "?"),
        snap.get("user_objects", "?"), child.get("rss_bytes", 0) / _MiB,
        child.get("count", 0), _mib_reading(gpu.get("local_usage_bytes")),
        pj64 / _MiB, f"{min(largest):.0f} MiB" if largest else "unmeasured",
        sysd.get("load_pct", "?"), _mib_reading(snap.get("scratch_bytes")))
    cpu, machine = snap.get("cpu") or {}, snap.get("machine") or {}
    if cpu or machine:
        log.info("cpu: server %s%% (%s); machine busy %s%%, DPC %s%%, interrupt %s%%",
                 cpu.get("process_pct", "?"),
                 ", ".join(f"{k} {v}%" for k, v in
                           list((cpu.get("threads") or {}).items())[:4]),
                 machine.get("busy_pct", "?"), machine.get("dpc_pct", "?"),
                 machine.get("interrupt_pct", "?"))
    if growers:
        log.info("mem growers vs baseline: %s", ", ".join(
            f"{g['type']} +{g['delta']}" for g in growers[:6]))

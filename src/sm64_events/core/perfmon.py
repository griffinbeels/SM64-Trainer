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
and the loop continues; the server is never put at risk by its own monitor."""
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from sm64_events.core.procmem import (resource_alarms, sample,
                                      top_type_growth)

log = logging.getLogger("sm64.procmem")

_MiB = 1024 ** 2
_DEFAULT_LOG = Path("data") / "perf_log.jsonl"
_USE_DEFAULT = object()  # sentinel: resolve _DEFAULT_LOG at call time, so tests
                         # (conftest) can redirect it and never write the real
                         # diagnostic file — a test sample would corrupt the
                         # human's session data the analyzer reads.


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
        "gc_counts": snap.get("gc", {}).get("counts"),
        "scratch_mib": (round(snap["scratch_bytes"] / _MiB, 1)
                        if "scratch_bytes" in snap else None),
        "system": snap.get("system"),
        "children": snap.get("children"),
        "gpu": snap.get("gpu"),
        "processes": snap.get("processes"),
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
                      max_bytes: int = 50 * 1024 * 1024) -> None:
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


class PerfMonitor:
    """Samples every `interval_s`: logs an expanded `mem:` line + a top-growers
    line, fires each resource alarm once, and appends a perf_log.jsonl record.
    `latest` (trimmed) backs /health. Runs as an asyncio task — no extra
    thread. Supersedes the RSS-only MemoryMonitor."""

    def __init__(self, scratch_dir: Path | None = None,
                 interval_s: float = 60.0,
                 perf_log_path=_USE_DEFAULT,
                 gauges: Callable[[], dict] | None = None,
                 self_pid: int | None = None,
                 max_log_bytes: int = 50 * 1024 * 1024,
                 watch_processes=("Project64.exe",),
                 enabled: bool = True):
        self._enabled = enabled    # SM64_PERFMON=0 -> run() is a no-op (zero
                                   # per-60s cost: no heap walk, no probes, no
                                   # log line) for audio-sensitive sessions
        self._scratch_dir = scratch_dir
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

    def _logpath(self) -> Path | None:
        """Resolve the perf-log path at use time (see __init__)."""
        return (_DEFAULT_LOG if self._perf_log_path is _USE_DEFAULT
                else self._perf_log_path)

    def _collect(self) -> dict:
        """One full sample (heap walk + resources + children + GPU + watched
        processes + scratch)."""
        return sample(self._scratch_dir, count_objects=True, resources=True,
                      children_of=self._pid, histogram=True, gpu=True,
                      processes=self._watch)

    def _tick(self) -> dict:
        """Sample, log, alarm, persist. Returns the record (testable without
        the async loop)."""
        snap = self._collect()
        try:
            gvals = self._gauges() if self._gauges is not None else {}
        except Exception:
            log.exception("perfmon gauges failed")
            gvals = {}

        if not self._baseline:
            self._baseline = snap
            self._baseline_types = snap.get("types", {})

        growers = top_type_growth(self._baseline_types, snap.get("types", {}),
                                  n=10)
        child = snap.get("children") or {}
        sysd = snap.get("system") or {}
        gpu = snap.get("gpu") or {}
        pj64 = sum(p.get("rss_bytes", 0)
                   for p in (snap.get("processes") or {}).values())
        log.info(
            "mem: rss=%.0f MiB priv=%.0f MiB obj=%d thr=%d handles=%s gdi=%s "
            "user=%s child=%.0f MiB(%d) gpu=%.0f MiB pj64=%.0f MiB "
            "sys_load=%s%% scratch=%.0f MiB",
            snap.get("rss_bytes", 0) / _MiB, snap.get("private_bytes", 0) / _MiB,
            snap.get("objects", -1), snap.get("threads", -1),
            snap.get("handles", "?"), snap.get("gdi_objects", "?"),
            snap.get("user_objects", "?"), child.get("rss_bytes", 0) / _MiB,
            child.get("count", 0), gpu.get("local_usage_bytes", 0) / _MiB,
            pj64 / _MiB, sysd.get("load_pct", "?"),
            snap.get("scratch_bytes", 0) / _MiB)
        if growers:
            log.info("mem growers vs baseline: %s", ", ".join(
                f"{g['type']} +{g['delta']}" for g in growers[:6]))

        for msg in resource_alarms(self._baseline, snap):
            if msg not in self._fired:
                log.warning("resource growth alarm: %s", msg)
                self._fired.add(msg)

        record = perf_record(
            snap, gvals, uptime_s=time.monotonic() - self._t0,
            t_utc=datetime.now(timezone.utc).isoformat(), top_growers=growers)
        path = self._logpath()
        if path is not None:
            write_perf_record(path, record, max_bytes=self._max_log_bytes)

        # /health surface: everything but the full top_types blob (kept small)
        self.latest = {k: v for k, v in record.items() if k != "top_types"}
        return record

    async def run(self) -> None:
        import asyncio
        if not self._enabled:
            log.info("perf monitor DISABLED (SM64_PERFMON=0) — no sampling, "
                     "no perf_log.jsonl, zero overhead")
            return
        start_new_session_log(self._logpath())  # one run = one clean log
        while True:
            try:
                self._tick()
            except Exception:
                log.exception("perfmon tick failed (continuing)")
            await asyncio.sleep(self._interval_s)

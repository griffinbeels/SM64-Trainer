"""Passive native evidence from known plugin files and the existing control page.

No registry search, process inventory, graphics calls, capture lease or ROM reads.
Only the last observed installation supplies directories. Automatic reports use
recognized numeric/enum fields; raw text is exclusively for redacted local export.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from collections.abc import Mapping
import math
import os
from pathlib import Path
import re
import threading

from sm64_events.replay.capturecontrol import CaptureControl
from sm64_events.replay.ownedclose import CleanupPending, retain

TAIL_BYTES = 32 * 1024
LOG_NAMES = ("sm64_trainer_gfx.activation.log", "sm64_trainer_gfx.log",
             "sm64_trainer_gfx.activation.log.1", "sm64_trainer_gfx.log.1")
EVENT_NAMES = frozenset({
    "init_begin", "init_result", "wrapper_loaded", "wrapped_loaded", "wrapped_info",
    "rom_open_begin", "rom_open_end", "rom_closed_begin", "rom_closed_end",
    "gpu_request", "gpu_prepare", "gpu_failure", "gpu_source_refusal", "gpu_phase",
    "gpu_cadence", "gpu_summary", "gpu_summary_phase", "gpu_summary_cadence", "bindings_reconstructed",
    "callback_summary", "callback_final_window", "callback_stall", "callback_progress", "callback_pacing",
    "diagnostics_stop", "close_begin", "close_end",
})
NUMBER_FIELDS = frozenset({
    "wrapper_version", "success", "wrapped_ms", "initiated", "practice_rom", "runtime",
    "epoch", "reason", "detail", "cause", "phase", "frequency", "request_qpc", "surface_qpc",
    "prepare_begin_qpc", "snapshots_ms", "sampler_ms", "luid_ms", "channel_ms", "reader_qpc",
    "import_begin_qpc", "import_ms", "observed_qpc", "first_qpc", "phase_started",
    "source", "retirements", "bridges", "unpublished", "returning", "decisions", "copies",
    "unsampled", "accounted", "oldest", "full", "busy", "baseline_full", "baseline_busy",
    "committed", "offered", "observed", "calls", "total_ticks", "max_ticks", "max_started_qpc",
    "records", "pictures", "same_origin", "invalid", "intervals", "min_ticks", "slots",
    "window_calls", "mean_ms", "p99_ms", "max_ms", "total_ms", "begin_qpc", "end_qpc",
    "watched", "dropped_records", "snapshot_bytes", "bridge_bytes", "sample_calls", "sample_reuses",
    "publish_busy", "refused", "peak_pending", "peak_bridges",
    "version", "started_qpc", "last_completed_qpc", "age_ticks", "last_qpc", "generation", "resets", "samples",
    "snapshot_busy", "durations", "duration_max_ticks", "duration_max_started_qpc", "gaps", "gap_max_ticks",
    "gap_max_started_qpc", "cpu_samples", "cpu_invalid", "cpu_thread_changes", "cpu_commit_busy",
    "queued_samples", "unsent_samples", "in_flight", "window_started_qpc", "previous_emit_ticks",
    # gpu_failure: the bindings cause of a no-surface deadline (source_refusal.h).
    "bindings_site", "bindings_gl_error", "bindings_flags", "bindings_invalidations",
    "bindings_after_ms", "bindings_contexts", "reconstruct_attempts", "reconstruct_successes",
    # bindings_reconstructed: the renderer rebuilt its witness from the driver.
    "site", "gl_error", "attempts", "successes", "ms_since_created",
})
ENUM_FIELDS = {
    "callback": frozenset({"ProcessDList", "UpdateScreen", "ProcessRDPList"}),
    "phase": frozenset({"wait", "take", "sample", "decisions", "copy", "bridge_returns", "reap", "frontier"}),
    "first_refusal": frozenset({"unavailable"}),
    "state": frozenset({"in_flight", "no_new_callback", "queued", "unsent", "empty"}),
    "coverage": frozenset({"callback"}),
}
_PREFIX = re.compile(r"^(\d{4}-\d\d-\d\dT[\d:.]+Z) pid=(\d+) tid=\d+ (?:tid_role=submitter )?build=([\w.-]{1,100}) event=(\w+) ")
_PAIR = re.compile(r"\b([a-z_]+)=([^\s]+)")


def technical_fields(value: object) -> dict:
    """One typed boundary for parsed lines and directly supplied SDK events."""
    if not isinstance(value, Mapping):
        return {}
    result = {}
    for key, item in value.items():
        if key in NUMBER_FIELDS:
            if isinstance(item, str) and re.fullmatch(r"-?\d{1,20}(?:\.\d{1,6})?", item):
                item = float(item) if "." in item else int(item)
            if type(item) in (int, float) and abs(item) < 2 ** 64 and math.isfinite(item):
                result[key] = item
                continue
        if key in ENUM_FIELDS and isinstance(item, str) and item in ENUM_FIELDS[key]:
            result[key] = item
    return result


def bounded_tail(path: Path, limit: int = TAIL_BYTES) -> tuple[str, int]:
    """The byte bound applies to reads, not just to the final rendered lines."""
    with path.open("rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        offset = max(0, size - limit)
        stream.seek(offset)
        raw = stream.read(limit)
    if offset and b"\n" in raw:
        raw = raw.split(b"\n", 1)[1]
    return raw.decode("utf-8-sig", "replace"), offset


def technical_events(text: str, *, producer_pid=None, build_id=None, producer_birth=None) -> list[dict]:
    """Unknown events, details and freeform paths/names never cross this boundary."""
    rows = []
    for line in text.splitlines():
        match = _PREFIX.match(line)
        if not match or match[4] not in EVENT_NAMES:
            continue
        bound = bool(producer_pid is not None and build_id and producer_birth)
        born = producer_birth / 10000000 - 11644473600 if producer_birth else None
        try:
            timestamp = datetime.fromisoformat(match[1].replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        matches = bool(bound and int(match[2]) == producer_pid and match[3] == build_id and timestamp >= born)
        row = dict(timestamp=match[1], event=match[4], producer_matches=matches,
                   identity_bound=bound, capture_build=match[3])
        row["fields"] = technical_fields(dict(_PAIR.findall(line[match.end():])))
        rows.append(row)
    return rows[-32:]


def installation_folders(layer: dict, local_appdata: str | None) -> list[tuple[str, Path]]:
    """Fixed observed install paths plus their Windows UAC redirection twins."""
    found = {}
    for field in ("plugin_dir", "pj64_dir"):
        value = layer.get(field)
        if isinstance(value, str) and Path(value).is_absolute():
            path = Path(value)
            found[path] = field
    for path in list(found):
        if path == Path(layer.get("pj64_dir") or ""):
            found.setdefault(path / "Plugin", "plugin_dir")
    if local_appdata:
        for path, role in list(found.items()):
            if re.fullmatch(r"[A-Za-z]:\\", path.anchor):
                virtual = Path(local_appdata) / "VirtualStore" / path.relative_to(path.anchor)
                found.setdefault(virtual, "virtual_" + role)
    return [(role, path) for path, role in list(found.items())[:6]]


class NativeDiagnostics:
    """One owner retains partial read-only handles until closure is proved."""

    def __init__(self, *, control_factory=CaptureControl, local_appdata=None):
        self._factory = control_factory
        self._appdata = local_appdata if local_appdata is not None else os.environ.get("LOCALAPPDATA")
        self._pending = None
        self._lock = threading.Lock()

    def _close_pending(self):
        if self._pending is None:
            return True
        try:
            self._pending.close()
        except CleanupPending as error:
            self._pending = retain(self._pending, error)
            return False
        except (OSError, RuntimeError):
            return False  # The successfully constructed object still owns its handles.
        self._pending = None
        return True

    def _control(self):
        if not self._close_pending():
            return {"available": False, "error_type": "CleanupPending"}
        try:
            self._pending = self._factory()
        except CleanupPending as error:
            self._pending = error.owner
            return {"available": False, "error_type": "CleanupPending"}
        except (OSError, RuntimeError) as error:
            return {"available": False, "error_type": type(error).__name__}
        try:
            result = {"available": True, **asdict(self._pending.status())}
        except CleanupPending as error:
            self._pending = retain(self._pending, error)
            result = {"available": False, "error_type": "CleanupPending"}
        except (OSError, RuntimeError) as error:
            result = {"available": False, "error_type": type(error).__name__}
        finally:
            closed = self._close_pending()
        return result if closed else {"available": False, "error_type": "CleanupPending"}

    def snapshot(self, layer: dict | None = None) -> dict:
        folders = installation_folders(layer or {}, self._appdata)
        if not folders:
            return {"available": False, "error_type": "InstallationNotObserved"}
        if not self._lock.acquire(blocking=False):
            return {"available": False, "error_type": "Busy"}
        try:
            control = self._control()
            result = {"control": control, "logs": [], "events": []}
            birth = (control.get("producer_created_hi", 0) << 32) | control.get("producer_created_lo", 0)
            for role, folder in folders:
                for name in LOG_NAMES:
                    row = {"source": role, "file": name}
                    path = folder / name
                    try:
                        if path.is_symlink() or path.resolve().parent != folder.resolve():
                            raise OSError("diagnostic link refused")
                        text, offset = bounded_tail(path)
                        row.update(available=True, offset=offset, tail=text)
                        result["events"].extend(technical_events(text, producer_pid=control.get("producer_pid"),
                                                                 build_id=control.get("build_id"), producer_birth=birth))
                    except OSError as error:
                        row.update(available=False, error_type=type(error).__name__)
                    result["logs"].append(row)
            result["events"] = sorted(result["events"], key=lambda row: row["timestamp"])[-32:]
            result["available"] = bool(control.get("available") or any(row.get("available") for row in result["logs"]))
            return result
        finally:
            self._lock.release()

    def close(self):
        with self._lock:
            if not self._close_pending():
                raise CleanupPending(self, "passive control handles remain owned")

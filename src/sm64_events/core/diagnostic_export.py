"""Local support exports omit identities and unrelated application activity.

Technical status is shared with automatic reporting. Legacy journal/UI/perf
sections retain counts, clocks and machine states, with display strings omitted.
"""
from __future__ import annotations

from collections.abc import Mapping
import json
import re

from sm64_events.core.error_reporting_privacy import LOG_LINE_LIMIT, operational_log, routine_access, scrub_text
from sm64_events.core.native_diagnostics import technical_events

_TEXT_FIELDS = frozenset({"state", "status", "type", "surface", "source", "phase", "kind", "reason", "error",
    "last_error", "operation", "component", "clock", "region", "game_version", "frame_source", "audio_mode",
    "encoder", "codec", "channel", "build_id", "capture_build", "observed_utc", "t_utc", "client_utc", "server_utc",
    "timestamp_utc", "message", "outcome", "outcome_detail", "anchor_type", "igt", "error_type"})
_DROP = frozenset({"machine", "members", "by_name", "top_cpu", "top_private", "top_hard_faults", "top_io",
    "account", "user_id", "email", "username", "author", "player", "player_name", "display_name", "token", "cookie",
    "authorization", "headers", "request", "environment_variables", "database", "rom_data", "vars",
    "last_invalid_sample", "pad", "controller", "stick_x", "stick_y", "buttons", "rdram"})
_FIELDS = _TEXT_FIELDS | frozenset({"id", "seq", "frame", "last_frame", "session_id", "channel", "clients", "held", "db",
    "polling", "tracking", "memory", "inputs", "input_storage", "input_flush", "samples", "frames", "straddles",
    "invalid_samples", "sink_failures", "history_failures", "edge_checks", "edge_mismatches", "skips", "skipped_frames",
    "worst_skip", "pending_frames", "rejected_frames", "failures", "recoveries", "restarts", "retry_in_s",
    "dropped_events", "gap_pending", "emulator_attached", "uptime_s", "rss_mib", "private_mib", "handles", "gdi", "user",
    "threads", "os_threads", "cpu", "system", "children", "processes", "gauges", "sampling", "stalls", "gc_counts",
    "top_growers", "scratch_mib", "process_pct", "load_pct", "avail_phys_bytes", "total_phys_bytes", "commit_bytes",
    "commit_limit_bytes", "count", "worst_late_ms", "window_s", "collect_ms", "mode", "surface", "marks", "rows",
    "cards", "cells", "logs", "total", "open", "active", "name", "title", "note", "strat", "course_id", "star_id",
    "level", "area", "igt_frames", "igt_source", "igt", "payload", "checks", "plugin", "pictures", "game"})


def report_value(value, *, key="", depth=0):
    if depth > 12:
        return "[depth limit]"
    if isinstance(value, Mapping):
        return {(str(name) if name in _FIELDS else f"field_{index}"): report_value(item, key=str(name), depth=depth + 1)
                for index, (name, item) in enumerate(list(value.items())[:120]) if name not in _DROP}
    if isinstance(value, (list, tuple)):
        return [report_value(item, key=key, depth=depth + 1) for item in value[:100]]
    if isinstance(value, str):
        return scrub_text(value) if key in _TEXT_FIELDS else "[text omitted]"
    return value if value is None or isinstance(value, (int, float, bool)) else "[value omitted]"


def report_line(line: str) -> str:
    """Structured observation lines keep no custom labels; operation logs are scrubbed."""
    try:
        return json.dumps(report_value(json.loads(line)), ensure_ascii=False)
    except (ValueError, TypeError):
        parts = line.split(maxsplit=3)
        if len(parts) == 4 and operational_log(parts[2], parts[3]):
            return scrub_text(line, LOG_LINE_LIMIT)
        frame = re.search(r'File "([^"\r\n]+)", line (\d+), in ([\w<>]+)', line)
        if frame:
            filename = frame[1].replace("\\", "/")
            marker = filename.find("sm64_events/")
            filename = filename[marker:] if marker >= 0 else filename.rsplit("/", 1)[-1]
            return f"  {scrub_text(filename)}:{frame[2]} in {frame[3]}"
        return "[nontechnical log detail omitted]"


def routine_line(line: str) -> bool:
    """A successful request line; the server-log tail skips it before counting."""
    parts = line.split(maxsplit=3)
    return len(parts) == 4 and routine_access(parts[2], parts[3])


def native_export(snapshot: dict) -> str:
    """Show recognized log records without ever exporting arbitrary native strings."""
    sections = []
    for row in snapshot.get("native_log_tails", [])[:24]:
        events = technical_events(row.get("tail", ""))
        sections.append({"source": row.get("source"), "file": row.get("file"),
                         "events": events, "unrecognized_details": "omitted"})
    return json.dumps(sections, indent=2)

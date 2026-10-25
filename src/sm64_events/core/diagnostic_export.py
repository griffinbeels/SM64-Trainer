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
    "timestamp_utc", "message", "outcome", "outcome_detail", "anchor_type", "igt", "error_type",
    "pictures_via", "refresh_error", "sink_error", "igt_source", "igt_timed_at", "cause", "released_by"})
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
    "level", "area", "igt_frames", "igt_source", "igt", "payload", "checks", "plugin", "pictures", "game"}) | frozenset({
    # Fixed schema names that showed as field_N in a tester's report
    # (2026-10-25). A name is not a value: strings under these keys stay
    # omitted unless _TEXT_FIELDS lists them, and a key that is data (a
    # process, runner or strategy name) is never listed, so it stays field_N.
    # Setup (capturelayer.LayerStatus.as_dict and diagnostic_status):
    "pj64_dir", "pj64_running", "registry_graphics_dll", "wrapper_present", "wrapper_current", "wrapper_selected",
    "wrapped_name", "layer_alive", "consented_at", "problems", "pictures_via", "steps", "plugin_pid",
    "pictures_flowing", "plugin_dir", "renderer_present", "renderer_current", "previous_graphics_dll",
    "automatic_update", "configuration_consistent", "installation_verified", "observed_unix_s", "refresh_error",
    "done", "action", "label",
    # Health and perf samples (perfmon, poller.perf_stats, recorder.perf_gauges, InputSampler.health):
    "objects", "gpu", "rss_bytes", "private_bytes", "poll_hz", "tick_ms_ema", "tick_ms_max", "ticks",
    "ring_bytes", "idle", "recording", "sink_error",
    # What the screen drew (ui/uilog.js readTargets):
    "context",
    # Journal payloads, every key his journal's 25 event types carry:
    "from", "from_transient", "to", "attempt_id", "attempt_ids", "reason", "strat_tag", "cause", "dustless",
    "frames_late", "landing_frames", "kind", "which", "from_area", "action", "action_timer", "counter",
    "engaged_age_frames", "landmark", "ordinal", "auto", "classification", "segment_id", "frames",
    "previous_frames", "timer_mode", "restored_attempt_id", "restored_frames", "acted_tracking", "area_load",
    "frames_since_dialog", "frames_since_door", "frames_since_warp_op", "igt_frames_before", "loads_journaled",
    "mario_acted", "paused_frames_before", "prev_action", "prev_area", "save_pending", "teleport", "warp_op",
    "route_id", "segment_ids", "attempt_rules", "spawn_node", "already_collected", "carried_igt", "coins",
    "course_name", "derived_igt_frames", "grab_frame", "grab_igt_frames", "igt_reconstructed", "igt_timed_at",
    "mario_alive", "num_stars", "published_after", "result_writes", "star_name", "usamune_stop",
    "igt_frames_restored", "load_signature", "sole_selection", "held_frames", "released_by"})


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

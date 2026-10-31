"""The complete automatic diagnostic schema; additions require explicit review.

Rebuilt at SDK capture, transport admission and retry loading. No arbitrary
mapping, freeform native detail, tail, path, actor name or input value survives.
"""
from __future__ import annotations

from collections.abc import Mapping
import json
import math
import re

from sm64_events.core.error_reporting_privacy import scrub_text
from sm64_events.core.native_diagnostics import EVENT_NAMES, technical_fields

MAX_SNAPSHOT_BYTES = 24 * 1024
_CAPTURE_STAGES = ("channel", "encoder", "sink_prepare", "first_offer", "media_setup", "tick", "between_ticks",
                   "audio_drain", "adapter_pump", "heartbeat", "media_drain", "media_age", "report",
                   "mux_video", "mux_audio", "ledger_feed", "copy_lane")
# Fixed names used by gpu_render.cpp and gl_snapshot.cpp. A caller-supplied
# string which merely resembles an OpenGL function is not evidence.
_ENTRY_POINTS = frozenset({
    "glCreateFramebuffers", "glDeleteFramebuffers", "glNamedFramebufferTexture", "glNamedFramebufferReadBuffer",
    "glNamedFramebufferDrawBuffer", "glCheckNamedFramebufferStatus", "glBlitNamedFramebuffer", "glCreateTextures",
    "glTextureStorage2D", "glCreateBuffers", "glNamedBufferStorage", "glDeleteBuffers", "glGetNamedBufferSubData",
    "glBindBuffer", "glGetTextureSubImage", "glFenceSync", "glClientWaitSync", "glDeleteSync", "glCreateMemoryObjectsEXT",
    "glDeleteMemoryObjectsEXT", "glMemoryObjectParameterivEXT", "glImportMemoryWin32NameEXT", "glTextureStorageMem2DEXT",
    "glAcquireKeyedMutexWin32EXT", "glReleaseKeyedMutexWin32EXT", "glGetUnsignedBytevEXT", "glCopyTextureSubImage2D",
    "glBindFramebuffer",
})
# The latest render list's own numbers (ghost/capture.py LATEST_HOOKS): microseconds and counts.
GHOST_HOOK_FIELDS = ("hook_list_micros", "hook_open_micros", "hook_batch_micros", "hook_close_micros",
                     "hook_finalize_micros", "hook_error_reads", "hook_ghosts")
# The renderer's draw path facts (ghost/draw_facts.py FIELDS): step and result
# names from fixed code tables, booleans, sizes, counts, "unknown" for anything
# the renderer did not report, and one sentence built from those tables.
GHOST_DRAW_FIELDS = (
    "draw_refusal", "draw_list_gate", "draw_present_gate", "draw_lists", "draw_presents",
    "draw_observing", "draw_faulted", "draw_compositor", "draw_model", "draw_planned",
    "draw_create_step", "draw_prepare_result", "draw_batch_result", "draw_finalize_result",
    "draw_last_fault", "draw_fault_site", "draw_fault_gl_error",
    "draw_observed_width", "draw_observed_height", "draw_prepared_width", "draw_prepared_height",
    "draw_presentation_ready", "draw_presentation_faulted", "draw_ready_step",
    "draw_fbe", "draw_fxaa", "draw_overscan", "draw_blit_workaround", "draw_n64_depth_compare",
    "draw_fragment_depth_write", "draw_multisampling", "draw_rdram_size", "draw_rdram_supported",
    "draw_evaluations", "draw_playback_refusals",
)


def mapping(value):
    return value if isinstance(value, Mapping) else {}


def pick(value, keys) -> dict:
    if not isinstance(value, Mapping) or not value:
        return {}
    result = {}
    for key in keys:
        item = value.get(key)
        if isinstance(item, bool) or type(item) is int and abs(item) < 2 ** 64:
            result[key] = item
        elif type(item) is float and math.isfinite(item):
            result[key] = item
        elif isinstance(item, str):
            identity = key in {"capture_build", "build_id"} and re.fullmatch(r"[0-9a-f]{64}(?:-[a-z0-9-]{1,30})?", item)
            result[key] = item if identity else scrub_text(item, 140)
    return result


def _numbers(value, keys) -> dict:
    return {key: item for key, item in mapping(value).items() if key in keys
            and type(item) in (int, float) and abs(item) < 2 ** 64 and math.isfinite(item)}


def _startup(value):
    """Keep pre-channel progress and the GPU it ran on, never request nonces.
    The vendor/renderer strings name the adapter (as `gpu_name` already
    does): a no-surface startup (10034) is otherwise unattributable."""
    value = mapping(value)
    result = {key: item for key, item in value.items()
              if key in {"epoch", "load_error", "width", "height", "control_generation",
                         "bindings_site", "bindings_gl_error", "bindings_flags", "bindings_invalidations",
                         "bindings_after_ms", "bindings_contexts"}
              and type(item) is int and 0 <= item <= 0xffffffff}
    for key in ("gl_version", "context_gl_version"):
        version = value.get(key)
        if isinstance(version, str) and (match := re.match(r"^([0-9]{1,2})\.([0-9]{1,2})(?=[.\s]|$)", version)):
            result[key] = f"{int(match[1])}.{int(match[2])}"
    result.update(pick(value, ("context_gl_vendor", "context_gl_renderer", "bindings_cause")))
    if value.get("context_identity") in ("context_created", "first_refusal"):
        result["context_identity"] = value["context_identity"]
    site = value.get("bindings_site_name")
    if isinstance(site, str) and re.fullmatch(r"[a-z_]{1,40}", site):
        result["bindings_site_name"] = site
    entry = value.get("missing_entry_point")
    if isinstance(entry, str) and entry in _ENTRY_POINTS:
        result["missing_entry_point"] = entry
    if isinstance(value.get("entry_point_missing"), bool):
        result["entry_point_missing"] = value["entry_point_missing"]
    elif isinstance(entry, str):
        result["entry_point_missing"] = bool(entry)
    return result


def _gpu(value):
    if not isinstance(value, Mapping) or not value:
        return {}
    result = pick(value, ("stage", "reason", "codec", "requested_codec", "encoder_backend", "capture_build",
                          "native_reason", "native_result", "native_stage", "native_operation", "native_hresult",
                          "encoder_error", "encoder_result", "driver_error", "gpu_vendor_id", "gpu_name", "gpu_driver",
                          "observed_unix_s"))
    result["startup"] = _startup(value.get("startup"))
    if isinstance(value.get("native_receipt_available"), bool):
        result["native_receipt_available"] = value["native_receipt_available"]
    timings = value.get("stage_timings", {})
    if isinstance(timings, Mapping):
        result["stage_timings"] = {key: _numbers(timings.get(key), ("count", "total_ms", "max_ms"))
                                   for key in _CAPTURE_STAGES if key in timings}
    return result


def _native(value):
    if not isinstance(value, Mapping) or not value:
        return {}
    result = pick(value, ("available", "error_type"))
    control = mapping(value.get("control"))
    result["control"] = pick(control, ("available", "error_type", "producer_pid", "generation", "state",
                                                   "reason", "ack_heartbeat", "capabilities", "rom_open", "baseline_rom", "build_id"))
    if isinstance(control.get("gpu_startup"), Mapping):
        result["control"]["gpu_startup"] = _startup(control["gpu_startup"])
    logs = [row for row in value.get("logs", [])[:24] if isinstance(row, Mapping)] \
        if isinstance(value.get("logs"), list) else []
    # Most candidate log names are absent on any one install. A count keeps the
    # search explicit without spending the byte budget the native events need.
    def absent(row):
        return row.get("available") is False and row.get("error_type") == "FileNotFoundError"
    result["logs"] = [pick(row, ("source", "file", "available", "error_type", "offset"))
                      for row in logs if not absent(row)]
    previous = value.get("missing_log_files")
    counted = sum(map(absent, logs)) + (previous if type(previous) is int and 0 <= previous <= 24 else 0)
    if counted:
        result["missing_log_files"] = min(counted, 24)
    events = value.get("events", [])
    result["events"] = []
    for row in events[-32:] if isinstance(events, list) else []:
        if not isinstance(row, Mapping) or row.get("event") not in EVENT_NAMES:
            continue
        clean = pick(row, ("timestamp", "event", "producer_matches", "identity_bound", "capture_build"))
        clean["fields"] = technical_fields(row.get("fields"))
        result["events"].append(clean)
    return result


SETUP_CHECK_IDS = ("graphics_plugin", "frame_buffer_emulation", "memory_size", "counter_factor", "cpu_core",
                   "emulator_build", "rsp_plugin", "audio_plugin", "install_location", "controller_plugin")


def _setup_check(value):
    """The setup check (core/setup_profile.py): fixed ids, statuses and
    importances, the user's value as bounded scrubbed text, a 64-hex hash."""
    value = mapping(value)
    if not value:
        return {}
    result = pick(value, ("version", "unknown", "required_differs", "error_type"))
    for key, allowed in (("rom", r"[0-9A-F]{8}-[0-9A-F]{8}-C:[0-9A-F]{2}"),
                         ("rdb", r"Project64 folder|VirtualStore|missing")):
        if isinstance(value.get(key), str) and re.fullmatch(allowed, value[key]):
            result[key] = value[key]
    recorded = mapping(value.get("baseline")).get("recorded")
    if isinstance(recorded, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", recorded):
        result["baseline_recorded"] = recorded
    flagged = value.get("flagged")
    result["flagged"] = [item for item in flagged if item in SETUP_CHECK_IDS] if isinstance(flagged, list) else []
    items = []
    for row in value.get("items", []) if isinstance(value.get("items"), list) else []:
        row = mapping(row)
        if (row.get("id") not in SETUP_CHECK_IDS or row.get("status") not in ("match", "differs", "unknown")
                or row.get("importance") not in ("required", "recommended", "noted")):
            continue
        clean = {key: row[key] for key in ("id", "status", "importance")}
        clean.update(pick(row, ("yours", "tested")))
        sha = row.get("yours_sha256")
        if isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha):
            clean["yours_sha256"] = sha
        items.append(clean)
    result["items"] = items
    return result


def safe_snapshot(value) -> dict:
    """Structural projection, including unknown/busy sources and observation age."""
    if not isinstance(value, Mapping):
        return {}
    result = {"schema": 1, **pick(value, ("captured_utc", "error_type", "truncated"))}
    sources = value.get("sources", {})
    result["sources"] = {key: pick(sources.get(key), ("available", "error_type", "read_utc", "observed_unix_s"))
                         for key in ("capture", "ghost", "setup", "pipeline", "resources", "native")
                         if isinstance(sources, Mapping) and key in sources}
    capture = mapping(value.get("capture"))
    recorder = mapping(capture.get("recorder"))
    result["capture"] = {"recorder": pick(recorder, ("recording_enabled", "capture_state", "recording", "window_found",
        "audio_mode", "frame_source", "idle", "pictures_fed", "encode_backlog", "publication_error"))}
    result["capture"].update(pick(capture, ("picture_input_matching",)))
    result["capture"]["recorder"].update({key: recorder[key]
        for key in ("gpu_fallback_active", "pose_source_owned", "pose_capture_paused")
        if type(recorder.get(key)) is bool})
    for key in ("recovery", "audio_health", "frame_source_health"):
        result["capture"]["recorder"][key] = pick(recorder.get(key), ("kind", "state", "pending", "attempts", "failures",
            "error", "retry_in_s", "attach_alive", "automatic_idle", "sessions_started"))
    gpu = mapping(capture.get("gpu"))
    result["capture"]["gpu"] = {**pick(gpu, ("failures", "retry_in_s", "unsupported_reason")),
                                "latest": _gpu(gpu.get("latest")), "first_failure": _gpu(gpu.get("first_failure"))}
    ghost = mapping(value.get("ghost"))
    result["ghost"] = pick(ghost, ("enabled", "has_target", "candidates_count", "playing_count", "source_available", "source_reason"))
    result["ghost"].update({key: ghost[key] for key in ("expected_display", "overlay_hidden", "owner_suspended")
                           if type(ghost.get(key)) is bool})
    result["ghost"]["native"] = pick(ghost.get("native") if isinstance(ghost, Mapping) else None, (
        "available", "error_type", "reason", "state", "dropped", "playback_state", "playback_reason", "displayed",
        "prepared_generation", "active_generation", "clips_prepared", "refused_batches", "hook_micros", "hook_gl_queries",
        *GHOST_HOOK_FIELDS, *GHOST_DRAW_FIELDS, "observed_unix_s"))
    setup = mapping(value.get("setup"))
    result["setup"] = pick(setup, ("state", "pj64_running", "wrapper_present", "wrapper_current", "wrapper_selected",
        "renderer_present", "renderer_current", "installation_verified", "layer_alive", "pictures_flowing", "observed_unix_s"))
    for key in ("configuration_consistent", "frame_buffer_emulation_off"):
        if type(setup.get(key)) is bool:
            result["setup"][key] = setup[key]
    result["setup"]["check"] = _setup_check(setup.get("check"))
    runtime = mapping(setup.get("runtime"))
    result["setup"]["runtime"] = {"checks": pick(runtime.get("checks"), ("plugin", "pictures", "inputs", "game")),
        "rom": pick(runtime.get("rom"), ("state", "region")), "target": pick(runtime.get("target"), ("state",)),
        **pick(runtime, ("paused", "observed_unix_s"))}
    pipeline = mapping(value.get("pipeline"))
    result["pipeline"] = pick(pipeline, ("emulator_attached", "paused", "frame"))
    for key in ("polling", "tracking", "inputs", "input_storage"):
        result["pipeline"][key] = pick(pipeline.get(key) if isinstance(pipeline, Mapping) else None, (
            "state", "error", "failures", "restarts", "recoveries", "retry_in_s", "samples", "frames", "invalid_samples",
            "sink_failures", "history_failures", "edge_checks", "edge_mismatches", "skips", "skipped_frames", "worst_skip",
            "pending_frames", "rejected_frames", "dropped_events", "gap_pending"))
    resources = mapping(value.get("resources"))
    result["resources"] = pick(resources, ("t_utc", "uptime_s", "rss_mib", "private_mib", "handles", "gdi", "user", "threads", "os_threads"))
    result["native"] = _native(value.get("native"))
    # Shed the least useful evidence first: found-log rows, then the oldest
    # native events one at a time (the latest 32 survive whenever they fit).
    def oversize():
        return len(json.dumps(result).encode()) > MAX_SNAPSHOT_BYTES
    if oversize() and result["native"].get("logs"):
        result["native"]["logs"] = []
        result["truncated"] = True
    events = result["native"].get("events", [])
    while events and oversize():
        events.pop(0)
        result["truncated"] = True
    # The fallback is bounded even when several known fields carry long numbers/text.
    if oversize():
        result["capture"]["gpu"]["first_failure"] = {}
    if len(json.dumps(result).encode()) > MAX_SNAPSHOT_BYTES:
        # Several malformed but allowed scalar fields can still exceed the
        # byte limit. Keep the original error deliverable with an explicit
        # evidence failure and the same canonical shape on every scrub pass.
        return safe_snapshot({"captured_utc": result.get("captured_utc"), "truncated": True, "error_type": "Oversize"})
    return result

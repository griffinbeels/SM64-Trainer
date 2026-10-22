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

MAX_SNAPSHOT_BYTES = 16 * 1024
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
    """Keep pre-channel progress, never raw GL identity or request nonces."""
    value = mapping(value)
    result = {key: item for key, item in value.items()
              if key in {"epoch", "load_error", "width", "height", "control_generation"}
              and type(item) is int and 0 <= item <= 0xffffffff}
    version = value.get("gl_version")
    if isinstance(version, str) and (match := re.match(r"^([0-9]{1,2})\.([0-9]{1,2})(?=[.\s]|$)", version)):
        result["gl_version"] = f"{int(match[1])}.{int(match[2])}"
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
    logs = value.get("logs", [])
    result["logs"] = [pick(row, ("source", "file", "available", "error_type", "offset"))
                      for row in logs[:24] if isinstance(row, Mapping)] if isinstance(logs, list) else []
    events = value.get("events", [])
    result["events"] = []
    for row in events[-32:] if isinstance(events, list) else []:
        if not isinstance(row, Mapping) or row.get("event") not in EVENT_NAMES:
            continue
        clean = pick(row, ("timestamp", "event", "producer_matches", "identity_bound", "capture_build"))
        clean["fields"] = technical_fields(row.get("fields"))
        result["events"].append(clean)
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
        "prepared_generation", "active_generation", "clips_prepared", "refused_batches", "hook_micros", "hook_gl_queries", "observed_unix_s"))
    setup = mapping(value.get("setup"))
    result["setup"] = pick(setup, ("state", "pj64_running", "wrapper_present", "wrapper_current", "wrapper_selected",
        "renderer_present", "renderer_current", "installation_verified", "layer_alive", "pictures_flowing", "observed_unix_s"))
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
    if len(json.dumps(result).encode()) > MAX_SNAPSHOT_BYTES:
        result["native"]["events"] = result["native"].get("events", [])[-8:]
        result["truncated"] = True
    # The fallback is bounded even when several known fields carry long numbers/text.
    if len(json.dumps(result).encode()) > MAX_SNAPSHOT_BYTES:
        result["native"]["events"] = []
        result["capture"]["gpu"]["first_failure"] = {}
    if len(json.dumps(result).encode()) > MAX_SNAPSHOT_BYTES:
        # Several malformed but allowed scalar fields can still exceed the
        # byte limit. Keep the original error deliverable with an explicit
        # evidence failure and the same canonical shape on every scrub pass.
        return safe_snapshot({"captured_utc": result.get("captured_utc"), "truncated": True, "error_type": "Oversize"})
    return result

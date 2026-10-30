"""Allowlisted automatic diagnostics, scrubbed before persistence or delivery.

This intentionally does not accept arbitrary SDK context: request bodies, locals,
user identities, source lines and SDK scope attachments never become automatic
reports. The only attachment is the transport's own redacted Debug report.
"""
from __future__ import annotations

import json
import re
import ipaddress
from collections.abc import Mapping
from pathlib import PurePosixPath
from urllib.parse import urlsplit

_SECRET = re.compile(
    r'''(?i)(?<!\w)["']?(?:authorization|cookie|password|passwd|secret|token|api[_-]?key|dsn|username|user_id|account_id|display_name)["']?'''
    r'''\s*[=:]\s*(?:bearer\s+)?(?:"[^"]*"|'[^']*'|[^\s,;}]+)'''
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]+=*")
_URL = re.compile(r"(?i)\b(?:https?|file)://[^\s\"'<>)]+")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_WIN_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\)[^\r\n\"'<>|]*")
_POSIX_PATH = re.compile(r"(?<![\w:/])/(?!ui/)[^\s\"'<>;,]+")
_IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
_IPV6 = re.compile(r"(?<![\w])\[?(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}\]?(?![\w])")
_OPAQUE = re.compile(r"\b(?=[A-Za-z0-9_+/=-]*[0-9])[A-Za-z0-9_+/=-]{40,}\b")
# The SDK's random per-run trace: Sentry refuses a log item without one
# ("invalid", no_data: beta.31 delivered none), and it links an error to the
# same run's warning logs. Random per process, like a PID: not a device id.
_TRACE_ID = re.compile(r"[0-9a-f]{32}")
_SPAN_ID = re.compile(r"[0-9a-f]{16}")


def trace_ids(trace_id: object, span_id: object = None) -> dict:
    """Validated trace/span ids, or nothing; never free text."""
    if not (isinstance(trace_id, str) and _TRACE_ID.fullmatch(trace_id)):
        return {}
    result = {"trace_id": trace_id}
    if isinstance(span_id, str) and _SPAN_ID.fullmatch(span_id):
        result["span_id"] = span_id
    return result
_CONTEXT_KEYS = frozenset({
    "operation", "component", "stage", "phase", "action", "code", "errno",
    "winerror", "error_type", "setup_state", "plugin_status", "platform",
    "version", "line", "column", "file", "browser_stack", "elevated",
    "status", "status_code", "method", "route", "source", "reason", "stack", "path",
    "encoder_backend", "codec", "requested_codec", "capture_build", "gpu_vendor_id", "gpu_name", "gpu_driver",
    "native_result", "native_stage", "native_operation", "native_hresult", "encoder_error", "driver_error",
    "encoder_result", "native_reason",
    "gl_vendor", "gl_renderer", "gl_version", "missing_entry_point",
    "stalled_for_s",
    # Why a no-surface startup (10034) refused: the GPU named at context
    # creation, and the first bindings invalidation (replay/gpudiagnostics.py).
    "context_gl_vendor", "context_gl_renderer", "context_gl_version", "context_identity",
    "bindings_site", "bindings_site_name", "bindings_cause", "bindings_gl_error", "bindings_flags",
    "bindings_invalidations", "bindings_after_ms", "bindings_contexts",
})
# Operational loggers only: gameplay event wires, profiles, community/account
# and process inventories stay local. Breadcrumbs, Sentry Logs and the Debug
# report's server-log section all filter through `operational_log`.
# The ghost's decisions (ghost/session.py): ids, counts and reason codes only,
# so it is operational while the rest of sm64.ghost is not.
GHOST_STATE_LOGGER = "sm64.ghost.state"
OPERATIONAL_LOGGERS = frozenset({"sm64.replay", "sm64.setup", "sm64.capturelayer", "sm64.desktop",
                                 "sm64.ghost.capture", GHOST_STATE_LOGGER, "sm64.updater", "sm64.poller",
                                 "uvicorn.error", "uvicorn.access"})
_ACCESS_STATUS = re.compile(r'"\s+([1-5][0-9]{2})\s*$')
LOG_LINE_LIMIT = 600
BREADCRUMB_LIMIT = 100
BREADCRUMB_BYTES = 24 * 1024
_LOG_TEXT_ATTRIBUTES = frozenset({"sentry.severity_text", "sentry.sdk.name", "sentry.sdk.version",
                                  "process.runtime.name", "process.runtime.version", "code.function.name",
                                  "thread.name", "sentry.message.template", "sentry.origin"})


def _safe_url(match: re.Match) -> str:
    """Retain local shipped JS locations, never URL credentials or queries."""
    try:
        parsed = urlsplit(match.group())
        if parsed.hostname in ("127.0.0.1", "localhost", "::1") and re.fullmatch(r"/ui/[A-Za-z0-9_./-]+\.m?js(?::[0-9]+){0,2}", parsed.path):
            return parsed.path.lstrip("/")
    except ValueError:
        return "[url]"
    return "[url]"


def scrub_text(value: object, limit: int = 600) -> str:
    """Remove common private values while retaining short diagnostic messages."""
    text = str(value)[:4000]
    for pattern, replacement in (
        (_SECRET, "[credential]"), (_BEARER, "[credential]"), (_URL, _safe_url), (_EMAIL, "[email]"),
        (_WIN_PATH, "[path]"), (_POSIX_PATH, "[path]"), (_IPV4, _safe_ip), (_IPV6, _safe_ip), (_OPAQUE, "[value]"),
    ):
        text = pattern.sub(replacement, text)
    return text[:limit]


def routine_access(name: object, message: object) -> bool:
    """A request that succeeded (1xx-3xx). The page polls, so these bury the
    evidence; a failing endpoint (4xx/5xx) or an unparsed line is kept."""
    status = _ACCESS_STATUS.search(str(message)) if name == "uvicorn.access" else None
    return status is not None and int(status[1]) < 400


def operational_log(name: object, message: object) -> bool:
    """The one keep rule for breadcrumbs, Sentry Logs and the report's server log."""
    return name in OPERATIONAL_LOGGERS and not routine_access(name, message)


def _safe_ip(match: re.Match) -> str:
    try:
        ipaddress.ip_address(match.group().strip("[]"))
        return "[ip]"
    except ValueError:
        return match.group()


def safe_context(context: object) -> dict:
    """Retain documented diagnostic fields only; callers cannot add raw data."""
    if not isinstance(context, Mapping):
        return {}
    result = {
        key: value if isinstance(value, (bool, int, float)) or (
            key == "capture_build" and isinstance(value, str)
            and re.fullmatch(r"[0-9a-f]{64}-gpu-runtime", value)
        ) else scrub_text(value)
        for key, value in context.items()
        if key in _CONTEXT_KEYS and isinstance(value, (str, bool, int, float))
    }
    # The SDK truncates wide dictionaries before before_send. A small nested
    # group preserves update versions/counts beside the technical error codes.
    update = context.get("update")
    if isinstance(update, Mapping):
        clean = {}
        for key in ("current_version", "target_version"):
            value = update.get(key)
            if isinstance(value, str) and re.fullmatch(r"v?\d{1,8}\.\d{1,8}\.\d{1,8}(?:[-+][A-Za-z0-9.-]{1,60})?", value):
                clean[key] = value
        for key in ("attempt", "attempts", "download_bytes"):
            value = update.get(key)
            if type(value) is int and 0 <= value < 2 ** 64:
                clean[key] = value
        if clean:
            result["update"] = clean
    return result


def _source_file(value: object) -> str:
    filename = str(value).replace("\\", "/")
    marker = filename.find("sm64_events/")
    return scrub_text(filename[marker:] if marker >= 0 else PurePosixPath(filename).name, 180)


def _frames(stacktrace: object) -> dict:
    if not isinstance(stacktrace, dict):
        return {}
    frames = []
    for frame in stacktrace.get("frames", [])[-30:]:
        if not isinstance(frame, dict):
            continue
        safe = {"filename": _source_file(frame.get("filename", ""))}
        for key in ("module", "function"):
            if isinstance(frame.get(key), str):
                safe[key] = scrub_text(frame[key], 180)
        for key in ("lineno", "colno", "in_app"):
            if isinstance(frame.get(key), (int, bool)):
                safe[key] = frame[key]
        frames.append(safe)
    return {"frames": frames}


def safe_breadcrumb(crumb: dict, _hint: object = None) -> dict | None:
    """Only operational logs: event wires, profiles and process inventories stay local."""
    category = str(crumb.get("category", ""))
    if not operational_log(category, crumb.get("message", "")):
        return None
    result = {
        "category": scrub_text(category, 120),
        "message": scrub_text(crumb.get("message", ""), LOG_LINE_LIMIT),
        "level": scrub_text(crumb.get("level", "info"), 20),
        "type": "default",
    }
    stamp = crumb.get("timestamp")
    if isinstance(stamp, (float, int)):
        result["timestamp"] = stamp
    elif isinstance(stamp, str) and re.fullmatch(r"[\dTZ:.+\-]{1,40}", stamp):
        result["timestamp"] = stamp
    elif hasattr(stamp, "isoformat"):
        result["timestamp"] = stamp.isoformat()
    return result


def _build_identity(event: dict) -> dict:
    # These identities come from trusted startup/build configuration. A commit
    # SHA is essential evidence, despite looking like an opaque access token.
    result = {}
    for key in ("release", "environment"):
        value = event.get(key)
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._+@/-]{1,160}", value):
            result[key] = value
    return result


def _safe_contexts(contexts: object) -> dict:
    """OS and runtime names/versions, and the run's validated trace ids."""
    if not isinstance(contexts, dict):
        return {}
    safe = {}
    for name in ("os", "runtime"):
        if isinstance(contexts.get(name), dict):
            safe[name] = {key: scrub_text(value, 120) for key, value in contexts[name].items()
                          if key in ("name", "version", "build") and isinstance(value, str)}
    trace = contexts.get("trace")
    if isinstance(trace, dict) and (ids := trace_ids(trace.get("trace_id"), trace.get("span_id"))):
        safe["trace"] = ids
    return safe


def safe_event(event: dict) -> dict:
    """Rebuild an SDK event from safe fields instead of blacklisting payloads."""
    result = _build_identity(event)
    for key in ("event_id", "timestamp", "platform", "level", "logger"):
        if key in event and isinstance(event[key], (str, int, float)):
            result[key] = scrub_text(event[key], 160)
    if "message" in event:
        result["message"] = scrub_text(event["message"])
    logentry = event.get("logentry")
    if isinstance(logentry, dict):
        result["logentry"] = {"formatted": scrub_text(logentry.get("formatted") or logentry.get("message", ""))}
    values = []
    for exception in event.get("exception", {}).get("values", [])[-4:]:
        safe = {key: scrub_text(exception[key]) for key in ("type", "value", "module") if key in exception}
        safe["stacktrace"] = _frames(exception.get("stacktrace"))
        mechanism = exception.get("mechanism", {})
        if isinstance(mechanism.get("handled"), bool):
            safe["mechanism"] = {"handled": mechanism["handled"], "type": "generic"}
        values.append(safe)
    if values:
        result["exception"] = {"values": values}
    context = safe_context(event.get("extra", {}))
    extra = event.get("extra", {})
    if isinstance(extra, Mapping) and isinstance(extra.get("diagnostics"), Mapping):
        from .diagnostic_privacy import safe_snapshot
        context["diagnostics"] = safe_snapshot(extra["diagnostics"])
    if context:
        result["extra"] = context
    if contexts := _safe_contexts(event.get("contexts", {})):
        result["contexts"] = contexts
    crumbs = event.get("breadcrumbs", {})
    if isinstance(crumbs, dict) and isinstance(crumbs.get("values"), list):
        result["breadcrumbs"] = {"values": _bounded_breadcrumbs(crumbs["values"])}
    return result


def _bounded_breadcrumbs(values: list) -> list:
    """Newest first within a byte budget: the lead-up never pushes an event past its cap."""
    kept, used = [], 0
    for crumb in reversed(values[-BREADCRUMB_LIMIT:]):
        safe = safe_breadcrumb(crumb) if isinstance(crumb, dict) else None
        if safe is None:
            continue
        used += len(json.dumps(safe, default=str))
        if used > BREADCRUMB_BYTES:
            break
        kept.append(safe)
    return kept[::-1]


def _log_attributes(attributes: Mapping) -> dict:
    """Allowlisted log attributes: hostname, PIDs, raw arguments and extras never leave."""
    result = {}
    for key, value in attributes.items():
        if key == "code.file.path" and isinstance(value, str):
            result[key] = _source_file(value)
        elif key in ("code.line.number", "sentry.severity_number") and type(value) is int:
            result[key] = value
        elif key in ("sentry.release", "sentry.environment") and _build_identity({"release": value}):
            result[key] = value
        elif key in _LOG_TEXT_ATTRIBUTES and isinstance(value, str):
            result[key] = scrub_text(value, 180)
    return result


def _operational_log(name: object, severity: object, body: object, attributes: Mapping):
    """The Debug report's server-log filter, for WARNING and above (OTel severity 13+)."""
    if not operational_log(name, body) or type(severity) is not int or severity < 13:
        return None
    return scrub_text(body, LOG_LINE_LIMIT), {"logger.name": name, **_log_attributes(attributes)}


def safe_log(log: Mapping) -> dict | None:
    """Project one SDK log record (before batching) or drop it."""
    attributes = log.get("attributes") if isinstance(log.get("attributes"), Mapping) else {}
    projected = _operational_log(attributes.get("logger.name"), log.get("severity_number"), log.get("body"), attributes)
    if projected is None or not isinstance(log.get("time_unix_nano"), int):
        return None
    body, attributes = projected
    ids = trace_ids(log.get("trace_id"), log.get("span_id"))
    return {"severity_text": scrub_text(log.get("severity_text", ""), 20), "severity_number": log["severity_number"],
            "body": body, "attributes": attributes, "time_unix_nano": log["time_unix_nano"],
            "trace_id": ids.get("trace_id"), "span_id": ids.get("span_id")}


def safe_log_item(item: object, run_trace: str = "") -> dict | None:
    """Rebuild one serialized log item at transport admission. A row without a
    valid trace takes the transport's `run_trace`: Sentry requires one."""
    from sentry_sdk.utils import serialize_attribute
    if not isinstance(item, Mapping) or not isinstance(item.get("attributes"), Mapping):
        return None
    attributes = {key: value.get("value") for key, value in item["attributes"].items() if isinstance(value, Mapping)}
    projected = _operational_log(attributes.get("logger.name"), attributes.get("sentry.severity_number"),
                                 item.get("body"), attributes)
    stamp = item.get("timestamp")
    if projected is None or type(stamp) not in (int, float):
        return None
    body, attributes = projected
    ids = trace_ids(item.get("trace_id"), item.get("span_id")) or trace_ids(run_trace)
    return {"timestamp": stamp, "level": scrub_text(item.get("level", ""), 20), "body": body, **ids,
            "attributes": {key: serialize_attribute(value) for key, value in attributes.items()}}

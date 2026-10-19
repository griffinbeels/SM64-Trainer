"""Allowlisted automatic diagnostics, scrubbed before persistence or delivery.

This intentionally does not accept arbitrary SDK context: request bodies, locals,
user identities, source lines and attachments never become automatic reports.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import PurePosixPath
from urllib.parse import urlsplit

_SECRET = re.compile(
    r"(?i)\b(?:authorization|cookie|password|passwd|secret|token|api[_-]?key|dsn)"
    r'''\s*[=:]\s*(?:bearer\s+)?(?:"[^"]*"|'[^']*'|[^\s,;]+)'''
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]+=*")
_URL = re.compile(r"(?i)\b(?:https?|file)://[^\s\"'<>)]+")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_WIN_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\)[^\r\n\"'<>|]*")
_POSIX_PATH = re.compile(r"(?<![\w:])/(?:Users|home|tmp|var|mnt|media|private)/[^\s\"'<>]*")
_OPAQUE = re.compile(r"\b(?=[A-Za-z0-9_+/=-]*[0-9])[A-Za-z0-9_+/=-]{40,}\b")
_CONTEXT_KEYS = frozenset({
    "operation", "component", "stage", "phase", "action", "code", "errno",
    "winerror", "error_type", "setup_state", "plugin_status", "platform",
    "version", "line", "column", "file", "browser_stack", "elevated",
    "status", "status_code", "method", "route", "source", "reason", "stack", "path",
    "encoder_backend", "codec", "requested_codec", "capture_build", "gpu_vendor_id", "gpu_name", "gpu_driver",
    "native_result", "native_stage", "native_operation", "native_hresult", "encoder_error", "driver_error",
    "encoder_result", "native_reason",
    "gl_vendor", "gl_renderer", "gl_version", "missing_entry_point",
})


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
        (_WIN_PATH, "[path]"), (_POSIX_PATH, "[path]"), (_OPAQUE, "[value]"),
    ):
        text = pattern.sub(replacement, text)
    return text[:limit]


def safe_context(context: object) -> dict:
    """Retain documented diagnostic fields only; callers cannot add raw data."""
    if not isinstance(context, Mapping):
        return {}
    return {
        key: value if isinstance(value, (bool, int, float)) or (
            key == "capture_build" and isinstance(value, str)
            and re.fullmatch(r"[0-9a-f]{64}-gpu-runtime", value)
        ) else scrub_text(value)
        for key, value in context.items()
        if key in _CONTEXT_KEYS and isinstance(value, (str, bool, int, float))
    }


def _frames(stacktrace: object) -> dict:
    if not isinstance(stacktrace, dict):
        return {}
    frames = []
    for frame in stacktrace.get("frames", [])[-30:]:
        if not isinstance(frame, dict):
            continue
        filename = str(frame.get("filename", "")).replace("\\", "/")
        marker = filename.find("sm64_events/")
        filename = filename[marker:] if marker >= 0 else PurePosixPath(filename).name
        safe = {"filename": scrub_text(filename, 180)}
        for key in ("module", "function"):
            if isinstance(frame.get(key), str):
                safe[key] = scrub_text(frame[key], 180)
        for key in ("lineno", "colno", "in_app"):
            if isinstance(frame.get(key), (int, bool)):
                safe[key] = frame[key]
        frames.append(safe)
    return {"frames": frames}


def safe_breadcrumb(crumb: dict, _hint: object = None) -> dict | None:
    """Only application logs provide breadcrumbs; network/log bodies stay out."""
    category = str(crumb.get("category", ""))
    if not category.startswith(("sm64.", "sm64_events.")):
        return None
    return {
        "category": scrub_text(category, 120),
        "message": scrub_text(crumb.get("message", ""), 220),
        "level": scrub_text(crumb.get("level", "info"), 20),
        "type": "default",
    }


def _build_identity(event: dict) -> dict:
    # These identities come from trusted startup/build configuration. A commit
    # SHA is essential evidence, despite looking like an opaque access token.
    result = {}
    for key in ("release", "environment"):
        value = event.get(key)
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._+@/-]{1,160}", value):
            result[key] = value
    return result


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
    if context:
        result["extra"] = context
    contexts = event.get("contexts", {})
    if isinstance(contexts, dict):
        safe = {}
        for name in ("os", "runtime"):
            if isinstance(contexts.get(name), dict):
                safe[name] = {key: scrub_text(value, 120) for key, value in contexts[name].items()
                              if key in ("name", "version", "build") and isinstance(value, str)}
        if safe:
            result["contexts"] = safe
    crumbs = event.get("breadcrumbs", {})
    if isinstance(crumbs, dict):
        safe = [safe_breadcrumb(crumb) for crumb in crumbs.get("values", [])[-20:]]
        result["breadcrumbs"] = {"values": [crumb for crumb in safe if crumb]}
    return result

"""Automatic, private, bounded error reports; inert until configured with a DSN.

The user preference survives restarts. Captures are safe from any application
thread, and transport failures never become application failures. The only
attachment is the same redacted Debug report the user can export, built on the
transport's worker; no ROM, recording, database, input or screenshot is sent.
Warning-and-above operational log lines go out as Sentry Logs through the
Debug report's server-log filter; INFO lines stay breadcrumbs.
"""
from __future__ import annotations

import atexit
import json
import logging
import sys
import threading
import time
from pathlib import Path

import sentry_sdk
from sentry_sdk.integrations.excepthook import ExcepthookIntegration
from sentry_sdk.integrations.logging import LoggingIntegration, ignore_logger, ignore_logger_for_sentry_logs
from sentry_sdk.integrations.threading import ThreadingIntegration

from .error_reporting_privacy import BREADCRUMB_LIMIT, safe_breadcrumb, safe_context, safe_event, safe_log
from .error_reporting_transport import PersistentTransport

_lock = threading.RLock()
_client = None
_transport = None
_directory: Path | None = None
_enabled = False
_configuration_error = False
_preference_error = False
_diagnostic_source = None
_report_source = None
_collecting = threading.local()
_consent_generation = 0
_consent_since = 0.0


def _before_send(event, _hint):
    if not _enabled:
        return None
    generation = _consent_generation
    extra = event.get("extra")
    requested = extra.pop("_reporting_epoch", None) if isinstance(extra, dict) else None
    if requested is not None and requested != generation:
        return None
    crumbs = event.get("breadcrumbs", {})
    if isinstance(crumbs, dict):
        crumbs["values"] = [crumb for crumb in crumbs.get("values", [])
                            if isinstance(crumb, dict) and isinstance(crumb.get("data"), dict)
                            and crumb["data"].get("reporting_generation") == generation]
    trace = (event.get("contexts") or {}).get("trace")  # the run's trace, which its logs carry too
    event["contexts"] = {"runtime": {
        "name": sys.implementation.name,
        "version": ".".join(str(part) for part in sys.version_info[:3]),
    }, "os": {"name": sys.platform}}
    if isinstance(trace, dict):
        event["contexts"]["trace"] = trace
    if sys.platform == "win32":
        version = sys.getwindowsversion()
        event["contexts"]["os"]["version"] = f"{version.major}.{version.minor}.{version.build}"
    source = _diagnostic_source
    if source is not None and not getattr(_collecting, "active", False):
        _collecting.active = True
        try:
            event.setdefault("extra", {})["diagnostics"] = _current_history(source())
        except Exception:  # noqa: BLE001 -- preserve the error even when evidence is unavailable.
            event.setdefault("extra", {})["diagnostics"] = {"error_type": "Unavailable"}
        finally:
            _collecting.active = False
    return safe_event(event) if _enabled and generation == _consent_generation else None


def _current_history(snapshot):
    """Do not backfill old native history after an opt-out/re-enable cycle."""
    from datetime import datetime
    from .diagnostic_privacy import safe_snapshot
    result = safe_snapshot(snapshot)
    native = result.get("native", {})
    recent = []
    for row in native.get("events", []):
        try:
            if datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")).timestamp() >= _consent_since:
                recent.append(row)
        except (KeyError, ValueError, TypeError):
            pass
    native["events"] = recent
    gpu = result.get("capture", {}).get("gpu", {})
    for name in ("first_failure", "latest"):
        stamp = gpu.get(name, {}).get("observed_unix_s")
        if isinstance(stamp, (int, float)) and stamp < _consent_since:
            gpu[name] = {}
    return result


def set_diagnostic_source(source):
    """Bind one app lifetime; an older app cannot unregister its replacement."""
    global _diagnostic_source
    _diagnostic_source = source
    def unbind():
        global _diagnostic_source
        if _diagnostic_source is source:
            _diagnostic_source = None
    return unbind


def set_report_source(source):
    """Bind the app's Debug report builder; an older app cannot unregister its replacement."""
    global _report_source
    _report_source = source
    def unbind():
        global _report_source
        if _report_source is source:
            _report_source = None
    return unbind


def _debug_report():
    """Called on the transport worker only, and only while reporting is enabled."""
    source = _report_source
    return source() if source is not None and _enabled else None


def _before_send_log(log, _hint):
    return safe_log(log) if _enabled else None


def _before_breadcrumb(crumb, hint):
    generation = _consent_generation
    result = safe_breadcrumb(crumb, hint) if _enabled else None
    if not _enabled or generation != _consent_generation:
        return None
    if result is not None:
        result["data"] = {"reporting_generation": generation}
    return result


def _clear_breadcrumbs():
    for scope in (sentry_sdk.get_current_scope(), sentry_sdk.get_isolation_scope(), sentry_sdk.get_global_scope()):
        scope.clear_breadcrumbs()


def _read_preference(directory: Path, default: bool) -> bool:
    try:
        value = json.loads((directory / "preference.json").read_text(encoding="utf-8"))
        return value["enabled"] if isinstance(value.get("enabled"), bool) else default
    except FileNotFoundError:
        return default
    except (OSError, ValueError, TypeError, AttributeError):
        # An unreadable previous choice must not silently re-enable transmission.
        return False


def configure(*, dsn, release, environment, directory, enabled=True):
    """Configure once at normal startup; absent/invalid DSNs remain offline."""
    global _client, _transport, _directory, _enabled, _configuration_error, _consent_generation, _consent_since
    shutdown()
    with _lock:
        _clear_breadcrumbs()
        _consent_generation += 1
        _consent_since = time.time()
        _directory = Path(directory)
        _enabled = _read_preference(_directory, bool(enabled))
        _configuration_error = False
        if not dsn:
            return
        try:
            # Never capture error collector/HTTP diagnostics recursively.
            for logger in ("sentry_sdk.*", "urllib3.*", "sm64_events.core.error_reporting*"):
                ignore_logger(logger)
                ignore_logger_for_sentry_logs(logger)
            transport = PersistentTransport({"dsn": dsn}, _directory, enabled=_enabled, report=_debug_report)
            _transport = transport
            sentry_sdk.init(
                dsn=dsn, release=release, environment=environment,
                transport=transport, default_integrations=False, auto_enabling_integrations=False,
                integrations=[ExcepthookIntegration(), ThreadingIntegration(),
                              LoggingIntegration(level=logging.INFO, event_level=logging.ERROR,
                                                 sentry_logs_level=logging.WARNING)],
                send_default_pii=False, include_local_variables=False,
                include_source_context=False, attach_stacktrace=False, max_breadcrumbs=BREADCRUMB_LIMIT,
                before_send=_before_send, before_breadcrumb=_before_breadcrumb,
                enable_logs=True, before_send_log=_before_send_log,
                traces_sample_rate=0, profiles_sample_rate=0, auto_session_tracking=False,
                send_client_reports=False, shutdown_timeout=0,
            )
            _client = sentry_sdk.get_client()
        except Exception:  # noqa: BLE001 -- offline diagnostics must not prevent app startup
            _configuration_error = True
            if _transport:
                _transport.kill()
            _transport = None
            _client = None


def capture_exception(error, *, context=None):
    """Capture a caught terminal failure without leaking caller scopes."""
    if _client is None or not _enabled:
        return None
    try:
        with sentry_sdk.new_scope() as scope:
            scope.set_client(_client)
            for key, value in safe_context(context).items():
                scope.set_extra(key, value)
            return sentry_sdk.capture_exception(error)
    except Exception:  # noqa: BLE001 -- reporting is never allowed to break the action
        return None


def collection_epoch():
    """An ephemeral consent fence for optional observers; never a player identity."""
    with _lock:
        return _consent_generation if _client is not None and _enabled else None


def capture_problem(message, *, context=None, expected_generation=None):
    """Capture detectable failures which did not throw an exception."""
    generation = _consent_generation if expected_generation is None else expected_generation
    client = _client
    if client is None or not _enabled or generation != _consent_generation:
        return None
    try:
        with sentry_sdk.new_scope() as scope:
            scope.set_client(client)
            # The callback consumes this before projection. Never hold the
            # preference lock across diagnostic IO: opt-out must stay prompt.
            scope.set_extra("_reporting_epoch", generation)
            for key, value in safe_context(context).items():
                scope.set_extra(key, value)
            return sentry_sdk.capture_message(message, level="error")
    except Exception:  # noqa: BLE001 -- reporting is never allowed to break the action
        return None


def set_enabled(enabled):
    """Persist the user's choice; disabling drops all unsent reports."""
    global _enabled, _preference_error, _consent_generation, _consent_since
    with _lock:
        if bool(enabled) != _enabled:
            _consent_generation += 1
            _consent_since = time.time()
        _enabled = bool(enabled)
        if not _enabled:
            _clear_breadcrumbs()
        _preference_error = False
        try:
            if _transport:
                _transport.set_enabled(_enabled)
            elif _directory and not _enabled:
                for path in _directory.glob("*.envelope"):
                    path.unlink(missing_ok=True)
        except OSError:
            _preference_error = True
        try:
            if _directory:
                _directory.mkdir(parents=True, exist_ok=True)
                temporary = _directory / "preference.tmp"
                temporary.write_text(json.dumps({"enabled": _enabled}), encoding="utf-8")
                temporary.replace(_directory / "preference.json")
        except OSError:
            _preference_error = True


def status():
    """Safe UI status with no DSN, paths, error contents or report identities."""
    with _lock:
        return {
            "configured": _client is not None, "enabled": _enabled,
            "configuration_error": _configuration_error, "preference_error": _preference_error,
            **(_transport.status() if _transport else {"pending": 0}),
        }


def shutdown():
    """Stop owned worker and persist queued events without waiting for upload."""
    global _client, _transport
    with _lock:
        if _transport:
            _transport.kill()
        if _client:
            _client.close(timeout=0)
        _transport = None
        _client = None


# Default SDK integrations are intentionally disabled, including its exit hook.
# Uncaught main-thread exceptions reach sys.excepthook immediately before exit;
# persist that final incoming report even when its daemon has not run yet.
atexit.register(shutdown)

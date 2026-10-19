"""Automatic, private, bounded error reports; inert until configured with a DSN.

The user preference survives restarts. Captures are safe from any application
thread, and transport failures never become application failures. No gameplay,
ROM, recording, raw log or screenshot attachments are collected.
"""
from __future__ import annotations

import json
import logging
import sys
import threading
from pathlib import Path

import sentry_sdk
from sentry_sdk.integrations.excepthook import ExcepthookIntegration
from sentry_sdk.integrations.logging import LoggingIntegration, ignore_logger
from sentry_sdk.integrations.threading import ThreadingIntegration

from .error_reporting_privacy import safe_breadcrumb, safe_context, safe_event
from .error_reporting_transport import PersistentTransport

_lock = threading.RLock()
_client = None
_transport = None
_directory: Path | None = None
_enabled = False
_configuration_error = False
_preference_error = False


def _before_send(event, _hint):
    if not _enabled:
        return None
    event["contexts"] = {"runtime": {
        "name": sys.implementation.name,
        "version": ".".join(str(part) for part in sys.version_info[:3]),
    }, "os": {"name": sys.platform}}
    if sys.platform == "win32":
        version = sys.getwindowsversion()
        event["contexts"]["os"]["version"] = f"{version.major}.{version.minor}.{version.build}"
    return safe_event(event)


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
    global _client, _transport, _directory, _enabled, _configuration_error
    shutdown()
    with _lock:
        _directory = Path(directory)
        _enabled = _read_preference(_directory, bool(enabled))
        _configuration_error = False
        if not dsn:
            return
        try:
            # Never capture error collector/HTTP diagnostics recursively.
            for logger in ("sentry_sdk.*", "urllib3.*", "sm64_events.core.error_reporting*"):
                ignore_logger(logger)
            transport = PersistentTransport({"dsn": dsn}, _directory, enabled=_enabled)
            _transport = transport
            sentry_sdk.init(
                dsn=dsn, release=release, environment=environment,
                transport=transport, default_integrations=False, auto_enabling_integrations=False,
                integrations=[ExcepthookIntegration(), ThreadingIntegration(),
                              LoggingIntegration(level=logging.INFO, event_level=logging.ERROR, sentry_logs_level=None)],
                send_default_pii=False, include_local_variables=False,
                include_source_context=False, attach_stacktrace=False, max_breadcrumbs=20,
                before_send=_before_send, before_breadcrumb=safe_breadcrumb,
                traces_sample_rate=0, profiles_sample_rate=0, auto_session_tracking=False,
                send_client_reports=False, enable_logs=False, shutdown_timeout=0,
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


def capture_problem(message, *, context=None):
    """Capture detectable failures which did not throw an exception."""
    if _client is None or not _enabled:
        return None
    try:
        with sentry_sdk.new_scope() as scope:
            scope.set_client(_client)
            for key, value in safe_context(context).items():
                scope.set_extra(key, value)
            return sentry_sdk.capture_message(message, level="error")
    except Exception:  # noqa: BLE001 -- reporting is never allowed to break the action
        return None


def set_enabled(enabled):
    """Persist the user's choice; disabling drops all unsent reports."""
    global _enabled, _preference_error
    with _lock:
        _enabled = bool(enabled)
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

"""Optional public Sentry configuration; installed builds use their own bundle.

The DSN is a public ingest identifier, never an administrative API credential.
No account/project means an inert collector with a reachable privacy setting.
"""
import json
import logging
import os
from pathlib import Path
import re
import sys
import threading
from urllib.parse import urlsplit

CONFIG_FILE = "error-reporting.json"
_started = False
_lock = threading.Lock()


def validate_dsn(value: str) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("Sentry DSN must be a bounded public reporting URL.")
    if not value:
        return ""
    try:
        url = urlsplit(value)
        valid = (url.scheme == "https" and url.hostname is not None
                 and url.hostname.endswith(".sentry.io")
                 and url.port in (None, 443) and not url.password
                 and re.fullmatch(r"[A-Za-z0-9_-]+", url.username or "")
                 and re.fullmatch(r"/[0-9]+", url.path)
                 and not url.query and not url.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Use a public HTTPS Sentry DSN, without a secret key.")
    return value


def configured_dsn() -> str:
    if not getattr(sys, "frozen", False):
        return validate_dsn(os.environ.get("SM64_SENTRY_DSN", ""))
    bundle = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    path = bundle / CONFIG_FILE
    try:
        if path.stat().st_size > 4096:
            raise ValueError("Packaged error reporting configuration is too large.")
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ""  # Older bundles did not configure reporting.
    if (not isinstance(data, dict) or set(data) != {"schema", "dsn"}
            or type(data["schema"]) is not int or data["schema"] != 1):
        raise ValueError("Invalid packaged error reporting configuration.")
    return validate_dsn(data["dsn"])


def initialize_reporting() -> None:
    """Initialize once, before normal startup work, without breaking startup."""
    global _started
    with _lock:
        if _started:
            return
        try:
            from sm64_events.core import error_reporting
            from sm64_events.core.build_channel import current_build
            from sm64_events.core.paths import data_root, migrate_legacy_data_dir
            from sm64_events.core.version import __version__
            build = current_build()
            # Migrate before persisting preferences can occupy the new profile.
            migrate_legacy_data_dir()
            error_reporting.configure(
                dsn=configured_dsn(), release=f"{__version__}+{build.build_id or 'source'}",
                environment=build.channel if getattr(sys, "frozen", False) else "development",
                directory=data_root() / "error-reporting")
            _started = True
        except Exception:  # noqa: BLE001 - diagnostics cannot prevent application startup
            # Do not log the exception value: a malformed URL can contain a secret.
            logging.getLogger(__name__).warning("Automatic error reporting could not initialize.")

# src/sm64_events/core/logging_setup.py
"""Bounded local debug logging (UTC), startup expiry and console output."""
import logging
from logging.handlers import RotatingFileHandler
import time
from pathlib import Path

from sm64_events.core.paths import data_root, diagnostics_dir, logs_dir
from sm64_events.core.diagnostic_retention import SERVER_LOG_MAX_BYTES, prune_debug_logs, prune_reports


def configure_logging(log_dir: Path | None = None) -> None:
    # Both normal entry points come here; privileged/native helpers do not.
    from sm64_events.core.reporting_setup import initialize_reporting
    initialize_reporting()
    # Route through core.paths so a frozen exe logs to %LOCALAPPDATA% (not the
    # cwd it was double-clicked from); from source this is still ./logs.
    normal_startup = log_dir is None
    log_dir = logs_dir() if normal_startup else log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)sZ %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    formatter.converter = time.gmtime
    root = logging.getLogger()
    if root.handlers:  # already configured (e.g. uvicorn --reload re-import)
        return
    prune_debug_logs(log_dir, data_directory=data_root() / "data" if normal_startup else None)
    if normal_startup:
        prune_reports(diagnostics_dir())
    root.setLevel(logging.INFO)
    for handler in (RotatingFileHandler(log_dir / "sm64_events.log", encoding="utf-8",
                                       maxBytes=SERVER_LOG_MAX_BYTES, backupCount=1),
                    logging.StreamHandler()):
        handler.setFormatter(formatter)
        root.addHandler(handler)

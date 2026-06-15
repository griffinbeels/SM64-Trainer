# src/sm64_events/core/logging_setup.py
"""Persistent file logging (UTC) plus console output."""
import logging
import time
from pathlib import Path

from sm64_events.core.paths import logs_dir


def configure_logging(log_dir: Path | None = None) -> None:
    # Route through core.paths so a frozen exe logs to %LOCALAPPDATA% (not the
    # cwd it was double-clicked from); from source this is still ./logs.
    log_dir = logs_dir() if log_dir is None else log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)sZ %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    formatter.converter = time.gmtime
    root = logging.getLogger()
    if root.handlers:  # already configured (e.g. uvicorn --reload re-import)
        return
    root.setLevel(logging.INFO)
    for handler in (logging.FileHandler(log_dir / "sm64_events.log", encoding="utf-8"),
                    logging.StreamHandler()):
        handler.setFormatter(formatter)
        root.addHandler(handler)

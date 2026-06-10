# src/sm64_events/core/logging_setup.py
"""Persistent file logging (UTC) plus console output."""
import logging
import time
from pathlib import Path


def configure_logging(log_dir: Path = Path("logs")) -> None:
    log_dir.mkdir(exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)sZ %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    formatter.converter = time.gmtime
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in (logging.FileHandler(log_dir / "sm64_events.log", encoding="utf-8"),
                    logging.StreamHandler()):
        handler.setFormatter(formatter)
        root.addHandler(handler)

# src/sm64_events/main.py
"""Composition root: registry -> memory -> poller -> detectors -> tracking -> app."""
import logging
from pathlib import Path

from sm64_events.core.logging_setup import configure_logging
from sm64_events.detectors.anchors import AnchorDetector
from sm64_events.detectors.lifecycle import GameResetDetector
from sm64_events.detectors.star_grab import StarGrabDetector
from sm64_events.memory.pj64 import Pj64Memory
from sm64_events.server.app import create_app
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.poller import Poller
from sm64_events.storage.db import Database
from sm64_events.tracking.service import TrackerService

DB_PATH = Path("data") / "tracker.db"


def build():
    configure_logging()
    memory = Pj64Memory()
    broadcaster = Broadcaster()
    try:
        db = Database(DB_PATH)
    except Exception:
        logging.getLogger("sm64.tracker").exception(
            "database unavailable - running broadcast-only")
        db = None
    service = TrackerService(db, broadcaster)
    detectors = [GameResetDetector(), AnchorDetector(), StarGrabDetector()]
    poller = Poller(memory, detectors, service)  # service IS the event sink
    return create_app(poller, broadcaster, service=service)


app = build()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8064)

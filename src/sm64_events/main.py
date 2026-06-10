# src/sm64_events/main.py
"""Composition root: wire registry -> memory -> poller -> detectors -> app."""
from sm64_events.core.logging_setup import configure_logging
from sm64_events.detectors.lifecycle import GameResetDetector
from sm64_events.detectors.star_grab import StarGrabDetector
from sm64_events.memory.pj64 import Pj64Memory
from sm64_events.server.app import create_app
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.poller import Poller


def build():
    configure_logging()
    memory = Pj64Memory()
    broadcaster = Broadcaster()
    poller = Poller(memory, [GameResetDetector(), StarGrabDetector()], broadcaster)
    return create_app(poller, broadcaster)


app = build()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8064)

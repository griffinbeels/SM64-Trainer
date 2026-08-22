"""The one app-with-a-live-session harness every import API test drives.

Driven through the real lifespan, deliberately: importing requires a live
session the way every other write command does, so a harness that skipped
startup would be exercising a path the app never takes.

The REAL shipped ladders are loaded, so the stars an import lands on actually
grade. A hand-made seed would leave every imported time unrankable and the
celebration guard in `test_import_api.py` unable to fail.
"""
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

import sm64_events
from sm64_events.ranks.standards import RankStandards
from sm64_events.server.app import create_app
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.poller import Poller
from sm64_events.storage.db import Database
from sm64_events.tracking.service import TrackerService


class OfflineMemory:
    attached = False

    def attach(self):
        return False

    def detach(self):
        pass


def bundled_standards_seed() -> Path:
    return (Path(sm64_events.__file__).parent / "data"
            / "rank_standards.seed.json")


@contextmanager
def make_client(tmp_path):
    """`(client, db, service)` over a fresh database with the shipped
    ladders loaded."""
    db = Database(tmp_path / "t.db")
    broadcaster = Broadcaster()
    ranks = RankStandards(tmp_path / "rs.json",
                          seed_path=bundled_standards_seed())
    ranks.load()
    service = TrackerService(db, broadcaster, ranks=ranks)
    poller = Poller(OfflineMemory(), [], service)
    with TestClient(create_app(poller, broadcaster, service=service)) as client:
        yield client, db, service

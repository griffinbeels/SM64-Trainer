"""The service half of importing a time he already earned."""
import asyncio

from sm64_events.server.broadcaster import Broadcaster
from sm64_events.storage.db import Database
from sm64_events.tracking.importing import ImportCandidate
from sm64_events.tracking.service import TrackerService


def make(tmp_path):
    db = Database(tmp_path / "t.db")
    svc = TrackerService(db, Broadcaster())
    asyncio.run(svc.start())
    return db, svc


def candidate(cs=886, key="star:1:0", strat="Standard", version=None):
    return ImportCandidate(entity_key=key, strat_tag=strat, time_cs=cs,
                           game_version=version)


def test_import_inserts_a_pb_with_no_attempt(tmp_path):
    db, svc = make(tmp_path)
    summary = asyncio.run(svc.import_times("manual", [candidate()]))
    assert summary["imported"] == 1
    row = db.current_pb(1, 0, "igt", strat_tag="Standard")
    assert row["frames"] == 266
    assert row["attempt_id"] is None
    assert row["imported_from"] == "manual"


def test_the_version_survives_into_the_stored_row(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.import_times("sheet:DentoriousRed",
                                 [candidate(version="jp")]))
    row = db.current_pb(1, 0, "igt", strat_tag="Standard")
    assert row["game_version"] == "jp"


def test_importing_the_same_batch_twice_changes_nothing(tmp_path):
    """The improvement rule is what makes the button safe to press twice."""
    db, svc = make(tmp_path)
    batch = [candidate()]
    asyncio.run(svc.import_times("manual", batch))
    before = len(db.pbs())
    second = asyncio.run(svc.import_times("manual", batch))
    assert second["imported"] == 0
    assert second["already_faster"] == 1
    assert len(db.pbs()) == before


def test_import_creates_no_attempt(tmp_path):
    """The journal records what the GAME did, and the projector re-derives
    every attempt from it. A brought-in time was never played, so nothing here
    may appear as a run."""
    db, svc = make(tmp_path)
    before = len(db.attempts())
    asyncio.run(svc.import_times("manual", [candidate()]))
    assert len(db.attempts()) == before


def test_a_slower_import_never_replaces_a_faster_one(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.import_times("manual", [candidate(886)]))
    asyncio.run(svc.import_times("manual", [candidate(1200)]))
    assert db.current_pb(1, 0, "igt", strat_tag="Standard")["frames"] == 266


def test_removing_a_source_erases_only_its_rows(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.import_times("manual", [candidate()]))
    asyncio.run(svc.import_times("sheet:DentoriousRed",
                                 [candidate(700, key="star:2:0")]))
    assert svc.remove_imported("manual") == 1
    assert db.current_pb(1, 0, "igt", strat_tag="Standard") is None
    assert db.current_pb(2, 0, "igt", strat_tag="Standard") is not None


def test_removing_an_import_restores_what_it_superseded(tmp_path):
    """Latest-row-wins is the pbs contract, so deleting the imported row makes
    the previous save current again -- the same restoration undo_pb relies on."""
    db, svc = make(tmp_path)
    db.insert_pb(course_id=1, star_id=0, strat_tag="Standard",
                 timer_mode="igt", frames=400, attempt_id=None,
                 saved_utc="2026-08-19T00:00:00Z")
    asyncio.run(svc.import_times("manual", [candidate(886)]))
    assert db.current_pb(1, 0, "igt", strat_tag="Standard")["frames"] == 266
    svc.remove_imported("manual")
    assert db.current_pb(1, 0, "igt", strat_tag="Standard")["frames"] == 400

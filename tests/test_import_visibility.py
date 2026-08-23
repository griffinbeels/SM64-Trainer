"""An imported time has to be SOMEWHERE he looks.

It is an attempt row (2026-08-22), so it reaches every surface the way a
played one does — the practice log builds its sections from one pass over
attempts, and so does the picker's rank map. These pin that no surface is
special-casing it, and that absence still means "never practised".
"""
import asyncio
import json

from sm64_events.ranks.standards import RankStandards
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.storage.db import Database
from sm64_events.tracking.importing import ImportCandidate
from sm64_events.tracking.service import TrackerService
from sm64_events.tracking.views import build_entity_ranks, build_session_view

IMPORTED_CS = 4450            # 0'44"50 -> 1335 frames
IMPORTED_FRAMES = 1335


def standards(tmp_path):
    path = tmp_path / "rs.json"
    path.write_text(json.dumps({"version": 1, "entities": {
        "star:1:0": {"clock": "igt", "strategies": {"Standard": {
            "Mario": 44.90, "Grandmaster": 45.50, "Master": 46.70,
            "Diamond": 47.46, "Platinum": 49.10, "Gold": 50.10,
            "Silver": 59.06, "Bronze": 63.10}}}}}))
    store = RankStandards(path)
    store.load()
    return store


def make(tmp_path):
    db = Database(tmp_path / "t.db")
    svc = TrackerService(db, Broadcaster())
    asyncio.run(svc.start())
    svc.ranks = standards(tmp_path)
    return db, svc


def import_a_star(svc):
    asyncio.run(svc.import_times("manual", [ImportCandidate(
        entity_key="star:1:0", strat_tag="Standard", time_cs=IMPORTED_CS)]))


def import_a_segment_gold(db, svc):
    segment_id = db.segment_defs()[0]["id"]
    asyncio.run(svc.import_times("livesplit", [ImportCandidate(
        entity_key=f"segment:{segment_id}", strat_tag="Standard",
        time_cs=1200, timer_mode="rta")]))
    return segment_id


def star_section(view):
    return next((s for s in view["stars"]
                 if (s["course_id"], s["star_id"]) == (1, 0)), None)


def test_an_imported_star_is_a_section_with_one_row_in_its_log(tmp_path):
    db, svc = make(tmp_path)
    import_a_star(svc)
    section = star_section(build_session_view(db, svc, "igt", scope="lifetime"))
    assert section["pb"]["igt"]["frames"] == IMPORTED_FRAMES
    assert section["pb"]["igt"]["display"] == '0\'44"50'
    (row,) = section["attempts"]
    assert row["id"] == section["pb"]["igt"]["attempt_id"]
    assert row["outcome"] == "success"
    assert row["igt_frames"] == IMPORTED_FRAMES
    # No caveat: the time he wrote down is the legal quantity.
    assert not row.get("caveat")


def test_it_is_in_the_session_he_made_it_in(tmp_path):
    """Recent activity is a record of what he just did, and bringing a time
    in is something he just did — the row lands there with a Save-time
    timestamp, like any other row of the session."""
    db, svc = make(tmp_path)
    import_a_star(svc)
    section = star_section(build_session_view(db, svc, "igt", scope="session"))
    assert section is not None
    assert len(section["attempts"]) == 1


def test_the_picker_ranks_a_star_he_has_only_imported(tmp_path):
    """The star grid is the surface that answers 'how good am I at this'."""
    db, svc = make(tmp_path)
    import_a_star(svc)
    assert build_entity_ranks(db, svc)["star:1:0"]["rank"] == "Mario"


def test_an_imported_gold_is_a_segment_section_with_one_row(tmp_path):
    """Rule 11: a LiveSplit gold earns a card exactly as a sheet time does."""
    db, svc = make(tmp_path)
    segment_id = import_a_segment_gold(db, svc)
    view = build_session_view(db, svc, "igt", scope="lifetime")
    section = next(s for s in view["segments"] if s["segment_id"] == segment_id)
    assert section["pb"]["rta"]["frames"] == 360
    (row,) = section["attempts"]
    assert row["rta_frames"] == 360


def test_a_star_with_neither_attempts_nor_a_pb_stays_absent(tmp_path):
    """Absence is the picker's 'never practised', so a corpus-wide listing
    here would erase that signal."""
    db, svc = make(tmp_path)
    assert build_entity_ranks(db, svc) == {}
    assert star_section(build_session_view(db, svc, "igt", scope="lifetime")) \
        is None

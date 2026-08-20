"""An imported time has to be SOMEWHERE he looks.

The practice log builds its sections from one pass over attempts, and the
picker's rank map does the same. A brought-in best has no attempt behind it, so
without these it lands in the store and shows on no surface at all — import 400
times, open the tab you live in, see nothing.
"""
import asyncio
import json

from sm64_events.ranks.standards import RankStandards
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.storage.db import Database
from sm64_events.tracking.service import TrackerService
from sm64_events.tracking.views import build_entity_ranks, build_session_view

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


def an_imported_pb(db):
    db.insert_pb(course_id=1, star_id=0, strat_tag="Standard",
                 timer_mode="igt", frames=IMPORTED_FRAMES, attempt_id=None,
                 saved_utc="2026-08-20T00:00:00Z", imported_from="manual")


def test_a_star_with_only_an_imported_pb_gets_a_lifetime_section(tmp_path):
    db, svc = make(tmp_path)
    an_imported_pb(db)
    view = build_session_view(db, svc, "igt", scope="lifetime")
    section = next(s for s in view["stars"]
                   if (s["course_id"], s["star_id"]) == (1, 0))
    assert section["pb"]["igt"]["frames"] == IMPORTED_FRAMES
    assert section["pb"]["igt"]["display"] == '0\'44"50'
    assert section["pb"]["igt"]["attempt_id"] is None
    assert section["attempts"] == []


def test_the_session_view_stays_a_record_of_the_session(tmp_path):
    """A brought-in best belongs to no session, so it must not sit in every
    session's log forever -- the lifetime view is where everything you HAVE
    lives."""
    db, svc = make(tmp_path)
    an_imported_pb(db)
    view = build_session_view(db, svc, "igt", scope="session")
    assert not [s for s in view["stars"]
                if (s["course_id"], s["star_id"]) == (1, 0)]


def test_the_picker_ranks_a_star_he_has_only_imported(tmp_path):
    """The star grid is the surface that answers 'how good am I at this', and
    it is not session-scoped -- so this is where an import shows up first."""
    db, svc = make(tmp_path)
    an_imported_pb(db)
    ranked = build_entity_ranks(db, svc)
    assert ranked["star:1:0"]["rank"] == "Mario"


def test_a_star_with_neither_attempts_nor_a_pb_stays_absent(tmp_path):
    """Absence is the picker's 'never practised', so a corpus-wide listing
    here would erase that signal."""
    db, svc = make(tmp_path)
    assert build_entity_ranks(db, svc) == {}
    view = build_session_view(db, svc, "igt", scope="lifetime")
    assert not [s for s in view["stars"]
                if (s["course_id"], s["star_id"]) == (1, 0)]

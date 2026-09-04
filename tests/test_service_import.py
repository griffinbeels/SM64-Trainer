"""The service half of importing a time he already earned.

An imported time IS an attempt — "It should show the new entry in the practice
log as an entry row. This is because it then affords us all of the
functionality of a practice log entry row (deleting, undoing, etc)"
(2026-08-22). So most of what is pinned here is that the row is a real
attempt: journaled, rebuilt on replay, and reachable by every row command.
"""
import asyncio
from datetime import datetime, timezone

import pytest

from sm64_events.core.events import Event
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.storage.db import Database
from sm64_events.tracking.importing import IMPORT_EVENT, ImportCandidate
from sm64_events.tracking.service import TrackerService


def make(tmp_path):
    db = Database(tmp_path / "t.db")
    svc = TrackerService(db, Broadcaster())
    asyncio.run(svc.start())
    return db, svc


def candidate(cs=886, key="star:1:0", strat="Standard", version=None):
    return ImportCandidate(entity_key=key, strat_tag=strat, time_cs=cs,
                           game_version=version)


def imported_attempts(db):
    return [a for a in db.attempts() if a.timed_by == "imported"]


def test_import_lands_a_pb_linked_to_its_own_attempt(tmp_path):
    db, svc = make(tmp_path)
    summary = asyncio.run(svc.import_times("manual", [candidate()]))
    assert summary["imported"] == 1
    row = db.current_pb(1, 0, "igt", strat_tag="Standard")
    assert row["frames"] == 266
    assert row["imported_from"] == "manual"
    (attempt,) = imported_attempts(db)
    assert row["attempt_id"] == attempt.id


def test_the_version_survives_into_the_stored_row(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.import_times("sheet:DentoriousRed",
                                 [candidate(version="jp")]))
    row = db.current_pb(1, 0, "igt", strat_tag="Standard")
    assert row["game_version"] == "jp"


def test_importing_the_same_batch_twice_changes_nothing(tmp_path):
    """The improvement rule is what makes the button safe to press twice —
    and it is what keeps a re-import from growing a second row."""
    db, svc = make(tmp_path)
    batch = [candidate()]
    asyncio.run(svc.import_times("manual", batch))
    before_pbs, before_rows = len(db.pbs()), len(db.attempts())
    second = asyncio.run(svc.import_times("manual", batch))
    assert second["imported"] == 0
    assert second["already_faster"] == 1
    assert len(db.pbs()) == before_pbs
    assert len(db.attempts()) == before_rows


def test_import_creates_a_real_attempt_row(tmp_path):
    """A success with no anchor, at the moment he pressed Save, on the clock
    the source measures. `timed_at` is "xcam" so the row wears no caveat —
    the time he wrote down IS the legal quantity ("the user DID beat it")."""
    db, svc = make(tmp_path)
    before = len(db.attempts())
    asyncio.run(svc.import_times("manual", [candidate()]))
    assert len(db.attempts()) == before + 1
    (attempt,) = imported_attempts(db)
    assert (attempt.course_id, attempt.star_id, attempt.segment_id) == (1, 0, None)
    assert attempt.outcome == "success"
    assert attempt.igt_frames == 266 and attempt.rta_frames is None
    assert attempt.strat_tag == "Standard"
    assert attempt.anchor_type == "none"
    assert attempt.started_utc == attempt.ended_utc
    assert attempt.timed_at == "xcam"
    assert attempt.closed_by == IMPORT_EVENT
    assert not attempt.cleared


def test_the_row_is_rebuilt_from_the_journal_on_replay(tmp_path):
    """Journaled, not inserted: the next reproject must find it again, or
    the first clear/restore anywhere in the app would make it vanish."""
    db, svc = make(tmp_path)
    asyncio.run(svc.import_times("manual", [candidate()]))
    (before,) = imported_attempts(db)
    asyncio.run(svc._reproject())
    (after,) = imported_attempts(db)
    assert after == before
    assert db.current_pb(1, 0, "igt", strat_tag="Standard")["attempt_id"] \
        == after.id


def test_an_import_touches_nothing_the_game_is_doing(tmp_path):
    """Nothing happened in the game: the run he has open stays open, and the
    import is not a grab the last-star guards can see."""
    db, svc = make(tmp_path)
    t0 = datetime(2026, 8, 22, tzinfo=timezone.utc)
    asyncio.run(svc.publish(Event(type="practice_reset", frame=1000,
                                  timestamp_utc=t0,
                                  payload={"igt_frames_before": 0})))
    assert svc._projector._open is not None
    asyncio.run(svc.import_times("manual", [candidate()]))
    assert svc._projector._open is not None, "the import closed his open run"
    assert svc._projector._last_star_grabbed is None


def test_clearing_the_row_erases_its_pb(tmp_path):
    """The 'deleting' he asked for is the row's own clear, and it takes the
    PB with it through the door `clear_attempt` already had."""
    db, svc = make(tmp_path)
    asyncio.run(svc.import_times("manual", [candidate()]))
    (attempt,) = imported_attempts(db)
    asyncio.run(svc.clear_attempt(attempt.id, reason="accidental"))
    assert db.current_pb(1, 0, "igt", strat_tag="Standard") is None
    (row,) = imported_attempts(db)
    assert row.cleared and row.cleared_reason == "accidental"


def test_a_slower_import_never_replaces_a_faster_one(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.import_times("manual", [candidate(886)]))
    asyncio.run(svc.import_times("manual", [candidate(1200)]))
    assert db.current_pb(1, 0, "igt", strat_tag="Standard")["frames"] == 266


def test_removing_a_source_erases_its_rows_and_only_its_rows(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.import_times("manual", [candidate()]))
    asyncio.run(svc.import_times("sheet:DentoriousRed",
                                 [candidate(700, key="star:2:0")]))
    assert asyncio.run(svc.remove_imported("manual")) == 1
    assert db.current_pb(1, 0, "igt", strat_tag="Standard") is None
    assert db.current_pb(2, 0, "igt", strat_tag="Standard") is not None
    assert [a.course_id for a in imported_attempts(db)] == [2]
    assert not [e for e in db.events() if e.type == IMPORT_EVENT
                and e.payload["source"] == "manual"]


def test_removing_an_import_restores_what_it_superseded(tmp_path):
    """Latest-row-wins is the pbs contract, so erasing the imported row makes
    the previous save current again -- the same restoration undo_pb relies on."""
    db, svc = make(tmp_path)
    db.insert_pb(course_id=1, star_id=0, strat_tag="Standard",
                 timer_mode="igt", frames=400, attempt_id=None,
                 saved_utc="2026-08-19T00:00:00Z")
    asyncio.run(svc.import_times("manual", [candidate(886)]))
    assert db.current_pb(1, 0, "igt", strat_tag="Standard")["frames"] == 266
    asyncio.run(svc.remove_imported("manual"))
    assert db.current_pb(1, 0, "igt", strat_tag="Standard")["frames"] == 400


def test_a_segment_of_yours_lands_on_the_rta_clock(tmp_path):
    """A segment named in his own sheet is a SEGMENT time, and an id resolved by name
    against this database means exactly what it says — which is the whole
    difference from the sheet's segment rows."""
    db, svc = make(tmp_path)
    mine = db.segment_defs()[0]["id"]
    summary = asyncio.run(svc.import_times("link", [ImportCandidate(
        entity_key=f"segment:{mine}", strat_tag="Standard", time_cs=1200,
        timer_mode="rta")]))
    assert summary["imported"] == 1
    row = db.current_pb(None, None, "rta", segment_id=mine,
                        strat_tag="Standard")
    assert row["frames"] == 360
    assert row["imported_from"] == "link"
    (attempt,) = imported_attempts(db)
    assert row["attempt_id"] == attempt.id
    assert attempt.segment_id == mine
    assert attempt.rta_frames == 360 and attempt.igt_frames is None
    assert attempt.timed_at is None       # a segment has no x-cam to be legal about


def test_a_segment_on_the_igt_clock_is_refused(tmp_path):
    """Segments have no IGT clock — the same rule save_pb enforces. Quietly
    re-clocking one would file a number against a measurement it is not."""
    db, svc = make(tmp_path)
    mine = db.segment_defs()[0]["id"]
    with pytest.raises(ValueError, match="no IGT clock"):
        asyncio.run(svc.import_times("manual", [candidate(key=f"segment:{mine}")]))


def test_a_segment_id_that_is_not_yours_is_refused(tmp_path):
    """A FOREIGN id is worse than a missing one: it may well exist here and
    name a different movement, so the time would land on the wrong thing."""
    db, svc = make(tmp_path)
    stranger = max(row["id"] for row in db.segment_defs()) + 500
    with pytest.raises(ValueError, match="not one of your segments"):
        asyncio.run(svc.import_times("sheet:someone", [ImportCandidate(
            entity_key=f"segment:{stranger}", strat_tag="Standard",
            time_cs=1200, timer_mode="rta")]))


def test_anything_that_is_neither_is_refused(tmp_path):
    _, svc = make(tmp_path)
    with pytest.raises(ValueError, match="only stars and your own segments"):
        asyncio.run(svc.import_times("manual", [candidate(key="area:6:1")]))


# -- round 4 (2026-08-24): an import fills an EMPTY hand ---------------------
# "After importing and successfully categorizing an entry, we should also
# automatically select the fastest strategy for each star / segment that
# we've successfully completed... If there are multiple entries for a given
# star/segment using different strategies, whichever's fastest becomes
# selected." An entity that already HAS an active strategy keeps it —
# explicit user choices take priority (his standing ruling, 2026-08-08).

from sm64_events.tracking.activestrat import ActiveStrats


def _active(db, svc):
    return ActiveStrats.from_db(db, svc.strat_by_star, svc.strat_by_segment)


def _default_less_segment(db):
    return next(d["id"] for d in db.segment_defs() if not d["default_strat"])


def test_an_import_selects_the_fastest_strategy_for_an_empty_hand(tmp_path):
    db, svc = make(tmp_path)
    seg = _default_less_segment(db)
    asyncio.run(svc.import_times("sheet:someone", [
        ImportCandidate(entity_key=f"segment:{seg}", strat_tag="Normal File",
                        time_cs=4200, timer_mode="rta"),
        ImportCandidate(entity_key=f"segment:{seg}", strat_tag="No 120",
                        time_cs=3836, timer_mode="rta")]))
    assert _active(db, svc).for_segment(seg) == "No 120"


def test_an_import_never_displaces_an_explicit_pick(tmp_path):
    db, svc = make(tmp_path)
    seg = _default_less_segment(db)
    asyncio.run(svc.set_strat_segment(seg, "My Pick"))
    asyncio.run(svc.import_times("sheet:someone", [ImportCandidate(
        entity_key=f"segment:{seg}", strat_tag="No 120",
        time_cs=3836, timer_mode="rta")]))
    assert _active(db, svc).for_segment(seg) == "My Pick"


def test_a_star_gets_the_same_fill_as_a_segment(tmp_path):
    """Rule 11 parity: the star side goes through the same fill."""
    db, svc = make(tmp_path)
    asyncio.run(svc.import_times("manual", [
        candidate(cs=2000, strat="Slow Way"),
        candidate(cs=1500, strat="Fast Way")]))
    assert _active(db, svc).for_star(1, 0) == "Fast Way"


def test_the_fill_survives_replay(tmp_path):
    """The selection is a journaled strat_set, so a reproject keeps it."""
    db, svc = make(tmp_path)
    asyncio.run(svc.import_times("manual", [candidate(strat="Fast Way")]))
    asyncio.run(svc._reproject())
    assert _active(db, svc).for_star(1, 0) == "Fast Way"


def test_clear_all_practice_data_wipes_the_filled_selection(tmp_path):
    """Round 4 item 3: "if I clear all practice data, naturally, all of
    these strategy selections should also be wiped out." The lifetime
    kind=all wipe hard-deletes the journal, strat_set rows included."""
    db, svc = make(tmp_path)
    seg = _default_less_segment(db)
    asyncio.run(svc.import_times("sheet:someone", [ImportCandidate(
        entity_key=f"segment:{seg}", strat_tag="No 120",
        time_cs=3836, timer_mode="rta")]))
    assert _active(db, svc).for_segment(seg) == "No 120"
    asyncio.run(svc.wipe_data("all", scope="lifetime"))
    assert _active(db, svc).for_segment(seg) is None
    assert _active(db, svc).for_star(1, 0) is None


def test_an_import_holds_what_it_could_not_place_and_the_undo_erases_it(tmp_path):
    """Round 28: the cells a door cannot land ride the same command as the
    ones it can, under the same source -- so one button press brings both,
    and one undo takes both. A held cell is not journaled: it is not an
    attempt, and the projector never reads it."""
    db, svc = make(tmp_path)
    held = [{"row_key": "sheet||row||1", "game_version": "jp",
             "time_cs": 1613, "reason": "subsections"}]
    summary = asyncio.run(svc.import_times("sheet:Raisn", [candidate()], held=held))
    assert summary["imported"] == 1
    assert [(c["source"], c["row_key"], c["time_cs"]) for c in db.held_times()] == [
        ("sheet:Raisn", "sheet||row||1", 1613)]
    assert all(e.type != "held" for e in db.events())
    asyncio.run(svc.import_times("manual", [candidate(700, key="star:2:0")]))
    assert asyncio.run(svc.remove_imported("sheet:Raisn")) == 1
    assert db.held_times() == [], "the undo must take the held cells with it"
    assert db.current_pb(2, 0, "igt", strat_tag="Standard") is not None

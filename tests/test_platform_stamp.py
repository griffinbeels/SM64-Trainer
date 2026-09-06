"""The platform stamp (pb-import round 29, item 2): an attempt remembers WHICH
MACHINE set its time, a personal best remembers it through its attempt, and
an absent stamp is resolved by exactly one rule.

The two literals are deliberately never spelled here -- `TrackerMode` is the
one door for them (tests/test_single_source.py), and a test that restated
them would be a third copy.
"""
import asyncio
from datetime import datetime, timezone

from sm64_events.core.events import Event
from sm64_events.core.modes import (DEFAULT_PLATFORM, PLATFORMS, TrackerMode,
                                    platform_from_payload, platform_of)
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.storage.db import Database
from sm64_events.tracking.service import TrackerService

from pb_commands import save_pb

T0 = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
CONSOLE = TrackerMode.N64.value
EMULATOR = TrackerMode.EMU.value


def ev(type_, frame, payload=None):
    return Event(type=type_, frame=frame, timestamp_utc=T0, payload=payload or {})


def xcam_star(frame=1350, igt=343, **extra):
    payload = {"course_id": 2, "star_id": 2, "igt_frames": igt,
               "igt_timed_at": "xcam", **extra}
    return ev("star_collected", frame, payload)


def make(tmp_path):
    db = Database(tmp_path / "t.db")
    svc = TrackerService(db, Broadcaster())
    asyncio.run(svc.start())
    return db, svc


# --- the registry -----------------------------------------------------------

def test_the_platforms_are_the_tracker_modes():
    assert PLATFORMS == tuple(mode.value for mode in TrackerMode)
    assert DEFAULT_PLATFORM == TrackerMode.EMU.value


def test_an_absent_stamp_resolves_to_the_emulator():
    assert platform_of(None) == EMULATOR
    assert platform_of(CONSOLE) == CONSOLE
    assert platform_of(EMULATOR) == EMULATOR
    # A string that is not a platform is not promoted to one by the resolver
    # either: it reads as the default rather than as itself.
    assert platform_of("gamecube") == EMULATOR


def test_only_a_known_platform_is_taken_off_a_payload():
    assert platform_from_payload({}) is None
    assert platform_from_payload({"platform": CONSOLE}) == CONSOLE
    assert platform_from_payload({"platform": "gamecube"}) is None


# --- the attempt ------------------------------------------------------------

def test_the_closing_event_stamps_the_attempt(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(xcam_star(platform=CONSOLE)))
    [attempt] = db.attempts()
    assert attempt.platform == CONSOLE


def test_an_emulator_closure_stores_no_stamp(tmp_path):
    # The emulator path writes no key; None is the STORED value and the
    # resolver, not the row, says what it means.
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(xcam_star()))
    [attempt] = db.attempts()
    assert attempt.platform is None
    assert platform_of(attempt.platform) == EMULATOR


def test_the_stamp_survives_a_reproject(tmp_path):
    # A fresh service over the same journal re-derives every attempt on
    # start(); the stamp must come back from the event, not from the row.
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(xcam_star(platform=CONSOLE)))
    db._conn.execute("UPDATE attempts SET platform = NULL")
    db._conn.commit()
    # The same replay every strategy/filter change runs; a second start()
    # would also run the boot prune, which is not what this test is about.
    asyncio.run(svc._reproject())
    [attempt] = db.attempts()
    assert attempt.platform == CONSOLE


def test_the_column_exists_on_a_fresh_db(tmp_path):
    db = Database(tmp_path / "fresh.db")
    columns = {row[1] for row in db._conn.execute("PRAGMA table_info(attempts)")}
    assert "platform" in columns


# --- the personal best ------------------------------------------------------

def test_a_pb_remembers_its_platform_through_its_attempt(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(xcam_star(platform=CONSOLE)))
    [attempt] = db.attempts()
    save_pb(svc, db, attempt.id, "igt")
    assert db.current_pb(2, 2, "igt")["platform"] == CONSOLE
    [row] = db.pbs()
    assert row["platform"] == CONSOLE
    assert row["attempt_id"] == attempt.id


def test_a_pb_with_no_attempt_reads_no_stamp(tmp_path):
    db, _svc = make(tmp_path)
    db.insert_pb(course_id=2, star_id=2, strat_tag=None, timer_mode="igt",
                 frames=300, attempt_id=None, saved_utc="2026-09-04T00:00:00Z")
    row = db.current_pb(2, 2, "igt")
    assert row["platform"] is None
    assert platform_of(row["platform"]) == EMULATOR


def test_the_strategy_filter_still_selects_by_the_pb_row(tmp_path):
    # The join brought a second strat_tag/course_id/star_id into scope; the
    # filter must keep reading the PB's own, not the attempt's.
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(xcam_star(platform=CONSOLE)))
    [attempt] = db.attempts()
    save_pb(svc, db, attempt.id, "igt")
    db.insert_pb(course_id=2, star_id=2, strat_tag="fast", timer_mode="igt",
                 frames=250, attempt_id=None, saved_utc="2026-09-04T00:00:01Z")
    assert db.current_pb(2, 2, "igt", strat_tag="fast")["frames"] == 250
    assert db.current_pb(2, 2, "igt", strat_tag="fast")["platform"] is None
    assert db.current_pb(2, 2, "igt")["frames"] == 250  # later saves win, any strat

"""The Daily Star: which entry a date deals, and what counts as today's best.

The deal is a promise between installs -- "any user using the app on the same
date should have the same practice suggestion" (task 0111) -- so the dates
below are pinned: a change that reshuffles them breaks that promise for every
user still on the previous version.
"""
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sm64_events.ranks import scopes
from sm64_events.ranks.dailystar import (DECK, EPOCH, FIRST_KEY, course_of,
                                         cycle_order, daily_entry)
from sm64_events.ranks.scorecard import template_rows
from sm64_events.server.dailystar_api import create_dailystar_router, todays_best
from sm64_events.storage.db import Database


def test_day_one_is_his_example():
    entry = daily_entry(EPOCH)
    assert (entry.day, entry.key) == (1, "star:2:2") == (1, FIRST_KEY)
    assert entry.course_name == "Whomp's Fortress"


def test_the_deal_is_pinned_so_installs_agree():
    dealt = [daily_entry(EPOCH + timedelta(days=n)).key for n in (1, 2, 12, 102, 103, 500)]
    assert dealt == ["star:13:1", "star:12:5", "star:7:0",
                     "star:22:0", "star:4:0", "star:7:1"]


def test_every_cycle_deals_the_whole_deck_once():
    for cycle in range(-2, 8):
        assert sorted(cycle_order(cycle)) == sorted(DECK)


def test_no_two_days_in_a_row_share_a_course():
    previous = None
    for offset in range(len(DECK) * 10):
        course = course_of(daily_entry(EPOCH + timedelta(days=offset)).key)
        assert course != previous, f"day {offset + 1} repeats course {course}"
        previous = course


def test_the_deck_is_the_overall_scorecard():
    """A Scorecard change must be a deliberate deck edit, never a silent
    reshuffle: fights are named by seed key in the deck."""
    fights = [(f"segment:{n}", f"Bowser {n}", f"seg:bowser-{n}") for n in (1, 2, 3)]
    card = [key.replace("segment:", "seg:bowser-")
            for row in template_rows(fight_segments=fights)
            for key, _label, _clock in row["entries"]
            if key not in scopes.UNTRACKED_CASTLE_STARS]
    assert list(DECK) == card


def _attempt(ended_utc, frames, **overrides):
    fields = dict(course_id=2, star_id=2, segment_id=None, outcome="success",
                  cleared=False, igt_frames=frames, rta_frames=None,
                  ended_utc=ended_utc, strat_tag="Standard", timed_by="igt")
    return SimpleNamespace(**{**fields, **overrides})


def _utc_at(day, hour, minute=0):
    local = datetime.combine(day, time(hour, minute)).astimezone()
    return local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def test_todays_best_is_the_fastest_finish_inside_the_local_day():
    day = date(2026, 9, 23)
    attempts = [
        _attempt(_utc_at(day - timedelta(days=1), 23, 59), 300),   # yesterday
        _attempt(_utc_at(day, 0, 1), 340),
        _attempt(_utc_at(day, 12), 331),
        _attempt(_utc_at(day, 13), 320, cleared=True),              # invalidated
        _attempt(_utc_at(day, 14), 310, timed_by="imported"),        # typed in
        _attempt(_utc_at(day, 15), None, outcome="reset"),
        _attempt(_utc_at(day, 16), 250, star_id=3),                 # other star
        _attempt(_utc_at(day + timedelta(days=1), 0, 1), 200),      # tomorrow
    ]
    best = todays_best(attempts, "star:2:2", "igt", day)
    assert best == {"frames": 331, "strat": "Standard", "attempts": 4}


def test_no_attempts_today_reads_as_not_played():
    assert todays_best([], "star:2:2", "igt", date(2026, 9, 23)) is None


def test_the_endpoint_answers_and_remembers_the_announcement(tmp_path):
    db = Database(tmp_path / "tracker.db")
    try:
        app = FastAPI()
        app.include_router(create_dailystar_router(SimpleNamespace(db=db)))
        client = TestClient(app)
        body = client.get("/api/daily-star", params={"date": "2026-09-23"}).json()
        assert body["key"] == body["entity_key"] == "star:2:2"
        assert body["label"] == "Shoot into the Wild Blue"
        assert (body["day"], body["clock"], body["today"], body["seen"]) == (1, "igt", None, False)
        client.post("/api/daily-star/seen", json={"date": "2026-09-23"})
        assert client.get("/api/daily-star", params={"date": "2026-09-23"}).json()["seen"]
        assert not client.get("/api/daily-star", params={"date": "2026-09-24"}).json()["seen"]
        assert client.get("/api/daily-star", params={"date": "nope"}).status_code == 400
    finally:
        db.close()

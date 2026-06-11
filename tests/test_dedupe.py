# tests/test_dedupe.py
"""Tests for journal duplicate-detection logic and end-to-end dedupe flow."""
import json
from datetime import datetime, timezone, timedelta

import pytest

from sm64_events.core.events import Event
from sm64_events.storage.db import Database
from sm64_events.storage.dedupe import find_duplicates, GAME_EVENT_TYPES
from sm64_events.tracking.projection import replay

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_BASE = datetime(2026, 6, 11, 10, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def row(id_, type_, frame, wall_offset_seconds, payload_dict=None):
    """Build a raw (id, type, frame, wall_time_utc, payload_json) tuple."""
    t = _BASE + timedelta(seconds=wall_offset_seconds)
    return (id_, type_, frame, _iso(t), json.dumps(payload_dict or {}))


def ev(type_="star_collected", frame=100, payload=None, offset=0.0) -> Event:
    return Event(
        type=type_, frame=frame,
        timestamp_utc=_BASE + timedelta(seconds=offset),
        payload=payload or {},
    )


# ---------------------------------------------------------------------------
# Pure function tests
# ---------------------------------------------------------------------------

def test_same_key_within_window_flagged():
    rows = [
        row(1, "star_collected", 100, 0.0, {"course_id": 1, "star_id": 1}),
        row(2, "star_collected", 100, 0.1, {"course_id": 1, "star_id": 1}),
    ]
    dups = find_duplicates(rows, window_seconds=5.0)
    assert dups == [2]


def test_same_key_outside_window_not_flagged():
    """Same (type, frame, payload) but 10 minutes apart — console power-on reuse."""
    rows = [
        row(1, "practice_reset", 50, 0.0),
        row(2, "practice_reset", 50, 600.0),  # 10 minutes later
    ]
    dups = find_duplicates(rows, window_seconds=5.0)
    assert dups == []


def test_triple_duplicate_both_later_ids_flagged():
    rows = [
        row(1, "game_reset", 200, 0.0),
        row(2, "game_reset", 200, 0.05),
        row(3, "game_reset", 200, 0.10),
    ]
    dups = find_duplicates(rows, window_seconds=5.0)
    assert set(dups) == {2, 3}
    assert 1 not in dups


def test_different_payloads_same_frame_not_flagged():
    rows = [
        row(1, "star_collected", 100, 0.0, {"course_id": 1, "star_id": 1}),
        row(2, "star_collected", 100, 0.1, {"course_id": 1, "star_id": 2}),
    ]
    dups = find_duplicates(rows, window_seconds=5.0)
    assert dups == []


def test_non_game_types_never_flagged():
    """session_started, target_set, attempt_cleared etc. are per-session, not dupes."""
    rows = [
        row(1, "session_started", 0, 0.0),
        row(2, "session_started", 0, 0.05),
        row(3, "target_set", 0, 0.0, {"course_id": 1, "star_id": 1}),
        row(4, "target_set", 0, 0.05, {"course_id": 1, "star_id": 1}),
        row(5, "attempt_cleared", 0, 0.0, {"attempt_id": 10}),
        row(6, "attempt_cleared", 0, 0.05, {"attempt_id": 10}),
    ]
    dups = find_duplicates(rows, window_seconds=5.0)
    assert dups == []


def test_all_game_types_can_be_flagged():
    """Sanity check every GAME_EVENT_TYPE is actually checked."""
    all_rows = []
    for i, t in enumerate(sorted(GAME_EVENT_TYPES)):
        id_base = i * 2 + 1
        all_rows.append(row(id_base,     t, 100 + i, 0.0))
        all_rows.append(row(id_base + 1, t, 100 + i, 0.1))
    dups = find_duplicates(all_rows, window_seconds=5.0)
    assert len(dups) == len(GAME_EVENT_TYPES)  # one dup per type


# ---------------------------------------------------------------------------
# End-to-end integration test
# ---------------------------------------------------------------------------

def _make_db(tmp_path) -> Database:
    return Database(tmp_path / "t.db")


def test_end_to_end_dedupe_halves_attempts_and_cleans_journal(tmp_path):
    """Simulate two concurrent server sessions writing the same game events.

    After deduplication the journal should contain only one copy of each
    game event and the attempts cache should reflect the cleaned journal.
    """
    db = _make_db(tmp_path)

    # Two sessions — simulating two server instances.
    s1 = db.insert_session(_iso(_BASE))
    s2 = db.insert_session(_iso(_BASE + timedelta(milliseconds=50)))

    # Each server records the same sequence: anchor → star grab.
    payload_reset = {}
    payload_star = {"course_id": 1, "star_id": 1}

    # Session 1 — original events.
    db.append_event(s1, seq=1, event=ev("practice_reset", frame=50, payload=payload_reset, offset=0.0))
    db.append_event(s1, seq=2, event=ev("star_collected", frame=150, payload=payload_star, offset=4.0))

    # Session 2 — duplicate events (same frame, same payload, ~100 ms later).
    db.append_event(s2, seq=1, event=ev("practice_reset", frame=50, payload=payload_reset, offset=0.1))
    db.append_event(s2, seq=2, event=ev("star_collected", frame=150, payload=payload_star, offset=4.1))

    # Verify baseline: 4 events, replay produces 2 attempts (one per session).
    assert len(db.events()) == 4
    all_attempts, _ = replay(db.events())
    assert len(all_attempts) == 2

    # Run dedupe.
    raw_rows = [
        (r.id, r.type, r.frame, r.wall_time_utc, json.dumps(r.payload))
        for r in db.events()
    ]
    dup_ids = find_duplicates(raw_rows, window_seconds=5.0)
    assert len(dup_ids) == 2  # one dup for each event type

    db.delete_events(dup_ids)

    # Journal should now have 2 events.
    remaining = db.events()
    assert len(remaining) == 2

    # Re-project and replace.
    clean_attempts, _ = replay(remaining)
    db.replace_attempts(clean_attempts)

    # Exactly 1 attempt after deduplication.
    final_attempts = db.attempts()
    assert len(final_attempts) == 1
    assert final_attempts[0].outcome == "success"
    assert final_attempts[0].course_id == 1
    assert final_attempts[0].star_id == 1

from dataclasses import dataclass

import pytest

from sm64_events.inputs.frame import InputFrame
from sm64_events.inputs.store import InputStore
from sm64_events.inputs.track import (document_for_attempt, target_of,
                                      track_for_attempt)
from sm64_events.storage.db import Database

AT = "2026-08-20T21:00:00+00:00"
LATER = "2026-08-20T21:00:10+00:00"


@dataclass
class FakeAttempt:
    id: int = 7
    started_utc: str = AT
    ended_utc: str = LATER
    anchor_frame: int | None = None
    rta_frames: int | None = None
    segment_id: int | None = None
    course_id: int | None = None
    star_id: int | None = None
    strat_tag: str | None = None


@pytest.fixture
def store(tmp_path):
    db = Database(tmp_path / "t.db")
    inputs = db.inputs
    inputs.append(db.insert_session(AT),
                  [(number, InputFrame(0x8000, 0, 0, 0))
                   for number in range(100, 110)], AT, LATER)
    return inputs


def test_an_anchor_frame_trims_what_came_before_it(store):
    got = track_for_attempt(store, FakeAttempt(anchor_frame=105))
    assert [number for number, _f in got] == [105, 106, 107, 108, 109]


def test_the_closing_frame_trims_what_the_chunk_held_after_it(store):
    """A chunk is ten seconds of capture that OVERLAPS the attempt's span,
    so it carries frames past the closing event. His first live attempt
    (2026-08-22) read 598 frames for a 444-frame run: the extra 154 were
    the next five seconds of the chunk the star landed in."""
    got = track_for_attempt(store, FakeAttempt(anchor_frame=102, rta_frames=3))
    assert [number for number, _f in got] == [102, 103, 104, 105]


def test_no_anchor_frame_keeps_everything_the_span_covers(store):
    got = track_for_attempt(store, FakeAttempt())
    assert len(got) == 10


def test_an_attempt_whose_span_has_no_chunks_resolves_to_nothing(store):
    got = track_for_attempt(store, FakeAttempt(
        started_utc="2026-08-19T00:00:00+00:00",
        ended_utc="2026-08-19T00:00:10+00:00"))
    assert got == []


def test_a_star_attempt_and_a_segment_attempt_both_name_a_target():
    """Star-segment parity: an input document exists for either kind."""
    assert target_of(FakeAttempt(course_id=24, star_id=1)) == "star 24 1"
    assert target_of(FakeAttempt(segment_id=12)) == "segment 12"


def test_an_attempt_with_no_declared_target_still_makes_a_document(store):
    """A failure with no target yet still recorded real input, and looking at
    it is how you find out what you were doing."""
    text = document_for_attempt(store, FakeAttempt())
    assert "target:   unknown" in text
    assert "origin:   attempt 7" in text


def test_the_document_starts_at_frame_zero(store):
    text = document_for_attempt(store, FakeAttempt(anchor_frame=105))
    body = [line for line in text.splitlines() if line and line[0].isdigit()]
    assert body[0].startswith("0")

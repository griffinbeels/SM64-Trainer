from dataclasses import dataclass

import pytest

from sm64_events.inputs.frame import InputFrame
from sm64_events.inputs.track import document_for_attempt, target_of, track_for_attempt
from sm64_events.memory import addresses as A
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
    igt_frames: int | None = None
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


def test_the_track_runs_THROUGH_the_first_star_grab_frame(tmp_path):
    """His rule 2026-08-22: "It should stop only AFTER mario enters the star
    grab. Once mario is in star grab, none of the players inputs matter, so
    that's where it should stop." The closing event's frame can sit a frame
    before the grab action; the track ends on the grab frame itself."""
    db = Database(tmp_path / "t.db")
    rollout = InputFrame(0xC000, 0, -45, 75, 0x010008A6)
    grab = InputFrame(0xC000, 0, -45, 75, A.ACT_FALL_AFTER_STAR_GRAB)
    dance = InputFrame(0, 0, 0, 0, A.ACT_STAR_DANCE_EXIT)
    db.inputs.append(db.insert_session(AT),
                     [(100 + step, rollout) for step in range(10)]
                     + [(110, grab), (111, dance), (112, dance), (113, dance)],
                     AT, LATER)
    got = track_for_attempt(db.inputs,
                            FakeAttempt(anchor_frame=100, rta_frames=9))
    assert [number for number, _f in got][-2:] == [109, 110]
    assert got[-1][1].action == A.ACT_FALL_AFTER_STAR_GRAB


def test_an_attempt_with_no_grab_after_its_close_ends_at_the_close(store):
    got = track_for_attempt(store, FakeAttempt(anchor_frame=102, rta_frames=3))
    assert [number for number, _f in got] == [102, 103, 104, 105]


def test_the_track_is_as_long_as_the_attempts_own_time(tmp_path):
    """His report, 2026-08-28: the timeline read 13"50 where the row above
    it read 13"56 -- "It should be IDENTICAL in length. The PB timing
    should exactly match the input display."

    The span used to run from OUR anchor, so its length was our RTA while
    the row showed Usamune's IGT: two clocks that disagree by a frame or
    two on nearly every run (measured over 90 of his successes -- exactly
    one frame off the RTA on 82 of them, -1..+2 off the IGT). Usamune's
    number is the graded one, so the track is cut to it.
    """
    db = Database(tmp_path / "t.db")
    rollout = InputFrame(0xC000, 0, -45, 75, 0x010008A6)
    grab = InputFrame(0xC000, 0, -45, 75, A.ACT_FALL_AFTER_STAR_GRAB)
    db.inputs.append(db.insert_session(AT),
                     [(90 + step, rollout) for step in range(30)]
                     + [(120, grab)], AT, LATER)
    # Usamune counted 22 frames; our own anchor-to-close delta says 20.
    got = track_for_attempt(db.inputs, FakeAttempt(
        anchor_frame=100, rta_frames=20, igt_frames=22))
    numbers = [number for number, _f in got]
    assert numbers[-1] == 120
    assert got[-1][1].action == A.ACT_FALL_AFTER_STAR_GRAB
    assert numbers[-1] - numbers[0] + 1 == 22, (
        "the track must be exactly as long as the time on the row")


def test_without_a_usamune_time_the_track_still_runs_from_the_anchor(store):
    """A reset has no IGT of its own -- nothing to match, so the older rule
    stands and the track starts where we saw the attempt begin."""
    got = track_for_attempt(store, FakeAttempt(anchor_frame=102, rta_frames=3))
    assert [number for number, _f in got] == [102, 103, 104, 105]


def test_a_neighbouring_chunk_is_reachable_when_the_attempt_needs_it(tmp_path):
    """A chunk is up to ten seconds of capture, so an attempt's own first or
    last frames routinely sit in a chunk whose wall-clock window does not
    overlap the attempt at all. His attempt 516 wanted one frame earlier
    than the query returned and 2317 wanted 35 later; both existed."""
    db = Database(tmp_path / "t.db")
    session = db.insert_session(AT)
    before = "2026-08-20T20:59:50+00:00"
    db.inputs.append(session, [(90 + n, InputFrame(0, 0, 0, 0))
                               for n in range(10)], before, before)
    db.inputs.append(session, [(100 + n, InputFrame(0, 0, 0, 0))
                               for n in range(10)], AT, LATER)
    after = "2026-08-20T21:00:20+00:00"
    db.inputs.append(session, [(110 + n, InputFrame(0, 0, 0, 0))
                               for n in range(10)], after, after)
    got = track_for_attempt(db.inputs, FakeAttempt(
        anchor_frame=100, rta_frames=14, igt_frames=20))
    numbers = [number for number, _f in got]
    assert numbers[0] == 95 and numbers[-1] == 114, (
        "the trim must be able to reach into the chunks on either side")
    assert len(numbers) == 20


def test_a_console_reset_inside_the_widened_window_is_not_crossed(tmp_path):
    """The counter restarts on a console reset, so numbers repeat within a
    session -- the widened search must never pull a frame from the other
    side of one just because its number lands in range."""
    db = Database(tmp_path / "t.db")
    session = db.insert_session(AT)
    before = "2026-08-20T20:59:50+00:00"
    db.inputs.append(session, [(1000 + n, InputFrame(0, 0, 0, 0))
                               for n in range(10)], before, before)
    db.inputs.append(session, [(100 + n, InputFrame(0, 0, 0, 0))
                               for n in range(20)], AT, LATER)
    got = track_for_attempt(db.inputs, FakeAttempt(
        anchor_frame=100, rta_frames=14, igt_frames=20))
    numbers = [number for number, _f in got]
    assert min(numbers) >= 100, "a pre-reset frame must never enter the track"


def test_the_end_is_the_DANCE_S_OWN_FIRST_FRAME_not_the_close(monkeypatch):
    """Round 32 item 58. The end anchors everything -- the start is derived
    from it as `last - (igt - 1)` -- and the dance routinely begins BEFORE
    our own close, since our clock and Usamune's disagree and the anchor
    can land late. Measured on four of his attempts: 7, 8 and 25 frames
    early, and each pushed the run's frame 0 that far late. At 25 he could
    see it: the fall after his reset read as "before the reset" while
    Usamune's timer, paused mid-fall, already said 0'00"20."""
    from sm64_events.inputs.track import _dance_start
    from sm64_events.memory import addresses as A

    dance = A.ACT_STAR_DANCE_EXIT
    def rows(spans, fall=0):
        # `fall` leading frames of each span are the midair fall that
        # precedes a dance (ACT_FALL_AFTER_STAR_GRAB); the rest dance.
        out = []
        for number in range(1000, 1400):
            action = 0
            for lo, hi in spans:
                if lo <= number <= hi:
                    action = A.ACT_FALL_AFTER_STAR_GRAB if number < lo + fall else dance
            out.append((number, InputFrame(0, 0, 0, 0, action)))
        return out

    # The dance CONTAINS the close: its own first frame wins, not the close.
    assert _dance_start(rows([(1100, 1300)]), 1125) == 1100
    # A dance starting just after the close still answers.
    assert _dance_start(rows([(1150, 1300)]), 1125) == 1150
    # A PREVIOUS star's dance, far behind, may never be chosen.
    assert _dance_start(rows([(1000, 1010), (1200, 1300)]), 1190) == 1200
    assert _dance_start(rows([(1000, 1010)]), 1300) is None
    # No dance at all (a reset, an abandon): nothing to anchor on.
    assert _dance_start(rows([]), 1234) is None
    # THE TIMER RUNS THROUGH THE FALL (2026-09-01, attempt 5534): a midair
    # grab falls five frames before the dance, and Usamune counts them --
    # his 576 frames reached from the spawn frame exactly to the last fall
    # frame. So the dance is the first DANCE action, not the first grab one.
    assert _dance_start(rows([(1100, 1300)], fall=5), 1104) == 1105
    # A run the capture cut short before the dance began ends where the
    # capture did.
    assert _dance_start(rows([(1100, 1104)], fall=5), 1102) == 1105

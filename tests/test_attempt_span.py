"""The attempt scene's span rule (tools/scene_lab/attempt_span.py) on synthetic task lists.

Griffin, 2026-10-26: "The ghost scene should start when the attempt starts; it should end during
fade out." A task list here is what the live spool reports: index, the game frame each task
showed, its level and area, and when.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import attempt_span as span_rule  # noqa: E402
from scene_spool_double import TaskRef  # noqa: E402

T0 = datetime(2026, 10, 26, 20, 16, tzinfo=timezone.utc)
RECORDING = {"recording": True, "reason": None, "holes": []}
STOPPED = {"recording": False, "reason": "Project64 closed the game.", "holes": []}


def at(index: int) -> datetime:
    return T0 + timedelta(seconds=index / 30)


def tasks(count: int, *, start: int = 0, timer=lambda index: 1000 + index, place=lambda index: (9, 1)):
    return [TaskRef(index, timer(index), *place(index), at(index), True) for index in range(start, count)]


def mark(first: int, closed: int, outcome: str = "success", *, timer=lambda index: 1000 + index, attempt_id=5):
    return span_rule.AttemptMark(attempt_id, timer(first), timer(closed), at(first), at(closed), outcome)


def test_a_star_grab_runs_through_the_fade_to_the_last_picture_before_the_next_place():
    """His BoB minute: the grab, the dance and the fade stay in BoB; castle inside appears next."""
    recorded = tasks(400, place=lambda index: (9, 1) if index < 300 else (6, 1))
    found = span_rule.span(mark(20, 150), recorded, [], RECORDING)
    assert isinstance(found, span_rule.Cut)
    assert (found.first.index, found.end.index, found.last.index, found.why) == (20, 150, 299, "fade")
    assert found.seconds == pytest.approx(9.3, abs=0.05)


def test_an_area_change_ends_it_too_and_so_does_a_jump_of_the_game_clock():
    recorded = tasks(300, place=lambda index: (9, 1) if index < 200 else (9, 2))
    assert span_rule.span(mark(10, 120), recorded, [], RECORDING).last.index == 199
    jumped = tasks(300, timer=lambda index: 1000 + index if index < 180 else 50 + index)
    found = span_rule.span(mark(10, 120), jumped, [], RECORDING)
    assert (found.last.index, found.why) == (179, "clock")


def test_until_the_fade_ends_it_waits_and_names_how_far_the_span_already_reaches():
    recorded = tasks(200)
    found = span_rule.span(mark(20, 150), recorded, [], RECORDING)
    assert isinstance(found, span_rule.Waiting) and found.first.index == 20 and found.through.index == 199
    assert "fade-out" in found.reason


def test_nothing_after_the_end_stops_it_at_the_cap():
    recorded = tasks(150 + span_rule.CAP_FRAMES + 5)
    found = span_rule.span(mark(20, 150), recorded, [], RECORDING)
    assert (found.last.index, found.why) == (150 + span_rule.CAP_FRAMES, "cap")


@pytest.mark.parametrize("outcome", ["reset", "loaded", "hard_reset"])
def test_a_reset_ends_it_on_the_picture_before_the_reset(outcome):
    found = span_rule.span(mark(20, 90, outcome), tasks(400), [], RECORDING)
    assert (found.last.index, found.why) == (89, "next start")


def test_the_next_attempts_anchor_ends_it_before_the_fade():
    recorded = tasks(400, place=lambda index: (9, 1) if index < 300 else (6, 1))
    reset = span_rule.Boundary(1000 + 180, at(180))
    found = span_rule.span(mark(20, 150), recorded, [reset], RECORDING)
    assert (found.last.index, found.why) == (179, "next attempt")
    own = span_rule.Boundary(1000 + 20, at(20))                 # its own anchor never ends it
    assert span_rule.span(mark(20, 150), recorded, [own], RECORDING).last.index == 299


def test_a_next_anchor_not_yet_recorded_holds_the_decision():
    recorded = tasks(160, place=lambda index: (9, 1))
    reset = span_rule.Boundary(1000 + 170, at(170))
    found = span_rule.span(mark(20, 150), recorded, [reset], RECORDING)
    assert isinstance(found, span_rule.Waiting) and "next attempt" in found.reason


def test_a_repeated_game_frame_is_found_at_the_occurrence_nearest_the_attempts_time():
    """A save state restores gGlobalTimer: frame 1100 is shown twice, 9 s apart."""
    recorded = tasks(600, timer=lambda index: 1000 + index if index < 300 else 800 + index)
    found = span_rule.span(span_rule.AttemptMark(5, 1100, 1180, at(300), at(380), "reset"), recorded, [], RECORDING)
    assert (found.first.index, found.end.index, found.last.index) == (300, 380, 379)
    early = span_rule.span(span_rule.AttemptMark(5, 1100, 1180, at(100), at(180), "reset"), recorded, [], RECORDING)
    assert (early.first.index, early.last.index) == (100, 179)


def test_a_hole_inside_the_span_makes_it_unavailable_with_how_many_pictures():
    recorded = [task for task in tasks(400) if not 50 <= task.index <= 52]
    found = span_rule.span(mark(20, 90, "reset"), recorded, [], {**RECORDING, "holes": [[50, 52]]})
    assert isinstance(found, span_rule.Unavailable) and found.reason == \
        "The scene recording lost 3 pictures during this attempt."
    gap = [task for task in tasks(400) if task.index != 60]
    assert "lost 1 picture during" in span_rule.span(mark(20, 90, "reset"), gap, [], RECORDING).reason
    assert isinstance(span_rule.span(mark(20, 40, "reset"), recorded, [], RECORDING), span_rule.Cut)


def test_what_cannot_have_a_scene_says_why():
    recorded = tasks(400, start=200)                             # the recording began 6 s after the attempt
    no_anchor = span_rule.AttemptMark(5, None, 200, at(210), at(250), "success")
    assert span_rule.span(no_anchor, recorded, [], RECORDING).reason == span_rule.NO_ANCHOR
    assert span_rule.span(mark(20, 150), recorded, [], RECORDING).reason == span_rule.BEFORE_RECORDING
    assert span_rule.span(mark(150, 390), tasks(300), [], STOPPED).reason == span_rule.NO_END
    assert span_rule.span(mark(150, 160), [], [], STOPPED).reason.startswith(span_rule.NOT_RECORDING)
    missing = [task for task in tasks(400) if task.index != 150]
    assert span_rule.span(mark(150, 200), missing, [], RECORDING).reason == span_rule.NO_START


def test_a_recording_that_stopped_after_the_end_ends_the_scene_there():
    found = span_rule.span(mark(20, 150), tasks(200), [], STOPPED)
    assert (found.last.index, found.why) == (199, "recording")


def test_a_start_or_end_not_yet_flushed_waits():
    assert isinstance(span_rule.span(mark(150, 160), tasks(140), [], RECORDING), span_rule.Waiting)
    found = span_rule.span(mark(20, 160), tasks(140), [], RECORDING)
    assert isinstance(found, span_rule.Waiting) and found.first.index == 20 and found.through is None


def test_a_scene_longer_than_the_limit_is_refused():
    long = span_rule.MAX_SCENE_FRAMES + 10
    found = span_rule.span(mark(0, long, "reset"), tasks(long + 5), [], RECORDING)
    assert isinstance(found, span_rule.Unavailable) and "10 minutes" in found.reason


def test_an_attempt_mark_survives_the_pipe():
    original = mark(20, 150)
    assert span_rule.AttemptMark.from_json(original.as_json()) == original
    attempt = type("Attempt", (), {"id": 9, "anchor_frame": 5, "closed_frame": 9, "outcome": "death",
                                   "started_utc": "2026-10-26T20:16:00Z", "ended_utc": "2026-10-26T20:16:01.5Z"})
    assert span_rule.AttemptMark.of(attempt).ended_utc == datetime(2026, 10, 26, 20, 16, 1, 500000, tzinfo=timezone.utc)

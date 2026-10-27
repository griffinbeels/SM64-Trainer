"""The attempt scene's span rule (tools/scene_lab/attempt_span.py) on synthetic task lists.

Griffin, 2026-10-26: "The ghost scene should start when the attempt starts; it should end during
fade out." A task list here is what the live spool reports: index, the game frame each task
showed, its level and area, and when.
"""
import struct
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import attempt_span as span_rule  # noqa: E402
from scene_spool_double import TaskRef  # noqa: E402

from sm64_events.memory.addresses import (  # noqa: E402
    ACT_FALL_AFTER_STAR_GRAB, ACT_STAR_DANCE_EXIT, ACT_STAR_DANCE_NO_EXIT)

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
    unread = [TaskRef(*vars(task).values()) if task.index != 70 else TaskRef(70, 1070, 9, 1, at(70), True, True)
              for task in tasks(400)]                               # the tap could not read task 70's list
    assert "lost 1 picture during" in span_rule.span(mark(20, 90, "reset"), unread, [], RECORDING).reason


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


# ---------------------------------------------------------------- the star dance (round 5)

RUNNING, IDLE = 0x04000440, 0x0C400201          # ACT_WALKING, ACT_IDLE (n64decomp sm64.h)
FALL, EXIT_DANCE, NO_EXIT_DANCE = ACT_FALL_AFTER_STAR_GRAB, ACT_STAR_DANCE_EXIT, ACT_STAR_DANCE_NO_EXIT
WARP_STOP = 111                                 # the exit warp's last iris picture: the game stops Mario


def marios(count: int, *, grab: int, dance: int | None = None, pause: int | None = WARP_STOP,
           leave: int | None = None, action: int = EXIT_DANCE, timer=lambda index: 1000 + index) -> dict:
    """Mario in tasks 0..count-1: running until `grab`, falling (a midair grab) until `dance`,
    then dancing with actionTimer counting from 0 on the dance's first frame, until the game
    stops at actionTimer `pause` (the exit warp at 111, his pause, a no-exit dance's dialog;
    None: it never does); from task `leave` on he idles."""
    dance = grab if dance is None else dance
    rows = {}
    for index in range(count):
        if index < grab:
            rows[index] = span_rule.Mario(timer(index), RUNNING, 0)
        elif index < dance:
            rows[index] = span_rule.Mario(timer(index), FALL, 0)
        elif leave is not None and index >= leave:
            rows[index] = span_rule.Mario(timer(index), IDLE, 0)
        else:
            counted = index - dance if pause is None else min(index - dance, pause)
            rows[index] = span_rule.Mario(timer(index), action, counted)
    return rows


def followed(rows: dict):
    return lambda first: rows


def test_a_star_ends_on_its_last_dance_frame_before_his_pause_not_at_the_cap():
    """His HMC star 553: no load after the grab, so the old rule ran to the cap; he paused at
    actionTimer 92, inside the exit fade, and the scene ends on that frame (his "~622")."""
    recorded = tasks(150 + span_rule.CAP_FRAMES + 5)
    states = marios(len(recorded), grab=150, pause=92)
    found = span_rule.span(mark(20, 150), recorded, [], RECORDING, followed(states))
    assert (found.end.index, found.last.index, found.why) == (150, 242, "dance")
    assert span_rule.span(mark(20, 150), recorded, [], RECORDING).why == "cap"        # without the states


def test_the_exit_fade_plays_to_its_last_iris_picture_before_the_level_change():
    """Unpaused, the fade that begins at actionTimer 80 runs on: the game stops Mario at 111 (his
    BoB minute) and frames of the frozen black iris follow before the castle loads."""
    loads = tasks(400, place=lambda index: (9, 1) if index < 264 else (6, 1))
    states = marios(400, grab=150, leave=264)
    found = span_rule.span(mark(20, 150), loads, [], RECORDING, followed(states))
    assert (found.last.index, found.why) == (150 + WARP_STOP, "dance")
    no_stop = marios(400, grab=150, pause=None, leave=264)                     # a dance the game never stopped
    assert span_rule.span(mark(20, 150), loads, [], RECORDING, followed(no_stop)).last.index == 263


def test_a_midair_grab_falls_then_dances_and_a_close_a_frame_early_still_counts():
    recorded = tasks(400)
    falls = marios(400, grab=150, dance=171)
    assert span_rule.span(mark(20, 150), recorded, [], RECORDING, followed(falls)).last.index == 171 + WARP_STOP
    late = marios(400, grab=150 + span_rule.DANCE_ENTRY_FRAMES)                       # the dance after the close
    assert span_rule.span(mark(20, 150), recorded, [], RECORDING, followed(late)).last.index == 152 + WARP_STOP


@pytest.mark.parametrize(("pause", "last"), [(40, 190), (80, 230), (92, 242), (1, 151)])
def test_a_pause_in_the_dance_ends_it_on_the_last_frame_that_moved(pause, last):
    """actionTimer counts by one each game frame of a dance; the first frame it holds is a stop."""
    states = marios(400, grab=150, pause=pause)
    found = span_rule.span(mark(20, 150), tasks(400), [], RECORDING, followed(states))
    assert (found.last.index, found.why) == (last, "dance")


def test_a_no_exit_dance_ends_where_the_game_stops_time_for_its_save_dialog():
    states = marios(400, grab=150, pause=80, action=NO_EXIT_DANCE)
    assert span_rule.span(mark(20, 150), tasks(400), [], RECORDING, followed(states)).last.index == 230
    over = marios(400, grab=150, pause=None, leave=200, action=NO_EXIT_DANCE)      # back to idle sooner
    assert span_rule.span(mark(20, 150), tasks(400), [], RECORDING, followed(over)).last.index == 199


def test_a_reset_during_the_dance_ends_it_before_the_next_attempt():
    reset = span_rule.Boundary(1000 + 190, at(190))
    found = span_rule.span(mark(20, 150), tasks(400), [reset], RECORDING, followed(marios(400, grab=150)))
    assert (found.last.index, found.why) == (189, "next attempt")


def test_until_the_take_has_read_the_dance_end_it_waits_even_past_the_cap():
    recorded = tasks(150 + span_rule.CAP_FRAMES + 5)
    states = marios(len(recorded), grab=150)
    early = {index: mario for index, mario in states.items() if index <= 200}
    found = span_rule.span(mark(20, 150), recorded, [], RECORDING, followed(early))
    assert isinstance(found, span_rule.Waiting) and found.reason == span_rule.DANCING
    assert (found.first.index, found.end.index) == (20, 150)
    assert span_rule.span(mark(20, 150), tasks(400), [], RECORDING, followed({})).reason == span_rule.DANCING
    assert span_rule.span(mark(20, 150), recorded, [], RECORDING, followed(states)).last.index == 150 + WARP_STOP


def test_an_end_no_dance_follows_keeps_the_fade_and_the_cap():
    """A death, a segment ending at a door: the round-2 rule, with or without Mario's states."""
    loads = tasks(400, place=lambda index: (9, 1) if index < 300 else (6, 1))
    running = marios(400, grab=400)
    found = span_rule.span(mark(20, 150), loads, [], RECORDING, followed(running))
    assert (found.last.index, found.why) == (299, "fade")
    unknown = dict.fromkeys(range(400))                         # the take's regions never held Mario
    assert span_rule.span(mark(20, 150), loads, [], RECORDING, followed(unknown)).why == "fade"
    assert span_rule.span(mark(20, 150), loads, [], RECORDING, lambda first: None).why == "fade"
    waiting = span_rule.span(mark(20, 150), tasks(200), [], RECORDING, followed(running))
    assert isinstance(waiting, span_rule.Waiting) and "fade-out" in waiting.reason and waiting.end.index == 150


def test_mario_is_read_from_a_tasks_regions_at_the_recordings_layout():
    layout = span_rule.layout_for("us")
    mario = layout.mario_struct & 0x1FFFFFFF
    base = mario & ~0xFF
    image = bytearray(0x200)
    struct.pack_into(">I", image, mario - base + 0x0C, EXIT_DANCE)
    struct.pack_into(">H", image, mario - base + 0x1A, 92)
    swapped = b"".join(image[at:at + 4][::-1] for at in range(0, len(image), 4))   # Project64 keeps words swapped
    regions = span_rule.tap.Regions(7, 0, {}, "task", (), [(base, len(image))], swapped)
    reader = span_rule.MarioReader.of([("global_timer", layout.global_timer & 0x1FFFFFFF, 4)])
    assert reader.read(regions, 4790) == span_rule.Mario(4790, EXIT_DANCE, 92)
    assert reader.read(None, 4790) is None and reader.read(regions, None) is None
    elsewhere = span_rule.tap.Regions(7, 0, {}, "task", (), [(0x330000, len(image))], swapped)
    assert reader.read(elsewhere, 4790) is None
    assert span_rule.MarioReader.of([("global_timer", 0x123456, 4)]) is None

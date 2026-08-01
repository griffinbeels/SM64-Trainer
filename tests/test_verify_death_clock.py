"""The death-clock live gate's pure core (tools/verify_death_clock.py).

The probe itself needs PJ64 and a human, but everything it PRINTS is decided
by these four functions, and a gate that names the wrong two candidates — or
calls a stalled clock healthy — sends the human away with a confident wrong
answer. That is the failure this file exists to prevent, so the cases below
are about what the numbers MEAN, not about wording.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from sm64_events.detectors.igt_clock import IgtClock          # noqa: E402
from verify_death_clock import (PAUSE_FRAMES, candidates,     # noqa: E402
                                counter_tracked_cleanly,
                                death_report, is_paused,
                                pause_prompt)


def running(start_frame, start_counter, n, stall=0):
    """n consecutive (global_timer, igt_overall) samples. The game frame always
    advances; the counter advances with it EXCEPT across the last `stall`
    samples, where it stands still — the pause-menu shape."""
    moving = max(0, n - stall)
    return [(start_frame + i, start_counter + min(i, max(0, moving - 1)))
            for i in range(n)]


def test_the_two_candidates_are_one_frame_apart():
    """The whole question is one frame. If these ever differ by more, the
    probe is asking about something else."""
    raw, ticked = candidates(1077)
    assert raw == '0\'35"90'
    assert ticked == '0\'35"93'
    assert IgtClock.DISPLAY_TICK == 1


def test_the_candidates_straddle_a_display_step():
    """format_igt quantizes 30 frames onto 30 centisecond values, so a
    one-frame difference is always a VISIBLE step — the human can tell the two
    readings apart on screen. A gate whose two options rendered identically
    would be unanswerable."""
    for counter in range(0, 300):
        raw, ticked = candidates(counter)
        assert raw != ticked, counter


def test_a_frozen_counter_over_a_running_game_frame_reads_as_paused():
    assert is_paused(running(9000, 500, PAUSE_FRAMES + 5, stall=PAUSE_FRAMES + 5))


def test_a_running_counter_is_not_a_pause():
    assert not is_paused(running(9000, 500, PAUSE_FRAMES + 5))


def test_a_brief_freeze_is_not_yet_a_pause():
    """Shorter than the streak: a couple of frames of no movement is poll
    noise, not the menu being open."""
    assert not is_paused(running(9000, 500, PAUSE_FRAMES + 5, stall=3))


def test_a_short_all_frozen_stream_is_not_a_pause_yet():
    """The case a distinct-counter check alone cannot catch: a stream only a
    few samples long, every one of them frozen — a freshly attached probe, or
    the samples after a poll gap. Every counter matches because there has not
    been time for one to differ, and calling that a pause would print a prompt
    about a number the human has not looked at yet."""
    for length in range(2, PAUSE_FRAMES):
        assert not is_paused(running(9000, 500, length, stall=length)), length


def test_a_freeze_that_reaches_the_streak_length_is_a_pause():
    """The other side of the boundary, so the guard above cannot be satisfied
    by simply never reporting a pause at all."""
    assert is_paused(running(9000, 500, PAUSE_FRAMES, stall=PAUSE_FRAMES))


def test_a_clean_run_up_to_the_death_is_reported_ok():
    ok, detail = counter_tracked_cleanly(running(9000, 500, 30))
    assert ok
    assert "1:1" in detail


def test_a_stalled_counter_is_reported_as_a_problem_with_the_frame_count():
    ok, detail = counter_tracked_cleanly(running(9000, 500, 30, stall=7))
    assert not ok
    assert "stalled for 7 frame" in detail


def test_a_counter_that_went_backward_is_reported_as_a_load():
    """A level load or reset inside the window means the reading is not this
    death's elapsed time at all — a different answer from 'it stalled'."""
    samples = running(9000, 500, 20) + [(9020, 3), (9021, 4)]
    ok, detail = counter_tracked_cleanly(samples)
    assert not ok
    assert "BACKWARD" in detail


def test_too_few_samples_is_not_silently_called_ok():
    ok, _ = counter_tracked_cleanly([(9000, 500)])
    assert not ok


def test_the_death_report_names_both_readings_and_what_each_implies():
    text = death_report(1, 8, "quicksand", 1077, running(9000, 500, 30))
    assert "Shifting Sand Land" in text and "quicksand" in text
    assert '0\'35"90' in text and '0\'35"93' in text
    assert "1077 frames" in text and "1078 frames" in text


def test_the_death_report_escalates_a_bad_clock_over_the_offset_question():
    text = death_report(2, 8, "quicksand", 1077, running(9000, 500, 30, stall=7))
    assert "PROBLEM" in text
    assert "outranks" in text


def test_the_pause_prompt_offers_exactly_two_labelled_answers():
    text = pause_prompt(1, 1077)
    assert "(a)" in text and "(b)" in text
    assert '0\'35"90' in text and '0\'35"93' in text
    # and says what each one costs, so the human is not just reading digits
    assert "death.py is right as it stands" in text
    assert "death.py should use the shared clock" in text

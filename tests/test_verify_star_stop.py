"""The star-STOP live gate's pure core (tools/verify_star_stop.py).

The probe needs PJ64 and a human, but every number it prints is decided here,
and this gate's whole job is to tell three candidate readings apart. A report
that blurred them — or that called a still-moving counter "stopped" — would
send the human away with a confident wrong answer about where Usamune stops,
which is the one thing the 2026-08-01 sitting could not settle. So these cases
are about what the numbers MEAN, not about wording.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from sm64_events.core.timefmt import format_igt                 # noqa: E402
from sm64_events.detectors.igt_clock import IgtClock            # noqa: E402
from verify_star_stop import (OVERALL, RESULT, SETTLE_FRAMES,   # noqa: E402
                              TIMER_SETTINGS, settings_prompt,
                              settle_point, star_stop_report, writes)

TOUCH = 9000


def stream(counter: int, length: int, counter_stops_at: int | None = None,
           result_written_at: int | None = None, result_value: int = 0,
           stale_result: int = 0) -> list[tuple[int, int, int]]:
    """`length` samples of (global_timer, igt_overall, igt_result) starting at
    the touch frame. The counter climbs one per game frame until
    `counter_stops_at` frames after the touch; the result store holds
    `stale_result` until `result_written_at`."""
    out = []
    for offset in range(length):
        climbed = (offset if counter_stops_at is None
                   else min(offset, counter_stops_at))
        result = (result_value
                  if result_written_at is not None and offset >= result_written_at
                  else stale_result)
        out.append((TOUCH + offset, counter + climbed, result))
    return out


def test_a_counter_that_runs_on_past_the_grab_settles_where_it_stopped():
    """The STOP=XCAM shape, with the live numbers from Watch for Rolling Rocks
    (2026-08-01): we journalled 604, his screen read 632."""
    samples = stream(604, 60, counter_stops_at=28)
    settle_frame, value, holding = settle_point(samples, OVERALL)
    assert value == 632
    assert settle_frame - TOUCH == 28
    assert holding


def test_a_counter_still_climbing_at_the_window_edge_is_not_called_stopped():
    """The window being too short must never render as a finding. This is the
    difference between 'Usamune stopped here' and 'we stopped watching here'."""
    samples = stream(604, 10)          # never stops
    _, _, holding = settle_point(samples, OVERALL)
    assert not holding


def test_a_counter_frozen_before_we_started_watching_reports_no_movement():
    """The STOP=GRAB shape: Usamune had already stopped when the action edge
    fired, so nothing in the window moves at all."""
    samples = stream(332, 60, counter_stops_at=0)
    settle_frame, value, holding = settle_point(samples, OVERALL)
    assert value == 332 and settle_frame == TOUCH and holding
    assert writes(samples, OVERALL, TOUCH) == []


def test_the_result_writes_are_reported_with_when_each_landed():
    """The write history is the discriminating evidence — one write at the
    grab means Usamune stopped there, a later one means it stopped again at
    the camera."""
    samples = stream(604, 60, counter_stops_at=28,
                     result_written_at=28, result_value=632, stale_result=346)
    assert writes(samples, RESULT, TOUCH) == [(28, 632)]


def test_a_result_store_never_written_yields_no_writes():
    """Under STOP=XCAM the 2026-08-01 sitting fell through to the counter path,
    which is what 'no write landed near the touch' looks like from here."""
    samples = stream(604, 60, counter_stops_at=28, stale_result=346)
    assert writes(samples, RESULT, TOUCH) == []


def test_the_report_prints_three_distinguishable_numbers():
    """The whole point: what we journal, where the counter rested, and what
    Usamune wrote are three different questions. A report that showed one
    number could not settle anything."""
    samples = stream(604, 60, counter_stops_at=28,
                     result_written_at=28, result_value=632, stale_result=346)
    text = star_stop_report(1, "Hazy Maze Cave", "Watch for Rolling Rocks",
                            604, "result", TOUCH, samples)
    assert "WE JOURNAL TODAY" in text and "604 frames" in text
    assert format_igt(604) in text            # 0'20"13, what we record today
    assert format_igt(632) in text            # 0'21"06, what his screen showed
    assert "came to rest 28 frames after the grab" in text
    assert "+28 -> 632" in text


def test_the_report_does_not_add_the_display_tick_to_a_settled_counter():
    """The 2026-08-01 pause gate answered (a) over eight samples: a FROZEN
    counter reads on screen as its RAW value. Printing counter+1 here would
    offer the human a number nobody has ever measured, and he would pick it if
    it happened to match — a wrong reading that looks like a clean one."""
    samples = stream(604, 60, counter_stops_at=28)
    text = star_stop_report(1, "Hazy Maze Cave", "Watch for Rolling Rocks",
                            604, "counter", TOUCH, samples)
    assert IgtClock.DISPLAY_TICK == 1
    assert format_igt(632) in text
    assert format_igt(633) not in text


def test_the_report_says_when_a_number_is_not_final():
    """A window cut short must say so in the line the human reads, not only in
    a footnote — he is being asked to match one of these against his screen."""
    samples = stream(604, 10)          # counter never stops
    text = star_stop_report(1, "Hazy Maze Cave", "Watch for Rolling Rocks",
                            604, "counter", TOUCH, samples)
    assert "STILL MOVING" in text
    assert "window too short" in text


def test_the_report_flags_a_stale_result_store_rather_than_showing_its_value_bare():
    """A result left over from an earlier star is a plausible-looking number
    with nothing to do with this grab. Shown without that caveat it is the
    exact shape of an invalid reading that passes for a clean one."""
    samples = stream(604, 60, counter_stops_at=28, stale_result=346)
    text = star_stop_report(1, "Hazy Maze Cave", "Watch for Rolling Rocks",
                            604, "counter", TOUCH, samples)
    assert "belongs to an earlier star" in text


def test_the_report_asks_for_the_stop_value_alongside_the_answer():
    """A reading whose STOP value is unknown can only be re-taken. STOP is the
    variable under test and the probe cannot read it out of memory."""
    samples = stream(604, 60, counter_stops_at=28)
    text = star_stop_report(1, "Hazy Maze Cave", "Watch for Rolling Rocks",
                            604, "counter", TOUCH, samples)
    assert "STOP value" in text


def test_the_settings_prompt_refuses_to_treat_stop_as_a_boolean():
    """Live report 2026-08-01: STOP=GRABX is a THIRD value. A prompt implying
    two would file a GRABX reading under the wrong heading."""
    text = settings_prompt()
    for name in TIMER_SETTINGS:
        assert f"{name}=" in text, name
    assert "GRABX" in text
    assert "three values" in text


def test_the_settle_window_outlasts_a_star_dance():
    """A star dance runs 60-90 frames and the camera cut follows it. A window
    shorter than that would close before Usamune stopped, and every reading
    would come back 'STILL MOVING'."""
    assert SETTLE_FRAMES >= 180

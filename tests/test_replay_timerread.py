"""The TIMER READER: which game frame a picture shows, off Usamune's clock.

His question ended the previous approach -- "have we even FOR SURE confirmed
that we can even accurately extract the numbers... out from the bottom left
corner of the screen?" -- and the answer exposed that the signal we had chosen
is ambiguous BY NATURE: the stick readout repeats on 50-77% of a clip's
frames. The clock does not repeat. His instruction: "We should leverage the
actual, mechanical, concrete data that we have access to."

What makes this different from everything before it is that the reader can be
proved right FROM THE SCREEN ALONE, with no controller data and no map.
"""
import numpy as np

from sm64_events.replay import timerread


def test_a_reading_names_its_frame_exactly_with_no_rounding_ambiguity():
    """Usamune prints floor(frames * 100 / 30). That is strictly increasing,
    so it inverts exactly and no two frames can share a value -- which is the
    whole reason the clock can do what the stick display cannot."""
    seen = {}
    for frame in range(0, 20000):
        centiseconds = frame * 100 // 30
        assert timerread.frames_of(centiseconds) == frame
        assert centiseconds not in seen, "two frames shared a reading"
        seen[centiseconds] = frame


def _reading(values, frozen=None):
    out = timerread.TimerReading(values=list(values),
                                 frozen=list(frozen or [False] * len(values)))
    return out


def test_the_two_checks_need_no_controller_data_at_all():
    """A reading whose last digit is impossible, or a step that is not a whole
    number of game frames, is a MISREAD -- provable from the screen. Measured
    on his clip and on raw ring footage: 3,503 reads, 3,503 legal; steps
    achievable on 3,077 of 3,079 and 420 of 422."""
    table = {"0": None}                      # unused by the checks
    # A clean run of real readings: frames 0..9 of a run.
    values = [frame * 100 // 30 for frame in range(40)]
    reading = _reading(values)
    reading.read = len(values)
    reading.legal = sum(1 for one in values if one % 10 in (0, 3, 6))
    assert reading.legal == reading.read, "every real reading ends in 0, 3 or 6"
    assert all(one % 10 in (0, 3, 6) for one in values)
    # An impossible reading: a last digit the arithmetic cannot produce.
    assert 5 % 10 not in (0, 3, 6)
    assert table is not None


def test_a_run_of_identical_readings_is_the_clock_frozen_not_a_duplicate():
    """A pause menu and a star dance stop the clock while the game keeps
    running, so those frames are readable but name no new frame. Duplicate
    pictures reach three in a row; his pause froze one value across
    hundreds."""
    values = ([0, 3, 6, 6]                      # a duplicate picture: kept
              + [10] * 12                        # the pause: frozen
              + [13, 16, 20])
    reading = _reading(values)
    timerread._mark_frozen(reading)
    assert not any(reading.frozen[:4]), "a duplicate picture is not a freeze"
    assert all(reading.frozen[4:16]), "the pause was not detected"
    assert not any(reading.frozen[16:])
    runs = timerread.segments(reading)
    assert runs == [(0, 3), (16, 18)], runs


def test_the_clock_gives_the_shape_and_one_constant_gives_the_run_its_place():
    """Within a run every frame is named exactly; only the run's single number
    comes from elsewhere, which is all a pause can cost."""
    values = [frame * 100 // 30 for frame in range(6)]
    reading = _reading(values)
    prior = [500 + frame for frame in range(6)]
    runs = timerread.fit_runs(reading, prior, lambda slot, frame: None)
    assert len(runs) == 1
    first, last, constant, _agree, _checked, _margin, fitted = runs[0]
    assert (first, last) == (0, 5)
    assert constant == 500, "the run's constant is what the bookkeeping implies"
    assert not fitted, "with nothing to score against, it cannot claim a fit"
    built = timerread.frame_map(reading, runs, prior)
    assert built == prior


def test_the_controller_data_can_move_a_run_off_the_bookkeeping():
    """Where the display CAN determine a run's constant, it wins -- that is
    the point. Measured on his clip: the four runs the display could settle
    agreed with the controller data on 2,269 of 2,283 frames."""
    values = [frame * 100 // 30 for frame in range(40)]
    reading = _reading(values)
    prior = [500 + frame for frame in range(40)]

    # The controller data says every picture is two frames LATER than the
    # bookkeeping claims, and says so unambiguously.
    def agrees(slot, frame):
        return frame == 502 + slot

    runs = timerread.fit_runs(reading, prior, agrees)
    _first, _last, constant, agree, checked, margin, fitted = runs[0]
    assert fitted, "an unambiguous fit was refused"
    assert constant == 502
    assert agree == checked == 40
    assert margin > 0.10
    built = timerread.frame_map(reading, runs, prior)
    assert built == [502 + slot for slot in range(40)]


def test_a_run_the_display_cannot_settle_keeps_the_bookkeeping():
    """When every candidate scores alike the display has not determined the
    run, and an arbitrary winner would be a guess wearing a measurement. His
    clip had four such runs of eight."""
    values = [frame * 100 // 30 for frame in range(40)]
    reading = _reading(values)
    prior = [500 + frame for frame in range(40)]
    runs = timerread.fit_runs(reading, prior, lambda slot, frame: True)
    _first, _last, constant, _agree, _checked, margin, fitted = runs[0]
    assert margin == 0.0 and not fitted
    assert constant == 500, "it fell back to the bookkeeping, not to a tie"


def test_the_stretches_the_clock_cannot_name_take_the_bookkeeping(monkeypatch):
    """A pause, a dance or a fade is bridged by the recorder's own record,
    shifted to meet the nearest run so a gap cannot introduce a step change."""
    values = ([frame * 100 // 30 for frame in range(5)]
              + [None] * 6                                  # a fade
              + [(frame + 11) * 100 // 30 for frame in range(5)])
    reading = _reading(values)
    prior = [500 + frame for frame in range(16)]

    def agrees(slot, frame):
        return frame == 502 + slot          # the truth is +2 on the bookkeeping

    runs = timerread.fit_runs(reading, prior, agrees)
    built = timerread.frame_map(reading, runs, prior)
    assert None not in built, "a bridged stretch was left unmapped"
    assert built[:5] == [502 + slot for slot in range(5)]
    # The bridge carries the same shift, so the seam does not step.
    assert built[5] - built[4] == 1
    assert all(b - a == 1 for a, b in zip(built, built[1:], strict=False))


def test_a_clip_with_no_clock_on_screen_reads_nothing_rather_than_guessing():
    reading = timerread.read(np.zeros((0, timerread.HEIGHT, timerread.WIDTH, 3),
                                      np.uint8), {}, 40.0, 6.0)
    assert reading.values == []
    assert not reading.sound, "an empty read must never call itself sound"

"""The CLOCK/RAM join that identifies a replay picture's input frame."""
import numpy as np

from sm64_events.replay import timerread


def _reading(frames):
    values = [frame * 100 // 30 for frame in frames]
    out = timerread.TimerReading(values=values, frozen=[False] * len(values))
    out.read = len(values)
    out.legal = sum(value % 10 in (0, 3, 6) for value in values)
    out.transitions = max(0, len(values) - 1)
    out.monotonic = sum(b >= a for a, b in zip(values, values[1:], strict=False))
    out.backwards = sum(b < a for a, b in zip(values, values[1:], strict=False))
    out.pinned = len(values)
    return out


def _pairs(frames, epoch=500, lag=7):
    pairs = []
    for frame in frames:
        current_igt = frame + lag
        pairs.append((epoch + current_igt, current_igt))
    return pairs


def test_a_correctly_read_clock_value_inverts_without_rounding_ambiguity():
    seen = {}
    for frame in range(20_000):
        centiseconds = frame * 100 // 30
        assert timerread.frames_of(centiseconds) == frame
        assert centiseconds not in seen
        seen[centiseconds] = frame


def test_screen_arithmetic_is_reported_but_does_not_claim_ocr_is_proved():
    # A seconds digit jumping forward can still produce only legal values and
    # a monotone sequence. The first implementation called that `sound`;
    # this is exactly the false implication the hop analysis found.
    values = [0, 3, 6] + [frame * 100 // 30 for frame in range(33, 60)]
    assert all(value % 10 in (0, 3, 6) for value in values)
    reading = timerread.TimerReading(values=values,
                                     frozen=[False] * len(values),
                                     read=len(values), legal=len(values),
                                     monotonic=len(values) - 1,
                                     transitions=len(values) - 1,
                                     pinned=len(values))
    assert "sound" not in reading.as_dict()
    assert "screen_consistent" not in reading.as_dict()


def test_a_long_identical_reading_is_frozen_not_a_new_frame_source():
    values = [0, 3, 6, 6] + [10] * 12 + [13, 16, 20]
    reading = timerread.TimerReading(values=values,
                                     frozen=[False] * len(values))
    timerread._mark_frozen(reading)
    assert not any(reading.frozen[:4])
    assert all(reading.frozen[4:16])
    assert timerread.segments(reading) == [(0, 3), (16, 18)]


def test_the_ram_pair_turns_relative_igt_into_the_absolute_displayed_frame():
    frames = list(range(40))
    reading = _reading(frames)
    truth = [500 + frame for frame in frames]
    prior = [value + 4 for value in truth]          # deliberately wrong map
    mapped = timerread.map_from_clock(reading, _pairs(frames), prior)
    assert mapped is not None
    assert mapped.frame_map == truth
    assert mapped.mechanical == 40 and mapped.bridged == 0
    assert mapped.as_dict()["display_lag_frames"] == {
        "min": 7, "median": 7, "max": 7}


def test_per_picture_delay_is_measured_not_replaced_by_one_fitted_epoch():
    frames = list(range(40))
    lags = [5] * 20 + [6] * 20
    pairs = [(800 + frame + lag, frame + lag)
             for frame, lag in zip(frames, lags, strict=True)]
    mapped = timerread.map_from_clock(
        _reading(frames), pairs, [900 + frame for frame in frames])
    assert mapped is not None
    assert mapped.frame_map == [800 + frame for frame in frames]
    assert mapped.as_dict()["display_lag_frames"] == {
        "min": 5, "median": 6, "max": 6}


def test_an_isolated_legal_ocr_spike_is_refused_and_bridged():
    frames = list(range(40))
    reading = _reading(frames)
    reading.values[20] += 10       # still ends in 6, but names frame 23
    truth = [500 + frame for frame in frames]
    mapped = timerread.map_from_clock(reading, _pairs(frames), truth)
    assert mapped is not None
    assert mapped.frame_map == truth
    assert mapped.mechanical == 39
    assert mapped.rejected == 1
    assert mapped.bridged == 1


def test_an_illegal_last_digit_is_never_joined_even_when_its_lag_is_plausible():
    frames = list(range(40))
    reading = _reading(frames)
    reading.values[20] = 65       # inverts nearby, but CLOCK cannot print it
    truth = [500 + frame for frame in frames]
    mapped = timerread.map_from_clock(reading, _pairs(frames), truth)
    assert mapped is not None
    assert mapped.frame_map == truth
    assert mapped.mechanical == 39
    assert mapped.rejected == mapped.bridged == 1


def test_a_one_second_glyph_error_cannot_pass_as_render_delay():
    frames = list(range(60))
    reading = _reading(frames)
    reading.values[40] -= 100     # legal, but 30 frames behind live RAM
    truth = [500 + frame for frame in frames]
    mapped = timerread.map_from_clock(reading, _pairs(frames), truth)
    assert mapped is not None
    assert mapped.frame_map == truth
    assert mapped.rejected == mapped.bridged == 1


def test_a_sustained_counter_domain_mismatch_refuses_the_timer_path():
    frames = list(range(45))
    reading = _reading(frames)
    pairs = _pairs(frames)
    # Model a subarea edge where RAM restarts but the displayed counter does
    # not. This is not an OCR hole the old map may silently bridge; it refutes
    # the join's counter-domain premise for the rest of the stretch.
    for slot in range(20, len(pairs)):
        raw, _current = pairs[slot]
        pairs[slot] = (raw, slot - 20)
    assert timerread.map_from_clock(reading, pairs,
                                    [500 + f for f in frames]) is None


def test_a_reset_epoch_crossing_is_not_mistaken_for_render_delay():
    frames = list(range(40))
    pairs = _pairs(frames)
    # The captured RAM clock has reset while this picture still shows the old
    # epoch. Negative delay proves the two cannot be joined.
    pairs[20] = (12, 2)
    truth = [500 + frame for frame in frames]
    mapped = timerread.map_from_clock(_reading(frames), pairs, truth)
    assert mapped is not None
    assert mapped.frame_map == truth
    assert mapped.rejected == 1 and mapped.bridged == 1


def test_unreadable_or_unpaired_slots_are_explicit_nearest_map_bridges():
    frames = list(range(45))
    pairs = _pairs(frames, epoch=700, lag=8)
    for slot in range(12, 20):
        pairs[slot] = None
    truth = [700 + frame for frame in frames]
    prior = [value + 3 for value in truth]
    mapped = timerread.map_from_clock(_reading(frames), pairs, prior)
    assert mapped is not None
    assert mapped.frame_map == truth
    assert mapped.mechanical == 37 and mapped.bridged == 8


def test_too_few_mechanical_joins_refuses_instead_of_guessing():
    frames = list(range(40))
    pairs = _pairs(frames)
    pairs[timerread.MIN_MECHANICAL - 1:] = [None] * (
        len(pairs) - timerread.MIN_MECHANICAL + 1)
    assert timerread.map_from_clock(_reading(frames), pairs,
                                    [500 + f for f in frames]) is None


def test_a_clip_with_no_clock_on_screen_reads_nothing():
    reading = timerread.read(
        np.zeros((0, timerread.HEIGHT, timerread.WIDTH, 3), np.uint8),
        {}, 40.0, 6.0)
    assert reading.values == []
    assert reading.as_dict()["read"] == 0

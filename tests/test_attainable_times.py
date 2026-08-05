"""Only 30 of every 100 centisecond values can appear on Usamune's timer.

The clock is a frame counter at 30 fps and centiseconds are only how it
prints, so a derived threshold of 15.01 asks for something nobody can ever
hit. These are the three round trips every derived ladder goes through."""
import pytest

from sm64_events.core.timefmt import (GAME_FPS, attainable_cs, cs_of_frame,
                                      format_igt, frame_at_or_after,
                                      next_attainable_cs, prev_attainable_cs)

DISPLAYABLE = {(f % GAME_FPS) * 100 // GAME_FPS for f in range(GAME_FPS)}


def test_only_thirty_of_a_hundred_centisecond_values_exist():
    assert len(DISPLAYABLE) == GAME_FPS
    assert sorted(DISPLAYABLE)[:10] == [0, 3, 6, 10, 13, 16, 20, 23, 26, 30]


@pytest.mark.parametrize("cs", range(0, 6000, 7))
def test_rounding_up_lands_on_the_set_and_never_goes_backwards(cs):
    out = attainable_cs(cs)
    assert out >= cs and out % 100 in DISPLAYABLE


@pytest.mark.parametrize("cs", range(0, 6000, 7))
def test_the_next_step_is_strictly_later_and_still_displayable(cs):
    out = next_attainable_cs(cs)
    assert out > cs and out % 100 in DISPLAYABLE


@pytest.mark.parametrize("cs", range(1, 6000, 7))
def test_the_previous_step_is_strictly_earlier_and_still_displayable(cs):
    out = prev_attainable_cs(cs)
    assert out < cs and out % 100 in DISPLAYABLE


def test_a_displayable_time_is_left_exactly_where_it_is():
    for frames in range(0, 300):
        cs = cs_of_frame(frames)
        assert attainable_cs(cs) == cs


def test_the_second_rolls_over_rather_than_inventing_a_hundredth():
    assert attainable_cs(997) == 1000
    assert next_attainable_cs(996) == 1000
    assert prev_attainable_cs(1000) == 996


def test_the_frame_round_trip_agrees_with_the_display_formatter():
    """cs_of_frame is format_igt's own rule without the punctuation; if the
    two drift, every derived ladder is quantised to a clock the screen does
    not use."""
    for frames in range(0, 3600, 7):
        cs = cs_of_frame(frames)
        assert format_igt(frames) == (
            f"{cs // 6000}'{(cs % 6000) // 100:02d}\"{cs % 100:02d}")
        assert frame_at_or_after(cs) == frames

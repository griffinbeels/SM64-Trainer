"""Where the overlay goes, and when it must not be on screen at all.

The rectangle is the game PICTURE (Project64's client area in screen
coordinates), never its frame -- a ghost aligned to the title bar is a ghost
offset by the title bar. `scale` exists for one reason: PJ64 1.6 is
DPI-unaware, so a reader and a placer that disagree about DPI awareness see
the same window at two sizes.
"""
import pytest

from sm64_events.ghost.window.geometry import (
    MIN_PICTURE_SIDE,
    GameWindow,
    Rect,
    dpi_scale,
    follow_decision,
    scale_rect,
)

PICTURE = Rect(x=120, y=80, width=1600, height=1224)
PLAYING = GameWindow(found=True, minimized=False, client=PICTURE)


def test_the_overlay_takes_the_game_picture_rectangle():
    placement = follow_decision(PLAYING)
    assert placement.show is True
    assert placement.rect == PICTURE
    assert placement.reason == "follow"


def test_no_project64_means_no_overlay():
    for gone in (None, GameWindow(found=False, minimized=False, client=None)):
        placement = follow_decision(gone)
        assert placement.show is False
        assert placement.rect is None
        assert placement.reason == "gone"


def test_a_minimized_emulator_takes_the_ghost_with_it():
    placement = follow_decision(
        GameWindow(found=True, minimized=True, client=PICTURE))
    assert placement.show is False
    assert placement.reason == "minimized"


@pytest.mark.parametrize("width,height", [
    (MIN_PICTURE_SIDE - 1, 1224), (1600, MIN_PICTURE_SIDE - 1), (0, 0)])
def test_a_degenerate_picture_is_not_worth_covering(width, height):
    """A window mid-restore reports a few pixels for a frame or two; placing a
    window there makes a flicker the player sees."""
    placement = follow_decision(GameWindow(
        found=True, minimized=False,
        client=Rect(x=0, y=0, width=width, height=height)))
    assert placement.show is False
    assert placement.reason == "too_small"


def test_switching_the_ghost_off_beats_every_other_reason():
    placement = follow_decision(PLAYING, wanted_visible=False)
    assert placement.show is False
    assert placement.reason == "hidden_by_request"


def test_a_missing_client_rect_is_not_a_crash():
    placement = follow_decision(
        GameWindow(found=True, minimized=False, client=None))
    assert placement.show is False
    assert placement.reason == "too_small"


def test_at_150_percent_scaling_the_rectangle_grows_by_the_scale_factor():
    """Live 2026-07-23: PJ64's client is 1600x1224 logical / 2400x1836
    physical at 150 %. A logical read placed physically needs this."""
    placement = follow_decision(PLAYING, scale=1.5)
    assert placement.rect == Rect(x=180, y=120, width=2400, height=1836)


def test_scaling_keeps_the_far_edges_on_the_same_pixel():
    """Scale the EDGES, not the size: rounding a width independently of an
    origin walks the right edge off the picture by a pixel."""
    scaled = scale_rect(Rect(x=1, y=1, width=3, height=3), 1.5)
    assert scaled.x + scaled.width == round((1 + 3) * 1.5)
    assert scaled.y + scaled.height == round((1 + 3) * 1.5)


def test_scale_of_one_changes_nothing():
    assert scale_rect(PICTURE, 1.0) == PICTURE


@pytest.mark.parametrize("read_dpi,place_dpi,expected", [
    (96, 96, 1.0), (96, 144, 1.5), (144, 96, pytest.approx(2 / 3)),
    (96, 192, 2.0), (0, 96, 1.0), (96, 0, 1.0),
])
def test_the_scale_between_two_dpi_readings(read_dpi, place_dpi, expected):
    """A nonsense DPI (a query that failed and returned 0) must leave the
    rectangle alone rather than collapse it to a point."""
    assert dpi_scale(read_dpi, place_dpi) == expected

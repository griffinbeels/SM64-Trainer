"""Where the overlay goes, when it must not be on screen, and how we know.

The rectangle is the game PICTURE (Project64's client area in screen
coordinates), never its frame -- a ghost aligned to the title bar is a ghost
offset by the title bar.

There is no DPI factor in here any more. The follower reads and places in one
DPI context, so what it read is what it asks for; `rects_agree` is how it
finds out whether Windows agreed, and `correction_for` is its one bounded
retry before it hides rather than leaves an unproved rectangle on screen.
"""
import pytest

from sm64_events.ghost.window.geometry import (
    MIN_PICTURE_SIDE,
    PLACE_TOLERANCE_PX,
    GameWindow,
    Rect,
    correction_for,
    follow_decision,
    rects_agree,
    scale_rect,
)

PICTURE = Rect(x=120, y=80, width=1600, height=1224)
PLAYING = GameWindow(found=True, minimized=False, client=PICTURE)


def test_the_overlay_takes_the_game_picture_rectangle():
    placement = follow_decision(PLAYING)
    assert placement.show is True
    assert placement.rect == PICTURE
    assert placement.reason == "follow"


def test_what_was_read_is_what_is_asked_for():
    """The 2026-09-19 fix, as an assertion: no factor, no correction, no
    guess. A rectangle read in one DPI context and placed in that same context
    needs nothing done to it, and the old `GetDpiForWindow` ratio multiplied
    it by 1.5 at his display scaling whether or not anything was wrong."""
    assert follow_decision(PLAYING).rect is PICTURE


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


def test_the_ghost_leaves_when_he_is_using_another_app():
    """He reported the overlay "covering other apps". An overlay glued to a
    window nobody is looking at is an overlay over whatever they ARE looking
    at, so the emulator not being the foreground app hides it -- and it comes
    back the moment he clicks into the game again."""
    away = GameWindow(found=True, minimized=False, client=PICTURE,
                      foreground=False)
    placement = follow_decision(away)
    assert placement.show is False
    assert placement.rect is None
    assert placement.reason == "not_foreground"
    assert follow_decision(PLAYING).show is True


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


# --- proving a placement ----------------------------------------------------

def test_a_window_that_landed_where_it_was_told_agrees():
    assert rects_agree(PICTURE, PICTURE) is True


def test_a_rounding_edge_still_agrees_but_a_scale_never_does():
    nudged = Rect(x=PICTURE.x + PLACE_TOLERANCE_PX, y=PICTURE.y,
                  width=PICTURE.width, height=PICTURE.height)
    assert rects_agree(PICTURE, nudged) is True
    assert rects_agree(PICTURE, scale_rect(PICTURE, 1.5)) is False


@pytest.mark.parametrize("got", [None, Rect(x=120, y=80, width=1600, height=1500)])
def test_a_window_somewhere_else_does_not_agree(got):
    assert rects_agree(PICTURE, got) is False


def test_a_uniformly_scaled_window_is_corrected_by_asking_for_less():
    """His symptom, as arithmetic: we asked for the picture and Windows made
    it half again as big. Asking for picture/1.5 lands it on the picture."""
    got = scale_rect(PICTURE, 1.5)
    corrected = correction_for(PICTURE, got)
    assert corrected is not None
    assert rects_agree(PICTURE, scale_rect(corrected, 1.5))


@pytest.mark.parametrize("got,why", [
    (Rect(x=120, y=80, width=2400, height=1224), "only one side is wrong"),
    (Rect(x=120, y=80, width=16000, height=12240), "outside the sane band"),
    (Rect(x=0, y=0, width=0, height=0), "nothing to divide by"),
    (PICTURE, "the size is right; the position is somebody else's problem"),
])
def test_anything_that_is_not_a_clean_scale_is_not_corrected(got, why):
    assert correction_for(PICTURE, got) is None, why


def test_scaling_keeps_the_far_edges_on_the_same_pixel():
    """Scale the EDGES, not the size: rounding a width independently of an
    origin walks the right edge off the picture by a pixel."""
    scaled = scale_rect(Rect(x=1, y=1, width=3, height=3), 1.5)
    assert scaled.x + scaled.width == round((1 + 3) * 1.5)
    assert scaled.y + scaled.height == round((1 + 3) * 1.5)


def test_scale_of_one_changes_nothing():
    assert scale_rect(PICTURE, 1.0) == PICTURE

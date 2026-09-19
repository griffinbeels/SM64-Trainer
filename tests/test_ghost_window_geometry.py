"""Where the overlay goes, when it must not be on screen, and how we know.

The rectangle is the game PICTURE -- the part of Project64's client area its
RENDERER draws into, in screen coordinates. Not its window frame (a ghost
aligned to the title bar is a ghost offset by the title bar) and, since round
3, not the whole client area either: Project64 keeps a status bar inside the
client, and the numbers that prove it are in `CLIENT`/`STATUS_BAR` below.

There is no DPI factor in here any more. The follower reads and places in one
DPI context, so what it read is what it asks for; `rects_agree` is how it
finds out whether Windows agreed, and `correction_for` is its one bounded
retry before it hides rather than leaves an unproved rectangle on screen.
"""
import pytest

from sm64_events.ghost.window.geometry import (
    MIN_PICTURE_SIDE,
    PLACE_TOLERANCE_PX,
    ChildWindow,
    GameWindow,
    Rect,
    bottom_strip,
    correction_for,
    follow_decision,
    picture_rect,
    rects_agree,
    scale_rect,
)

#: His running emulator, read per-monitor-aware on 2026-09-19. The strip is
#: `msctls_statusbar32`; the browser is the ROM list, style-hidden while a game
#: runs and docked to the TOP, which is why it must never be subtracted.
CLIENT = Rect(x=18, y=81, width=2400, height=1836)
STATUS_BAR = ChildWindow(hwnd=498929110, cls="msctls_statusbar32",
                         rect=Rect(x=18, y=1883, width=2400, height=35),
                         visible=True)
ROM_BROWSER = ChildWindow(hwnd=424350494, cls="SysListView32",
                          rect=Rect(x=18, y=81, width=2400, height=1802),
                          visible=False)
#: What the renderer actually gets, and what his clips are recorded from.
RENDERED = Rect(x=18, y=81, width=2400, height=1802)

PICTURE = Rect(x=120, y=80, width=1600, height=1224)
PLAYING = GameWindow(found=True, minimized=False, client=PICTURE)
EMULATOR = GameWindow(found=True, minimized=False, client=CLIENT,
                      children=(STATUS_BAR, ROM_BROWSER))


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


def test_the_ghost_stays_up_when_he_clicks_another_app():
    """Round 3's reversal, in his words: "if I click out of PJ64, the ghost
    disappears. This is very jarring, because it blinks in / out."

    It is safe because the ghost is inserted directly above Project64 rather
    than above everything, so an app he raises over the emulator is over the
    ghost too. Nothing here may branch on which app has the keyboard.
    """
    away = GameWindow(found=True, minimized=False, client=PICTURE,
                      foreground=False)
    placement = follow_decision(away)
    assert placement.show is True
    assert placement.reason == "follow"
    assert placement.rect == PICTURE
    assert placement == follow_decision(PLAYING), (
        "the foreground makes no difference at all to the decision")


def test_a_window_with_no_footage_on_it_stays_off_his_screen():
    """Live 2026-09-19: at 80 % the ghost read as a translucent dark sheet
    over his game AND his browser. Whole-window alpha dims everything under
    the window, so a window with nothing to show is a window making the game
    harder to see -- it may only be up while the page has a picture."""
    placement = follow_decision(PLAYING, presentable=False)
    assert placement.show is False
    assert placement.reason == "no_footage"
    assert follow_decision(PLAYING, presentable=True).show is True
    assert placement.rect == PICTURE, (
        "the one refusal that still knows where the window would go: the "
        "other visibility strategy needs somewhere to put a clear window")


def test_the_window_state_reasons_beat_the_page_one():
    """A minimized emulator says so, rather than blaming the page."""
    gone = GameWindow(found=False, minimized=False, client=None)
    assert follow_decision(gone, presentable=False).reason == "gone"
    assert follow_decision(PLAYING, wanted_visible=False,
                           presentable=False).reason == "hidden_by_request"


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


# --- the picture inside the client area -------------------------------------

def test_the_picture_is_the_client_minus_project64s_status_bar():
    """Round 3, item 3, as arithmetic. His client is 1836 tall and the strip
    takes 34 of it; a ghost on the whole client is stretched by 1.9 %, which
    is the drift he could see at the bottom edge and on the Mario icon."""
    assert picture_rect(CLIENT, (STATUS_BAR, ROM_BROWSER)) == RENDERED
    assert EMULATOR.picture == RENDERED
    assert follow_decision(EMULATOR).rect == RENDERED


def test_the_picture_is_what_his_clips_are_recorded_from():
    """The independent check on the rule: the capture layer records the
    RENDERER's framebuffer, floor-even cropped. His clips are all 1600x1200,
    and 1600x1200 is the logical client (1600x1224) minus the strip (23),
    floored -- the whole client would have recorded 1600x1224."""
    logical_client = Rect(x=12, y=54, width=1600, height=1224)
    logical_strip = ChildWindow(hwnd=1, cls="msctls_statusbar32",
                                rect=Rect(x=12, y=1255, width=1600, height=23),
                                visible=True)
    rendered = picture_rect(logical_client, (logical_strip,))
    assert (rendered.width & ~1, rendered.height & ~1) == (1600, 1200)


def test_the_rom_browser_is_not_chrome_even_when_it_is_showing():
    """Same width, same left edge, but nearly the whole client and docked to
    the top. Subtracting it would leave the ghost a sliver."""
    showing = ChildWindow(hwnd=ROM_BROWSER.hwnd, cls=ROM_BROWSER.cls,
                          rect=ROM_BROWSER.rect, visible=True)
    assert bottom_strip(CLIENT, (showing,)) is None
    assert picture_rect(CLIENT, (showing,)) == CLIENT


def test_an_emulator_with_its_status_bar_turned_off_keeps_its_whole_client():
    hidden = ChildWindow(hwnd=STATUS_BAR.hwnd, cls=STATUS_BAR.cls,
                         rect=STATUS_BAR.rect, visible=False)
    assert picture_rect(CLIENT, (hidden,)) == CLIENT
    assert picture_rect(CLIENT, ()) == CLIENT
    assert picture_rect(None, ()) is None


def test_stacked_strips_are_walked_so_the_picture_clears_all_of_them():
    """A second strip sitting ON the status bar does not touch the client's
    bottom edge, so the search has to move the edge up and look again."""
    upper = ChildWindow(hwnd=2, cls="ReBarWindow32",
                        rect=Rect(x=18, y=1848, width=2400, height=35),
                        visible=True)
    assert bottom_strip(CLIENT, (STATUS_BAR, upper)) is upper
    assert picture_rect(CLIENT, (STATUS_BAR, upper)).height == 1848 - 81
    assert picture_rect(CLIENT, (STATUS_BAR,)) == RENDERED, (
        "with only the status bar the walk stops after one step")


@pytest.mark.parametrize("child,why", [
    (ChildWindow(hwnd=3, cls="Tooltip",
                 rect=Rect(x=18, y=1883, width=300, height=35), visible=True),
     "a floating control that does not span the client"),
    (ChildWindow(hwnd=4, cls="Docked",
                 rect=Rect(x=18, y=81, width=2400, height=35), visible=True),
     "docked to the TOP, so it is not between the picture and the bottom"),
    (ChildWindow(hwnd=5, cls="Panel",
                 rect=Rect(x=18, y=900, width=2400, height=1017), visible=True),
     "more than half the client: content, not chrome"),
])
def test_only_a_strip_docked_along_the_bottom_is_subtracted(child, why):
    assert bottom_strip(CLIENT, (child,)) is None, why
    assert picture_rect(CLIENT, (child,)) == CLIENT


def test_a_strip_taller_than_the_client_cannot_erase_the_picture():
    """A window mid-restore reports nonsense for a frame; the answer is the
    client, which `follow_decision` then refuses for being too small."""
    swallowing = ChildWindow(hwnd=6, cls="msctls_statusbar32",
                             rect=Rect(x=18, y=81, width=2400, height=1836),
                             visible=True)
    assert picture_rect(CLIENT, (swallowing,)) == CLIENT


def test_a_picture_too_small_to_cover_is_refused_after_the_strip_comes_off():
    """The size test runs on the PICTURE, not the client: a window whose whole
    client is status bar has no picture worth covering."""
    tiny = Rect(x=0, y=0, width=1600, height=2 * MIN_PICTURE_SIDE - 12)
    strip = ChildWindow(hwnd=7, cls="msctls_statusbar32",
                        rect=Rect(x=0, y=MIN_PICTURE_SIDE - 6, width=1600,
                                  height=MIN_PICTURE_SIDE - 6),
                        visible=True)
    game = GameWindow(found=True, minimized=False, client=tiny,
                      children=(strip,))
    assert follow_decision(game).reason == "too_small"
    assert follow_decision(
        GameWindow(found=True, minimized=False, client=tiny)).show is True, (
        "the same client with no strip is big enough, so the strip is what "
        "made the difference")


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

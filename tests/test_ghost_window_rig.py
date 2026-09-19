"""The rig's own arithmetic, proved without a window.

The rig is an INSTRUMENT, and an instrument whose readings have never been
driven with a known input is not evidence. These drive each reading with a
value whose answer is known by hand: a buffer that is entirely black, a
rectangle exactly 1.5 times another, a title whose counters have not moved.

What is deliberately NOT here: anything that creates, styles, shows or
captures a window. Those are the parts that can only be proved on his machine
with the rig's own safety rules around them, and pretending otherwise is how
the overlay got two green runs and two failures.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import pytest  # noqa: E402

from ghost_window_rig import (  # noqa: E402
    HWND_TOP,
    REQUIRED_EX_BITS,
    SWP_NOMOVE_NOSIZE_NOACTIVATE,
    Rect,
    _picture_verdict,
    checked_child_opacity,
    child_command,
    foreground_verdict,
    parse_tick_title,
    rect_inside,
    rects_intersect,
    scale_between,
    standin_candidates,
    style_report,
    summarize_pixels,
    tick_rates,
    zorder_relation,
)
from sm64_events.ghost.window import style  # noqa: E402


def bgra(pixels):
    """A pixel buffer in the order GetDIBits hands one back."""
    out = bytearray()
    for red, green, blue in pixels:
        out += bytes((blue, green, red, 0))
    return bytes(out)


# --- the styles Windows kept -----------------------------------------------

def test_style_report_names_the_promise_that_went_missing():
    kept = style.OVERLAY_EX_STYLE & ~style.WS_EX_TRANSPARENT
    report = style_report(style.OVERLAY_EX_STYLE, kept)
    assert report["missing_required"] == ["TRANSPARENT"]
    assert report["dropped_bits"] == ["TRANSPARENT"]
    assert report["equal"] is False


def test_style_report_is_content_when_every_bit_survived():
    report = style_report(style.OVERLAY_EX_STYLE, style.OVERLAY_EX_STYLE)
    assert report["missing_required"] == []
    assert report["equal"] is True
    assert set(REQUIRED_EX_BITS) <= set(report["kept_bits"])


def test_style_report_notices_a_bit_windows_added():
    report = style_report(style.OVERLAY_EX_STYLE,
                          style.OVERLAY_EX_STYLE | style.WS_EX_TOPMOST)
    assert report["added_bits"] == ["TOPMOST"]
    # A window that came back TOPMOST still keeps all four promises, so the
    # required list must not be where that shows up.
    assert report["missing_required"] == []


# --- the counters in the title ---------------------------------------------

def test_parse_tick_title_reads_the_counters():
    parsed = parse_tick_title(
        "RIG t=1234 raf=57 rvfc=12 vis=visible vt=0.4 rs=4 paused=0 err=none")
    assert parsed["raf"] == 57
    assert parsed["rvfc"] == 12
    assert parsed["vis"] == "visible"
    assert parsed["vt"] == 0.4


def test_parse_tick_title_refuses_a_title_that_is_not_one():
    assert parse_tick_title("SM64 Trainer ghost overlay") is None
    assert parse_tick_title("") is None
    assert parse_tick_title(None) is None


def test_tick_rates_are_per_second_not_totals():
    rates = tick_rates({"at": 10.0, "raf": 0, "rvfc": 0},
                       {"at": 20.0, "raf": 600, "rvfc": 300})
    assert rates["raf_per_s"] == 60.0
    assert rates["rvfc_per_s"] == 30.0
    assert rates["span_s"] == 10.0


def test_tick_rates_report_a_frozen_page_as_zero_not_as_missing():
    rates = tick_rates({"at": 0.0, "raf": 3, "rvfc": 0},
                       {"at": 10.0, "raf": 3, "rvfc": 0})
    assert rates["measured"] is True
    assert rates["raf_per_s"] == 0.0
    assert rates["rvfc_per_s"] == 0.0


def test_tick_rates_say_so_when_there_was_nothing_to_measure():
    assert tick_rates(None, None)["measured"] is False
    assert tick_rates({"at": 5.0}, {"at": 5.0})["measured"] is False


# --- the window's own pixels ------------------------------------------------

def test_summarize_pixels_calls_an_all_black_window_black():
    summary = summarize_pixels(bgra([(0, 0, 0)] * 4), 2, 2)
    assert summary["non_black_fraction"] == 0.0
    assert summary["mean_rgb"] == [0.0, 0.0, 0.0]
    assert summary["brightest_channel"] == 0


def test_summarize_pixels_separates_footage_from_a_black_sheet():
    half = bgra([(255, 220, 40), (255, 220, 40), (0, 0, 0), (0, 0, 0)])
    summary = summarize_pixels(half, 2, 2)
    assert summary["non_black_fraction"] == 0.5
    assert summary["mean_rgb"] == [127.5, 110.0, 20.0]


def test_summarize_pixels_ignores_noise_under_the_black_threshold():
    nearly = bgra([(8, 8, 8)] * 4)
    assert summarize_pixels(nearly, 2, 2)["non_black_fraction"] == 0.0


def test_summarize_pixels_refuses_a_buffer_that_is_short():
    summary = summarize_pixels(b"\x00" * 4, 2, 2)
    assert summary["ok"] is False
    assert summary["reason"] == "short_buffer"


# --- rectangles -------------------------------------------------------------

def test_scale_between_finds_the_one_and_a_half_that_broke_round_one():
    logical = Rect(x=0, y=0, width=640, height=480)
    physical = Rect(x=0, y=0, width=960, height=720)
    assert scale_between(logical, physical) == 1.5


def test_scale_between_refuses_two_rectangles_that_merely_differ():
    assert scale_between(Rect(0, 0, 640, 480), Rect(0, 0, 960, 480)) is None
    assert scale_between(None, Rect(0, 0, 10, 10)) is None


def test_rects_intersect_is_edge_exclusive():
    assert rects_intersect(Rect(0, 0, 10, 10), Rect(5, 5, 10, 10))
    assert not rects_intersect(Rect(0, 0, 10, 10), Rect(10, 0, 10, 10))


def test_rect_inside_rejects_a_window_the_desktop_would_clip():
    screen = Rect(x=-2560, y=-147, width=7840, height=2560)
    assert rect_inside(screen, Rect(x=0, y=0, width=960, height=720))
    assert not rect_inside(screen, Rect(x=5000, y=0, width=960, height=720))
    assert not rect_inside(screen, Rect(x=-3000, y=0, width=100, height=100))


SCREEN = Rect(x=-2560, y=-147, width=7840, height=2560)
MAXIMIZED = Rect(x=14, y=12, width=2409, height=1910)


def test_standin_candidates_are_distinct_places_of_the_asked_size():
    spots = standin_candidates(640, 480, count=4, screen=SCREEN)
    assert len(spots) == 4
    assert {(spot.width, spot.height) for spot in spots} == {(640, 480)}
    assert len({(spot.x, spot.y) for spot in spots}) == 4


def test_every_candidate_fits_on_the_desktop():
    """A spot the desktop would clip is a try wasted, and a rig that runs out
    of tries reports "no room" for a machine that had plenty."""
    for spot in standin_candidates(640, 480, screen=SCREEN):
        assert rect_inside(SCREEN, spot)


def test_a_maximized_emulator_still_leaves_the_rig_somewhere_to_stand():
    """His actual setup, 2026-09-19: Project64 maximized over a 2560-wide
    monitor left every spot on the old diagonal over the emulator, and the rig
    refused to run at all. The desktop is wider than one monitor."""
    spots = standin_candidates(640, 480, screen=SCREEN, blocked=(MAXIMIZED,))
    assert spots, "nowhere to stand on a desktop with room to spare"
    for spot in spots:
        assert not rects_intersect(spot, MAXIMIZED)


def test_a_desktop_with_no_room_at_all_returns_nothing_rather_than_a_bad_spot():
    one_screen = Rect(x=0, y=0, width=700, height=540)
    assert standin_candidates(640, 480, screen=one_screen,
                              blocked=(one_screen,)) == []


# --- z-order ----------------------------------------------------------------

def test_zorder_relation_reads_the_list_front_first():
    order = [11, 22, 33]
    assert zorder_relation(order, 11, 22) == "directly_above"
    assert zorder_relation(order, 11, 33) == "above"
    assert zorder_relation(order, 33, 11) == "below"


def test_zorder_relation_says_which_window_it_could_not_find():
    assert zorder_relation([22], 11, 22) == "overlay_missing"
    assert zorder_relation([11], 11, 22) == "other_missing"


# --- the foreground guard ---------------------------------------------------

def test_foreground_verdict_calls_our_own_window_ours_first():
    # The stand-in is titled like the emulator on purpose. If that title won,
    # a focus steal would read as "the player is in his game" and the rig would
    # wait politely instead of stopping.
    verdict = foreground_verdict(7, "Project64 1.6 - rig stand-in", {7}, set())
    assert verdict == "ours"


def test_foreground_verdict_protects_the_real_emulator():
    assert foreground_verdict(9, "Project64 Version 1.6", set(), set()) == "project64"
    assert foreground_verdict(9, "Notepad", set(), set()) == "other"
    assert foreground_verdict(0, "", set(), set()) == "none"


def test_foreground_verdict_claims_a_window_of_a_process_we_started():
    assert foreground_verdict(9, "anything", set(), {4242}, pid=4242) == "ours"


# --- the rig may not start a child that could be seen ----------------------

def test_the_rig_refuses_to_start_the_child_at_a_visible_opacity():
    # The child decides for itself when to show its window, so the instrument
    # must not be able to hand it an opacity that would paint over his game.
    for refused in (1.0, 0.5, 0.01, 0.002):
        with pytest.raises(ValueError):
            checked_child_opacity(refused)


def test_the_rig_allows_only_a_fully_transparent_child():
    assert checked_child_opacity(0.0) == 0.0
    # Rounds to alpha 0, so it can put nothing on screen.
    assert checked_child_opacity(0.001) == 0.001


# --- the picture the overlay is given ---------------------------------------

def test_the_rig_says_which_child_came_off_the_picture():
    aware = {"subtracted": "msctls_statusbar32"}
    verdict = _picture_verdict(aware, [18, 81, 2400, 1836], [18, 81, 2400, 1802])
    assert "msctls_statusbar32" in verdict


def test_the_rig_does_not_call_an_unchanged_picture_a_success():
    """The reading that would have agreed with the bug: a strip was found and
    the picture came back as the whole client anyway."""
    aware = {"subtracted": "msctls_statusbar32"}
    unchanged = _picture_verdict(aware, [18, 81, 2400, 1836],
                                 [18, 81, 2400, 1836])
    assert "nothing came off" in unchanged
    assert "no strip" in _picture_verdict({"subtracted": None},
                                          [18, 81, 2400, 1836],
                                          [18, 81, 2400, 1836])
    assert "no rectangle" in _picture_verdict({"subtracted": None}, None, None)


# --- knocking the ghost down, without touching his keyboard -----------------

def test_the_reorder_the_rig_provokes_cannot_activate_anything():
    """The rig raises its OWN stand-in to generate the z-order change the
    overlay has to survive. The flags are what keep that from becoming a
    focus change or a window on his screen: no move, no resize, no activate,
    and never `SWP_SHOWWINDOW`."""
    assert SWP_NOMOVE_NOSIZE_NOACTIVATE & style.SWP_NOMOVE
    assert SWP_NOMOVE_NOSIZE_NOACTIVATE & style.SWP_NOSIZE
    assert SWP_NOMOVE_NOSIZE_NOACTIVATE & style.SWP_NOACTIVATE
    assert not SWP_NOMOVE_NOSIZE_NOACTIVATE & style.SWP_SHOWWINDOW
    assert HWND_TOP == 0, (
        "the front of the ORDINARY band; -1 would be the topmost band, which "
        "is the one thing the overlay may never join")


# --- the command line the rig starts the child with -------------------------

def test_a_rectangle_on_a_left_hand_monitor_survives_the_command_line():
    """The failure this exists to stop: Project64 maximized pushed the
    stand-in onto the screen left of the primary, the rectangle started with a
    minus sign, and argparse read it as a flag with no value. The child exited
    2 before writing one line of its own log, which reads exactly like "the
    overlay never made a window"."""
    from sm64_events.ghost.window.child import build_parser

    negative = Rect(x=-2540, y=-127, width=640, height=457)
    command = child_command("file:///clip.html", negative, 0.0,
                            anchor_hwnd=4242, python="python")
    args = build_parser().parse_args(command[3:])
    assert args.fixed_rect == negative
    assert args.anchor_hwnd == 4242
    assert args.no_reorder_watch is False


def test_the_child_is_only_told_to_sit_above_a_window_when_there_is_one():
    plain = child_command("file:///c.html", Rect(x=0, y=0, width=8, height=8),
                          0.0, python="python")
    assert not [part for part in plain if part.startswith("--anchor-hwnd")]
    assert not [part for part in plain if "reorder" in part]
    measured = child_command("file:///c.html", Rect(x=0, y=0, width=8, height=8),
                             0.0, reorder_watch=False, python="python")
    assert "--no-reorder-watch" in measured


def test_the_command_cannot_carry_an_opacity_that_would_be_seen():
    with pytest.raises(ValueError):
        child_command("file:///c.html", Rect(x=0, y=0, width=8, height=8),
                      0.5, python="python")

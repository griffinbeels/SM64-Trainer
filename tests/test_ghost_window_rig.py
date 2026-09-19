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

from ghost_window_rig import (  # noqa: E402
    REQUIRED_EX_BITS,
    Rect,
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


def test_standin_candidates_are_distinct_places_of_the_asked_size():
    spots = standin_candidates(640, 480, count=4)
    assert len(spots) == 4
    assert {(spot.width, spot.height) for spot in spots} == {(640, 480)}
    assert len({(spot.x, spot.y) for spot in spots}) == 4
    # Each try moves in both directions, so a spot rejected for sitting over
    # his emulator is not retried at the same place.
    assert [spot.x for spot in spots] == sorted({spot.x for spot in spots})
    assert [spot.y for spot in spots] == sorted({spot.y for spot in spots})


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

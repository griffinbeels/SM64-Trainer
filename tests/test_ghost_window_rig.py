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
    COLOUR_KEY_RGB,
    HWND_TOP,
    MAX_SHOW_ALPHA,
    REQUIRED_EX_BITS,
    SWP_NOMOVE_NOSIZE_NOACTIVATE,
    Rect,
    _colourkey_verdict,
    _picture_verdict,
    checked_child_opacity,
    child_command,
    colour_ref,
    colourkey_sample_boxes,
    coverage_mask,
    foreground_verdict,
    grid_cells,
    inset,
    parse_tick_title,
    rect_inside,
    rects_intersect,
    region_delta,
    scale_between,
    split_halves,
    standin_candidates,
    still_block,
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


# --- the colour key --------------------------------------------------------

def screen(width, height, colour=(0, 0, 0)):
    """A capture buffer of one flat colour, in the BGRA order GetDIBits gives."""
    red, green, blue = colour
    return bytes((blue, green, red, 0)) * (width * height)


def test_colour_ref_is_byte_swapped_the_way_windows_wants():
    # COLORREF is 0x00BBGGRR. A key written the other way round keys a colour
    # nothing on the page paints, which looks exactly like a key that does not
    # work at all.
    assert colour_ref((255, 0, 255)) == 0x00FF00FF
    assert colour_ref((1, 2, 3)) == 0x00030201
    assert colour_ref(COLOUR_KEY_RGB) == 0x00FF00FF


def test_grid_cells_tile_the_window_with_no_gap_and_no_overlap():
    rect = Rect(x=100, y=50, width=240, height=160)
    cells = grid_cells(rect, columns=3, rows=2)
    assert len(cells) == 6
    assert cells["r1"] == Rect(x=100, y=50, width=80, height=80)
    assert cells["r3"] == Rect(x=260, y=50, width=80, height=80)
    assert cells["r6"] == Rect(x=260, y=130, width=80, height=80)
    covered = sum(cell.width * cell.height for cell in cells.values())
    assert covered == rect.width * rect.height


def test_grid_cells_still_tile_a_width_that_does_not_divide():
    rect = Rect(x=0, y=0, width=241, height=161)
    cells = grid_cells(rect, columns=3, rows=2)
    covered = sum(cell.width * cell.height for cell in cells.values())
    assert covered == rect.width * rect.height


def test_split_halves_divides_a_region_without_losing_a_column():
    halves = split_halves(Rect(x=10, y=10, width=81, height=20))
    assert halves["left"].width + halves["right"].width == 81
    assert halves["right"].x == halves["left"].x + halves["left"].width


def test_inset_keeps_the_sample_off_the_region_edges():
    box = inset(Rect(x=0, y=0, width=80, height=80), 0.4)
    assert box.width == 32 and box.height == 32
    assert box.x == 24 and box.y == 24


def test_colourkey_sample_boxes_name_every_region_and_stay_inside():
    rect = Rect(x=0, y=0, width=240, height=160)
    boxes = colourkey_sample_boxes(rect)
    assert set(boxes) == {
        "r1_nothing", "r2_key", "r3_white", "r4_white_half_alpha",
        "r5_video_opaque", "r5_video_transparent",
        "r6_canvas_opaque", "r6_canvas_cleared"}
    for box in boxes.values():
        assert rect_inside(rect, box)


def test_region_delta_reads_zero_when_the_screen_did_not_move():
    before = screen(8, 8, (40, 40, 40))
    row = region_delta(before, before, 8, 8, Rect(x=0, y=0, width=8, height=8),
                       Rect(x=0, y=0, width=8, height=8))
    assert row["mean_delta_rgb"] == [0.0, 0.0, 0.0]
    assert row["max_abs_delta"] == 0
    assert row["changed_fraction"] == 0.0


def test_region_delta_measures_a_blend_toward_white():
    # What an unpunched white region does under an alpha of 3/255 over a dark
    # backdrop: a couple of levels, which is the whole signal being read.
    before = screen(4, 4, (40, 40, 40))
    after = screen(4, 4, (43, 43, 43))
    row = region_delta(before, after, 4, 4, Rect(x=0, y=0, width=4, height=4),
                       Rect(x=0, y=0, width=4, height=4))
    assert row["mean_delta_rgb"] == [3.0, 3.0, 3.0]
    assert row["changed_fraction"] == 1.0


def test_region_delta_addresses_a_box_inside_the_captured_rectangle():
    origin = Rect(x=100, y=100, width=4, height=2)
    before = screen(4, 2, (0, 0, 0))
    after = bytearray(before)
    after[0:4] = bytes((0, 0, 10, 0))  # one pixel, red +10, at (100,100)
    row = region_delta(before, bytes(after), 4, 2,
                       Rect(x=100, y=100, width=1, height=1), origin)
    assert row["mean_delta_rgb"] == [10.0, 0.0, 0.0]


def test_region_delta_refuses_a_box_outside_what_was_captured():
    origin = Rect(x=0, y=0, width=4, height=4)
    row = region_delta(screen(4, 4), screen(4, 4), 4, 4,
                       Rect(x=3, y=3, width=4, height=4), origin)
    assert row["ok"] is False
    assert row["reason"] == "box_outside_capture"


def test_the_colourkey_verdict_refuses_to_decide_when_the_control_is_flat():
    # Every "no change" is meaningless unless the OPAQUE white region moved:
    # a capture that cannot see the window reads identically to a window whose
    # every pixel was punched out.
    flat = {name: {"ok": True, "mean_delta_rgb": [0.0, 0.0, 0.0]}
            for name in ("r1_nothing", "r2_key", "r3_white",
                         "r4_white_half_alpha", "r5_video_transparent",
                         "r5_video_opaque", "r6_canvas_cleared",
                         "r6_canvas_opaque")}
    verdict = _colourkey_verdict(flat)
    assert verdict["decided"] is False
    assert "did not see the window" in verdict["reason"]


def test_the_colourkey_verdict_reads_a_punched_key_against_a_live_control():
    regions = {
        "r3_white": {"ok": True, "mean_delta_rgb": [2.4, 2.4, 2.4]},
        "r2_key": {"ok": True, "mean_delta_rgb": [0.0, 0.0, 0.0]},
        "r1_nothing": {"ok": True, "mean_delta_rgb": [0.0, 0.0, 0.0]},
        "r5_video_transparent": {"ok": True, "mean_delta_rgb": [0.0, 0.0, 0.0]},
        "r5_video_opaque": {"ok": True, "mean_delta_rgb": [2.4, 2.4, 2.4]},
        "r6_canvas_cleared": {"ok": True, "mean_delta_rgb": [0.0, 0.0, 0.0]},
        "r6_canvas_opaque": {"ok": True, "mean_delta_rgb": [2.4, 2.4, 2.4]},
        "r4_white_half_alpha": {"ok": True, "mean_delta_rgb": [1.2, 1.2, 1.2]},
    }
    verdict = _colourkey_verdict(regions)
    assert verdict["decided"] is True
    assert verdict["key_punched"] is True
    assert verdict["video_alpha_punched"] is True
    assert verdict["video_opaque_shows"] is True
    assert verdict["half_alpha_delta"] == 1.2


def test_a_title_prefix_picks_which_rig_page_is_being_read():
    line = "RK t=12 vrs=4 canvas=tainted va_o=255 va_t=0 dpr=1.5"
    assert parse_tick_title(line) is None            # not the counting page
    parsed = parse_tick_title(line, prefix="RK ")
    assert parsed["vrs"] == 4
    assert parsed["canvas"] == "tainted"
    assert parsed["va_t"] == 0


def test_still_block_finds_the_one_corner_that_is_not_moving():
    import numpy as np

    moved = np.ones((40, 60), dtype=bool)
    moved[0:12, 0:12] = False          # the only still corner
    brightness = np.full((40, 60), 200, dtype=np.uint8)
    assert still_block(moved, brightness, 10, 10, step=2) == (0, 0)


def test_still_block_prefers_the_darker_of_two_still_corners():
    import numpy as np

    moved = np.ones((40, 60), dtype=bool)
    moved[0:12, 0:12] = False
    moved[0:12, 40:52] = False
    brightness = np.full((40, 60), 255, dtype=np.uint8)
    brightness[0:12, 40:52] = 10       # the dark one has the headroom
    assert still_block(moved, brightness, 10, 10, step=2) == (40, 0)


def test_still_block_gives_up_when_the_whole_desktop_is_moving():
    import numpy as np

    moved = np.ones((40, 60), dtype=bool)
    brightness = np.zeros((40, 60), dtype=np.uint8)
    assert still_block(moved, brightness, 10, 10, step=2) is None


def test_still_block_refuses_a_block_bigger_than_the_screen():
    import numpy as np

    moved = np.zeros((8, 8), dtype=bool)
    brightness = np.zeros((8, 8), dtype=np.uint8)
    assert still_block(moved, brightness, 20, 20, step=2) is None


def test_coverage_mask_rules_out_the_hole_between_two_screens():
    # The virtual screen is the bounding box of the monitors, so a desktop
    # whose screens are not aligned contains rectangles no monitor covers.
    # A hole is perfectly still and perfectly black, which is exactly what a
    # hunt for a quiet corner settles on -- and nothing composites there.
    origin = Rect(x=-100, y=-50, width=200, height=100)
    monitors = [Rect(x=-100, y=-50, width=100, height=50),
                Rect(x=0, y=0, width=100, height=50)]
    covered = coverage_mask((100, 200), origin, monitors)
    assert bool(covered[0, 0]) is True        # inside the first monitor
    assert bool(covered[50, 100]) is True     # inside the second
    assert bool(covered[0, 150]) is False     # the hole above the second
    assert bool(covered[90, 10]) is False     # the hole below the first


def test_the_show_ceiling_stays_invisible():
    # 3/255 is about 1.2 %. The number is load-bearing: it is the most this
    # rig will ever put on his screen, and a change to it is a change to that
    # promise.
    assert MAX_SHOW_ALPHA == 3

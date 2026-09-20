"""Where Mario's rectangle lands, driven through node.

`ui/ghost/mariobox.js` is import-free, so every number it produces can be read
without a browser. What is worth pinning here is the part a screenshot cannot
argue about: THREE PIXEL SPACES have to stay apart.

  the LIST's space   the picture the boxes were measured in
  the VIDEO's space  what `drawImage` cuts from
  the CANVAS's space where it lands

A cut-out that is beside Mario rather than on him is almost always two of
those mixed up, and it looks exactly like a bad detector.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MODULE = (REPO / "src" / "sm64_events" / "ui" / "ghost" / "mariobox.js").as_uri()

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")


def run_node(names: str, body: str):
    script = (f"import {{ {names} }} from {MODULE!r};\n"
              f"console.log(JSON.stringify({body}));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def contain(box_w, box_h, pic_w, pic_h):
    return run_node("containRect",
                    f"containRect({box_w}, {box_h}, {pic_w}, {pic_h})")


def draw(box, list_size, picture, rect, margin=None):
    args = (f"{json.dumps(box)}, {json.dumps(list_size)}, "
            f"{json.dumps(picture)}, {json.dumps(rect)}")
    if margin is not None:
        args += f", {margin}"
    return run_node("marioDraw", f"marioDraw({args})")


def grown(box, list_size, margin=None):
    args = f"{json.dumps(box)}, {json.dumps(list_size)}"
    if margin is not None:
        args += f", {margin}"
    return run_node("grownBox", f"grownBox({args})")


# -- the picture inside the window -------------------------------------------

def test_a_window_of_the_clips_own_shape_is_a_plain_scale():
    """Nothing is letterboxed when the aspects agree, which is the ordinary
    case: the ghost window sits on the game picture and both are 4:3."""
    assert contain(640, 480, 320, 240) == {"x": 0, "y": 0, "width": 640,
                                           "height": 480, "scale": 2}


def test_a_wider_window_centres_the_picture_and_leaves_the_sides_clear():
    """What `object-fit: contain` with `object-position: center` does, written
    out so it can be measured -- the `<video>` beside the canvas gets the same
    treatment from the browser, and the two have to register."""
    rect = contain(800, 400, 320, 240)
    assert rect["scale"] == pytest.approx(400 / 240)
    assert rect["height"] == 400 and rect["y"] == 0
    assert rect["width"] == pytest.approx(533.333, abs=0.01)
    assert rect["x"] == pytest.approx((800 - rect["width"]) / 2)


def test_a_taller_window_centres_it_the_other_way():
    rect = contain(320, 480, 320, 240)
    assert rect["scale"] == 1 and rect["x"] == 0
    assert rect["y"] == 120 and rect["height"] == 240


@pytest.mark.parametrize("args", [(0, 480, 320, 240), (640, 0, 320, 240),
                                  (640, 480, 0, 240), (640, 480, 320, 0)])
def test_a_picture_with_no_size_has_nowhere_to_land(args):
    """A `<video>` before `loadedmetadata` reports 0x0, and the page asks on
    every presented picture -- so "no answer" has to be expressible."""
    assert contain(*args) is None


# -- the margin around him ---------------------------------------------------

def test_the_margin_is_a_fraction_of_his_larger_side():
    """A detector's rectangle ends at his hat and his shoes, and a cut-out
    with nothing around it reads as a sticker. The margin follows MARIO --
    the box's larger side -- so a tall thin Mario and a wide diving one both
    get one in proportion to themselves."""
    assert grown([100, 60, 40, 60], {"width": 320, "height": 240}, 0.1) == {
        "x": 94, "y": 54, "width": 52, "height": 72}
    # The same box, wide instead of tall: the margin follows the 60 either way.
    assert grown([100, 60, 60, 40], {"width": 320, "height": 240}, 0.1) == {
        "x": 94, "y": 54, "width": 72, "height": 52}


def test_a_mario_at_the_edge_keeps_the_margin_he_can_have():
    """Clamped to the picture rather than shifted into it: the footage itself
    has nothing beyond its own edge, and sliding the box inward would move
    him."""
    assert grown([0, 0, 40, 60], {"width": 320, "height": 240}, 0.1) == {
        "x": 0, "y": 0, "width": 46, "height": 66}
    assert grown([280, 180, 40, 60], {"width": 320, "height": 240}, 0.1) == {
        "x": 274, "y": 174, "width": 46, "height": 66}


def test_a_picture_he_is_not_in_grows_nothing():
    assert grown(None, {"width": 320, "height": 240}) is None
    assert grown([10, 10, 0, 20], {"width": 320, "height": 240}) is None


# -- the three spaces, kept apart --------------------------------------------

def test_a_box_measured_on_the_same_decode_maps_straight_through():
    rect = contain(640, 480, 320, 240)
    answer = draw([100, 60, 40, 60], {"width": 320, "height": 240},
                  {"width": 320, "height": 240}, rect, 0.1)
    # source, in the video's pixels: the grown box unchanged
    assert (answer["sx"], answer["sy"]) == (94, 54)
    assert (answer["sw"], answer["sh"]) == (52, 72)
    # destination, doubled by the window and offset by nothing
    assert (answer["dx"], answer["dy"]) == (188, 108)
    assert (answer["dw"], answer["dh"]) == (104, 144)


def test_a_box_measured_on_another_decode_is_converted_rather_than_trusted():
    """THE ONE THAT GOES WRONG SILENTLY. SAM 2.1 ran on its own decode of the
    clip and nothing guarantees it matched the browser's. Reading the list's
    numbers as the video's would put the cut-out beside Mario, which looks
    exactly like a bad detector."""
    rect = contain(640, 480, 640, 480)
    answer = draw([100, 60, 40, 60], {"width": 320, "height": 240},
                  {"width": 640, "height": 480}, rect, 0.1)
    # the list's 94,54 in a picture twice the size
    assert (answer["sx"], answer["sy"]) == (188, 108)
    assert (answer["sw"], answer["sh"]) == (104, 144)
    # ...and on a window that is the video's own size, straight through
    assert (answer["dx"], answer["dy"]) == (188, 108)


def test_the_cut_out_lands_where_the_whole_picture_would_put_it():
    """The claim the Mario view rests on: both views are the SAME mapping and
    one of them stops drawing the scenery. Checked by putting a box at the
    picture's own corners and reading back the window's."""
    rect = contain(800, 400, 320, 240)
    whole = draw([0, 0, 320, 240], {"width": 320, "height": 240},
                 {"width": 320, "height": 240}, rect, 0)
    assert whole["dx"] == pytest.approx(rect["x"])
    assert whole["dy"] == pytest.approx(rect["y"])
    assert whole["dw"] == pytest.approx(rect["width"])
    assert whole["dh"] == pytest.approx(rect["height"])


def test_nothing_to_draw_answers_nothing():
    rect = contain(640, 480, 320, 240)
    size = {"width": 320, "height": 240}
    assert draw(None, size, size, rect) is None
    assert draw([1, 1, 10, 10], size, {"width": 0, "height": 0}, rect) is None
    assert draw([1, 1, 10, 10], size, size, None) is None


def test_the_shipped_margin_is_none_or_small():
    """He HAS now seen it, and asked for none: *"We should be as close to
    mario's extents as possible"* (2026-09-20). So the shipped value is zero,
    and what is pinned is only that it stays a small fraction -- the number
    itself is his to move, and a test that owned it would turn his next
    preference into a red build."""
    fraction = run_node("MARGIN_FRACTION", "MARGIN_FRACTION")
    assert 0 <= fraction < 0.25


def test_a_margin_still_grows_the_box_when_one_is_asked_for():
    """The mechanism outlives the shipped zero. Without this, setting the
    constant to 0 would leave every line that grows a box untested, and the
    next value he asks for would be the first thing to exercise it."""
    # A tenth of the larger side (60) is 6 on every side.
    grown = run_node("grownBox", "grownBox([100, 100, 40, 60], "
                     "{width: 320, height: 240}, 0.1)")
    assert grown == {"x": 94, "y": 94, "width": 52, "height": 72}, grown
    # And it keeps the margin it can have at an edge rather than running off
    # the picture, which is what the footage itself does.
    corner = run_node("grownBox", "grownBox([0, 0, 40, 60], "
                      "{width: 320, height: 240}, 0.5)")
    assert corner == {"x": 0, "y": 0, "width": 70, "height": 90}, corner


# -- can this page draw this ghost as Mario alone? ---------------------------

def usable(source, frame_callback=True, pictures=4):
    return run_node("usableBoxes",
                    f"usableBoxes({json.dumps(source)}, "
                    f"{{frameCallback: {json.dumps(frame_callback)}, "
                    f"pictures: {pictures}}})")


def a_source(boxes=4, **over):
    body = {"mario_boxes": {"width": 320, "height": 240,
                            "boxes": [[1, 2, 3, 4]] * boxes},
            "mario_boxes_reason": None}
    body.update(over)
    return body


def test_a_list_as_long_as_the_clip_can_be_drawn():
    assert usable(a_source()) == {"usable": True, "reason": None,
                                  "pictures": 4}


def test_an_engine_that_cannot_say_which_picture_is_on_screen_refuses():
    """THE ONE THAT WAS SILENT. The cut-out is drawn from the presented
    picture's index, so without `requestVideoFrameCallback` the page has
    nothing to key on -- and it used to report itself presentable and paint
    nothing, which is a window up over his game with nothing in it."""
    answer = usable(a_source(), frame_callback=False)
    assert answer["usable"] is False
    assert answer["reason"] == "no_frame_callback"
    # ...and it refuses on that ground FIRST, whatever else is wrong: an engine
    # that cannot answer cannot be talked round by a better list.
    assert usable(a_source(boxes=9), frame_callback=False)["reason"] \
        == "no_frame_callback"


def test_a_list_of_another_length_is_not_this_clips():
    assert usable(a_source(boxes=9))["reason"] == "box_list_count"
    assert usable(a_source(boxes=9))["usable"] is False


def test_no_list_carries_the_servers_own_reason():
    """The server looked for the file and knows why there is none; repeating
    its reasoning here would be a second opinion that drifts."""
    assert usable({"mario_boxes": None,
                   "mario_boxes_reason": "box_list_version"})["reason"] \
        == "box_list_version"
    assert usable({"mario_boxes": None})["reason"] == "no_box_list"
    assert usable({})["reason"] == "no_box_list"


@pytest.mark.parametrize("broken", [
    {"width": 0, "height": 240, "boxes": [[1, 2, 3, 4]] * 4},
    {"width": 320, "height": 0, "boxes": [[1, 2, 3, 4]] * 4},
    {"width": 320, "height": 240, "boxes": "four"},
])
def test_a_list_the_page_cannot_use_falls_back_rather_than_throwing(broken):
    """The server validates every one of these, so reaching the page means a
    build mismatch -- and a page that threw here would leave the overlay
    loading forever rather than showing the whole picture."""
    assert usable({"mario_boxes": broken})["usable"] is False

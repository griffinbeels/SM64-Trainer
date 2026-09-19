"""Where Mario is in every picture of a ghost's clip, and when to refuse it.

The box list is made OUTSIDE the app -- a hand-run SAM 2.1 session writes one
file beside the clip -- so nothing about it is guaranteed by the code that
reads it. It is re-validated on every read, like the clip's own sidecar: a
file that was fine when it was written is not fine now if the clip was
re-extracted, if the maker wrote a different count, or if a number is out of
the picture. Every refusal is NAMED, because a ghost that silently has no
Mario view is indistinguishable from one whose boxes were quietly dropped.
"""
import json

import pytest

from sm64_events.ghost.mariobox import (BOX_LIST_VERSION, REASONS,
                                        box_list_path, read_box_list)


#: A saved clip's real name, from his own `replays/` tree.
SAVED_NAME = "attempt_0573_whomps-fortress_shoot-into-the-wild-blue_0m11s43.mp4"


def clip_at(tmp_path, name=SAVED_NAME):
    path = tmp_path / name
    path.write_bytes(b"not really a clip")
    return path


def a_box_list(clip, *, pictures=4, width=1600, height=1200, boxes=None,
               version=BOX_LIST_VERSION, **extra):
    """One file in the frozen I6 shape, written where the reader looks."""
    body = {"version": version, "maker": "sam2.1 by hand",
            "clip": clip.name, "width": width, "height": height,
            "pictures": pictures,
            "boxes": [[10 + i, 20 + i, 60, 90] for i in range(pictures)]
            if boxes is None else boxes}
    body.update(extra)
    path = box_list_path(clip)
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


# -- where it lives ----------------------------------------------------------

def test_the_box_list_sits_beside_the_clip_under_the_clips_own_name(tmp_path):
    clip = clip_at(tmp_path)
    assert box_list_path(clip).parent == clip.parent
    assert box_list_path(clip).name == f"{clip.stem}.mario.json"
    assert box_list_path(clip).name.endswith(".mario.json")


def test_a_clip_whose_name_has_a_dot_in_it_still_finds_its_boxes(tmp_path):
    """Built from the whole stem rather than by replacing "the last suffix".
    Saved clips are named by the app and carry no dots of their own, but they
    are FILES in his replays tree -- he renames them, and a copy called
    `wf blue coins v2.mp4` would otherwise look for its boxes under
    `wf blue coins v2.mario.json`... which is right, while `v2.mp4` inside a
    name like `run.v2.mp4` is where `with_suffix` goes wrong."""
    clip = clip_at(tmp_path, name="whomps run.v2.mp4")
    a_box_list(clip, pictures=2, boxes=[[1, 2, 3, 4], None])
    assert box_list_path(clip).name == "whomps run.v2.mario.json"
    found, reason = read_box_list(clip, pictures=2)
    assert reason is None and len(found.boxes) == 2


# -- the ordinary answer -----------------------------------------------------

def test_a_good_list_reads_back_as_the_boxes_it_was_written_with(tmp_path):
    clip = clip_at(tmp_path)
    a_box_list(clip, pictures=3)
    found, reason = read_box_list(clip, pictures=3)
    assert reason is None
    assert (found.width, found.height) == (1600, 1200)
    assert found.boxes == ((10, 20, 60, 90), (11, 21, 60, 90), (12, 22, 60, 90))
    assert found.maker == "sam2.1 by hand"


def test_a_picture_without_mario_is_a_hole_rather_than_a_guess(tmp_path):
    """`null` means he is not in this picture -- a cutscene, a warp, a frame
    the maker could not answer for. The page paints nothing there; inventing
    the previous box would put a stale Mario on his screen."""
    clip = clip_at(tmp_path)
    a_box_list(clip, pictures=3,
               boxes=[[1, 2, 3, 4], None, [5, 6, 7, 8]])
    found, reason = read_box_list(clip, pictures=3)
    assert reason is None
    assert found.boxes == ((1, 2, 3, 4), None, (5, 6, 7, 8))


def test_the_payload_is_the_shape_the_page_reads(tmp_path):
    clip = clip_at(tmp_path)
    a_box_list(clip, pictures=2, boxes=[[1, 2, 3, 4], None])
    found, _ = read_box_list(clip, pictures=2)
    assert found.payload() == {"width": 1600, "height": 1200,
                               "maker": "sam2.1 by hand",
                               "boxes": [[1, 2, 3, 4], None]}


# -- every refusal, and it is named ------------------------------------------

def test_no_file_is_the_ordinary_case_and_says_so(tmp_path):
    """Most ghosts have no box list: they were recorded before this existed.
    That is not a fault, and the practice card says so in those words."""
    found, reason = read_box_list(clip_at(tmp_path), pictures=4)
    assert found is None
    assert reason == "no_box_list"


@pytest.mark.parametrize("body", [
    "{ not json",
    '"a string"',
    "[1, 2, 3]",
    '{"version": 1}',
])
def test_a_file_that_is_not_a_box_list_is_refused_rather_than_trusted(
        tmp_path, body):
    clip = clip_at(tmp_path)
    box_list_path(clip).write_text(body, encoding="utf-8")
    found, reason = read_box_list(clip, pictures=4)
    assert found is None and reason == "box_list_unreadable"


def test_a_list_from_another_version_of_the_format_is_refused(tmp_path):
    clip = clip_at(tmp_path)
    a_box_list(clip, version=BOX_LIST_VERSION + 1)
    found, reason = read_box_list(clip, pictures=4)
    assert found is None and reason == "box_list_version"


def test_a_list_that_does_not_index_like_the_clip_is_refused(tmp_path):
    """THE ONE THAT MATTERS MOST. The boxes index exactly like `picture_igt`
    and `frame_map` -- one entry per picture, same order. A list of another
    length would still draw something for every picture, silently a few
    pictures out, which is the failure nobody would recognise on screen."""
    clip = clip_at(tmp_path)
    a_box_list(clip, pictures=4)
    found, reason = read_box_list(clip, pictures=5)
    assert found is None and reason == "box_list_count"


def test_a_list_whose_own_count_disagrees_with_its_boxes_is_refused(tmp_path):
    clip = clip_at(tmp_path)
    a_box_list(clip, pictures=4, boxes=[[1, 2, 3, 4]] * 3)
    found, reason = read_box_list(clip, pictures=4)
    assert found is None and reason == "box_list_count"


@pytest.mark.parametrize("box", [
    [1, 2, 3],                  # not four numbers
    [1, 2, 3, 4, 5],
    [1.5, 2, 3, 4],             # not whole numbers
    ["1", 2, 3, 4],
    [True, 2, 3, 4],            # a bool is an int in Python and is not one here
    [-1, 2, 3, 4],              # outside the picture
    [1, -2, 3, 4],
    [1590, 2, 20, 4],           # runs off the right edge
    [1, 1190, 4, 20],           # off the bottom
    [1, 2, 0, 4],               # no width is not a box
    [1, 2, 4, 0],
])
def test_a_box_that_is_not_a_rectangle_in_the_picture_is_refused(tmp_path, box):
    clip = clip_at(tmp_path)
    a_box_list(clip, pictures=2, boxes=[[1, 2, 3, 4], box])
    found, reason = read_box_list(clip, pictures=2)
    assert found is None and reason == "box_list_boxes"


@pytest.mark.parametrize("size", [{"width": 0}, {"height": -3},
                                  {"width": "1600"}, {"height": 12.5}])
def test_a_list_that_does_not_say_what_picture_it_measured_is_refused(
        tmp_path, size):
    clip = clip_at(tmp_path)
    a_box_list(clip, pictures=2, **size)
    found, reason = read_box_list(clip, pictures=2)
    assert found is None and reason == "box_list_unreadable"


# -- what the player is told -------------------------------------------------

def test_every_reason_has_a_sentence_a_player_could_read():
    """The practice card shows this beside a control he cannot use, so it says
    what is missing rather than what failed."""
    assert set(REASONS) >= {"no_box_list", "box_list_unreadable",
                            "box_list_version", "box_list_count",
                            "box_list_boxes"}
    for reason, text in REASONS.items():
        assert text.endswith(".") and text[0].isupper(), reason
        for jargon in ("json", "null", "parse", "validate", "schema",
                       "index", "sidecar"):
            assert jargon not in text.lower(), f"{reason}: {text}"

# src/sm64_events/ghost/mariobox.py
"""WHERE MARIO IS IN EVERY PICTURE OF A GHOST'S CLIP.

His words, 2026-09-20: *"let's do tight rectangles on our existing ghosts,
let's try it."* A ghost is the whole recorded screen, and over the live game
two cameras' worth of scenery overlap into something he called *"extremely
hard to understand"*. A box per picture is what lets the overlay draw only the
part of the footage that is Mario.

**The boxes are made OUTSIDE the app.** A hand-run SAM 2.1 session writes one
file beside the clip; nothing in this repository produces it, and the clip
itself is never touched (his standing constraint: the whole-picture ghost and
the original footage both stay). So this module is a READER, and it trusts
nothing:

* the clip may have been re-extracted since the boxes were made, which changes
  how many pictures it holds;
* the maker may have written a different count from the list it shipped;
* a number may be outside the picture it claims to measure.

Every one of those is refused BY NAME rather than absorbed. A list that is a
few pictures out would still draw a box on every picture -- Mario, a moment
late, every time -- which is the failure nobody would recognise on screen; and
a ghost that silently lost its Mario view reads exactly like a ghost that never
had one. Re-validated on every read for the same reason the clip's own sidecar
is: being correct when it was written is not being correct now.

**The boxes index exactly like `picture_igt` and `frame_map`** -- one entry per
picture, in the clip's own order -- so nothing here does timing work. The page
looks up the picture it is actually showing and reads the box at that index.

MEASURED by the boxes worker, 2026-09-20 (evidence under the feature folder's
`evidence/mario_boxes/`; not reproducible from this repository): box k belongs
to the k-th PRESENTED picture, and its exact 90 kHz presentation tick matches
`media_clock.source_pts` for every picture of all three of his clips, with no
mismatches. `replay/service.py` stores those ticks as `round(t, 6)`, and the
rounded value and the browser's own `mediaTime` come out as the SAME double --
which is what makes the index the page derives for `picture_igt` the box's
index too.

THE LOOKUP ITSELF HAS NO TOLERANCE, so read that as a claim about equality and
not as licence. On the `frame_times` path `ui/frame.js::slotAtTime` takes the
last picture whose start is `<= seconds`: one microsecond under names the
picture BEFORE, which is a box one picture late on every frame. The page does
not rely on the equality holding -- it looks up with a tenth of a millisecond
of slack (`PICTURE_EPSILON` in `ui/ghost/index.html`, far under a picture's
33 ms), and its real-browser check runs on a clip carrying per-picture
`frame_times` so the boundary is exercised rather than rounded away.

One thing that reads like a count error and is not: a clip can DECODE more
frames than there are entries (0573 decodes 440 against 435). Those are a
keyframe lead-in with negative times that the clip's own edit list hides, and
browsers skip them -- `requestVideoFrameCallback` never reports one. The count
checked below is therefore the per-picture ARRAYS' length, never the decoder's.
"""
import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

log = logging.getLogger("sm64.ghost")

#: The format the frozen interface names. A file from another version is
#: refused rather than read hopefully: the whole point of the version is that
#: the next one may mean something different by the same keys.
BOX_LIST_VERSION = 1

#: Beside the clip, under the clip's WHOLE name. A saved clip carries the
#: attempt's time in its filename (`attempt_0573_wf 0'11"43.mp4`), so it has
#: dots of its own and `Path.with_suffix` would replace the wrong one.
BOX_LIST_SUFFIX = ".mario.json"

# One sentence per refusal, in the words a player would use: the practice card
# shows these beside a control he cannot use, so they say what is MISSING
# rather than what failed.
REASONS: dict[str, str] = {
    "no_box_list":
        "This run has not had Mario found in it yet, so only the whole "
        "picture can be shown.",
    "box_list_unreadable":
        "The file saying where Mario is in this run cannot be read, so only "
        "the whole picture can be shown.",
    "box_list_version":
        "The file saying where Mario is in this run was made by a different "
        "version of the trainer, so only the whole picture can be shown.",
    "box_list_count":
        "The file saying where Mario is does not match this recording "
        "picture for picture, so only the whole picture can be shown.",
    "box_list_boxes":
        "The file saying where Mario is puts him outside the picture, so "
        "only the whole picture can be shown.",
    "box_list_other_clip":
        "The file saying where Mario is was made for a different recording, "
        "so only the whole picture can be shown.",
}


@dataclass(frozen=True)
class MarioBoxList:
    """Mario's rectangle in every picture of one clip.

    `width` and `height` are the picture the boxes were MEASURED in, which is
    not necessarily the size the browser decodes -- the page scales between the
    two rather than assuming they agree. A `None` entry means Mario is not in
    that picture at all.
    """

    width: int
    height: int
    boxes: tuple[tuple[int, int, int, int] | None, ...]
    maker: str | None = None

    def payload(self) -> dict:
        """The JSON the page reads. Lists rather than tuples, because this
        goes out over the wire and comes back as JSON on the other side."""
        return {"width": self.width, "height": self.height,
                "maker": self.maker,
                "boxes": [list(box) if box else None for box in self.boxes]}


def box_list_path(clip: Path) -> Path:
    """The file this clip's boxes would live in, whether or not it exists."""
    return clip.parent / f"{clip.stem}{BOX_LIST_SUFFIX}"


def _whole(value) -> bool:
    """A whole number, and NOT a bool.

    `True` is an `int` in Python and `1.0` compares equal to `1`, so every
    check here goes through this rather than through `==`. Measured on a probe
    of this reader: `"version": true` and `"version": 1.0` both passed the
    first version, and a `True` coordinate would have put a box at x=1.
    """
    return isinstance(value, int) and not isinstance(value, bool)


def _checked_box(box, width: int, height: int):
    """One entry: Mario's rectangle, or None for a picture he is not in.

    A rectangle has to be four whole numbers, have some area, and lie inside
    the picture the list says it measured. Anything else is refused for the
    WHOLE list -- a single bad row means the maker's run does not describe this
    clip, and the honest answer is no boxes rather than most of them.
    """
    if box is None:
        return None, True
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None, False
    if not all(_whole(value) for value in box):
        return None, False
    x, y, w, h = box
    if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > width or y + h > height:
        return None, False
    return (x, y, w, h), True


def _parse(text: str, clip_name: str, pictures: int | None):
    """The whole check, over a string. Split out so the memo below has
    something pure to hold."""
    try:
        raw = json.loads(text)
    except ValueError:
        return None, "box_list_unreadable"
    except RecursionError:
        # A deeply nested file is malformed input, not a crash: `json` raises
        # this rather than a ValueError, and letting it out reached the ghost
        # session's broad except, which disabled the WHOLE ghost and told him
        # "the trainer is not holding recordings this run" -- a sentence about
        # something else entirely.
        log.debug("ghost mario boxes nested too deeply", exc_info=True)
        return None, "box_list_unreadable"
    if not isinstance(raw, dict):
        return None, "box_list_unreadable"
    version = raw.get("version")
    if not _whole(version) or version != BOX_LIST_VERSION:
        return None, "box_list_version"
    # THE FILE NAMES THE RECORDING IT WAS MADE FOR, so a list copied beside
    # another clip is refused rather than drawn over the wrong footage. Absent
    # is allowed: the name is a cross-check, not the identity (the identity is
    # where the file sits), and a maker that omits it is not wrong.
    named = raw.get("clip")
    if named is not None and named != clip_name:
        return None, "box_list_other_clip"
    width, height, boxes = raw.get("width"), raw.get("height"), raw.get("boxes")
    if not _whole(width) or not _whole(height) or width <= 0 or height <= 0:
        return None, "box_list_unreadable"
    if not isinstance(boxes, list):
        return None, "box_list_unreadable"
    declared = raw.get("pictures")
    if declared is not None and not _whole(declared):
        return None, "box_list_unreadable"
    if declared is not None and declared != len(boxes):
        return None, "box_list_count"
    if pictures is not None and len(boxes) != pictures:
        return None, "box_list_count"
    checked = []
    for box in boxes:
        value, ok = _checked_box(box, width, height)
        if not ok:
            return None, "box_list_boxes"
        checked.append(value)
    maker = raw.get("maker")
    return MarioBoxList(width=width, height=height, boxes=tuple(checked),
                        maker=maker if isinstance(maker, str) else None), None


@lru_cache(maxsize=8)
def _parsed(data: bytes, clip_name: str, pictures: int | None):
    """The parse, remembered against the file's own BYTES.

    The memo answers again only while the file on disk is byte-for-byte the
    one it parsed. A rewrite or a delete misses it, which keeps the promise this
    module opens with -- re-validated on every read -- while not re-parsing
    thousands of rectangles every four seconds. Reading the bytes is cheap; the
    parse is not. It was keyed on (mtime_ns, size) until 2026-09-23: a
    same-size rewrite inside one timestamp tick came back from the memo, which
    the rewrite test caught under a loaded merge check. A 1m44 segment is about 3,200 pictures and longer ones exist, and
    the ghost session re-resolves on a timer whether or not anything moved.

    Bounded at eight entries because the key is the content: without a bound,
    a file he rewrites in a loop would grow the memo forever. The value is a
    frozen dataclass over a tuple, so sharing one is safe.
    """
    return _parse(data.decode("utf-8-sig"), clip_name, pictures)


def read_box_list(clip: Path, *, pictures: int | None = None):
    """`(the list, None)` or `(None, a named reason)`.

    `pictures` is how many pictures the CLIP holds, taken from the view payload
    the ghost source already has. It is the check that matters: the boxes are
    an index into that clip, so a list of another length is not this clip's.
    Passing None skips only that comparison; the file's own declared count is
    still checked against the boxes it ships.

    Read as `utf-8-sig`: anything PowerShell wrote starts with a byte-order
    mark, and refusing a file for a BOM would be refusing it for how it was
    saved rather than for what it says.
    """
    path = box_list_path(clip)
    try:
        return _parsed(path.read_bytes(), clip.name, pictures)
    except FileNotFoundError:
        return None, "no_box_list"
    except (OSError, UnicodeDecodeError):
        log.debug("ghost mario boxes unreadable: %s", path, exc_info=True)
        return None, "box_list_unreadable"


__all__ = ["BOX_LIST_SUFFIX", "BOX_LIST_VERSION", "REASONS", "MarioBoxList",
           "box_list_path", "read_box_list"]

# src/sm64_events/inputs/runs.py
"""Runs, holes and the capture axis: the ONE derivation every reader shares.

Four places used to fold a track into runs, each with its own loop -- the
chunk store, the document, the timeline payload and the action row -- and two
of them re-derived the zero-based axis as well. They agreed by luck. The
document's copy did not handle a counter restart at all and wrote a gap row
that ran backwards. So the two ideas live here once, and a reader says only
what makes two frames THE SAME for its purpose.

A run is a stretch of CONSECUTIVE frame numbers whose frames compare equal
under the reader's own `same`. A hole (a skipped number) always starts a new
run, which is what lets a capture gap survive every round trip as a gap
rather than being silently interpolated across.

The capture axis is where a track's frames sit on a timeline. It is a
position in the CAPTURE, not the raw counter: the game's frame counter
restarts on a console reset, so a track spanning one contains a descending
number, and zero-basing on the first frame alone produces NEGATIVE offsets
(the fixture render read "-937 frames" before this existed, 2026-08-21).
Each backward step lays the next stretch of counter END TO END after the
last; holes inside a stretch stay holes. A reset is a seam in the recording,
not a jump backwards through it.
"""
import operator
from typing import Callable, NamedTuple

from sm64_events.inputs.frame import InputFrame

Same = Callable[[InputFrame, InputFrame], bool]


class Run(NamedTuple):
    start: int
    length: int
    frame: InputFrame      # the first frame of the run; every other is `same`

    @property
    def end(self) -> int:
        """One past the last frame number in the run."""
        return self.start + self.length


def same_state(frame: InputFrame, previous: InputFrame) -> bool:
    """Two frames that carry the same captured state -- the pad AND Mario.
    `pressed` is left out: it is a derived flag, not state. What the chunk
    store and the document both mean by "the same frame"."""
    return (frame.buttons == previous.buttons
            and frame.stick_x == previous.stick_x
            and frame.stick_y == previous.stick_y
            and frame.action == previous.action
            and frame.yaw == previous.yaw
            and frame.speed == previous.speed)


def collapse(frames: list[tuple[int, InputFrame]], same: Same = operator.eq,
             max_length: int | None = None) -> list[Run]:
    """Fold consecutive, `same` frames into runs; a skipped number starts one.

    `max_length` caps a run for a storage format with a small length field.
    """
    runs: list[Run] = []
    for number, frame in frames:
        if runs:
            last = runs[-1]
            if (number == last.end and same(frame, last.frame)
                    and (max_length is None or last.length < max_length)):
                runs[-1] = last._replace(length=last.length + 1)
                continue
        runs.append(Run(number, 1, frame))
    return runs


def stretches(frames: list[tuple[int, InputFrame]]
              ) -> list[tuple[int, int, int]]:
    """The axis's seams: one (axis_start, raw_start, length) per stretch of
    ascending counter, split at every restart. THE raw<->axis conversion
    fact -- the moment markers join through it, and the timeline payload
    ships it so a clip's frame_map (raw game frames) can land on the axis
    in the browser without a second copy of this rule."""
    axis = capture_axis(frames)
    out: list[list[int]] = []
    previous: int | None = None
    for (raw, _frame), (position, _same) in zip(frames, axis):
        if previous is None or raw < previous:
            out.append([position, raw, 0])
        current = out[-1]
        current[2] = raw - current[1] + 1
        previous = raw
    return [tuple(row) for row in out]


def axis_of(raw: int, seams: list[tuple[int, int, int]]) -> int | None:
    """A raw counter value's place on the axis, through `stretches`' seams;
    None when no stretch holds it (before the track, after it, or between
    two epochs). THE conversion -- the markers and the overlay both call
    this, and the browser runs the same rule off the shipped seams."""
    for axis_start, raw_start, length in seams:
        if raw_start <= raw < raw_start + length:
            return axis_start + (raw - raw_start)
    return None


def capture_axis(frames: list[tuple[int, InputFrame]]
                 ) -> list[tuple[int, InputFrame]]:
    """The same frames renumbered from zero along the capture."""
    if not frames:
        return []
    out: list[tuple[int, InputFrame]] = []
    offset = -frames[0][0]
    previous: int | None = None
    for number, frame in frames:
        if previous is not None and number < previous:
            offset = (out[-1][0] + 1) - number
        out.append((number + offset, frame))
        previous = number
    return out

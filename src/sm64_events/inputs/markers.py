# src/sm64_events/inputs/markers.py
"""Moments from the JOURNAL, joined onto an input track by frame.

His ask (round 32): *"other event data when they occur (e.g., grabbed a
bobomb, grabbed a pole, wall kicked on frame X, etc)"*. A wall kick is an
ACTION and already a span on Mario's row; a bob-omb pickup, a pole grab, a
switch press, an enemy defeated are MOMENTS the trainer already journals, at
the frame they happened. Nothing new is captured -- this is one join.

Which rows qualify and what each says are the RECORDER's own rules
(`tracking/eventlabel.py`: `is_step` and `label_event`), so the timeline
marks exactly the rows the recorder would list for that stretch of play,
in the same words. The only work here is placing a raw frame number on the
capture axis, which is not the raw counter: the counter restarts on a
console reset, so a track can hold the same number twice, and the axis lays
each stretch end to end (`inputs/runs.py`).
"""
from sm64_events.inputs.frame import InputFrame
from sm64_events.inputs.runs import capture_axis
from sm64_events.tracking.eventlabel import is_step, label_event


def _stretches(frames: list[tuple[int, InputFrame]]
               ) -> list[tuple[list[int], list[int]]]:
    """The track split at every counter restart: (raw numbers, axis
    positions) per stretch, both ascending within it."""
    axis = capture_axis(frames)
    out: list[tuple[list[int], list[int]]] = []
    previous: int | None = None
    for (raw, _frame), (position, _same) in zip(frames, axis):
        if previous is None or raw < previous:
            out.append(([], []))
        out[-1][0].append(raw)
        out[-1][1].append(position)
        previous = raw
    return out


def markers_of(rows, frames: list[tuple[int, InputFrame]],
               names: dict) -> list[dict]:
    """`rows` are the journal rows in the attempt's window, oldest first;
    `frames` the track in capture order; `names` the landmark catalogue.

    A moment inside a capture hole sits where it happened: the axis keeps
    holes as holes, so its position is its distance into the stretch. One
    before the track's first frame or after its last is not on the axis and
    is dropped: the track already ends at the grab, so what follows is not
    part of the run.

    Rows and stretches are both in time order, so a row that does not fit
    the current stretch is looked for in the later ones, never the earlier.
    """
    stretches = _stretches(frames)
    if not stretches:
        return []
    markers: list[dict] = []
    repeats: dict[str, int] = {}
    current = 0
    for row in rows:
        if not is_step(row) or row.frame is None:
            continue
        label = label_event(row, names)
        if label is None:
            continue
        placed = None
        for index in range(current, len(stretches)):
            raws, positions = stretches[index]
            if row.frame < raws[0] or row.frame > raws[-1]:
                continue
            placed = positions[0] + (row.frame - raws[0])
            current = index
            break
        if placed is None:
            continue
        repeat = repeats[label] = repeats.get(label, 0) + 1
        markers.append({"frame": placed, "type": row.type,
                        "label": label if repeat < 2 else f"{label} ({repeat})"})
    # By frame, so the drawer's row and its "last moment" lookup can both
    # assume order. Journal id order almost always agrees; a corrective row
    # journaled late is the case this guards.
    markers.sort(key=lambda marker: marker["frame"])
    return markers

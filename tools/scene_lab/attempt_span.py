"""Which recorded pictures make an attempt's scene (M4 B4, lab only): the span rule.

    mark = AttemptMark.of(attempt)                        # a completed tracking Attempt
    found = span(mark, spool.tasks(since), boundaries, spool.health())
    # Cut(first, last, end, why) | Waiting(first, reason) | Unavailable(reason)

Griffin, 2026-10-26: "The ghost scene should start when the attempt starts; it should end
during fade out." Read as: the attempt's first frame through the last picture of the fade-out
after its end, stopping before the next place or the next attempt appears.

First: the task whose global_timer (the tap's identity extra, gGlobalTimer when the game
dispatched the task) equals the attempt's anchor frame (the journal's frame for the anchor
that started its clock), at the occurrence nearest the attempt's start time: the timer
restarts at a console reset and a save state restores it, so one value can recur.
End: the task at the closing event's frame (closed_frame), the occurrence at or after the
first nearest the attempt's end time.
Last: the earliest of
  - the task before the end, when the closing event itself started what came next
    (a reset, a save state load, a console reset: CLOSED_BY_NEXT_START);
  - the task before the first one after the end in another level or area: the fade-out's
    final picture;
  - the task before the game clock stops counting by one after the end (a load or console
    reset the journal did not report);
  - the task before the next attempt's anchor (`boundaries`: anchors seen after the start);
  - CAP_FRAMES after the end, when nothing else comes;
  - the newest recorded task, once the recording has stopped.

Evidence, his BoB minute (tap-20261025-184022, measured 2026-10-26 from the tap's pictures and
Mario's action in the task regions): after the star grab at task 2105
(ACT_FALL_AFTER_STAR_GRAB, the dance from 2127) the exit's Mario-shaped iris closed to black
from task 2219 (mean picture brightness 124.6) to 2239 (0.0); castle inside appeared at task
2241, so that scene's last picture is task 2240, black, 135 frames (4.5 s) after the grab and
21 frames (0.7 s) of fade (the real renderer drew the cut's first, iris and last pictures). The painting entry faded to white
over 30 frames (2464: 131.7, 2494: 238.0) before the next area at 2496. The game clock counted
by exactly one across all 2609 tasks, level loads included. The cap leaves room for a midair
grab's fall and dance and a death's animation; an attempt whose end brings no load (he stands
still, or the practice ROM keeps him in the level) shows CAP_FRAMES after its end.

Limits: the journal's anchor frame and the tap's global_timer are both gGlobalTimer, but at
different moments of the game frame (the poller's read, the task's dispatch); the BoB minute
had no journal attempts, so a one-frame offset between them is untested until his live play.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

FPS = 30
CAP_FRAMES = 10 * FPS                 # a scene runs at most 10 s past its end
MAX_SCENE_FRAMES = 10 * 60 * FPS      # 10 minutes: a scene's disk and cut time stay bounded
MATCH_SECONDS = 3.0                   # how far a task's time may lie from the journal's for one frame
CLOSED_BY_NEXT_START = ("reset", "loaded", "hard_reset")

NO_ANCHOR = "This attempt has no recorded start frame, so it has no scene."
BEFORE_RECORDING = "The scene recording started after this attempt began."
NO_START = "The scene recording has no picture of this attempt's first frame."
NO_END = "The scene recording stopped before this attempt ended."
NO_PICTURES = "This attempt ended on the frame it started, so it has no pictures."
NOT_RECORDING = "The scene recording is off."


def utc(value) -> datetime:
    """A journal timestamp ("...Z" or "+00:00") or a datetime, as an aware datetime."""
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))


@dataclass(frozen=True)
class AttemptMark:
    """What the span rule reads of a completed attempt (tracking/projection.py Attempt)."""
    id: int
    anchor_frame: int | None
    closed_frame: int | None
    started_utc: datetime
    ended_utc: datetime
    outcome: str

    @classmethod
    def of(cls, attempt) -> AttemptMark:
        return cls(attempt.id, attempt.anchor_frame, getattr(attempt, "closed_frame", None),
                   utc(attempt.started_utc), utc(attempt.ended_utc), attempt.outcome)

    def as_json(self) -> dict:
        return {"id": self.id, "anchor_frame": self.anchor_frame, "closed_frame": self.closed_frame,
                "started_utc": self.started_utc.isoformat(), "ended_utc": self.ended_utc.isoformat(),
                "outcome": self.outcome}

    @classmethod
    def from_json(cls, data: dict) -> AttemptMark:
        return cls(int(data["id"]), data.get("anchor_frame"), data.get("closed_frame"),
                   utc(data["started_utc"]), utc(data["ended_utc"]), str(data["outcome"]))


@dataclass(frozen=True)
class Boundary:
    """An anchor seen on the event stream: where a next attempt's clock started."""
    frame: int
    utc: datetime


@dataclass(frozen=True)
class Cut:
    first: object                     # live_spool.TaskRef
    last: object
    end: object
    why: str                          # which rule ended it: "next start", "fade", "clock", "next attempt", "cap", "recording"

    @property
    def seconds(self) -> float:
        return round((self.last.utc - self.first.utc).total_seconds() + 1 / FPS, 1)


@dataclass(frozen=True)
class Waiting:
    first: object | None              # known once the start is recorded: a take may already be writing it
    reason: str
    through: object | None = None     # the newest task already known to lie inside the span, past its end


@dataclass(frozen=True)
class Unavailable:
    reason: str


def find(tasks: list, frame: int, when: datetime, after: int = -1):
    """The task showing game frame `frame` nearest `when` (within MATCH_SECONDS), at an index
    after `after`; None when the recording holds no such picture."""
    best = None
    for task in tasks:
        if task.global_timer == frame and task.index > after:
            gap = abs((task.utc - when).total_seconds())
            if gap <= MATCH_SECONDS and (best is None or gap < best[0]):
                best = (gap, task)
    return best and best[1]


def lost_pictures(tasks: list, health: dict, first: int, last: int) -> int:
    """Pictures the recording lost between task indexes first and last (inclusive): its holes,
    tasks missing from the list, and tasks whose display list the tap could not read whole."""
    lost = sum(max(0, min(last, b) - max(first, a) + 1) for a, b in health.get("holes") or ())
    inside = [task for task in tasks if first <= task.index <= last]
    gaps = sum(later.index - earlier.index - 1 for earlier, later in zip(inside, inside[1:], strict=False))
    return max(lost, gaps) + sum(bool(getattr(task, "refused", False)) for task in inside)


def span(mark: AttemptMark, tasks: list, boundaries: list[Boundary], health: dict):
    """The attempt's scene in the recording's task indexes (oldest first), or why there is none
    yet or none at all."""
    if mark.anchor_frame is None:
        return Unavailable(NO_ANCHOR)
    recording = bool(health.get("recording"))
    if not tasks:
        return Waiting(None, "Waiting for the scene recording.") if recording else \
            Unavailable(NOT_RECORDING if not health.get("reason") else f"{NOT_RECORDING} {health['reason']}")
    newest = tasks[-1]
    first = find(tasks, mark.anchor_frame, mark.started_utc)
    if first is None:
        if (tasks[0].utc - mark.started_utc).total_seconds() > MATCH_SECONDS:
            return Unavailable(BEFORE_RECORDING)
        if recording and (mark.started_utc - newest.utc).total_seconds() > -MATCH_SECONDS:
            return Waiting(None, "Waiting for the recording of this attempt's start.")
        return Unavailable(NO_START)
    closed = mark.anchor_frame if mark.closed_frame is None else mark.closed_frame
    end = find(tasks, closed, mark.ended_utc, first.index - 1) if mark.closed_frame is not None else next(
        (task for task in tasks if task.index >= first.index and task.utc >= mark.ended_utc), None)
    if end is None:
        if recording and (mark.ended_utc - newest.utc).total_seconds() > -MATCH_SECONDS:
            return Waiting(first, "Waiting for the recording of this attempt's end.")
        return Unavailable(NO_END)
    found = _last(mark, tasks, boundaries, first, end, recording)
    if isinstance(found, Waiting):
        return found
    last, why = found
    if last.index < first.index:
        return Unavailable(NO_PICTURES)
    lost = lost_pictures(tasks, health, first.index, last.index)
    if lost:
        return Unavailable(f"The scene recording lost {lost} picture{'s' if lost != 1 else ''} during this attempt.")
    if last.index - first.index + 1 > MAX_SCENE_FRAMES:
        return Unavailable(f"This attempt is longer than the {MAX_SCENE_FRAMES // FPS // 60} minutes a scene can hold.")
    return Cut(first, last, end, why)


def _last(mark: AttemptMark, tasks: list, boundaries: list[Boundary], first, end, recording: bool):
    """(last task, why) or Waiting: the earliest stop after the end that the recording holds."""
    by_index = {task.index: task for task in tasks}
    if mark.outcome in CLOSED_BY_NEXT_START:
        before = by_index.get(end.index - 1)
        return (before, "next start") if before is not None else (end, "next start")
    stops = []
    for boundary in boundaries:
        if boundary.utc <= mark.started_utc or (mark.ended_utc - boundary.utc).total_seconds() > MATCH_SECONDS:
            continue                                              # its own anchor, or one inside it
        task = find(tasks, boundary.frame, boundary.utc, end.index - 1)
        if task is not None and task.index > first.index:
            stops.append((task.index - 1, "next attempt"))
        elif task is None and recording and (boundary.utc - tasks[-1].utc).total_seconds() > -MATCH_SECONDS:
            return Waiting(first, "Waiting for the recording of the next attempt's start.")
    cap = end.index + CAP_FRAMES
    previous = end
    for task in tasks:
        if task.index <= end.index:
            continue
        if task.index > cap:
            stops.append((cap, "cap"))
            break
        if (task.level, task.area) != (end.level, end.area):
            stops.append((previous.index, "fade"))
            break
        if task.global_timer != previous.global_timer + 1:
            stops.append((previous.index, "clock"))
            break
        previous = task
        if task.index == cap:
            stops.append((cap, "cap"))
            break
    if not stops:
        if recording:
            return Waiting(first, "Waiting for the end of the fade-out.", previous)
        stops.append((tasks[-1].index, "recording"))
    index, why = min(stops)
    while index not in by_index and index > first.index:        # a hole: lost_pictures reports it
        index -= 1
    return by_index.get(index, end), why

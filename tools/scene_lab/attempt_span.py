"""Which recorded pictures make an attempt's scene (M4 B4, lab only): the span rule.

    mark = AttemptMark.of(attempt)                        # a completed tracking Attempt
    found = span(mark, spool.tasks(since), boundaries, spool.health(), marios)
    # Cut(first, last, end, why) | Waiting(first, reason, through, end) | Unavailable(reason)
    reader = MarioReader.of(source.identity)              # Mario in a task's scene state
    found = dance(end_index, {index: reader.read(regions, global_timer), ...})   # Dance(last, seen) | None

Griffin, 2026-10-26: "The ghost scene should start when the attempt starts; it should end
during fade out." Read as: the attempt's first frame through the last picture of the fade-out
after its end, stopping before the next place or the next attempt appears.

Griffin, 2026-10-27, on his Hazy Maze Cave star: "the last actual frame of the ghost should be
~622 or so but then mario remains frozen there for over 200 additional frames ... We don't
even need to store anything after the star dance, because from there, the user cannot take any
actions." So an end that Mario's star dance follows (a star, a key, a segment ending on one)
stops on the dance's last frame; every other end keeps the rule above.

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
  - the star dance's last frame (dance()), when Mario grabbed a star at the end;
  - CAP_FRAMES after the end, when nothing else comes;
  - the newest recorded task, once the recording has stopped.

The dance: Mario's action and actionTimer come from each task's scene state (the tap's task
regions hold gMarioStates; MarioReader), which only the take following the attempt reads, as
it writes each frame (`marios`, by task index). The end is a star grab when Mario is in a grab
or dance action (addresses.STAR_GRAB_ACTIONS: the midair fall, then the dance) at the end or
within DANCE_ENTRY_FRAMES after it. The dance's last frame is then the earliest of
  - the frame before the game stops: in a dance actionTimer counts by exactly one each game
    frame (n64decomp general_star_dance_handler), so a frame where it holds while the game
    frame moves on is his pause, the no-exit dance's save dialog stopping time at 80, or the
    exit warp itself: its fade begins at actionTimer 80 and runs 32 frames, and at 111, its
    last iris picture with Mario in it, the game stops updating him for the level change;
  - the frame before Mario's action leaves the grab and dance actions.
That is where a ghost's own clip ends its dance (ghost/archive.py dance_tail: the clock stops
at the exit warp or a pause; docs/ghost-native-runtime.md, the dance ends at actionTimer 111),
so a stage and an equal-time ghost end together. The fade-out stays in the scene, as he asked
in round 2; the frozen frames after it do not.
Until the take's states reach the dance's end (or an earlier stop), the span waits. Without a
take (`marios` None: a take cut from behind the recording) the rule above decides, and that
take stops itself at the dance's end as it reads it (attempt_cutter.Take).

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

Evidence for the dance, his three Hazy Maze Cave "WK Over Wall" stars (2026-10-27 17:00-17:02
UTC, attempts 505, 524, 553; their takes read frame by frame, measured 2026-10-27): each closed
at x-cam on the dance's first frame (actionTimer 0, ACT_STAR_DANCE_EXIT after
ACT_FALL_AFTER_STAR_GRAB), and the exit fade began 80 frames later (sDelayedWarpOp 17,
WARP_OP_STAR_EXIT, its timer 31 counting down). Then he paused: actionTimer and the warp's
timer stopped while the game frame ran on, at actionTimer 80 (attempt 505), 92 (553, his
"Frame 622 of 830", game frame 4790) and 100 (524, for 19 frames before his reset). With no
load after it, the old rule ran 505 and 553 to the cap: 841 and 830 shown frames, 220 and 208
of them frozen. Read again through MarioReader and dance() (Mario found on every frame of all
nine takes), each ends on its last frame before the pause: 505 at 621 shown frames (game frame
1396), 524 at 633 (2872, was 655), 553 at 622 (4790); his six resets keep their ends. His own
ghost clips of the three stars end on those same game frames (replays/native attempt_N.json
last_global_timer 1396, 2872, 4790). The BoB grab above, which he did not pause, ends at task
2238: actionTimer 111, the exit warp's last iris picture before the game stopped Mario (2239
and 2240 repeat 111 under the black iris).

Limits: the journal's anchor frame and the tap's global_timer are both gGlobalTimer, but at
different moments of the game frame (the poller's read, the task's dispatch); on his live play
(2026-10-27) every take began on its anchor's game frame, and each star's closing frame was its
dance's first (actionTimer 0).
"""
from __future__ import annotations

import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tap  # noqa: E402
# A close timed at x-cam may precede the first recorded grab or dance frame by this many.
from sm64_events.ghost.archive import DANCE_ENTRY_FRAMES  # noqa: E402
from sm64_events.memory.addresses import (  # noqa: E402
    MARIO_ACTION_OFF, MARIO_ACTION_TIMER_OFF, STAR_DANCE_ACTIONS, STAR_GRAB_ACTIONS)
from sm64_events.memory.layout import VERSIONS, layout_for  # noqa: E402

FPS = 30
CAP_FRAMES = 10 * FPS                 # a scene runs at most 10 s past its end
MAX_SCENE_FRAMES = 10 * 60 * FPS      # 10 minutes: a scene's disk and cut time stay bounded
MATCH_SECONDS = 3.0                   # how far a task's time may lie from the journal's for one frame
CLOSED_BY_NEXT_START = ("reset", "loaded", "hard_reset")
DANCING = "Waiting for the end of the star dance."

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
    end: object | None = None         # the closing event's task, once recorded (a take watches its dance)


@dataclass(frozen=True)
class Unavailable:
    reason: str


@dataclass(frozen=True)
class Mario:
    """Mario in one recorded task, as the dance rule reads him."""
    global_timer: int
    action: int
    timer: int                        # actionTimer


class MarioReader:
    """Mario's action and actionTimer in a task's scene state (its tap.Regions of kind "task",
    which copy gMarioStates with every task), at the layout of the recording's version."""

    def __init__(self, layout):
        mario = layout.mario_struct & 0x1FFFFFFF
        self.fields = ((mario + MARIO_ACTION_OFF, 4), (mario + MARIO_ACTION_TIMER_OFF, 2))

    @classmethod
    def of(cls, identity) -> MarioReader | None:
        """The reader for the version whose gGlobalTimer the recording's identity extras name
        ((name, physical address, bytes), as tap.identity_spec gives them); None for none."""
        timer = next((address for name, address, _size in identity if name == "global_timer"), None)
        for version in VERSIONS:
            layout = layout_for(version)
            if timer is not None and layout.global_timer & 0x1FFFFFFF == timer and layout.mario_struct:
                return cls(layout)
        return None

    def read(self, regions, global_timer: int | None) -> Mario | None:
        """Mario in this task, or None when its regions do not hold him."""
        if regions is None or global_timer is None:
            return None
        pieces, at = [], 0
        view = memoryview(regions.data)
        for begin, size in regions.spans:
            pieces.append((begin, view[at:at + size]))
            at += size
        values = [tap.word_value(pieces, address, size) for address, size in self.fields]
        return None if None in values else Mario(global_timer, *values)


@dataclass(frozen=True)
class Dance:
    """The star dance after an attempt's end, as far as the states read show it."""
    last: int | None                  # its last task index, once the states show it ended
    seen: int                         # the newest task index the states reach


def dance(end: int, marios: Mapping[int, Mario | None]) -> Dance | None:
    """The star dance that follows the end task `end`, from Mario in each task after it (by task
    index, in recording order; None where a task's state lacks him). None when no dance
    follows: Mario is in no grab or dance action from the end through DANCE_ENTRY_FRAMES after
    it. Dance(None, seen) while the states read so far reach no end of it."""
    danced, previous, seen = False, None, end - 1
    while seen + 1 in marios:
        seen += 1
        mario = marios[seen]
        if not danced:
            danced = mario is not None and mario.action in STAR_GRAB_ACTIONS
            if not danced and seen >= end + DANCE_ENTRY_FRAMES:
                return None
        if mario is None or not danced:
            continue
        if mario.action not in STAR_GRAB_ACTIONS:
            return Dance(seen - 1, seen)                        # the dance is over
        if (previous is not None and mario.action in STAR_DANCE_ACTIONS and mario.action == previous.action
                and mario.global_timer == previous.global_timer + 1 and mario.timer == previous.timer):
            return Dance(seen - 1, seen)                        # the game stopped: a pause, a dialog, the warp
        previous = mario
    return Dance(None, seen)


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


def span(mark: AttemptMark, tasks: list, boundaries: list[Boundary], health: dict,
         marios: Callable[[int], Mapping[int, Mario | None] | None] | None = None):
    """The attempt's scene in the recording's task indexes (oldest first), or why there is none
    yet or none at all. `marios(first index)`: Mario in each task the take following the
    attempt from that task has read (by task index), or None when no take follows it."""
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
    found = _last(mark, tasks, boundaries, first, end, recording, marios and marios(first.index))
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


def _last(mark: AttemptMark, tasks: list, boundaries: list[Boundary], first, end, recording: bool,
          states: Mapping[int, Mario | None] | None):
    """(last task, why) or Waiting: the earliest stop after the end that the recording holds."""
    by_index = {task.index: task for task in tasks}
    if mark.outcome in CLOSED_BY_NEXT_START:
        before = by_index.get(end.index - 1)
        return (before, "next start") if before is not None else (end, "next start")
    stops = _next_attempts(mark, tasks, boundaries, first, end, recording)
    if isinstance(stops, Waiting):
        return stops
    dancing = _dance_stop(None if states is None else dance(end.index, states), by_index, stops)
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
            return Waiting(first, DANCING if dancing is not None else "Waiting for the end of the fade-out.",
                           previous, end)
        stops.append((tasks[-1].index, "recording"))
    if dancing is not None and min(stops)[0] > dancing:
        return Waiting(first, DANCING, previous, end)            # the dance may end before that stop
    index, why = min(stops)
    while index not in by_index and index > first.index:        # a hole: lost_pictures reports it
        index -= 1
    return by_index.get(index, end), why


def _next_attempts(mark: AttemptMark, tasks: list, boundaries: list[Boundary], first, end, recording: bool):
    """[(the task before a next attempt's anchor, "next attempt")], or Waiting while an anchor
    after the end is not yet recorded."""
    stops = []
    for boundary in boundaries:
        if boundary.utc <= mark.started_utc or (mark.ended_utc - boundary.utc).total_seconds() > MATCH_SECONDS:
            continue                                              # its own anchor, or one inside it
        task = find(tasks, boundary.frame, boundary.utc, end.index - 1)
        if task is not None and task.index > first.index:
            stops.append((task.index - 1, "next attempt"))
        elif task is None and recording and (boundary.utc - tasks[-1].utc).total_seconds() > -MATCH_SECONDS:
            return Waiting(first, "Waiting for the recording of the next attempt's start.", end=end)
    return stops


def _dance_stop(star: Dance | None, by_index: dict, stops: list) -> int | None:
    """Adds the dance's last task to `stops` once the recording's tasks hold it. While the dance
    goes on past what the states show, the newest task index they reach: a later stop waits."""
    if star is None:
        return None
    if star.last is not None and star.last in by_index:
        stops.append((star.last, "dance"))
        return None
    return star.seen

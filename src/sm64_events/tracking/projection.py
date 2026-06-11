"""Pure attempt projection: journal events in -> attempts out.

Two-pass projection: cleared_ids() first, then the sequential Projector —
so a grab marked "mistake" never moves the practice target, which
retroactively re-attributes every later failure. Attempt ids are the
journal id of the attempt's first event: stable across rebuilds.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Attempt:
    id: int                    # journal id of the attempt's first event
    session_id: int
    course_id: int | None      # None = failure with no declared target yet
    star_id: int | None
    strat_tag: str | None
    anchor_type: str           # practice_reset | state_loaded | none
    anchor_frame: int | None
    outcome: str               # success | reset | hard_reset | abandoned
    outcome_detail: str | None
    igt_frames: int | None
    rta_frames: int | None
    started_utc: str
    ended_utc: str
    cleared: bool
    cleared_reason: str | None


ANCHOR_EVENT_TYPES = ("practice_reset", "state_loaded")


def cleared_ids(events) -> dict[int, str | None]:
    """attempt_id -> reason for attempts whose LAST clear/restore is a clear."""
    cleared: dict[int, str | None] = {}
    for ev in events:
        if ev.type == "attempt_cleared":
            cleared[int(ev.payload["attempt_id"])] = ev.payload.get("reason")
        elif ev.type == "attempt_restored":
            cleared.pop(int(ev.payload["attempt_id"]), None)
    return cleared


class Projector:
    """Sequential pass; feed() returns attempts CLOSED by that event."""

    def __init__(self, cleared: dict[int, str | None] | None = None):
        self._cleared = cleared if cleared is not None else {}
        self.target: tuple[int, int] | None = None
        self.strat_tag: str | None = None
        self._open = None  # EventRow of the open attempt's anchor

    def feed(self, ev) -> list[Attempt]:
        if ev.type in ANCHOR_EVENT_TYPES:
            closed = self._close_by_reset(ev)
            self._open = ev
            return closed
        if ev.type == "star_collected":
            return self._close_by_grab(ev)
        if ev.type == "game_reset":
            return self._close(ev, outcome="hard_reset", igt_frames=None)
        if ev.type == "session_started":
            return self._close(ev, outcome="abandoned", igt_frames=None)
        if ev.type == "target_set":
            self.target = (ev.payload["course_id"], ev.payload["star_id"])
            if "strat_tag" in ev.payload:
                self.strat_tag = ev.payload["strat_tag"]
            return []
        return []

    # -- closers -------------------------------------------------------------
    def _close_by_reset(self, ev) -> list[Attempt]:
        igt = ev.payload.get("igt_frames_before") if ev.type == "practice_reset" else None
        return self._close(ev, outcome="reset", igt_frames=igt)

    def _close_by_grab(self, ev) -> list[Attempt]:
        grabbed = (ev.payload["course_id"], ev.payload["star_id"])
        first = self._open if self._open is not None else ev
        attempt = self._build(
            first=first, close=ev, outcome="success",
            course_id=grabbed[0], star_id=grabbed[1],
            igt_frames=ev.payload.get("igt_frames"))
        self._open = None
        if not attempt.cleared:
            self.target = grabbed  # last VALID grab moves the practice target
        return [attempt]

    def _close(self, ev, outcome: str, igt_frames: int | None) -> list[Attempt]:
        if self._open is None:
            return []
        course_id, star_id = self.target if self.target else (None, None)
        attempt = self._build(first=self._open, close=ev, outcome=outcome,
                              course_id=course_id, star_id=star_id,
                              igt_frames=igt_frames)
        self._open = None
        return [attempt]

    def _build(self, first, close, outcome, course_id, star_id, igt_frames) -> Attempt:
        is_anchored = first.type in ANCHOR_EVENT_TYPES
        rta = (close.frame - first.frame
               if is_anchored and close.frame >= first.frame else None)
        return Attempt(
            id=first.id, session_id=first.session_id,
            course_id=course_id, star_id=star_id, strat_tag=self.strat_tag,
            anchor_type=first.type if is_anchored else "none",
            anchor_frame=first.frame if is_anchored else None,
            outcome=outcome, outcome_detail=None,
            igt_frames=igt_frames, rta_frames=rta,
            started_utc=first.wall_time_utc, ended_utc=close.wall_time_utc,
            cleared=first.id in self._cleared,
            cleared_reason=self._cleared.get(first.id))


def replay(events) -> tuple[list[Attempt], Projector]:
    proj = Projector(cleared_ids(events))
    attempts: list[Attempt] = []
    for ev in events:
        attempts.extend(proj.feed(ev))
    return attempts, proj


def project(events) -> list[Attempt]:
    return replay(events)[0]

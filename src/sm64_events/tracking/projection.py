"""Pure attempt projection: journal events in -> attempts out.

Two-pass projection: cleared_ids() first, then the sequential Projector —
so a grab marked "mistake" never moves the practice target, which
retroactively re-attributes every later failure. Attempt ids are the
journal id of the attempt's first event: stable across rebuilds.

Caveats (hard-won — keep these current):

1. Same-tick reset-race: when a practice_reset and star_collected land in
   the same poll tick (anchors run before star_grab), the reset opens a new
   attempt and the grab closes it with rta near 0 while igt carries the
   PRIOR attempt's reconstructed time (see star_grab.py docstring) — the
   row's two clocks legitimately disagree; consumers must prefer igt for
   such rows.

2. Clearing invariant: attempt_cleared/attempt_restored payloads carry
   Attempt.id, which is the journal id of the attempt's FIRST event — for
   an anchored success that is the ANCHOR's id, NOT the star_collected
   event's id. Clearing by the grab's journal id is a silent no-op.

3. Payload trust: event payloads come from our own detectors/service and
   are trusted; a KeyError on a required key (course_id/star_id) means a
   corrupt journal and should fail loud rather than skip rows.

4. Outcomes: success (star grabbed), reset (practice_reset closes open
   attempt), hard_reset (game_reset), abandoned (session_started or
   level_changed closes open attempt), death (Mario entered a death action).
   outcome_detail carries the death cause string; menu detail has no Phase 1
   producer (reserved).

5. Inactivity discard: reset-closures where mario_acted=False are dropped
   entirely — the player never acted, so the closed attempt is not a real
   attempt. The anchor still opens the next attempt. Old journals lack the
   key; default is True, so rebuilds are stable.
   Additionally, reset-closures with paused_frames_before >=
   PAUSE_DISCARD_FRAMES are dropped the same way: a long Usamune-menu pause
   immediately before the reset means the player went AFK and came back —
   discarded even when the attempt had real activity (user decision).

6. Strategy memory is PER STAR: strat_by_star[(course_id, star_id)] stores
   the last-set strategy for that star independently. Switching targets
   never leaks the previous star's strat. The strat_tag on an attempt is the
   attributed star's last-remembered strategy at close time.

7. Dust-trick attachment: rollout/jump events accumulate and attach to
   whichever attempt closes next (covers anchored AND grab-only attempts).
   Every boundary event (anchor, grab, death, game_reset, session_started,
   level_changed) zeroes the accumulators after its close runs, so counts
   never leak across attempts, idle gaps, or discarded no-op resets.

8. Rollout payload compat: journals written before the corrected timing
   model (no "landing_frames" key) counted visible slide frames as
   frames_late — there, frames_late == 1 IS the frame-perfect input
   (decomp-verified; see detectors/dust.py). _rollout_is_dustless()
   re-derives the classification on replay; new payloads are trusted.
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
    outcome: str               # success | reset | hard_reset | abandoned | death
    outcome_detail: str | None  # death cause; menu detail has no Phase 1 producer
    igt_frames: int | None
    rta_frames: int | None
    started_utc: str
    ended_utc: str
    cleared: bool
    cleared_reason: str | None
    rollouts_total: int = 0      # dust-trick sub-events during this attempt
    rollouts_dustless: int = 0
    jumps_total: int = 0         # chained double/triple jumps
    jumps_dustless: int = 0


ANCHOR_EVENT_TYPES = ("practice_reset", "state_loaded")

# AFK rule (spec 2026-06-11): a reset arriving after >=5 s of pause (the
# Usamune menu freezes IGT while gGlobalTimer keeps running) closes a run the
# player walked away from — that is AFK, not a practice reset. Discard applies
# even when the attempt had real activity before the pause (user decision).
PAUSE_DISCARD_FRAMES = 150  # 5 s x 30 fps

# Events that delimit attempts; each one zeroes the rollout accumulator
# after its close runs (see docstring caveat 7).
BOUNDARY_EVENT_TYPES = frozenset(ANCHOR_EVENT_TYPES) | {
    "star_collected", "death", "game_reset", "session_started",
    "level_changed"}


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
        self.strat_by_star: dict[tuple[int, int], str | None] = {}
        self._open = None  # EventRow of the open attempt's anchor
        self._rollouts_total = 0
        self._rollouts_dustless = 0
        self._jumps_total = 0
        self._jumps_dustless = 0

    @property
    def strat_tag(self) -> str | None:
        """The current target's remembered strategy (per-star memory)."""
        return self.strat_by_star.get(self.target) if self.target else None

    def feed(self, ev) -> list[Attempt]:
        closed = self._dispatch(ev)
        if ev.type in BOUNDARY_EVENT_TYPES:
            self._rollouts_total = self._rollouts_dustless = 0
            self._jumps_total = self._jumps_dustless = 0
        return closed

    def _dispatch(self, ev) -> list[Attempt]:
        if ev.type in ANCHOR_EVENT_TYPES:
            closed = self._close_by_reset(ev)
            self._open = ev
            return closed
        if ev.type == "star_collected":
            return self._close_by_grab(ev)
        if ev.type == "death":
            return self._close_by_death(ev)
        if ev.type == "game_reset":
            return self._close(ev, outcome="hard_reset", igt_frames=None)
        if ev.type == "session_started":
            return self._close(ev, outcome="abandoned", igt_frames=None)
        if ev.type == "level_changed":
            return self._close(ev, outcome="abandoned", igt_frames=None)
        if ev.type == "target_set":
            c, s = ev.payload["course_id"], ev.payload["star_id"]
            self.target = (c, s)
            if "strat_tag" in ev.payload:
                self.strat_by_star[(c, s)] = ev.payload["strat_tag"]
            return []
        if ev.type == "rollout":
            self._rollouts_total += 1
            if self._rollout_is_dustless(ev.payload):
                self._rollouts_dustless += 1
            return []
        if ev.type == "jump":
            self._jumps_total += 1
            if ev.payload.get("dustless"):
                self._jumps_dustless += 1
            return []
        return []

    @staticmethod
    def _rollout_is_dustless(p: dict) -> bool:
        """Compat shim (docstring caveat 8): old payloads lack
        landing_frames and misclassified the frame-perfect case —
        frames_late == 1 meant ONE visible slide frame, which is perfect."""
        if "landing_frames" in p:
            return bool(p.get("dustless"))
        return bool(p.get("dustless")) or p.get("frames_late") == 1

    # -- closers -------------------------------------------------------------
    def _close_by_reset(self, ev) -> list[Attempt]:
        if ev.payload.get("paused_frames_before", 0) >= PAUSE_DISCARD_FRAMES:
            # AFK: a long menu pause immediately before the reset — throw the
            # run out (old journals lack the key -> 0 -> kept). The anchor
            # still opens the next attempt.
            self._open = None
            return []
        if not ev.payload.get("mario_acted", True):
            # no-op reset spam: the player never acted, so the closed
            # attempt isn't a real attempt — drop it (anchor still opens
            # the next one). Old journals lack the key -> default True.
            self._open = None
            return []
        igt = ev.payload.get("igt_frames_before") if ev.type == "practice_reset" else None
        return self._close(ev, outcome="reset", igt_frames=igt)

    def _close_by_grab(self, ev) -> list[Attempt]:
        grabbed = (ev.payload["course_id"], ev.payload["star_id"])
        first = self._open if self._open is not None else ev
        strat = self.strat_by_star.get(grabbed)
        attempt = self._build(
            first=first, close=ev, outcome="success", outcome_detail=None,
            course_id=grabbed[0], star_id=grabbed[1],
            igt_frames=ev.payload.get("igt_frames"), strat=strat)
        self._open = None
        if not attempt.cleared:
            self.target = grabbed  # last VALID grab moves the practice target
        return [attempt]

    def _close_by_death(self, ev) -> list[Attempt]:
        # Deaths count even without an anchor (mirrors grab-only synthesis):
        # a death is always a meaningful failed attempt.
        first = self._open if self._open is not None else ev
        course_id, star_id = self.target if self.target else (None, None)
        strat = self.strat_by_star.get(self.target) if self.target else None
        attempt = self._build(
            first=first, close=ev, outcome="death",
            outcome_detail=ev.payload.get("cause"),
            course_id=course_id, star_id=star_id,
            igt_frames=ev.payload.get("igt_frames"), strat=strat)
        self._open = None
        return [attempt]

    def _close(self, ev, outcome: str, igt_frames: int | None) -> list[Attempt]:
        if self._open is None:
            return []
        course_id, star_id = self.target if self.target else (None, None)
        strat = self.strat_by_star.get(self.target) if self.target else None
        attempt = self._build(
            first=self._open, close=ev, outcome=outcome, outcome_detail=None,
            course_id=course_id, star_id=star_id,
            igt_frames=igt_frames, strat=strat)
        self._open = None
        return [attempt]

    def _build(self, first, close, outcome, outcome_detail, course_id, star_id,
               igt_frames, strat) -> Attempt:
        is_anchored = first.type in ANCHOR_EVENT_TYPES
        rta = (close.frame - first.frame
               if is_anchored and close.frame >= first.frame else None)
        return Attempt(
            id=first.id, session_id=first.session_id,
            course_id=course_id, star_id=star_id, strat_tag=strat,
            anchor_type=first.type if is_anchored else "none",
            anchor_frame=first.frame if is_anchored else None,
            outcome=outcome,
            outcome_detail=outcome_detail,
            igt_frames=igt_frames, rta_frames=rta,
            started_utc=first.wall_time_utc, ended_utc=close.wall_time_utc,
            cleared=first.id in self._cleared,
            cleared_reason=self._cleared.get(first.id),
            rollouts_total=self._rollouts_total,
            rollouts_dustless=self._rollouts_dustless,
            jumps_total=self._jumps_total,
            jumps_dustless=self._jumps_dustless)


def replay(events) -> tuple[list[Attempt], Projector]:
    proj = Projector(cleared_ids(events))
    attempts: list[Attempt] = []
    for ev in events:
        attempts.extend(proj.feed(ev))
    return attempts, proj


def project(events) -> list[Attempt]:
    return replay(events)[0]

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
   For attempts opened by an acted_tracking anchor the judgment is
   event-based (a mario_acted journal event during the attempt) and applies
   to EVERY non-success closure: reset, death, abandoned, hard_reset.
   Successes always count.

6. Strategy memory is PER STAR: strat_by_star[(course_id, star_id)] stores
   the last-set strategy for that star independently. Switching targets
   never leaks the previous star's strat. The strat_tag on an attempt is the
   attributed star's last-remembered strategy at close time. strat_set events
   write the same memory without moving the target.

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

9. Castle attempts: an attempt OPENED while the tracked level is a castle
   hub (CASTLE_LEVELS) is castle movement — discarded on every non-success
   closure, never attributed to a star. Judgment is at open time; the
   closing level_changed updates _level only AFTER its close runs, so exits
   are judged by the level the attempt lived in. _level starts None
   (unknown -> attribute) so pre-level-detector journals replay unchanged.

10. Tagged target identity: self.target is ("star", course_id, star_id) |
    ("segment", segment_id) | None. Journals written before segments carry
    target_set payloads WITHOUT a "kind" key — those replay as star targets,
    so historical journals rebuild unchanged. Star attribution/strat lookups
    go through _star_target(); a segment target attributes star-side
    failures to nothing (course/star None), mirroring no-target behavior.

11. Segment attempts: the SegmentEngine (tracking/segments.py) runs on the
    SAME feed, AFTER _dispatch — its MatchContext sees the post-event level
    plus the pre-event level captured before _dispatch. Engine-closed
    attempts get strat (strat_by_segment) and cleared state stamped here
    with the same first-event-id keying as star attempts (caveat 2: a
    segment attempt's id is its ARM event's journal id + the namespace
    offset, see segments.SEGMENT_ATTEMPT_OFFSET). A non-cleared segment
    success auto-follows the target exactly like a star grab does.
"""
from dataclasses import dataclass, replace

from sm64_events.memory.addresses import CASTLE_LEVELS
# one-way import: segments.py pulls Attempt lazily at call time, so this
# module-level import cannot cycle (see SegmentEngine.feed).
from sm64_events.tracking.segments import (
    SEGMENT_ATTEMPT_OFFSET, MatchContext, SegmentEngine)


@dataclass(frozen=True)
class Attempt:
    id: int                    # journal id of the attempt's first event
                               # (segment attempts: + namespace offset — caveat 11)
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
    segment_id: int | None = None  # set => segment attempt; course/star None


ANCHOR_EVENT_TYPES = ("practice_reset", "state_loaded")

# AFK rule (spec 2026-06-11): a reset/load arriving after >=5 s of pause (the
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

    def __init__(self, cleared: dict[int, str | None] | None = None,
                 segments: list | None = None):
        self._cleared = cleared if cleared is not None else {}
        # ("star", course_id, star_id) | ("segment", segment_id) | None
        self.target: tuple | None = None
        self.strat_by_star: dict[tuple[int, int], str | None] = {}
        self.strat_by_segment: dict[int, str | None] = {}
        self._segments = SegmentEngine(segments or [])
        self.segment_notices: list[dict] = []  # live-broadcast queue, drained by service
        self._num_stars: int | None = None
        self._open = None  # EventRow of the open attempt's anchor
        self._open_acted = False  # mario_acted seen since the last anchor; only meaningful while _open is set
        self._level: int | None = None   # gCurrLevelNum per level_changed; None = unknown (legacy journals)
        self._open_castle = False        # open attempt was OPENED in a castle hub level; only meaningful while _open is set (the open site re-arms it)
        self._rollouts_total = 0
        self._rollouts_dustless = 0
        self._jumps_total = 0
        self._jumps_dustless = 0

    @property
    def strat_tag(self) -> str | None:
        """The current target's remembered strategy (per-target memory)."""
        if self.target and self.target[0] == "star":
            return self.strat_by_star.get(self.target[1:])
        if self.target and self.target[0] == "segment":
            return self.strat_by_segment.get(self.target[1])
        return None

    def _star_target(self) -> tuple[int, int] | None:
        """(course_id, star_id) when the target is a star, else None —
        segment targets attribute star-side failures to nothing."""
        if self.target and self.target[0] == "star":
            return self.target[1], self.target[2]
        return None

    def feed(self, ev) -> list[Attempt]:
        prev_level = self._level  # _dispatch may move it (level_changed)
        closed = self._dispatch(ev)
        if ev.type == "star_collected" and "num_stars" in ev.payload:
            self._num_stars = ev.payload["num_stars"]
        elif ev.type == "game_reset":
            self._num_stars = None  # file can change at the title screen: unknown until the next grab
        seg_closed, self.segment_notices = self._segments.feed(
            ev, MatchContext(level=self._level, prev_level=prev_level,
                             num_stars=self._num_stars))
        for a in seg_closed:
            # same first-event-id cleared keying as _build (caveat 2/11)
            a = replace(a,
                        strat_tag=self.strat_by_segment.get(a.segment_id),
                        cleared=a.id in self._cleared,
                        cleared_reason=self._cleared.get(a.id))
            if a.outcome == "success" and not a.cleared:
                self.target = ("segment", a.segment_id)
            closed.append(a)
        if ev.type in BOUNDARY_EVENT_TYPES:
            self._rollouts_total = self._rollouts_dustless = 0
            self._jumps_total = self._jumps_dustless = 0
        return closed

    def _dispatch(self, ev) -> list[Attempt]:
        if ev.type in ANCHOR_EVENT_TYPES:
            closed = self._close_by_reset(ev)
            self._open = ev
            self._open_acted = False
            self._open_castle = self._level in CASTLE_LEVELS
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
            closed = self._close(ev, outcome="abandoned", igt_frames=None)
            self._level = ev.payload["to"]
            return closed
        if ev.type == "target_set":
            if ev.payload.get("kind") == "segment":
                self.target = ("segment", ev.payload["segment_id"])
            else:  # legacy payloads have no kind: star (caveat 10)
                c, s = ev.payload["course_id"], ev.payload["star_id"]
                self.target = ("star", c, s)
                if "strat_tag" in ev.payload:
                    self.strat_by_star[(c, s)] = ev.payload["strat_tag"]
            return []
        if ev.type == "strat_set":
            # per-target strategy memory write WITHOUT moving the target
            # (target_set is the only other writer); explicit null clears.
            if ev.payload.get("kind") == "segment":
                self.strat_by_segment[ev.payload["segment_id"]] = \
                    ev.payload.get("strat_tag")
            else:
                self.strat_by_star[(ev.payload["course_id"],
                                    ev.payload["star_id"])] \
                    = ev.payload.get("strat_tag")
            return []
        if ev.type == "mario_acted":
            self._open_acted = True
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

    def _unacted_open(self) -> bool:
        """No-behavior rule (spec §2): the open attempt came from an
        acted-tracking anchor and no mario_acted event arrived during it.
        Legacy anchors (no marker) never match — old journals keep their
        original semantics. Stale values while _open is None are harmless:
        the only open-assignment site re-arms the flag."""
        return (self._open is not None
                and self._open.payload.get("acted_tracking", False)
                and not self._open_acted)

    def _open_is_castle(self) -> bool:
        """Castle rule (addendum 2026-06-11): an attempt opened while Mario
        was in a castle hub level is castle movement, never a star attempt.
        _level is None until the first level_changed -> legacy journals
        (no level detector) replay unchanged."""
        return self._open is not None and self._open_castle

    # -- closers -------------------------------------------------------------
    def _close_by_reset(self, ev) -> list[Attempt]:
        if ev.payload.get("paused_frames_before", 0) >= PAUSE_DISCARD_FRAMES:
            # AFK: a long menu pause immediately before the reset — throw the
            # run out (old journals lack the key -> 0 -> kept). The anchor
            # still opens the next attempt.
            self._open = None
            return []
        if self._open_is_castle():
            # castle movement, not a star attempt (addendum): discard
            self._open = None
            return []
        if self._unacted_open() or not ev.payload.get("mario_acted", True):
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
            # last VALID grab moves the practice target
            self.target = ("star", *grabbed)
        return [attempt]

    def _close_by_death(self, ev) -> list[Attempt]:
        if self._unacted_open():
            self._open = None
            return []
        if self._open_is_castle():
            # castle movement, not a star attempt (addendum): discard
            self._open = None
            return []
        # Deaths count even without an anchor (mirrors grab-only synthesis):
        # a death is always a meaningful failed attempt.
        first = self._open if self._open is not None else ev
        star_tgt = self._star_target()
        course_id, star_id = star_tgt if star_tgt else (None, None)
        strat = self.strat_by_star.get(star_tgt) if star_tgt else None
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
        if self._unacted_open():
            self._open = None
            return []
        if self._open_is_castle():
            # castle movement, not a star attempt (addendum): discard
            self._open = None
            return []
        star_tgt = self._star_target()
        course_id, star_id = star_tgt if star_tgt else (None, None)
        strat = self.strat_by_star.get(star_tgt) if star_tgt else None
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


def replay(events, segments=None) -> tuple[list[Attempt], Projector]:
    proj = Projector(cleared_ids(events), segments=segments)
    attempts: list[Attempt] = []
    for ev in events:
        attempts.extend(proj.feed(ev))
    return attempts, proj


def project(events, segments=None) -> list[Attempt]:
    return replay(events, segments=segments)[0]


def journal_id(attempt_id: int) -> int:
    """Recency-comparable id across kinds: segment attempt ids carry a
    namespace offset (segments.SEGMENT_ATTEMPT_OFFSET); strip it."""
    return attempt_id % SEGMENT_ATTEMPT_OFFSET

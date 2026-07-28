"""Replay a candidate segment definition against real journal history and
report what it would have done (spec 2026-07-28-multi-step-segments, Task 7).

Pure. The whole module exists because tracking/projection.replay() is already
pure over journal events, which is the asset no other SM64 autosplitter has:
LiveSplit ASL split definitions can only be tested by running, live, and the
community's documented remedy for a wrong one is the Undo Split hotkey. We
have a persistent event journal and a matcher that is pure over it, so we can
answer "would this definition have worked" BEFORE it is saved.

This is also the instrument the rest of the feature is measured with — the
staleness constants in tracking/segments.py came out of it, and the
record-a-segment and split/merge flows (Task 8+) both preview through it
before they commit anything.

THE TRAP (read before touching a candidate's id): segment attempt ids are
`arm.jid + SEGMENT_ATTEMPT_OFFSET * d.id` (segments.py), and
SEGMENT_ATTEMPT_OFFSET is 10**10. A candidate left at the natural default
id=0 would therefore produce attempt ids IDENTICAL to raw journal ids —
exactly the namespace STAR attempts occupy (a star attempt's id IS the
journal id of its first event, projection.py caveat 2). Backtesting an
unsaved definition would silently collide with the user's star history.
`backtest()` therefore ALWAYS stamps its own id onto `candidate` before
replaying it, discarding whatever id the caller's draft object happened to
carry:

- No `current` (a brand-new definition with nothing to replace): stamped
  with CANDIDATE_ID, a sentinel nonzero and clear of every real definition id
  (1-69 as of this spec). Bound check: attempt ids are id * 10**10 and
  SQLite's INTEGER caps at ~9.22e18, so any id up to ~9.2e8 is safe from
  overflow; CANDIDATE_ID = 10**6 sits comfortably inside both bounds, with
  headroom for the corpus to grow by orders of magnitude before it matters
  again.
- `current` supplied (candidate is a proposed EDIT of an already-saved
  definition): the candidate runs under CURRENT's own real id instead, so
  attempts from both replays land in the SAME namespace — the diff reads as
  "these attempts changed" rather than "everything is new AND everything is
  gone" (`current` is real, persisted data, so its own id is already safe;
  only the not-yet-saved `candidate` ever needs a stand-in id at all).
"""
from dataclasses import dataclass, replace

from sm64_events.tracking.projection import Attempt, replay
from sm64_events.tracking.segments import SegmentDef

# See "THE TRAP" above. Nonzero, and clear of every real definition id so a
# candidate backtest can never be mistaken for (or collide with) saved data.
CANDIDATE_ID = 10 ** 6


@dataclass(frozen=True)
class BacktestReport:
    fires: int                     # non-cleared successes
    attempts: list[dict]           # every attempt this candidate would have
                                    # recorded, any outcome — see _describe()
    unclosed: list[dict]           # still armed when the journal ended --
                                    # {"frame", "progress", "total",
                                    #  "deadline_frame", "reason"}. NOT the
                                    # original brief's "level": armed_arms()
                                    # (the sanctioned source, Task 4) carries
                                    # no game-level field, and inventing one
                                    # would be a fabricated fact rather than a
                                    # documented deviation -- progress/total
                                    # are the real fields it does carry.
    pb_before: int | None           # fastest non-cleared success under
                                    # `current`; None when it wasn't supplied
    pb_after: int | None            # fastest non-cleared success under `candidate`
    gained: int                    # attempts the candidate has that current did not
    lost: int                      # attempts current has that the candidate does not


def _describe(a: Attempt) -> dict:
    """One recorded Attempt -> the plain dict a caller renders. Keyed on
    `anchor_frame` (when the arm ARMED) rather than a bare "frame" — Attempt
    has no such field, and a reader who hasn't checked would take "frame" to
    mean when the attempt fired, not when it started."""
    return {"id": a.id, "anchor_frame": a.anchor_frame,
            "rta_frames": a.rta_frames, "outcome": a.outcome,
            "outcome_detail": a.outcome_detail,
            "started_utc": a.started_utc, "ended_utc": a.ended_utc,
            "cleared": a.cleared, "cleared_reason": a.cleared_reason}


def _run(events, definition: SegmentDef, time_filters):
    """One replay scoped to a single definition: its attempts, its still-
    armed-at-the-end detail (if any), and its fastest non-cleared success --
    all off the SAME replay so the three can never disagree about what this
    definition would have done."""
    attempts, proj = replay(events, segments=[definition],
                            time_filters=time_filters)
    mine = [a for a in attempts if a.segment_id == definition.id]
    unclosed: list[dict] = []
    arm = proj.armed_arms().get(definition.id)
    if arm is not None:
        unclosed.append({
            "frame": arm["start_frame"], "progress": arm["progress"],
            "total": arm["total"], "deadline_frame": arm["deadline_frame"],
            "reason": "still armed when the journal ended"})
    pb = min((a.rta_frames for a in mine
             if a.outcome == "success" and not a.cleared
             and a.rta_frames is not None), default=None)
    return mine, unclosed, pb


def backtest(events, candidate: SegmentDef, current: SegmentDef | None = None,
             time_filters: dict | None = None) -> BacktestReport:
    """Replay `candidate` (and, if supplied, `current` — the definition it
    would replace) against `events` and report what each would have done.

    `time_filters` passes through to replay() unchanged and defaults to
    None — deliberately NO filtering invented here. `service._time_filters()`
    returns STAR validity bounds only (its own docstring: "Segment bounds
    ride the defs themselves"); a segment's close-phase min_time/max_time
    guards are already part of `candidate`/`current` by construction, and
    projection.py's `_auto_ignored` runs off those guards for every replay,
    including this one — so the fire count is already faithful without a
    second filter layered on top."""
    run_id = current.id if current is not None else CANDIDATE_ID
    candidate = replace(candidate, id=run_id)  # THE TRAP — see module docstring
    attempts, unclosed, pb_after = _run(events, candidate, time_filters)
    fires = sum(1 for a in attempts if a.outcome == "success" and not a.cleared)

    pb_before = gained = lost = None
    if current is not None:
        current_attempts, _, pb_before = _run(events, current, time_filters)
        mine_keys = {(a.started_utc, a.rta_frames) for a in attempts}
        current_keys = {(a.started_utc, a.rta_frames) for a in current_attempts}
        gained = len(mine_keys - current_keys)
        lost = len(current_keys - mine_keys)

    return BacktestReport(
        fires=fires, attempts=[_describe(a) for a in attempts],
        unclosed=unclosed, pb_before=pb_before, pb_after=pb_after,
        gained=gained or 0, lost=lost or 0)

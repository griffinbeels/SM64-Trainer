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

THE AMBIGUITY `BacktestReport.arms` closes: `unclosed` is derived from
`proj.armed_arms()`, the projector's END-STATE armed set — at most one entry,
"still armed when the journal ended". A candidate that armed fifty times and
was silently disarmed fifty times (an off-route detour, a misrouted waypoint,
a start trigger paired with an unfireable end) reports `unclosed: []` —
IDENTICAL, on `fires`/`unclosed` alone, to a candidate whose start trigger
never matched anything in the whole journal. Both read `fires=0, unclosed=[]`;
one names a broken END (or an unfireable pair), the other names a broken
START. `arms` is every `segment_armed` notice the candidate's engine emitted
across the replay (segments.py's two arm sites, both `notices.append(...)`
under the SAME "event": "segment_armed" key), collected through
`projection.replay()`'s `on_notices` hook rather than a second event loop —
see that function's docstring for why. `arms == 0` -> the start trigger is
wrong; `arms > 0, fires == 0` -> the pair is unfireable or the end is wrong;
`unclosed` non-empty is the third, already-solved case (still running).
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
    arms: int                       # how many times CANDIDATE'S start trigger
                                    # armed over the whole replay (every
                                    # `segment_armed` notice, projection.replay's
                                    # on_notices hook) — see module docstring
                                    # "THE AMBIGUITY". unclosed alone cannot
                                    # tell "never armed" apart from "armed and
                                    # was silently disarmed every single time":
                                    # both report unclosed=[] AND fires=0.
                                    # `current`'s arm count isn't tracked --
                                    # this field diagnoses the CANDIDATE, the
                                    # thing being tested.
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
    armed-at-the-end detail (if any), its arm count, and its fastest
    non-cleared success -- all off the SAME replay so the four can never
    disagree about what this definition would have done.

    Arm count: `replay()`'s `on_notices` hook (see its docstring) is called
    with `proj.segment_notices` after every fed event; `segment_notices` is
    the engine's raw notice list for THAT event only, mixing every armed def
    together (a backtest only ever replays one), so filtering on
    `definition.id` here is a formality, not a real disambiguation need --
    kept anyway so a future multi-def replay through this same hook can't
    silently over-count."""
    arms = 0

    def _count_arms(notices):
        nonlocal arms
        arms += sum(1 for n in notices if n["event"] == "segment_armed"
                    and n["segment_id"] == definition.id)

    attempts, proj = replay(events, segments=[definition],
                            time_filters=time_filters, on_notices=_count_arms)
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
    return mine, unclosed, arms, pb


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
    attempts, unclosed, arms, pb_after = _run(events, candidate, time_filters)
    fires = sum(1 for a in attempts if a.outcome == "success" and not a.cleared)

    pb_before = gained = lost = None
    if current is not None:
        current_attempts, _, _, pb_before = _run(events, current, time_filters)
        mine_keys = {(a.started_utc, a.rta_frames) for a in attempts}
        current_keys = {(a.started_utc, a.rta_frames) for a in current_attempts}
        gained = len(mine_keys - current_keys)
        lost = len(current_keys - mine_keys)

    return BacktestReport(
        fires=fires, attempts=[_describe(a) for a in attempts],
        unclosed=unclosed, arms=arms, pb_before=pb_before, pb_after=pb_after,
        gained=gained or 0, lost=lost or 0)

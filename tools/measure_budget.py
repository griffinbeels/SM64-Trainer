"""Measure the two loose-arm staleness constants (MIN_BUDGET_FRAMES,
BUDGET_FACTOR in tracking/segments.py) against the real event journal
(spec 2026-07-28-multi-step-segments, Task 9).

Those two constants bound how long a LOOSE arm may run before it is
presumed abandoned (segments.py::budget_frames). They shipped in Task 3 as
a deliberate STARTING POINT, not a measurement, with an explicit promise
that this tool would replay the real journal and rewrite them with the
distribution. This is that tool -- re-runnable by a human later, whenever
the journal has grown enough to be worth re-checking.

What it does:
1. Takes a read-only ONLINE backup of data/tracker.db via sqlite3's own
   backup API -- never a file copy, which can catch a torn WAL mid-write
   (the live db is WAL-mode and a server may hold it open right now).
2. Replays the whole journal with every seeded definition forced
   match_mode="loose" (the budget only applies to SegmentEngine._feed_loose
   -- the stored definitions are still whatever match_mode they were
   authored with, and this never writes back to the db).
3. Reports, per definition and overall, the count of timed successes and
   their frame/second distribution (min/median/p95/max).
4. Sweeps a small grid of candidate (MIN_BUDGET_FRAMES, BUDGET_FACTOR)
   pairs and reports how many of the REAL completions each pair would have
   silently expired (disarmed before they closed) -- by re-running the
   full replay with the module's constants monkeypatched, not by comparing
   a candidate budget against the recorded rta after the fact. The
   staleness check runs first in _feed_loose's precedence, so a tighter
   deadline can only turn a would-be success into a silent no-row disarm;
   it can never create one, so "expired" is always
   baseline_successes - candidate_successes for these pairs.

Usage:
    uv run python tools/measure_budget.py [db]
"""
import dataclasses
import math
import statistics
import sys
import sqlite3
import tempfile
from dataclasses import replace
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from sm64_events.storage.db import Database
from sm64_events.tracking import segments as segments_module
from sm64_events.tracking.projection import replay
from sm64_events.tracking.segments import SegmentDef

FRAME_RATE = 30  # SM64/Usamune runs at 30 fps (domain rule 7) -- frames -> seconds

# A representative sweep, not exhaustive. MIN_BUDGET_FRAMES dominates for
# every definition with no timed-success history (budget_frames returns the
# floor unconditionally -- see "definitions with no history" below);
# BUDGET_FACTOR only starts to bind once a definition's best success exceeds
# MIN_BUDGET_FRAMES / BUDGET_FACTOR frames.
CANDIDATE_MIN_FRAMES = [1800, 2700, 3600, 4500, 5400, 7200]
CANDIDATE_FACTORS = [3, 4, 6, 8]


def _online_backup(live_path: Path, backup_path: Path) -> None:
    """Read-only snapshot via sqlite3's OWN backup API. `?mode=ro` opens the
    SOURCE without ever writing to it (works correctly against a live
    WAL-mode db a server may currently hold open); the destination is a
    fresh throwaway file, so a partial/interrupted backup never corrupts
    anything a server depends on."""
    src = sqlite3.connect(f"{live_path.resolve().as_uri()}?mode=ro", uri=True)
    dest = sqlite3.connect(str(backup_path))
    try:
        src.backup(dest)
    finally:
        src.close()
        dest.close()


def _segment_defs_from(db: Database) -> list[SegmentDef]:
    # Same inclusion-list construction service.py/api.py use: SegmentDef's
    # own fields, not an exclusion of whatever extra columns the row carries
    # (seed_key, seed_dirty, default_strat, created_utc, ...).
    keys = [f.name for f in dataclasses.fields(SegmentDef)]
    return [SegmentDef(**{k: row[k] for k in keys}) for row in db.segment_defs()]


def _percentile(sorted_values: list[int], pct: float) -> int:
    """Nearest-rank percentile over an already-sorted list: the ceil(pct*n)th
    real data point, not an interpolated value between two of them -- these
    are frame counts of actual completions, so the p95 should BE one of
    them. (Also what the hand-count this tool was built to reproduce used;
    linear interpolation was tried first and landed a few frames off.)"""
    idx = math.ceil(pct * len(sorted_values)) - 1
    return sorted_values[max(0, min(idx, len(sorted_values) - 1))]


def _distribution(frames: list[int]) -> dict | None:
    if not frames:
        return None
    s = sorted(frames)
    return {"count": len(s), "min": s[0], "median": statistics.median(s),
            "p95": _percentile(s, 0.95), "max": s[-1]}


def _fmt_dist(dist: dict | None) -> str:
    if dist is None:
        return "no timed successes"

    def frame_and_secs(f):
        return f"{f:>7.1f}f ({f / FRAME_RATE:>6.1f}s)"
    return (f"n={dist['count']:<4} min={frame_and_secs(dist['min'])}"
            f"  median={frame_and_secs(dist['median'])}"
            f"  p95={frame_and_secs(dist['p95'])}"
            f"  max={frame_and_secs(dist['max'])}")


def _timed_segment_successes(attempts, segment_id: int | None = None) -> list[int]:
    """rta_frames of every SEGMENT-attempt success -- a completion that
    actually reached its end trigger with a real time, the population the
    budget must never clip. `segment_id=None` means "any segment", i.e.
    `a.segment_id is not None` -- deliberately excluding star attempts:
    replay() feeds every event through the SAME Projector, so passing
    `segments=` doesn't stop it from also tracking star history, and star
    attempts (segment_id is always None for those) would otherwise dwarf the
    segment population this tool exists to measure.

    Deliberately INCLUDES `cleared` successes (~4% of the corpus: 4 of 106
    forced-loose completions, all user-tagged "accidental" via the practice
    UI's Clear button -- projection.py's cleared/cleared_reason). Clearing is
    a retroactive, user-driven curation decision made AFTER the arm already
    closed -- it says nothing about whether the arm was still alive when it
    reached its end trigger. Excluding cleared rows here undercounted the
    real population by exactly 4 and is what first put this tool 4 short of
    the hand-measured 105/106 (confirmed by reproducing the hand count with
    cleared included, then again with it excluded).

    The SAME reasoning covers projection.py's OTHER clearing path, even
    though this journal happens not to exercise it today: an out-of-range
    success auto-clears with a "cleared_reason" starting "auto: " (below a
    segment's min_time or above its max_time -- `_auto_ignored`). That is
    also a classification applied AFTER the arm closed, not a claim that the
    arm was already stale when it reached its end trigger, so it belongs in
    this population for the same reason a manual clear does. As of this
    measurement zero of the 67 seeded definitions carry a min_time/max_time
    guard (confirmed by scanning every corpus file), so the auto-clear path
    cannot yet be responsible for any of the 4 -- but the day a definition
    gains one of those guards, some of ITS successes may start arriving here
    auto-cleared, and a re-run should expect that shift rather than read it
    as a regression in this tool."""
    return [a.rta_frames for a in attempts
            if a.outcome == "success" and a.rta_frames is not None
            and a.segment_id is not None
            and (segment_id is None or a.segment_id == segment_id)]


def _successes_under(events, loose_defs, min_frames: int, factor: int) -> int:
    """Re-run the WHOLE journal with these candidate constants swapped into
    the segments module. A monkeypatch, not a parameter: budget_frames() and
    every _deadline_for() call site (segments.py) read the module globals at
    call time, so mutating them here reaches every arm/re-arm site for the
    duration of one replay. Restored in `finally` so later calls (and the
    rest of this process) see the real, shipped constants again."""
    orig_min = segments_module.MIN_BUDGET_FRAMES
    orig_factor = segments_module.BUDGET_FACTOR
    segments_module.MIN_BUDGET_FRAMES = min_frames
    segments_module.BUDGET_FACTOR = factor
    try:
        attempts, _ = replay(events, segments=loose_defs)
    finally:
        segments_module.MIN_BUDGET_FRAMES = orig_min
        segments_module.BUDGET_FACTOR = orig_factor
    return len(_timed_segment_successes(attempts))


def main() -> None:
    db_arg = sys.argv[1] if len(sys.argv) > 1 else "data/tracker.db"
    live_path = Path(db_arg)
    if not live_path.exists():
        print(f"ERROR: database not found: {live_path}", file=sys.stderr)
        sys.exit(1)

    with tempfile.TemporaryDirectory(prefix="measure_budget_") as scratch:
        backup_path = Path(scratch) / "tracker_snapshot.db"
        _online_backup(live_path, backup_path)
        db = Database(backup_path)
        try:
            events = db.events()
            seeded_defs = _segment_defs_from(db)
        finally:
            db.close()

    loose_defs = [replace(d, match_mode="loose") for d in seeded_defs]

    print("=== Staleness budget measurement (tools/measure_budget.py) ===")
    if events:
        print(f"Journal: {live_path} -- {len(events)} events, "
              f"{events[0].wall_time_utc} .. {events[-1].wall_time_utc}")
    else:
        print(f"Journal: {live_path} -- 0 events")
    print(f"Definitions: {len(seeded_defs)}")
    print()

    seeded_attempts, _ = replay(events, segments=seeded_defs)
    loose_attempts, _ = replay(events, segments=loose_defs)
    seeded_successes = _timed_segment_successes(seeded_attempts)
    loose_successes = _timed_segment_successes(loose_attempts)

    print(f"Timed successes as SEEDED (each def's own stored match_mode): "
          f"{len(seeded_successes)}")
    print(f"Timed successes with EVERY definition forced loose: "
          f"{len(loose_successes)}")
    print("  (the budget applies only to loose arms -- everything below "
          "measures the forced-loose run)")
    print()
    print("Overall forced-loose distribution:")
    print(f"  {_fmt_dist(_distribution(loose_successes))}")
    print()

    print("Per-definition breakdown (forced loose):")
    print(f"{'id':>3}  {'name':<42}  distribution")
    with_history = 0
    with_five_plus = 0
    for d in sorted(seeded_defs, key=lambda d: d.id):
        dist = _distribution(_timed_segment_successes(loose_attempts, d.id))
        if dist is not None:
            with_history += 1
            if dist["count"] >= 5:
                with_five_plus += 1
        print(f"{d.id:>3}  {d.name[:42]:<42}  {_fmt_dist(dist)}")
    print()
    no_history = len(seeded_defs) - with_history
    print(f"{with_history}/{len(seeded_defs)} definitions have >=1 timed success "
          f"under loose matching")
    print(f"{with_five_plus}/{len(seeded_defs)} definitions have >=5 timed successes")
    print(f"{no_history}/{len(seeded_defs)} definitions have NO history at all -- "
          f"budget_frames() returns the FLOOR (MIN_BUDGET_FRAMES) for these "
          f"every time; BUDGET_FACTOR never binds for them")
    print()

    print("Candidate (MIN_BUDGET_FRAMES, BUDGET_FACTOR) grid -- 'expired' is how "
          "many of the real completions above this pair would have silently "
          "disarmed before they closed (re-running the full replay with the "
          "pair swapped in, not a post-hoc rta comparison):")
    baseline = len(loose_successes)
    print(f"{'MIN_FRAMES':>10}  {'FACTOR':>6}  {'successes':>9}  {'expired':>7}")
    for min_frames in CANDIDATE_MIN_FRAMES:
        for factor in CANDIDATE_FACTORS:
            successes = _successes_under(events, loose_defs, min_frames, factor)
            expired = baseline - successes
            flag = "  <- CLIPS a real completion" if expired > 0 else ""
            print(f"{min_frames:>10}  {factor:>6}  {successes:>9}  {expired:>7}{flag}")


if __name__ == "__main__":
    main()

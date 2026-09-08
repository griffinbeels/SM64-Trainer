"""THE attempts -> per-entity-score bridge for MARELO (spec section 4.6).

The only place that decides which of a user's times an entity is graded on:
per STRATEGY we take the active rank mode's basis (the SAVED pb row in pb
mode, the window's mean in avg modes), then the entity takes the BEST
strategy. Pooling attempts across strategies before averaging would conflate
different skills -- an Avg-10 mixing two strats measures neither.

pb mode reads the pbs table through `views.latest_pbs_by_strategy`, preserving
the original ROM of each saved slot. It used to take min() over raw attempts,
which paid MARELO out before the user clicked Save as PB (task 0034).

An entity with no gradeable time is ABSENT from the returned map, never zero:
scopes.aggregate() supplies the zero, because only it knows the denominator."""
from typing import Callable, Iterable

from sm64_events.ranks import curves, scopes, scoring
from sm64_events.ranks.calibration import resolve_curve
from sm64_events.ranks.classify import RANK_MODES, average_frames, display_cs
from sm64_events.ranks.curve_types import CompiledCurve
from sm64_events.ranks.standards import entity_key
from sm64_events.tracking.projection import Attempt
from sm64_events.tracking.views import latest_pbs_by_strategy


def _key_of(attempt: Attempt) -> str:
    return entity_key(attempt.course_id, attempt.star_id, attempt.segment_id)


def _frames_of(attempt: Attempt, clock: str) -> int | None:
    frames = attempt.igt_frames if clock == "igt" else attempt.rta_frames
    if frames is None or (clock == "rta" and frames == 0):
        return None          # rta==0 is reset-race junk (projection docstring)
    return frames


def entity_curves(ranks_store, keys: Iterable[str], version=None
                  ) -> dict[str, CompiledCurve]:
    """Resolved Overall curves for rankable keys, captured for one reading."""
    out = {}
    for key in keys:
        curve = resolve_curve(ranks_store, key, version)
        if curve["ladder_cs"] or curve["nodes"]:
            out[key] = curve
    return out


def entity_ladders(ranks_store, keys: Iterable[str], version=None
                   ) -> dict[str, dict[str, int]]:
    """Display cutoffs from Overall curves; never reconstruct scoring from them."""
    return {key: curve["ladder_cs"]
            for key, curve in entity_curves(ranks_store, keys, version).items()}


def classify_entity(ladder: dict[str, int], score: float | None,
                    n: int) -> dict:
    """{tier, division, next_tier, next_division, gain} for one score
    against one entity's own best-possible ladder (`ladder`, e.g. one value
    of `entity_ladders`'s result), `gain` diluted by the scope's `n` slots.

    `ranks.scopes.aggregate` only sees SCORES, not ladders, and grades tier/
    division/gain against the FULL rank table -- a ragged ladder (one
    missing a tier) still crosses that tier's score range, so a full-table
    lookup can name a tier the ladder does not define (`ranks/scoring.py`'s
    invariant, line 8). This recomputes per-entity against the entity's OWN
    ladder instead, which is why both `server/ranks_api.py::_score_scope`
    (the user's own MARELO breakdown) and `library/board.py` (a runner's)
    call through here rather than each carrying its own copy -- the same
    "a value two surfaces show gets one door" rule this project enforces
    everywhere else (`tests/test_single_source.py`'s "the entity breakdown
    shape" row).

    Unpracticed entities (`score is None`) target Gold with no division --
    the breakdown's "next rank" column names what a first practiced attempt
    would target (the same Gold anchor `gain` below already grades against),
    and there is nothing to be a division INTO yet."""
    defined = scoring.defined_tiers(ladder)
    if score is None:
        return {"tier": None, "division": None,
                "next_tier": scopes.UNPRACTICED_TARGET_TIER,
                "next_division": None,
                "gain": scopes.gain_for(None, n, defined)}
    tier, division = scoring.division_for(score, defined)
    next_step = scoring.division_progress(score, defined)
    return {"tier": tier, "division": division,
            "next_tier": next_step["next_tier"],
            "next_division": next_step["next_division"],
            "gain": scopes.gain_for(score, n, defined)}


def entity_scores(attempts: list[Attempt], ranks_store, keys: Iterable[str],
                  mode: str, pb_rows: Iterable[dict] = ()) -> dict[str, float]:
    """{entity_key: 0..100} for entities with a gradeable time. Keys with no
    time are omitted -- absent, not zero.

    Mode-split for the same reason `views.grading_basis` is: `pb` mode grades
    the SAVED pb row, avg modes grade a window of attempts. Passing attempts
    AND pb rows rather than one of them is what lets the caller stay ignorant
    of which mode is active."""
    wanted = set(keys)
    if not wanted:
        return {}
    mode_def = RANK_MODES.get(mode) or RANK_MODES["pb"]
    if mode_def["order"] is None:
        return _pb_scores(wanted, ranks_store, pb_rows)
    return _average_scores(attempts, wanted, ranks_store, mode_def)


def _pb_scores(wanted: set[str], ranks_store,
               pb_rows: Iterable[dict]) -> dict[str, float]:
    return {key: score for key, (_row, score) in
            best_scored_pbs(ranks_store, wanted, pb_rows).items()}


def best_scored_pbs(ranks_store, keys: Iterable[str], pb_rows: Iterable[dict]
                    ) -> dict[str, tuple[dict, float]]:
    """pb mode: grade the SAVED pb, never the fastest attempt.

    THE bug this exists for (task 0034, 2026-07-28): this path used to take
    `min()` over every successful attempt, so MARELO paid out the instant a
    fast run landed and never waited for Save as PB -- while
    `views.grading_basis`, the resolver this module's docstring points at,
    had always returned the saved row. Two doors; this was the wrong one.

    `latest_pbs_by_strategy` collapses the table to the latest row per
    (entity, clock, strategy, ROM) -- latest-row-wins, NOT fastest-wins, which
    is exactly what makes `undo_pb` (it deletes the row) take the points back
    with no code of its own. The entity then takes its BEST strategy, the same
    rule the average path uses."""
    wanted = set(keys)
    out: dict[str, tuple[dict, float]] = {}
    resolved = {}
    for row in latest_pbs_by_strategy(list(pb_rows)).values():
        if not row["strat_tag"]:
            continue
        key = entity_key(row["course_id"], row["star_id"], row["segment_id"])
        if key not in wanted or row["timer_mode"] != ranks_store.clock_for(key):
            continue
        # An IMPORTED time remembers the ROM that set it, and a JP time on a US
        # curve reads as superhuman. An absent curve for that ROM cannot fall
        # back to the running ROM; unversioned played rows use the current one.
        identity = (key, row.get("game_version"))
        if identity not in resolved:
            resolved[identity] = resolve_curve(ranks_store, *identity)
        curve = resolved[identity]
        progress = curves.progress_for_time(curve, display_cs(row["frames"]))
        score = progress["score"] if progress is not None else None
        if score is not None and (key not in out or score > out[key][1]):
            out[key] = (row, score)
    return out


def _average_scores(attempts: list[Attempt], wanted: set[str],
                    ranks_store, mode_def: dict) -> dict[str, float]:
    """avg modes: the window's mean per strategy, then the entity's best
    strategy. Pooling attempts across strategies before averaging would
    conflate different skills -- an Avg-10 mixing two strats measures neither.

    Unchanged by task 0034 on purpose: grading a window of ATTEMPTS is what an
    average mode is, and `grading_basis` records the same decision ("avg modes
    grade attempt history, so a run never saved as PB still counts"). Imported
    ROMs use separate windows; legacy attempts with no recorded ROM retain
    the current grading version rather than claiming historical context."""
    by_strat: dict[tuple, list[int]] = {}
    for event in successes_for(attempts, ranks_store.clock_for):
        if event["key"] in wanted:
            slot = (event["key"], event["strat"], event["game_version"],
                    event["timer_mode"])
            by_strat.setdefault(slot, []).append(event["frames"])

    out: dict[str, float] = {}
    for (key, _strat, version, _clock), frames in by_strat.items():
        basis = (average_frames(frames, mode_def["window"],
                                mode_def["order"]) or [None])[0]
        if basis is None:
            continue
        curve = resolve_curve(ranks_store, key, version)
        progress = curves.progress_for_time(curve, display_cs(basis))
        score = progress["score"] if progress is not None else None
        if score is not None and (key not in out or score > out[key]):
            out[key] = score
    return out


def successes_for(attempts: list[Attempt],
                  clock_of: Callable[[str], str]) -> list[dict]:
    """The chronological feed ranks.history.history_series consumes.
    `attempts` must already be journal-id ordered (db.attempts() is)."""
    feed = []
    for attempt in attempts:
        if attempt.outcome != "success" or attempt.cleared or not attempt.strat_tag:
            continue
        key = _key_of(attempt)
        clock = clock_of(key)
        frames = _frames_of(attempt, clock)
        if frames is None:
            continue
        feed.append({"utc": attempt.ended_utc, "key": key,
                     "strat": attempt.strat_tag, "frames": frames,
                     "timer_mode": clock,
                     "game_version": getattr(attempt, "game_version", None)})
    return feed


def pb_feed(pb_rows: list[dict], clock_of: Callable[[str], str]) -> list[dict]:
    """The chronological feed `ranks.history.history_series` consumes in PB
    mode -- one entry per SAVED pb, in save order.

    Same shape as `successes_for` so history_series needs no branch of its
    own. It is a different SOURCE, not a filtered version of that one: an
    undone save has had its row deleted, so it is simply absent here, which is
    what makes undoing a PB rewrite the curve as well as the rating (task
    0034: "If I ever undo a pb, those same marelo points should be taken
    away").

    `pb_rows` must be id-ordered, which `db.pbs()` is."""
    feed = []
    for row in pb_rows:
        if not row["strat_tag"]:
            continue
        key = entity_key(row["course_id"], row["star_id"], row["segment_id"])
        if row["timer_mode"] != clock_of(key):
            continue
        feed.append({"utc": row["saved_utc"], "key": key,
                     "strat": row["strat_tag"], "frames": row["frames"],
                     "timer_mode": row["timer_mode"],
                     "game_version": row.get("game_version")})
    return feed

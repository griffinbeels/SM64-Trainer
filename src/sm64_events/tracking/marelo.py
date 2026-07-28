"""THE attempts -> per-entity-score bridge for MARELO (spec section 4.6).

The only place that decides which of a user's times an entity is graded on:
per STRATEGY we take the active rank mode's basis (the SAVED pb row in pb
mode, the window's mean in avg modes), then the entity takes the BEST
strategy. Pooling attempts across strategies before averaging would conflate
different skills -- an Avg-10 mixing two strats measures neither.

pb mode reads the pbs table through `views.current_pbs_by_strat`, the same
door the per-entity resolver uses. It used to take min() over raw attempts,
which paid MARELO out before the user clicked Save as PB (task 0034).

An entity with no gradeable time is ABSENT from the returned map, never zero:
scopes.aggregate() supplies the zero, because only it knows the denominator."""
from typing import Callable, Iterable

from sm64_events.ranks import scoring
from sm64_events.ranks.classify import RANK_MODES, average_frames, display_cs
from sm64_events.ranks.standards import entity_key
from sm64_events.tracking.projection import Attempt
from sm64_events.tracking.views import current_pbs_by_strat


def _key_of(attempt: Attempt) -> str:
    return entity_key(attempt.course_id, attempt.star_id, attempt.segment_id)


def _frames_of(attempt: Attempt, clock: str) -> int | None:
    frames = attempt.igt_frames if clock == "igt" else attempt.rta_frames
    if frames is None or (clock == "rta" and frames == 0):
        return None          # rta==0 is reset-race junk (projection docstring)
    return frames


def entity_ladders(ranks_store, keys: Iterable[str]) -> dict[str, dict[str, int]]:
    """{entity_key: best-possible ladder in centiseconds} for the given keys."""
    out: dict[str, dict[str, int]] = {}
    for key in keys:
        ladder = scoring.best_ladder(ranks_store.ladders(key))
        if ladder:
            out[key] = ladder
    return out


def entity_scores(attempts: list[Attempt], ranks_store, keys: Iterable[str],
                  mode: str, pb_rows: Iterable[dict] = ()) -> dict[str, float]:
    """{entity_key: 0..100} for entities with a gradeable time. Keys with no
    time are omitted -- absent, not zero.

    Mode-split for the same reason `views.grading_basis` is: `pb` mode grades
    the SAVED pb row, avg modes grade a window of attempts. Passing attempts
    AND pb rows rather than one of them is what lets the caller stay ignorant
    of which mode is active."""
    ladders = entity_ladders(ranks_store, keys)
    if not ladders:
        return {}
    mode_def = RANK_MODES.get(mode) or RANK_MODES["pb"]
    if mode_def["order"] is None:
        return _pb_scores(ladders, ranks_store, pb_rows)
    return _average_scores(attempts, ladders, ranks_store, mode_def)


def _pb_scores(ladders: dict[str, dict[str, int]], ranks_store,
               pb_rows: Iterable[dict]) -> dict[str, float]:
    """pb mode: grade the SAVED pb, never the fastest attempt.

    THE bug this exists for (task 0034, 2026-07-28): this path used to take
    `min()` over every successful attempt, so MARELO paid out the instant a
    fast run landed and never waited for Save as PB -- while
    `views.grading_basis`, the resolver this module's docstring points at,
    had always returned the saved row. Two doors; this was the wrong one.

    `current_pbs_by_strat` has already collapsed the table to the latest row
    per (entity, clock, strategy) -- latest-row-wins, NOT fastest-wins, which
    is exactly what makes `undo_pb` (it deletes the row) take the points back
    with no code of its own. The entity then takes its BEST strategy, the same
    rule the average path uses."""
    out: dict[str, float] = {}
    for row in current_pbs_by_strat(list(pb_rows)).values():
        key = entity_key(row["course_id"], row["star_id"], row["segment_id"])
        ladder = ladders.get(key)
        if ladder is None or row["timer_mode"] != ranks_store.clock_for(key):
            continue
        score = scoring.score_for(ladder, display_cs(row["frames"]))
        if score is not None and (key not in out or score > out[key]):
            out[key] = score
    return out


def _average_scores(attempts: list[Attempt], ladders: dict[str, dict[str, int]],
                    ranks_store, mode_def: dict) -> dict[str, float]:
    """avg modes: the window's mean per strategy, then the entity's best
    strategy. Pooling attempts across strategies before averaging would
    conflate different skills -- an Avg-10 mixing two strats measures neither.

    Unchanged by task 0034 on purpose: grading a window of ATTEMPTS is what an
    average mode is, and `grading_basis` records the same decision ("avg modes
    grade attempt history, so a run never saved as PB still counts")."""
    wanted = set(ladders)
    by_strat: dict[tuple[str, str], list[int]] = {}
    for attempt in attempts:
        if attempt.outcome != "success" or attempt.cleared or not attempt.strat_tag:
            continue
        key = _key_of(attempt)
        if key not in wanted:
            continue
        frames = _frames_of(attempt, ranks_store.clock_for(key))
        if frames is not None:
            by_strat.setdefault((key, attempt.strat_tag), []).append(frames)

    out: dict[str, float] = {}
    for (key, _strat), frames in by_strat.items():
        basis = (average_frames(frames, mode_def["window"],
                                mode_def["order"]) or [None])[0]
        if basis is None:
            continue
        score = scoring.score_for(ladders[key], display_cs(basis))
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
        frames = _frames_of(attempt, clock_of(key))
        if frames is None:
            continue
        feed.append({"utc": attempt.ended_utc, "key": key,
                     "strat": attempt.strat_tag, "frames": frames})
    return feed

"""THE attempts -> per-entity-score bridge for MARELO (spec section 4.6).

The only place that decides which of a user's times an entity is graded on:
per STRATEGY we take the active rank mode's basis (PB row in pb mode, the
window's mean in avg modes), then the entity takes the BEST strategy. Pooling
attempts across strategies before averaging would conflate different skills --
an Avg-10 mixing two strats measures neither.

An entity with no gradeable time is ABSENT from the returned map, never zero:
scopes.aggregate() supplies the zero, because only it knows the denominator."""
from typing import Callable, Iterable

from sm64_events.ranks import scoring
from sm64_events.ranks.classify import RANK_MODES, average_frames, display_cs
from sm64_events.ranks.standards import entity_key
from sm64_events.tracking.projection import Attempt


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
                  mode: str) -> dict[str, float]:
    """{entity_key: 0..100} for entities with a gradeable time. Keys with no
    time are omitted -- absent, not zero."""
    ladders = entity_ladders(ranks_store, keys)
    if not ladders:
        return {}
    mode_def = RANK_MODES.get(mode) or RANK_MODES["pb"]
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
        basis = (min(frames) if mode_def["order"] is None
                 else (average_frames(frames, mode_def["window"],
                                      mode_def["order"]) or [None])[0])
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

"""MARELO over time, recomputed rather than stored (spec section 6).

Score at a moment is a function of the attempts up to it, so a scope's history
is a chronological replay: maintain each (entity, strategy)'s frame list, apply
the active rank mode's window to get that strategy's basis, take the best
strategy per entity, and re-aggregate after every success. No new storage, and
every scope gets its own curve for free.

Two consequences the UI must state rather than hide: history is recomputed
against CURRENT standards (a seed bump reshapes the past), and editing a route
or excluding an entity retroactively rewrites that scope's curve.

Pure: the caller injects `entity_scorer`, so this module never touches the
standards store."""
from typing import Callable

from sm64_events.ranks import scopes
from sm64_events.ranks.classify import RANK_MODES, average_frames


def history_series(successes: list[dict], groups: list[dict],
                   entity_scorer: Callable[[str, int], float | None],
                   mode: str, max_points: int = 300) -> list[dict]:
    """[{utc, marelo, tier, division, practiced}] in chronological order.

    `successes` is [{utc, key, strat, frames}] already in order; anything whose
    key is not in `groups` is ignored, so callers may pass the whole journal."""
    members = {candidate_key for group in groups
               for candidate_key in group["candidates"]}
    mode_def = RANK_MODES.get(mode) or RANK_MODES["pb"]
    frames_by_strat: dict[tuple[str, str], list[int]] = {}
    scores: dict[str, float] = {}
    points: list[dict] = []

    for success in successes:
        entity_key = success["key"]
        if entity_key not in members or success["frames"] is None:
            continue
        frames_by_strat.setdefault(
            (entity_key, success["strat"] or ""), []).append(success["frames"])
        best_score = None
        for (candidate_key, _strategy_name), frames in frames_by_strat.items():
            if candidate_key != entity_key:
                continue
            basis = _basis(frames, mode_def)
            if basis is None:
                continue
            score = entity_scorer(entity_key, basis)
            if score is not None and (best_score is None or score > best_score):
                best_score = score
        if best_score is None:
            continue
        scores[entity_key] = best_score
        rolled = scopes.aggregate(scores, groups)
        points.append({"utc": success["utc"], "marelo": rolled["marelo"],
                       "tier": rolled["tier"], "division": rolled["division"],
                       "practiced": rolled["practiced"]})
    return _decimate(points, max_points)


def _basis(frames: list[int], mode_def: dict) -> int | None:
    """The frame count this mode grades for one (entity, strategy)."""
    if mode_def["order"] is None:                      # pb mode: the best ever
        return min(frames) if frames else None
    averaged = average_frames(frames, mode_def["window"], mode_def["order"])
    if averaged is None:
        return None
    mean_frames, _run_count = averaged
    return mean_frames


def _decimate(points: list[dict], max_points: int) -> list[dict]:
    """Thin a long series for display, ALWAYS keeping the newest point -- the
    current rank must be the one the chart ends on."""
    if max_points <= 0 or len(points) <= max_points:
        return points
    stride = len(points) / (max_points - 1)
    kept = [points[int(sample_index * stride)]
            for sample_index in range(max_points - 1)]
    kept.append(points[-1])
    return kept

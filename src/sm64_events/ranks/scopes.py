"""Scopes and aggregation (spec section 3 and section 5).

A scope is a named SET of rankable entities, and all three kinds are derived --
there is no scope registry to maintain. Every route in the library, including
user-created ones, is therefore automatically a rating with its own history.

Entities are resolved into GROUPS ({"need": k, "candidates": [...]}) rather
than a flat list, because a route's K-of-N step must contribute k slots scored
by its best k candidates -- the same best-K convention tracking/routes.py
already uses for success rates.

Absent vs zero is load-bearing: an entity with no ladder is ABSENT (in neither
numerator nor denominator), while a rankable entity you have not practiced
scores ZERO. Pure: no db, no I/O."""
from typing import Iterable

from sm64_events.ranks import scoring

_UNPRACTICED_TARGET = scoring.SCORE_ANCHORS["Gold"]


def rankable_entities(ladders_by_entity: dict[str, dict[str, dict[str, float]]],
                       excluded: Iterable[str] = ()) -> list[str]:
    """Entity keys with at least one ladder, minus the user's exclusions.
    `ladders_by_entity` is {entity_key: {strat: {rank: seconds}}}."""
    excluded_keys = set(excluded or ())
    return [entity_key for entity_key, ladders in ladders_by_entity.items()
            if ladders and entity_key not in excluded_keys]


def _candidate_key(candidate: dict) -> str | None:
    if candidate.get("type") == "segment":
        return f"segment:{candidate['segment_id']}"
    if candidate.get("type") == "star":
        return f"star:{candidate['course']}:{candidate['star']}"
    return None


def entity_groups(scope_id: str, *, rankable: Iterable[str],
                   routes: list[dict], segment_courses: dict[int, int]
                   ) -> list[dict] | None:
    """Resolve a scope id into groups, or None if the scope does not exist."""
    rankable_keys = list(rankable)
    ranked = set(rankable_keys)
    if scope_id == "overall":
        return [{"need": 1, "candidates": [entity_key]}
                for entity_key in rankable_keys]

    kind, _, rest = scope_id.partition(":")
    if kind == "course" and rest.isdigit():
        course = int(rest)
        members = [entity_key for entity_key in rankable_keys
                   if (entity_key.startswith(f"star:{course}:")
                       or (entity_key.startswith("segment:")
                           and segment_courses.get(
                               int(entity_key.split(":")[1])) == course))]
        return [{"need": 1, "candidates": [entity_key]}
                for entity_key in members]

    if kind == "route" and rest.isdigit():
        route = next((route for route in routes if route["id"] == int(rest)),
                     None)
        if route is None:
            return None
        groups = []
        for step in route.get("steps", []):
            candidates = [candidate_key for candidate_key in
                          (_candidate_key(candidate)
                           for candidate in step.get("candidates", []))
                          if candidate_key in ranked]
            if not candidates:
                continue          # nothing rankable here -> the step is absent
            groups.append({"need": min(step.get("need", 1), len(candidates)),
                           "candidates": candidates})
        return groups
    return None


def scope_list(*, routes: list[dict], courses: dict[int, str]) -> list[dict]:
    """Every pickable scope, overall first, then routes, then courses."""
    out = [{"id": "overall", "label": "Overall", "kind": "overall"}]
    out += [{"id": f"route:{route['id']}", "label": route["name"],
             "kind": "route"} for route in routes]
    out += [{"id": f"course:{course_id}", "label": name, "kind": "course"}
            for course_id, name in sorted(courses.items())]
    return out


def aggregate(scores: dict[str, float], groups: list[dict]) -> dict:
    """MARELO for one scope. `scores` holds PRACTICED entities only; a member
    missing from it contributes 0 to the numerator and 1 to the denominator --
    that is the coverage penalty, and it is why MARELO == mastery * coverage."""
    total, slots, practiced, entities = 0.0, 0, 0, []
    for group in groups:
        rows = sorted(
            ((scores.get(candidate_key), candidate_key)
             for candidate_key in group["candidates"]),
            key=lambda row: -(row[0] if row[0] is not None else -1.0))
        for score, candidate_key in rows[:min(group["need"], len(rows))]:
            total += score or 0.0
            slots += 1
            if score is not None:
                practiced += 1
            entities.append({"key": candidate_key, "score": score})
    if slots == 0:
        return {"marelo": None, "mastery": None, "coverage": None,
                "tier": None, "division": None, "n": 0, "practiced": 0,
                "entities": []}
    marelo = total / slots
    tier, division = scoring.division_for(marelo)
    for entity in entities:
        entity["gain"] = gain_for(entity["score"], slots)
    next_division_at, division_progress = _division_progress(marelo)
    return {"marelo": marelo,
            "mastery": (total / practiced) if practiced else 0.0,
            "coverage": practiced / slots,
            "tier": tier, "division": division,
            "next_division_at": next_division_at,
            "division_progress": division_progress,
            "n": slots, "practiced": practiced, "entities": entities}


def _division_progress(marelo: float) -> tuple[float, float]:
    """(score the next division begins at, 0..1 depth through the current one).

    Computed here, not in the UI: only this side knows the band edges, and a
    second copy of the arithmetic in JS is a drift waiting to happen."""
    tier, _ = scoring.division_for(marelo)
    low, high = scoring.tier_band(tier)
    width = (high - low) / scoring.DIVISIONS_PER_TIER
    if width <= 0:
        return scoring.TOP_SCORE, 1.0
    division_step = int((marelo - low) / width)
    division_low = low + division_step * width
    return (min(scoring.TOP_SCORE, division_low + width),
            max(0.0, min(1.0, (marelo - division_low) / width)))


def gain_for(score: float | None, slot_count: int) -> float:
    """The MARELO that reaching this entity's next tier is worth. Unpracticed
    entities target Gold rather than Iron, so they read as the real quests they
    are; a top-tier entity targets 100 so it never drops off the list.
    `slot_count` is the scope's total slots -- how many ways this entity's
    improvement gets diluted when averaged into the scope's MARELO."""
    if slot_count <= 0:
        return 0.0
    if score is None:
        return _UNPRACTICED_TARGET / slot_count
    return (scoring.next_tier_target(score) - score) / slot_count


def celebration_delta(tier: str, numeral: str,
                       watermark: int | None) -> dict | None:
    """A rank-up worth celebrating, or None. Fires ONLY on a rise; a drop is
    handled by the caller lowering the watermark silently, so re-climbing
    celebrates again. A first-ever rank (watermark None) is not a rank-UP."""
    if watermark is None:
        return None
    current = scoring.progression_key(tier, numeral)
    if current <= watermark:
        return None
    was_tier, was_numeral = _from_key(watermark)
    return {"from": {"tier": was_tier, "division": was_numeral},
            "to": {"tier": tier, "division": numeral},
            "tiers_gained": scoring.RANK_NAMES.index(was_tier)
            - scoring.RANK_NAMES.index(tier),
            "key": current}


def _from_key(key: int) -> tuple[str, str]:
    tier_index, division_index = divmod(key, scoring.DIVISIONS_PER_TIER)
    tier = scoring.RANK_NAMES[len(scoring.RANK_NAMES) - 1 - tier_index]
    return tier, scoring.DIVISION_NUMERALS[division_index]

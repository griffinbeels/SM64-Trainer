"""Choose a tile's Library strategy once for both its goal and its link.

An entity's overall minimum can combine unrelated routes. It remains useful
for overall scoring, but cannot supply a goal for a particular practice row.
"""
from collections import defaultdict

from sm64_events.library.placements import row_identity
from sm64_events.memory.addresses import star_name
from sm64_events.ranks.scorecard import hundred_coin_companion


def _rows_by_entity(payload, assignments):
    rows = defaultdict(list)
    for target in payload.get("targets", []):
        for collection, kind in (("approaches", "approach"), ("subsections", "subsection")):
            for item in target.get(collection, []):
                identity = row_identity(target, item, kind, assignments)
                if identity:
                    entity, strategy = identity
                    rows[entity].append({**item, "strategy": strategy,
                                         "target_label": target.get("label", "")})
    return rows


def _named_rows(rows, strategy):
    # Same precedence as Library's approachesForStrategy: a historical alias
    # can lead to a row, but never becomes that row's grading identity.
    if strategy:
        for field in ("strategy", "name", "matched_strategy"):
            matches = [row for row in rows if row.get(field) == strategy]
            if matches:
                return matches
    return []


def _default_label(key, label):
    parts = key.split(":")
    if parts[0] == "star" and parts[-1] == "6":
        course = int(parts[1])
        return f"{star_name(course, hundred_coin_companion(course))} + 100c"
    return label


def _choose(rows, active, label, ladders):
    matches = _named_rows(rows, active)
    if not matches and active != "Standard" and active in ladders:
        return active                 # a local strategy absent from the Sheet
    candidates = matches or rows
    # Repeated legacy aliases can point at several 100c routes. Prefer the
    # card's named route; otherwise retain the Library's row order.
    named = [row for row in candidates if row["target_label"] == label]
    candidates = named or candidates
    title = next((row for row in candidates
                  if row["name"] == row["target_label"]), None)
    standard = next((row for row in candidates if row["strategy"] == "Standard"), None)
    chosen = title or standard or next(iter(candidates), None)
    if chosen:
        return chosen["strategy"]
    if "Standard" in ladders:
        return "Standard"
    return next(iter(ladders), None)


def tile_strategies(rows_spec, ranks, active, payload, assignments):
    """Entity -> canonical strategy, including unpracticed stars and segments."""
    candidates = _rows_by_entity(payload, assignments)
    result = {}
    for row in rows_spec:
        for key, label, _clock in row["entries"]:
            parts = key.split(":")
            strategy = (active.for_star(int(parts[1]), int(parts[2]))
                        if parts[0] == "star" else active.for_segment(int(parts[1])))
            result[key] = _choose(candidates[key], strategy, _default_label(key, label),
                                  ranks.ladders(key) if ranks is not None else {})
    return result

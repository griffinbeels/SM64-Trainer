"""Published facts through the same local Scorecard and Rank readers."""
from sm64_events.community.progress_snapshot import checked_snapshot, resolve_rows, unavailable_segment_keys
from sm64_events.ranks.scorecard import build_card, card_keys, without_keys
from sm64_events.server.ranks_api import _score_scope, _summary_scope_ids
from sm64_events.server.scorecard_resolve import GoalResolver
from sm64_events.server.scorecard_rows import scope_rows


def _scope_complete(rank, coverage, unavailable):
    """Only omissions affecting actual non-excluded members hide this rank."""
    if coverage["unsupported_rows"]:
        return False
    return not any(row["key"] in unavailable and not row["excluded"] for row in rank["entities"])


def _published_chips(service, rows, coverage, unavailable):
    """The familiar summary scopes, evaluated only from this player's PBs."""
    fields = ("scope_id", "label", "tier", "division", "marelo", "n", "practiced")
    chips = []
    for scope_id in _summary_scope_ids(service):
        result = _score_scope(service, scope_id, pb_rows=rows, mode="pb")
        if _scope_complete(result, coverage, unavailable):
            chips.append({key: result[key] for key in fields})
    return chips


def compare(service, content, scope_id):
    content = checked_snapshot(content)
    definitions = service.db.segment_defs()
    rows, unbound = resolve_rows(content, definitions)
    unavailable = unavailable_segment_keys(content,definitions,rows)
    spec = without_keys(scope_rows(service, scope_id), service.rank_excluded())
    resolver = GoalResolver(service)
    keys = card_keys(spec)
    regions = resolver.scorecard_regions(service.ranks)
    local_rows = service.db.pbs()
    you = resolver.your_times(keys, regions, pb_rows=local_rows)
    player = resolver.your_times(keys, regions, pb_rows=rows)
    card = build_card(spec, you=you, goal=player)
    scored = _score_scope(service, scope_id, pb_rows=rows, mode="pb")
    complete = _scope_complete(scored, content["coverage"], unavailable)
    player_rank = scored if complete else None
    default = service.ranks.grading_version
    return {"card": card, "you_rank": _score_scope(service, scope_id, pb_rows=local_rows, mode="pb"),
            "player_rank": player_rank, "chips": _published_chips(service, rows, content["coverage"], unavailable),
            "context": {"scope_id": scope_id, "regions": regions, "basis": "pb",
                        "calibration_revision": service.ranks.calibration_revision,
                        "default_rom": default, "publisher_default_rom": content["default_rom"],
                        "label": f"Your ranked scope and exclusions · Scorecard regions below · unstamped times evaluated as {default.upper()}"},
            "coverage": {**content["coverage"], "unbound_rows": unbound, "scope_complete": complete,
                         "comparable_cells": card["total"]["counted"], "total_cells": card["total"]["total"]},
            "rank_reason": None if complete else "Some PBs are private or unavailable here; a complete published rank cannot be shown."}

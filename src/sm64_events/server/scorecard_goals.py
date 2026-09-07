"""Scorecard selections: one rank, any players and named custom time sets.

The existing single/multi wire shapes remain readable. Automatic is stored
without a computed tier so it can follow the current scope alongside players.
"""
from fastapi import HTTPException

from sm64_events.core.timefmt import attainable_cs
from sm64_events.ranks.classify import RANK_NAMES
from sm64_events.ranks.scoring import DIVISION_NUMERALS


def valid_division(tier, division):
    return (tier in RANK_NAMES and division in DIVISION_NUMERALS
            and (tier, division) != ("Iron", "V"))


def sources_of(value):
    """Normalize legacy choices without writing during a read.

    The last rank wins; players and custom sets keep their order. An absent
    rank means Automatic, including old player-only and custom-only choices.
    """
    values = (value.get("sources", []) if value.get("kind") == "multi"
              else [value]) if isinstance(value, dict) else []
    rank, extras = {"kind": "automatic"}, []
    for source in values or []:
        if not isinstance(source, dict):
            continue
        kind = source.get("kind")
        if kind == "automatic":
            rank = source
        elif kind == "division" and valid_division(source.get("tier"), source.get("division")):
            rank = source
        elif kind in ("runner", "custom") and source not in extras:
            extras.append(source)
    return [rank, *extras]


def combined(sources):
    return sources[0] if len(sources) == 1 else {"kind": "multi", "sources": sources}


def prepare_goal(value, custom_store):
    """Validate the entire write before saving anything; return choice + sets.

    Custom times are patches to a named set: adding entries from another route
    must retain its earlier stars/segments. Only explicitly edited times enter
    a set, never a frozen copy of the automatic rank's other targets.
    """
    if value is None:
        return None, custom_store
    sources = (value.get("sources") or []) if value.get("kind") == "multi" else [value]
    store, cleaned = dict(custom_store), []
    for source in sources:
        if not isinstance(source, dict):
            raise HTTPException(422, "a goal source must be an object")
        kind = source.get("kind")
        if kind == "automatic":
            cleaned.append({"kind": "automatic"})
        elif kind == "division":
            if not valid_division(source.get("tier"), source.get("division")):
                raise HTTPException(422, "unknown tier/division")
            cleaned.append({"kind": kind, "tier": source["tier"], "division": source["division"]})
        elif kind == "runner":
            name = source.get("runner")
            if not isinstance(name, str) or not name.strip():
                raise HTTPException(422, "a runner goal needs a name")
            cleaned.append({"kind": kind, "runner": name})
        elif kind == "custom":
            name = source.get("name")
            if not isinstance(name, str) or not name.strip():
                raise HTTPException(422, "a custom goal needs a name")
            name = name.strip()
            times = source.get("times")
            if times is not None:
                if not isinstance(times, dict) or any(
                        not isinstance(cs, int) or isinstance(cs, bool) or cs <= 0
                        for cs in times.values()):
                    raise HTTPException(422, "custom times must be positive centiseconds")
                store[name] = {**store.get(name, {}),
                               **{key: attainable_cs(cs) for key, cs in times.items()}}
            elif name not in store:
                raise HTTPException(404, f"no saved custom goal named {name!r}")
            cleaned.append({"kind": kind, "name": name})
        else:
            raise HTTPException(422, f"unknown goal kind {kind!r}")
    normalized = sources_of({"kind": "multi", "sources": cleaned})
    choice = combined(normalized)
    return (None if choice == {"kind": "automatic"} else choice), store

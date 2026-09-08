"""One Sheet row's local entity and strategy, shared by every consumer.

Sheet segment numbers identify seeded movements, never local database ids.
A subsection can only resolve to its own segment, never its parent's clock.
"""
from sm64_events.library.audit import row_key
from sm64_events.library.mapping import segment_seed_key
from sm64_events.library.seed_targets import seed_for


def target_entity(target, definitions):
    """Resolve a whole target against this database's actual definitions."""
    from sm64_events.library.adoptions import auto_match
    entity = target.get("entity_key")
    if entity and entity.startswith("star:"):
        return entity
    if entity:
        seed = segment_seed_key(entity)
        return next((f"segment:{d['id']}" for d in definitions
                     if seed and d.get("seed_key") == seed), None)
    seed = seed_for(target)
    if seed:
        return next((f"segment:{d['id']}" for d in definitions
                     if d.get("seed_key") == seed), None)
    match = auto_match(target.get("label", ""),
                       [(d["id"], d["name"]) for d in definitions])
    return match["entity"] if match else None


def automatic_rows(payload, explicit, definitions):
    """Automatic links supplement explicit links, with exact parent matching.

    An explicit approach link establishes the whole target's parent when all
    linked approaches agree. Existing pieces match by name AND parent, so a
    repeated 'Door entry' in another course never receives this row's times.
    """
    from sm64_events.library.adoptions import _normalized
    out = dict(explicit)
    for target in payload.get("targets", []):
        linked = {explicit[key] for item in target["approaches"]
                  if (key := row_key(target, item["name"], item["ids"])) in explicit}
        parent = (next(iter(linked)) if len(linked) == 1
                  else target_entity(target, definitions))
        for item in target["approaches"]:
            if parent and not parent.startswith("star:"):
                out.setdefault(row_key(target, item["name"], item["ids"]), parent)
        for item in target["subsections"]:
            # Repeated Sheet names are distinct measured pieces (e.g. the
            # two Big Bob-omb Warp fadeouts). A raw name cannot choose one.
            if sum(_normalized(other["name"]) == _normalized(item["name"])
                   for other in target["subsections"]) != 1:
                continue
            matches = [d for d in definitions
                       if parent in (d.get("parents") or [])
                       and _normalized(d["name"]) == _normalized(item["name"])]
            if len(matches) == 1:
                out.setdefault(row_key(target, item["name"], item["ids"]),
                               f"segment:{matches[0]['id']}")
    return out


def row_identity(target, item, kind, rows):
    """Return (entity, strategy), or None when the row has no local home."""
    from sm64_events.library.adoptions import sheet_strategy
    key = row_key(target, item["name"], item["ids"])
    entity = rows.get(key)
    if entity is None and kind == "approach":
        entity = item.get("entity_key") or target.get("entity_key")
        # Local segment resolution is provided in rows, never guessed here.
        if entity and entity.startswith("segment:"):
            entity = None
    return (entity, sheet_strategy(target, item, kind)) if entity else None

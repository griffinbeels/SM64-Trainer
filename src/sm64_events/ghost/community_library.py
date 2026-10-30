"""Local bindings and rival capability for community Library read models."""
from sm64_events.ghost.sharing_targets import bind, binding_index, describe
from sm64_events.ghost.rivals import OWN_RECORDING_REASON


def definitions(tracker):
    database = getattr(tracker, "db", None)
    return database.segment_defs() if database is not None else []


def portable_filter(entity, tracker):
    """A local Library entity is never a portable foreign segment number."""
    if entity is None:
        return None
    if not isinstance(entity, str):
        raise ValueError("Choose a Library star or segment.")
    parts = entity.split(":")
    if len(parts) == 3 and parts[0] == "star":
        return describe("star", "-".join(parts[1:]), None, ())
    if len(parts) == 2 and parts[0] == "segment":
        return describe("segment", parts[1], None, definitions(tracker))
    raise ValueError("Choose a Library star or segment.")


def decorate_page(page, tracker, rivals, collection):
    """Decorate detached rows; the metadata lease and active roster stay owned."""
    rows = definitions(tracker)
    index = binding_index(rows)
    decorated = []
    for row in page[collection]:
        pins = {}
        portable = row["target"]
        key, reason = bind(portable, rows, index=index)
        target = ({"kind": portable["kind"], "entity_key": key, "strat": row["strategy"]}
                  if key is not None else None)
        if target is not None and rivals is not None:
            capability = rivals.saved_availability(target, version=row["game_version"],
                                                   expected_clock=row["clock"])
            reason = capability["reason"]
            pins = capability["pins"]
        elif target is not None:
            reason = "Saving ghosts is unavailable in this Trainer window."
        entity = (f"star:{key.replace('-', ':')}" if portable["kind"] == "star"
                  else f"segment:{key}") if key is not None else None
        saved = ({"saved_slot": next((int(slot) for slot, identifier in pins.items()
                                      if identifier == row["id"]), None)} if collection == "recordings" else {})
        owned = collection == "recordings" and rivals is not None and rivals.is_owned_recording(row)
        if owned:
            reason = OWN_RECORDING_REASON
        decorated.append({**row, **saved, "local_target": target, "entity_key": entity,
                          "can_add": target is not None and reason is None,
                          "unavailable_reason": reason, "is_own": owned})
    return {**page, collection: decorated, "available": True, "reason": None}

"""Bounded discovery pages, separate from the active practice rival lists."""
from sm64_events.ghost.sharing_targets import canonical, checked_target
from sm64_events.ghost.portable import checked_hash

from .cache import checked_metadata, target_key
from .config import CommunityError
from .outbox import checked_strategy

MAX_PAGE = 20


def checked_identifier(identifier):
    try:
        checked_hash(identifier)
    except (ValueError, TypeError):
        raise CommunityError("invalid_recording") from None
    return identifier


def checked_recording(value, identifier, generation):
    if not isinstance(value, dict) or type(value.get("v")) is not int or value["v"] != 1:
        raise CommunityError("invalid_response")
    row = checked_metadata({key: item for key, item in value.items() if key != "v"})
    if row["id"] != identifier:
        raise CommunityError("identity_mismatch")
    if row["generation"] != generation:
        raise CommunityError("generation_changed")
    return row


def checked_cursor(cursor):
    if cursor is not None and (not isinstance(cursor, str) or not 1 <= len(cursor) <= 2048):
        raise CommunityError("invalid_cursor")
    return cursor


def recordings_query(target, clock, game_version, strategy, cursor):
    try:
        target = checked_target(target)
        strategy = checked_strategy(strategy)
    except ValueError:
        raise CommunityError("unsupported_identity") from None
    if (clock not in ("igt", "rta") or game_version not in ("us", "jp")
            or target.get("strat") not in (None, strategy)):
        raise CommunityError("unsupported_identity")
    target.pop("strat", None)
    query = {"target": canonical(target).decode(), "clock": clock, "rom": game_version, "strategy": strategy}
    if checked_cursor(cursor) is not None:
        query["cursor"] = cursor
    return (*target_key(target), clock, game_version, "strategy", strategy), query


def summary_query(cursor, target, game_version):
    query = {} if checked_cursor(cursor) is None else {"cursor": cursor}
    key = None
    if target is not None:
        try:
            target = checked_target(target)
        except ValueError:
            raise CommunityError("unsupported_identity") from None
        target.pop("strat", None)
        query["target"] = canonical(target).decode()
        key = target_key(target)
    if game_version is not None:
        if game_version not in ("us", "jp"):
            raise CommunityError("unsupported_identity")
        query["rom"] = game_version
    return (key, game_version), query


def check_summary_scope(rows, scope):
    key, game_version = scope
    if any(key is not None and target_key(row["target"]) != key
           or game_version is not None and row["game_version"] != game_version for row in rows):
        raise CommunityError("identity_mismatch")


def checked_page(value, *, summary, generation):
    field = "entries" if summary else "recordings"
    if (not isinstance(value, dict) or set(value) != {"v", "generation", field, "next_cursor", "complete"}
            or type(value["v"]) is not int or value["v"] != 1
            or not isinstance(value["generation"], str) or not 1 <= len(value["generation"]) <= 128
            or not isinstance(value[field], list) or len(value[field]) > MAX_PAGE
            or type(value["complete"]) is not bool):
        raise CommunityError("invalid_response")
    try:
        checked_cursor(value["next_cursor"])
    except CommunityError:
        raise CommunityError("invalid_response") from None
    if value["complete"] != (value["next_cursor"] is None):
        raise CommunityError("invalid_response")
    if value["generation"] != generation:
        raise CommunityError("generation_changed")
    rows, identities = [], set()
    for item in value[field]:
        row = checked_summary(item) if summary else checked_metadata(item)
        key = (*target_key(row["target"]), row["clock"], row["game_version"], row["strategy"]) if summary else row["id"]
        if key in identities or not summary and row["generation"] != generation:
            raise CommunityError("invalid_response")
        identities.add(key)
        rows.append(row)
    return {"generation": generation, field: rows, "next_cursor": value["next_cursor"], "complete": value["complete"]}


def checked_summary(row):
    if (not isinstance(row, dict) or set(row) != {"target", "clock", "game_version", "strategy", "recordings", "players", "best_frames"}
            or row["clock"] not in ("igt", "rta") or row["game_version"] not in ("us", "jp")
            or any(type(row[key]) is not int or not 1 <= row[key] <= maximum for key, maximum in
                   (("recordings", 2147483647), ("players", 2147483647), ("best_frames", 2592000)))
            or row["players"] > row["recordings"]):
        raise CommunityError("invalid_response")
    try:
        target = checked_target(row["target"])
        strategy = checked_strategy(row["strategy"])
    except ValueError:
        raise CommunityError("invalid_response") from None
    if target.get("strat") not in (None, strategy):
        raise CommunityError("invalid_response")
    return {**row, "target": target, "strategy": strategy}


def checked_metadata_batch(value, identifiers, generation):
    """Validate the entire partition before a single visibility lease renews."""
    if (not isinstance(value, dict) or set(value) != {"v", "recordings", "unavailable"}
            or type(value["v"]) is not int or value["v"] != 1
            or not isinstance(value["recordings"], list) or not isinstance(value["unavailable"], list)
            or len(value["recordings"]) + len(value["unavailable"]) != len(identifiers)):
        raise CommunityError("invalid_response")
    rows = [checked_metadata(row) for row in value["recordings"]]
    found = [row["id"] for row in rows] + value["unavailable"]
    if any(not isinstance(item, str) for item in found) or len(set(found)) != len(found) or set(found) != set(identifiers):
        raise CommunityError("invalid_response")
    if generation is not None and any(row["generation"] != generation for row in rows):
        raise CommunityError("generation_changed")
    return {row["id"]: row for row in rows}

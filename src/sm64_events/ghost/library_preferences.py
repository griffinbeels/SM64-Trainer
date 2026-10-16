"""Remembered creator credit and bounded dynamic search criteria, never row IDs."""
from uuid import uuid4

from sm64_events.ghost.portable import checked_author
from sm64_events.ghost.sharing_targets import checked_target

AUTHOR_KEY = "ghost_share_author"
SHORTCUTS_KEY = "ghost_library_shortcuts"
MAX_SHORTCUTS = 24


def _text(value, label, limit):
    if (not isinstance(value, str) or len(value.encode("utf-8")) > limit
            or any(ord(char) < 32 for char in value)):
        raise ValueError(f"Enter {label} within {limit} UTF-8 bytes.")
    return value.strip()


def checked_criteria(value):
    if not isinstance(value, dict) or set(value) != {"query", "source", "target", "practice", "sort", "bests"}:
        raise ValueError("Choose a search to save as your shortcut.")
    if (value["source"] not in ("local", "import", "all") or value["sort"] not in ("fastest", "recent")
            or type(value["practice"]) is not bool or type(value["bests"]) is not bool):
        raise ValueError("This shortcut has invalid search options.")
    target = value["target"]
    if target is not None:
        if not isinstance(target, dict) or set(target) != {"kind", "entity_key"}:
            raise ValueError("Choose a star or segment for this shortcut.")
        if target["kind"] == "star":
            checked_target(target)
        elif (target["kind"] != "segment" or not isinstance(target["entity_key"], str)
              or not target["entity_key"].isdecimal() or not 0 < int(target["entity_key"]) < 10**20
              or str(int(target["entity_key"])) != target["entity_key"]):
            raise ValueError("Choose a star or segment for this shortcut.")
        target = dict(target)
    return {**value, "query": _text(value["query"], "a search", 512), "target": target}


def shortcuts(database):
    stored = database.get_state(SHORTCUTS_KEY, [])
    if not isinstance(stored, list) or len(stored) > MAX_SHORTCUTS:
        return []
    result = []
    for row in stored:
        try:
            if (not isinstance(row, dict) or set(row) != {"id", "name", "version", "criteria"}
                    or type(row["version"]) is not int or row["version"] != 1):
                continue
            if not isinstance(row["id"], str) or len(row["id"]) != 32 or any(c not in "0123456789abcdef" for c in row["id"]):
                continue
            name = _text(row["name"], "a shortcut name", 128)
            if name:
                result.append({**row, "name": name, "criteria": checked_criteria(row["criteria"])})
        except ValueError:
            continue
    return result


def save_author(database, value):
    value = checked_author(value)
    database.set_state(AUTHOR_KEY, value)
    return value


def save_shortcut(database, name, criteria):
    name = _text(name, "a shortcut name", 128)
    if not name:
        raise ValueError("Name this shortcut so you can find it next time.")
    criteria = checked_criteria(criteria)
    rows = shortcuts(database)
    if len(rows) >= MAX_SHORTCUTS:
        raise ValueError("Your shortcuts are full; remove one before saving another.")
    rows.append({"id": uuid4().hex, "name": name, "version": 1, "criteria": criteria})
    database.set_state(SHORTCUTS_KEY, rows)
    return rows


def remove_shortcut(database, number):
    rows = shortcuts(database)
    if not any(row["id"] == number for row in rows):
        raise LookupError("This shortcut is no longer saved.")
    rows = [row for row in rows if row["id"] != number]
    database.set_state(SHORTCUTS_KEY, rows)
    return rows

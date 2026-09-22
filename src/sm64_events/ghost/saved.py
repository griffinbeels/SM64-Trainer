"""SAVED GHOSTS: runs he chose to race that are neither his PB nor a template.

His words, 2026-09-22: *"I also should be able to save a specific replay as a
ghost, even if it's not my PB ... a ghost might not necessarily be the template
we want."* A saved ghost is one attempt id filed under its star or segment. It
carries no input document, so it never touches the input comparison, and its
Mario poses are committed when he saves it (`ReplayService.preserve_native`).

Kept in the tracker database's state table: a short list he edits by hand,
read on every picker refresh, and gone with nothing else to repair.
"""
from __future__ import annotations

from datetime import datetime, timezone

SAVED_KEY = "ghost_saved"
#: A generous bound on a hand-made list; the picker plays at most 16.
MAX_SAVED = 512


def _rows(database) -> list[dict]:
    stored = database.get_state(SAVED_KEY, []) if database is not None else []
    if not isinstance(stored, list):
        return []
    return [row for row in stored if isinstance(row, dict)
            and type(row.get("attempt_id")) is int and row["attempt_id"] > 0
            and row.get("kind") in ("star", "segment") and isinstance(row.get("entity_key"), str)]


def saved_for(database, kind: str, entity_key: str) -> list[int]:
    """Saved attempt ids for one target, newest first."""
    return [row["attempt_id"] for row in reversed(_rows(database))
            if (row["kind"], row["entity_key"]) == (kind, entity_key)]


def is_saved(database, attempt_id: int) -> bool:
    return any(row["attempt_id"] == attempt_id for row in _rows(database))


def save(database, attempt_id: int, kind: str, entity_key: str) -> None:
    rows = [row for row in _rows(database) if row["attempt_id"] != attempt_id]
    rows.append({"attempt_id": attempt_id, "kind": kind, "entity_key": entity_key,
                 "saved_utc": datetime.now(timezone.utc).isoformat()})
    database.set_state(SAVED_KEY, rows[-MAX_SAVED:])


def remove(database, attempt_id: int) -> None:
    database.set_state(SAVED_KEY, [row for row in _rows(database) if row["attempt_id"] != attempt_id])

"""Exact Sheet target bindings declared alongside seeded segment clocks.

Resolved at use time, so offline snapshots gain corrected placement without
renaming source rows, losing newer observations or inventing local IDs.
"""
import json
from functools import lru_cache

from sm64_events.core.paths import bundled_defaults_seed
from sm64_events.library.audit import target_key


@lru_cache(maxsize=1)
def _bindings():
    path = bundled_defaults_seed()
    if path is None:
        return {}
    seed = json.loads(path.read_text(encoding="utf-8"))
    return {(row["sheet_target"]["section"], row["sheet_target"]["label"]):
            row["seed_key"] for row in seed["segments"] if row.get("sheet_target")}


def seed_for(target):
    return _bindings().get((target.get("section"), target.get("label")))


def promote_catalog_target(database, seed_row):
    """Reuse a Sheet-created ID, preserving PBs, edits and deleted entries.

    Returns (managed, definition). A managed missing definition is a deliberate
    deletion. Only the exact manual shape the catalog created may gain triggers;
    edited rows acquire seed identity but stay dirty, retaining every user field.
    """
    source = seed_row.get("sheet_target")
    if not source:
        return False, None
    registry = database.get_state("sheet_practice_catalog", {}) or {}
    keys = {target_key({**source, "version": version})
            for version in (None, "jp", "us")}
    ids = {sid for key, sid in (registry.get("targets") or {}).items() if key in keys}
    if len(ids) != 1:
        return False, None
    sid = ids.pop()
    definition = next((d for d in database.segment_defs() if d["id"] == sid), None)
    if definition is None:
        return True, None
    if definition.get("seed_key"):
        return False, None
    manual = {"name": source["label"], "enabled": True, "start_triggers": [],
              "end_triggers": [], "waypoints": [], "guards": [], "parents": [],
              "category": "Ultimate Sheet", "default_strat": "Standard",
              "match_mode": "strict", "clock_start": "trigger", "seed_dirty": 0}
    dirty = int(any(definition.get(k) != v for k, v in manual.items()))
    database.update_segment_def(sid, seed_key=seed_row["seed_key"], seed_dirty=dirty)
    return True, {**definition, "seed_key": seed_row["seed_key"], "seed_dirty": dirty}

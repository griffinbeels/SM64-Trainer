"""Give Sheet movements and pieces durable, local manual-practice entries.

The Sheet provides timing and parent identity, not detection conditions. New
definitions therefore have no triggers. The registry remembers local ids so
renames/edits survive refreshes, and a deleted definition is never recreated.
"""
from datetime import datetime, timezone

from sm64_events.library.audit import row_key, target_key
from sm64_events.library.placements import automatic_rows, target_entity

STATE_KEY = "sheet_practice_catalog"


def _piece_name(target, item):
    from sm64_events.library.adoptions import ROUTE_SEP, shares_its_entity
    name = item["name"]
    if sum(other["name"] == name for other in target["subsections"]) > 1:
        name += " [" + "|".join(item["ids"]) + "]"
    if shares_its_entity(target):
        name = target["label"] + ROUTE_SEP + name
    return name


class _Catalog:
    """One provisioning pass, with ownership of IDs and managed parents."""

    def __init__(self, database):
        self.db = database
        registry = database.get_state(STATE_KEY, {}) or {}
        self.targets = dict(registry.get("targets") or {})
        self.pieces = dict(registry.get("pieces") or {})
        self.piece_parents = dict(registry.get("piece_parents") or {})
        self.definitions = database.segment_defs()
        self.by_id = {d["id"]: d for d in self.definitions}
        self.changed = False

    def create(self, name, parents):
        sid = self.db.insert_segment_def(
            name, [], [], [], datetime.now(timezone.utc).isoformat(),
            enabled=True, parents=parents, default_strat="Standard",
            category="Ultimate Sheet")
        definition = {"id": sid, "name": name, "parents": parents}
        self.by_id[sid] = definition
        self.definitions.append(definition)
        self.changed = True
        return sid

    def parent(self, target, explicit):
        assigned = {explicit[key] for item in target["approaches"]
                    if (key := row_key(target, item["name"], item["ids"])) in explicit}
        if len(assigned) == 1:
            return next(iter(assigned))
        key = target_key(target)
        if key in self.targets:
            sid = self.targets[key]
            return f"segment:{sid}" if sid in self.by_id else None
        parent = target_entity(target, self.definitions)
        if parent is None and target.get("miss_reason") == "castle_movement":
            sid = self.create(target["label"], [])
            self.targets[key] = sid
            parent = f"segment:{sid}"
        return parent

    def piece(self, target, item, parent, automatic):
        key = row_key(target, item["name"], item["ids"])
        if key in self.pieces:
            sid = self.pieces[key]
            definition = self.by_id.get(sid)
            if definition is None:
                return None  # The registry remembers deliberate deletions.
            previous = self.piece_parents.get(key)
            if previous and previous != parent and definition.get("parents") == [previous]:
                self.db.update_segment_def(sid, parents=[parent])
                definition["parents"] = [parent]
                self.piece_parents[key] = parent
                self.changed = True
        elif key in automatic:
            return None  # Existing name/parent matches stay dynamically resolved.
        else:
            sid = self.create(_piece_name(target, item), [parent])
            self.pieces[key] = sid
            self.piece_parents[key] = parent
        return f"segment:{sid}"

    def provision(self, payload, explicit):
        linked, generated = dict(explicit), {}
        for target in payload.get("targets", []):
            parent = self.parent(target, explicit)
            if parent is None:
                continue
            if parent.startswith("segment:"):
                for item in target["approaches"]:
                    key = row_key(target, item["name"], item["ids"])
                    linked.setdefault(key, parent)
                    if target_key(target) in self.targets:
                        generated[key] = parent
            automatic = automatic_rows({"targets": [target]}, linked, self.definitions)
            for item in target["subsections"]:
                key = row_key(target, item["name"], item["ids"])
                if key not in explicit:
                    entity = self.piece(target, item, parent, automatic)
                    if entity:
                        linked[key] = generated[key] = entity
        if self.changed:
            self.db.set_state(STATE_KEY, {"version": 1, "targets": self.targets,
                "pieces": self.pieces, "piece_parents": self.piece_parents})
        # Seed/name matches must follow current definitions after a replacement;
        # only IDs this registry created are durable automatic assignments.
        return {key: entity for key, entity in generated.items() if key not in explicit}


def ensure_catalog(payload, explicit, database):
    """Create missing manual movements/pieces, returning their durable links.

    Stage RTA rows remain routes in Library. Existing recorder definitions
    are reused only by authoritative seed identity or exact name and parent.
    """
    if database is None or not hasattr(database, "insert_segment_def"):
        return {}
    return _Catalog(database).provision(payload, explicit)

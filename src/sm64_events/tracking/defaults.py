"""Editable-defaults reconcile (spec 2026-07-23-default-routes-foundation).

Mirrors ranks/standards._reconcile: a bundled seed refreshes rows the user
never touched (seed_dirty=0), leaves edited (seed_dirty=1) and user-created
(seed_key IS NULL) rows alone, and inserts anything missing. Segments come
first so route candidates can resolve seed_key -> local segment_id."""
import json
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def reconcile_defaults(db, seed: dict) -> None:
    if not isinstance(seed, dict):
        return
    seg_by_key = {s["seed_key"]: s for s in db.segment_defs()
                  if s.get("seed_key")}
    key_to_id: dict[str, int] = {}
    for srow in seed.get("segments", []):
        key = srow["seed_key"]
        existing = seg_by_key.get(key)
        if existing is None:
            sid = db.insert_segment_def(
                srow["name"], srow["start_triggers"], srow["end_triggers"],
                srow.get("guards", []), _now_iso(),
                enabled=srow.get("enabled", True),
                waypoints=srow.get("waypoints", []),
                category=srow.get("category"), seed_key=key)
            key_to_id[key] = sid
        else:
            key_to_id[key] = existing["id"]
            if not existing["seed_dirty"]:
                db.update_segment_def(existing["id"], name=srow["name"],
                    enabled=srow.get("enabled", True),
                    start_triggers=srow["start_triggers"],
                    end_triggers=srow["end_triggers"],
                    waypoints=srow.get("waypoints", []),
                    guards=srow.get("guards", []),
                    category=srow.get("category"))
    route_by_key = {r["seed_key"]: r for r in db.routes() if r.get("seed_key")}
    for rrow in seed.get("routes", []):
        steps = _resolve_steps(rrow["steps"], key_to_id)
        key = rrow["seed_key"]
        existing = route_by_key.get(key)
        if existing is None:
            db.insert_route(rrow["name"], steps, _now_iso(),
                            start_condition=rrow.get("start_condition"),
                            category=rrow.get("category"), seed_key=key)
        elif not existing["seed_dirty"]:
            db.update_route(existing["id"], updated_utc=_now_iso(),
                            name=rrow["name"], steps=steps,
                            start_condition=rrow.get("start_condition",
                                                     {"type": "reset_game"}),
                            category=rrow.get("category"))


def _resolve_steps(steps: list, key_to_id: dict) -> list:
    """Rewrite seed route candidates ({type:segment, seed_key}) to persisted
    ({type:segment, segment_id}). An unresolved key -> segment_id -1 (renders
    as a broken step, never a crash)."""
    out = []
    for step in steps:
        cands = []
        for c in step["candidates"]:
            if c.get("type") == "segment" and "seed_key" in c:
                cands.append({"type": "segment",
                              "segment_id": key_to_id.get(c["seed_key"], -1)})
            else:
                cands.append(dict(c))
        new = {"need": step.get("need", 1), "candidates": cands}
        if step.get("label") is not None:
            new["label"] = step["label"]
        out.append(new)
    return out

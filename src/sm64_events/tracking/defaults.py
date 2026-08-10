"""Editable-defaults reconcile (spec 2026-07-23-default-routes-foundation).

Mirrors ranks/standards._reconcile: a bundled seed refreshes rows the user
never touched (seed_dirty=0), leaves edited (seed_dirty=1) and user-created
(seed_key IS NULL) rows alone, and inserts anything missing. Segments come
first so route candidates can resolve seed_key -> local segment_id.

Every row is validated and applied INDIVIDUALLY (hardening 2026-07-24, spec
2026-07-24-default-routes-corpus §10). The corpus is now 84 segments and 48
routes, so one malformed row must not cost the whole refresh: a bad row is
skipped and described in the returned problem list while the good rows still
land. Reconcile never raises on seed CONTENT — callers log what came back.
"""
from datetime import datetime, timezone

from sm64_events.tracking.routes import validate_route
from sm64_events.tracking.segments import validate_definition

# Anything a wrong-shaped seed row can throw on its way through validation or
# the db call. Deliberately broad: the whole point is that no seed content can
# abort the loop, and the row is named in the problem list either way.
_SEED_ERRORS = (ValueError, TypeError, KeyError, AttributeError, IndexError)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _seed_key(row, kind: str) -> str:
    if not isinstance(row, dict):
        raise ValueError(f"{kind} row is not an object")
    key = row.get("seed_key")
    if not isinstance(key, str) or not key.strip():
        raise ValueError(f"{kind} row is missing its seed_key")
    return key


def _describe(row) -> str:
    """Identify a bad row in a problem message without dumping the whole row."""
    if isinstance(row, dict):
        return str(row.get("seed_key") or row.get("name") or "<unnamed>")
    return f"<{type(row).__name__}>"


def reconcile_defaults(db, seed: dict) -> list[str]:
    """Apply the bundled seed to the db. Returns human-readable problems for
    rows that were SKIPPED; an empty list means a clean seed."""
    problems: list[str] = []
    if not isinstance(seed, dict):
        return ["seed is not an object"]
    seg_by_key = {s["seed_key"]: s for s in db.segment_defs()
                  if s.get("seed_key")}
    key_to_id: dict[str, int] = {}
    for srow in seed.get("segments") or []:
        try:
            key = _seed_key(srow, "segment")
            validate_definition(srow)
        except _SEED_ERRORS as exc:
            problems.append(f"segment {_describe(srow)}: {exc}")
            continue
        try:
            existing = seg_by_key.get(key)
            if existing is None:
                key_to_id[key] = db.insert_segment_def(
                    srow["name"], srow["start_triggers"], srow["end_triggers"],
                    srow.get("guards", []), _now_iso(),
                    enabled=srow.get("enabled", True),
                    waypoints=srow.get("waypoints", []),
                    category=srow.get("category"), seed_key=key,
                    default_strat=srow.get("default_strat"),
                    # A seed row's own match_mode flows through once a later
                    # task puts one on it (spec 2026-07-28-multi-step-segments
                    # Item 0); "strict" until then, matching the column
                    # default and every existing row rather than the insert
                    # function's own now-gone "loose" default.
                    match_mode=srow.get("match_mode", "strict"),
                    parents=srow.get("parents"),
                    # "trigger" until the corpus retiming is priced (round
                    # 15 item 3) — a seed row may carry its own value later.
                    clock_start=srow.get("clock_start", "trigger"))
            else:
                key_to_id[key] = existing["id"]
                if not existing["seed_dirty"]:
                    db.update_segment_def(
                        existing["id"], name=srow["name"],
                        enabled=srow.get("enabled", True),
                        start_triggers=srow["start_triggers"],
                        end_triggers=srow["end_triggers"],
                        waypoints=srow.get("waypoints", []),
                        guards=srow.get("guards", []),
                        category=srow.get("category"),
                        default_strat=srow.get("default_strat"),
                        # Same reasoning as the insert branch above: an
                        # ALREADY-installed, untouched row must also pick up a
                        # later seed conversion, not just a fresh install.
                        match_mode=srow.get("match_mode", "strict"),
                        parents=srow.get("parents"),
                        clock_start=srow.get("clock_start", "trigger"))
        except _SEED_ERRORS as exc:
            problems.append(f"segment {key}: {exc}")
    route_by_key = {r["seed_key"]: r for r in db.routes() if r.get("seed_key")}
    for rrow in seed.get("routes") or []:
        try:
            key = _seed_key(rrow, "route")
            steps = resolve_steps(rrow["steps"], key_to_id)
            start_condition = (rrow.get("start_condition")
                               or {"type": "reset_game"})
            validate_route({"name": rrow.get("name"), "steps": steps,
                            "start_condition": start_condition})
        except _SEED_ERRORS as exc:
            problems.append(f"route {_describe(rrow)}: {exc}")
            continue
        try:
            existing = route_by_key.get(key)
            if existing is None:
                db.insert_route(rrow["name"], steps, _now_iso(),
                                start_condition=start_condition,
                                category=rrow.get("category"), seed_key=key)
            elif not existing["seed_dirty"]:
                db.update_route(existing["id"], updated_utc=_now_iso(),
                                name=rrow["name"], steps=steps,
                                start_condition=start_condition,
                                category=rrow.get("category"))
        except _SEED_ERRORS as exc:
            problems.append(f"route {key}: {exc}")

    # The LANDMARK CATALOGUE — the shipped answer to "which door is that". Same
    # contract as the two above and deliberately the simplest of the three: a
    # name has nothing to validate beyond being a non-empty string on a
    # non-empty key, so a bad row costs one skipped name and nothing else.
    for lrow in seed.get("landmarks") or []:
        try:
            key = _seed_key(lrow, "landmark")
            target, name = lrow.get("key"), lrow.get("name")
            if not isinstance(target, str) or not target.strip():
                raise ValueError("landmark row is missing the key it names")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("landmark row is missing its name")
            db.seed_landmark_name(target.strip(), name.strip(), key)
        except _SEED_ERRORS as exc:
            problems.append(f"landmark {_describe(lrow)}: {exc}")
    return problems


def resolve_steps(steps: list, key_to_id: dict) -> list:
    """Rewrite seed route candidates ({type:segment, seed_key}) to persisted
    ({type:segment, segment_id}). An unresolved key -> segment_id -1 (renders
    as a broken step, never a crash). Public because the corpus generator's
    tests resolve the same way to prove routes and segments agree."""
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

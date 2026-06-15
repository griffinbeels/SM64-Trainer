"""Route model + cumulative success + import/export resolution (spec 2026-06-14).

A route is an ordered list of STEPS; each step is a "complete K of N" group
(a single item is need=1 with one candidate). Steps reference segments by
LOCAL id; portability is handled at export (segment defs embedded) / import
(reconciled against the local segment list). Pure functions only — no db, no
I/O — so the service/view layers wire it and pytest covers the math directly.

No-data rule (user decision 2026-06-14): a step with no logged attempts has a
success rate of 0.0, which zeroes the cumulative product from that step down.
Group rate = product of the BEST-K candidate rates (K=1 'pick one' = the most
reliable option's rate)."""
from sm64_events.stats.registry import compute_stat

ROUTE_EXPORT_KIND = "sm64-route"
ROUTE_EXPORT_VERSION = 1


def _is_int(x) -> bool:
    # bool is an int subclass; reject it so True/False can't pose as ids
    return isinstance(x, int) and not isinstance(x, bool)


def _validate_item(item) -> None:
    if not isinstance(item, dict):
        raise ValueError("each candidate must be an object")
    kind = item.get("type")
    if kind == "star":
        if not (_is_int(item.get("course")) and _is_int(item.get("star"))):
            raise ValueError("star candidate needs integer course and star")
    elif kind == "segment":
        if not _is_int(item.get("segment_id")):
            raise ValueError("segment candidate needs an integer segment_id")
    else:
        raise ValueError(f"unknown candidate type {kind!r}")


def validate_route(d: dict) -> None:
    """Raise ValueError on the first structural problem (API maps it to 409).
    Structural only — segment_id EXISTENCE is checked in the service, where the
    db is available."""
    if not str(d.get("name", "")).strip():
        raise ValueError("name is required")
    steps = d.get("steps")
    if not isinstance(steps, list):
        raise ValueError("steps must be a list")
    # An empty route is a valid DRAFT: the builder creates the route empty and
    # adds steps afterward (live report 2026-06-14: POST {steps:[]} must not
    # 409). resolve_import keeps its OWN non-empty check for shared routes.
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError("each step must be an object")
        cands = step.get("candidates")
        if not isinstance(cands, list) or not cands:
            raise ValueError("each step needs a non-empty candidates list")
        need = step.get("need")
        if not _is_int(need) or need < 1 or need > len(cands):
            raise ValueError("need must be an integer in 1..len(candidates)")
        for c in cands:
            _validate_item(c)


def _item_attempts(item: dict, attempts):
    if item["type"] == "segment":
        sid = item["segment_id"]
        return [a for a in attempts if a.segment_id == sid]
    c, s = item["course"], item["star"]
    return [a for a in attempts
            if a.segment_id is None and a.course_id == c and a.star_id == s]


def _item_rate(item: dict, attempts) -> float:
    """Lifetime success rate for one item; no data -> 0.0.

    Reuses the registry's success_rate stat (failures = reset/hard_reset/death,
    cleared attempts excluded). success_rate ignores the clock arg."""
    rate = compute_stat("success_rate", _item_attempts(item, attempts), {}, "igt")
    return rate if rate is not None else 0.0


def _step_rate(step: dict, attempts) -> float:
    """Product of the best-K candidate rates (K = step['need'])."""
    rates = sorted((_item_rate(c, attempts) for c in step["candidates"]),
                   reverse=True)
    product = 1.0
    for r in rates[:step["need"]]:
        product *= r
    return product


def route_stats(steps: list, attempts) -> list[dict]:
    """Per-step success rate + cumulative (running product), in route order.
    attempts is the full lifetime attempt list (caller scopes nothing)."""
    out, cumulative = [], 1.0
    for step in steps:
        sr = _step_rate(step, attempts)
        cumulative *= sr
        out.append({"step_rate": sr, "cumulative": cumulative})
    return out


def export_route(name: str, steps: list, segment_defs: dict) -> dict:
    """Self-contained export. Segment candidates embed their full definition
    (resolved from segment_defs: id -> {name, start_triggers, end_triggers,
    guards}); star candidates are portable as-is. Raises ValueError if a step
    references a segment id not in segment_defs."""
    out_steps = []
    for step in steps:
        cands = []
        for c in step["candidates"]:
            if c["type"] == "segment":
                d = segment_defs.get(c["segment_id"])
                if d is None:
                    raise ValueError(
                        f"route references missing segment {c['segment_id']}")
                cands.append({"type": "segment", "segment": {
                    "name": d["name"], "start_triggers": d["start_triggers"],
                    "end_triggers": d["end_triggers"], "guards": d["guards"]}})
            else:
                cands.append(dict(c))
        out_step = {"need": step["need"], "candidates": cands}
        if step.get("label") is not None:
            out_step["label"] = step["label"]
        out_steps.append(out_step)
    return {"kind": ROUTE_EXPORT_KIND, "version": ROUTE_EXPORT_VERSION,
            "name": name, "steps": out_steps}


def _segment_matches(emb: dict, existing: dict) -> bool:
    return (existing["name"] == emb["name"]
            and existing["start_triggers"] == emb["start_triggers"]
            and existing["end_triggers"] == emb["end_triggers"]
            and existing.get("guards", []) == emb.get("guards", []))


def resolve_import(payload: dict, existing_defs: list) -> dict:
    """Pure reconciliation of an imported route against the local segment list.

    Returns {name, steps, to_create, reused, created}:
      - steps: ready to persist EXCEPT segment candidates carry either
        {"type":"segment","segment_id":<existing id>} (exact match reused) or
        {"type":"segment","create_index": i} (service creates to_create[i] then
        rewrites these to a real segment_id).
      - to_create: unique embedded segment defs with no local exact match.
      - reused / created: segment-name lists for the dry-run preview.
    Raises ValueError on a bad envelope or malformed step."""
    if payload.get("kind") != ROUTE_EXPORT_KIND:
        raise ValueError("not an sm64-route export")
    if payload.get("version") != ROUTE_EXPORT_VERSION:
        raise ValueError(f"unsupported route version {payload.get('version')!r}")
    name = str(payload.get("name", "")).strip()
    if not name:
        raise ValueError("import is missing a route name")
    steps_in = payload.get("steps")
    if not isinstance(steps_in, list) or not steps_in:
        raise ValueError("import has no steps")

    to_create, reused, created, out_steps = [], [], [], []
    for step in steps_in:
        if not isinstance(step, dict) or not isinstance(step.get("candidates"), list):
            raise ValueError("each step needs a candidates list")
        cands = []
        for c in step["candidates"]:
            if not isinstance(c, dict):
                raise ValueError("each candidate must be an object")
            if c.get("type") == "segment":
                emb = c.get("segment")
                if not isinstance(emb, dict) or not str(emb.get("name", "")).strip():
                    raise ValueError("embedded segment is missing its definition")
                emb_def = {"name": emb["name"],
                           "start_triggers": emb.get("start_triggers", []),
                           "end_triggers": emb.get("end_triggers", []),
                           "guards": emb.get("guards", [])}
                match = next((e for e in existing_defs
                              if _segment_matches(emb_def, e)), None)
                if match is not None:
                    cands.append({"type": "segment", "segment_id": match["id"]})
                    reused.append(emb_def["name"])
                else:
                    idx = next((i for i, d in enumerate(to_create)
                                if _segment_matches(emb_def, d)), None)
                    if idx is None:
                        idx = len(to_create)
                        to_create.append(emb_def)
                        created.append(emb_def["name"])
                    cands.append({"type": "segment", "create_index": idx})
            elif c.get("type") == "star":
                cands.append({"type": "star", "course": c.get("course"),
                              "star": c.get("star")})
            else:
                raise ValueError(f"unknown candidate type {c.get('type')!r}")
        out_step = {"need": step.get("need", 1), "candidates": cands}
        if step.get("label") is not None:
            out_step["label"] = step["label"]
        out_steps.append(out_step)
    return {"name": name, "steps": out_steps, "to_create": to_create,
            "reused": reused, "created": created}

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
    if not isinstance(steps, list) or not steps:
        raise ValueError("steps must be a non-empty list")
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


# Stub — implemented in Task 5.
def resolve_import(payload: dict, existing_defs: list) -> dict:
    raise NotImplementedError

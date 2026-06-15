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


# Stubs — implemented in Tasks 3, 4, 5 respectively.
def route_stats(steps: list, attempts) -> list[dict]:
    raise NotImplementedError


def export_route(name: str, steps: list, segment_defs: dict) -> dict:
    raise NotImplementedError


def resolve_import(payload: dict, existing_defs: list) -> dict:
    raise NotImplementedError

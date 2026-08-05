"""The audit view over a library snapshot, and the human's corrections to it.

Phase 1 classifies 702 rows and 252 targets from two signals plus a measured
veto. Every one of those decisions is a guess about somebody else's
spreadsheet, and a wrong one is invisible downstream: a subsection promoted to
an approach is simply a fast time attached to the wrong thing. So the
corrections are a first-class input, not a patch -- `tools/audit_library.py`
serves this view, the human rules on it, and `build.py` applies what he saved
the next time the snapshot is rebuilt.

An override is keyed by NAME rather than by row number. The sheet's own rules
forbid adding or deleting rows, but a key that survives a reshuffle costs
nothing and a key that does not would silently re-point every verdict."""
import json
from pathlib import Path

from sm64_events.library.mapping import (BOWSER_COURSES, MAIN_COURSES,
                                         SECRET_COURSES)
from sm64_events.memory.addresses import course_name, star_count, star_name

KEY_SEP = "||"

# What a target can BE. Closed, because an open vocabulary in an audit means
# two sessions inventing two names for the same verdict.
TARGET_CATEGORIES = (
    "star",             # a star we practice; carries an entity key
    "segment",          # a segment we model; carries an entity key
    "castle_movement",  # a movement the sheet times and we do not model
    "route",            # spans many stars in a chosen order -- a stage RTA
    "subsection",       # not a target at all -- part of the target above it
    "not_a_target",     # something real that we will never practice
)
ROW_KINDS = ("approach", "subsection")

# Rows this close to the veto floor (0.70) or to the part-slower-than-whole
# ceiling (1.0) are the ones worth a human's eye first.
NEAR_APPROACH = 0.85
NEAR_SUBSECTION = 0.85


def target_key(target: dict) -> str:
    """Unique, and by NAME rather than row number.

    The version is part of it because BBH opens two targets both called "Go on
    a Ghost Hunt", one per ROM version. Without it their keys collide and one
    correction silently rules on both -- which is the exact failure this audit
    exists to catch, arriving inside the audit itself."""
    version = target.get("version")
    tail = f"{KEY_SEP}{version}" if version else ""
    return f"{target['section']}{KEY_SEP}{target['label']}{tail}"


def row_key(target: dict, name: str, ids=()) -> str:
    """Rows repeat their name inside one target -- "Warp fadeout" appears
    twice under Big Bob-omb, once for approaches [1|2] and once for [3|4], and
    19 targets do something like it. The bracket ids are what tell them apart
    on the sheet, so they tell them apart here."""
    tail = f"{KEY_SEP}{'|'.join(ids)}" if ids else ""
    return f"{target_key(target)}{KEY_SEP}{name}{tail}"


def computed_category(target: dict) -> str:
    if target["entity_key"]:
        return "segment" if target["entity_key"].startswith("segment:") else "star"
    return target["miss_reason"] or "not_a_target"


def entity_choices(segment_names: dict | None = None) -> list:
    """Every entity an audited target could be pointed at.

    `segment_names` is {segment id: name} from the defaults seed; the caller
    supplies it so this module never reaches into the store."""
    out = []
    for course in list(MAIN_COURSES) + list(SECRET_COURSES) + [c for c, _, _ in BOWSER_COURSES]:
        for star in range(star_count(course)):
            out.append({"key": f"star:{course}:{star}",
                        "name": f"{course_name(course)} — {star_name(course, star)}",
                        "kind": "star"})
    for segment_id, name in sorted((segment_names or {}).items()):
        out.append({"key": f"segment:{segment_id}", "name": name, "kind": "segment"})
    return out


def load_overrides(path) -> dict:
    """{"targets": {key: {...}}, "rows": {key: {...}}} -- empty when absent."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return {"targets": {}, "rows": {}}
    if not isinstance(data, dict):
        return {"targets": {}, "rows": {}}
    return {"targets": data.get("targets") or {}, "rows": data.get("rows") or {}}


def save_overrides(path, overrides: dict) -> None:
    clean = {"targets": {}, "rows": {}}
    for scope in ("targets", "rows"):
        for key, value in (overrides.get(scope) or {}).items():
            if not isinstance(value, dict):
                continue
            kept = {k: v for k, v in value.items()
                    if k in ("category", "entity_key", "kind", "reason")
                    and v not in (None, "")}
            if kept:
                clean[scope][key] = kept
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    # newline="" so Windows does not rewrite every LF and double the diff.
    Path(path).write_text(json.dumps(clean, indent=1, ensure_ascii=False,
                                     sort_keys=True), encoding="utf-8", newline="")


def apply_overrides(payload: dict, overrides: dict) -> dict:
    """Return `payload` with the human's verdicts applied.

    Row kinds move BETWEEN a target's two lists rather than being stamped in
    place, so a consumer that only reads `approaches` cannot see a row the
    audit demoted -- the whole point of the correction.

    A row may also carry its OWN entity, because one sheet target can hold
    rows belonging to two of ours: under "Bowser in the Dark World Red Coins"
    the `Red coin star Xcam` rows are the 8-red-coin STAR, while the longer
    parent rows include the travel to the pipe and belong to the pipe segment
    (user, 2026-08-05). A row with no entity of its own inherits its target's,
    so the common case stays silent."""
    targets = overrides.get("targets") or {}
    rows = overrides.get("rows") or {}
    for target in payload["targets"]:
        verdict = targets.get(target_key(target))
        if verdict:
            if "entity_key" in verdict:
                target["entity_key"] = verdict["entity_key"] or None
            if "category" in verdict:
                target["miss_reason"] = (None if target["entity_key"]
                                         else verdict["category"])
            target["audited"] = True
        moved_up, moved_down = [], []
        for item in list(target["approaches"]):
            wanted = (rows.get(row_key(target, item["name"], item["ids"])) or {}).get("kind")
            if wanted == "subsection":
                target["approaches"].remove(item)
                moved_down.append(item)
        for item in list(target["subsections"]):
            wanted = (rows.get(row_key(target, item["name"], item["ids"])) or {}).get("kind")
            if wanted == "approach":
                target["subsections"].remove(item)
                moved_up.append(item)
        # Only rows that actually MOVED are stamped, so the stamp answers
        # "was this override load-bearing", not "did somebody save one". A
        # correction the parser now makes unaided leaves no stamp at all --
        # which is how `tests/test_library_seed.py` can tell a rule we encoded
        # from a one-off we merely patched.
        for item in moved_up + moved_down:
            item["overridden"] = True
        target["approaches"].extend(moved_up)
        target["subsections"].extend(moved_down)
        for item in target["approaches"] + target["subsections"]:
            own = (rows.get(row_key(target, item["name"], item["ids"])) or {}
                   ).get("entity_key")
            if own:
                item["entity_key"] = own
    return payload


def _row_view(target, item, kind, basis, overrides):
    key = row_key(target, item["name"], item["ids"])
    entries = item["entries"]
    return {"key": key, "name": item["name"], "ids": item["ids"], "kind": kind,
            "best_cs": item["best_cs"], "best_runner": item.get("best_runner", ""),
            "ratio": (round(item["best_cs"] / basis, 3)
                      if basis and item["best_cs"] else None),
            "entries": len(entries),
            "videos": sum(1 for e in entries if e["video"]),
            # Up to three examples, because a sheet link dies without warning
            # -- privated, deleted, purged from the host. Coverage inside a row
            # is near total, so a dead link is a nuisance rather than a hole
            # (user, 2026-08-05), but offering exactly one makes it look like
            # the library has nothing.
            "videos_sample": [e["video"] for e in entries if e["video"]][:3],
            "video": next((e["video"] for e in entries if e["video"]), None),
            "entity_key": item.get("entity_key"),
            "override": (overrides.get("rows") or {}).get(key)}


def audit_view(payload: dict, overrides: dict, segment_names=None) -> dict:
    """The whole snapshot reduced to what a person needs to judge it: every
    target, its computed verdict, its rows with the RATIO that decided each
    one, and a flag on anything sitting near a boundary."""
    shared = {}
    for target in payload["targets"]:
        if target["entity_key"]:
            shared[target["entity_key"]] = shared.get(target["entity_key"], 0) + 1
    # Targets the sheet stopped marking bold. We keep them by the structural
    # rule; a human still wants to know which ones the styling abandoned.
    drifted = {(row["section"], row["label"])
               for row in payload.get("styling_drift") or []}

    views = []
    for target in payload["targets"]:
        key = target_key(target)
        verdict = (overrides.get("targets") or {}).get(key)
        computed = computed_category(target)
        rows, flags, basis = [], [], None
        for item in target["approaches"]:
            view = _row_view(target, item, "approach", basis, overrides)
            if view["ratio"] is not None and view["ratio"] < NEAR_APPROACH:
                view["near"] = True
                flags.append("near-veto")
            rows.append(view)
            if item["best_cs"] is not None:
                basis = item["best_cs"] if basis is None else min(basis, item["best_cs"])
        slowest = max([a["best_cs"] for a in target["approaches"] if a["best_cs"]],
                      default=None)
        for item in target["subsections"]:
            view = _row_view(target, item, "subsection", slowest, overrides)
            if view["ratio"] is not None and view["ratio"] > NEAR_SUBSECTION:
                view["near"] = True
                flags.append("near-ceiling")
            rows.append(view)
        if target["entity_key"] and shared[target["entity_key"]] > 1:
            flags.append("shared-entity")
        if not target["entity_key"] and computed == "unknown":
            flags.append("unmapped")
        if (target["section"], target["label"]) in drifted:
            flags.append("styling-drift")
        views.append({
            "key": key, "group": target["group"], "section": target["section"],
            "label": target["label"],
            "computed": {"category": computed, "entity_key": target["entity_key"]},
            "override": verdict,
            "flags": sorted(set(flags)),
            "entries": sum(row["entries"] for row in rows),
            "videos": sum(row["videos"] for row in rows),
            "rows": rows})
    return {"sheet_revision": payload["sheet_revision"],
            "fetched_at": payload["fetched_at"],
            "categories": list(TARGET_CATEGORIES),
            "row_kinds": list(ROW_KINDS),
            "entities": entity_choices(segment_names),
            "targets": views}

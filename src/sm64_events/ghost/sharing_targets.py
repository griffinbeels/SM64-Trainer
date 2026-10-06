"""Portable target identity: stars are global; segment numbers are local.

A collection never creates or edits tracking definitions. Segment fingerprints
expand references and compare the actual matcher and clock, including parents.
Unmatched definitions stay in the collection until the matching target exists.
"""
from __future__ import annotations

import hashlib
import json
import re

from sm64_events.memory.addresses import course_name, star_count, star_name

MATCH_FIELDS = ("start_triggers", "end_triggers", "waypoints", "guards", "match_mode", "clock_start")


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def segment_signature(row, definitions, trail=()) -> str:
    """References use definition identity, never a foreign database's number."""
    number = row["id"]
    if number in trail or len(trail) >= 12:
        raise ValueError("This segment has a circular or excessively deep definition.")
    trail = (*trail, number)

    def expand(value):
        if isinstance(value, dict):
            return {key: (signature(item) if key == "segment_id" else expand(item))
                    for key, item in value.items()}
        if isinstance(value, list):
            return [expand(item) for item in value]
        return value

    def signature(reference):
        found = next((item for item in definitions if item["id"] == reference), None)
        if found is None:
            raise ValueError("This segment refers to a missing definition.")
        return segment_signature(found, definitions, trail)

    def parent_identity(parent):
        # SegmentDef parents are entity keys; star and area keys are global.
        if not isinstance(parent, str) or not re.fullmatch(r"star:\d+:\d+|segment:\d+|area:\d+(?::\d+)?", parent):
            raise ValueError("This segment has an invalid parent key.")
        return signature(int(parent.split(":")[1])) if parent.startswith("segment:") else parent

    parents = row.get("parents")
    if parents is None:
        parents = []
    if not isinstance(parents, list):
        raise ValueError("This segment has an invalid parent list.")
    portable_parents = [parent_identity(parent) for parent in parents]
    if len(set(parents)) != len(parents):
        raise ValueError("This segment repeats a parent key.")
    values = {key: expand(row.get(key, [] if key not in ("match_mode", "clock_start") else
                               "strict" if key == "match_mode" else "trigger")) for key in MATCH_FIELDS}
    values["parents"] = sorted(portable_parents)
    return hashlib.sha256(canonical(values)).hexdigest()


def checked_target(target):
    if not isinstance(target, dict) or set(target) - {"kind", "entity_key", "strat", "name", "signature"}:
        raise ValueError("This collection has an invalid practice target.")
    kind, key = target.get("kind"), target.get("entity_key")
    if not isinstance(key, str) or len(key) > 30:
        raise ValueError("This collection has an invalid practice target.")
    if kind == "star":
        parts = key.split("-")
        if len(parts) != 2 or not all(part.isdecimal() for part in parts):
            raise ValueError("This collection has an invalid star.")
        course, star = map(int, parts)
        if key != f"{course}-{star}" or not 0 <= star < star_count(course):
            raise ValueError("This collection has an unknown star.")
    elif kind == "segment":
        if not key.isdecimal() or int(key) <= 0 or key != str(int(key)):
            raise ValueError("This collection has an invalid segment.")
        signature = target.get("signature")
        if not isinstance(signature, str) or len(signature) != 64 or any(c not in "0123456789abcdef" for c in signature):
            raise ValueError("This segment does not identify its definition.")
    else:
        raise ValueError("A shared ghost belongs to a star or segment.")
    for field in ("name", "strat"):
        text = target.get(field)
        if text is not None and (not isinstance(text, str) or len(text.encode("utf-8")) > 256
                                 or any(ord(c) < 32 for c in text)):
            raise ValueError("This collection has an invalid target name or strategy.")
    return dict(target)


def describe(kind, key, strat, definitions):
    target = {"kind": kind, "entity_key": key, "strat": strat}
    if kind == "star":
        course, star = map(int, key.split("-"))
        target["name"] = f"{course_name(course)} · {star_name(course, star)}"
    else:
        row = next((row for row in definitions if str(row["id"]) == key), None)
        if row is None:
            raise ValueError("This ghost's segment no longer exists.")
        target.update(name=row["name"], signature=segment_signature(row, definitions))
    return checked_target(target)


def binding_index(definitions):
    """Fingerprint a definition snapshot once for an entire collection."""
    result = {}
    for row in definitions:
        try:
            if row.get("enabled", True):
                signature = segment_signature(row, definitions)
                result.setdefault(signature, []).append(str(row["id"]))
        except ValueError:
            continue
    return result


def bind(target, definitions=(), *, index=None):
    """Return the local target key or a readable reason; no numeric fallback."""
    if target["kind"] == "star":
        return target["entity_key"], None
    matches = (binding_index(definitions) if index is None else index).get(target["signature"], [])
    if len(matches) == 1:
        return matches[0], None
    reason = ("Several segments match this ghost's definition." if matches else
              "Add this segment's matching definition to race this ghost.")
    return None, reason

"""Which ghosts play belongs to the practice target, not its input comparison.

A target's choice is a SET: his PB ghost (on by default, `ghost/pb.py`) plus
any saved recordings he ticks. His words, 2026-09-22: *"it's a multiselector,
with the PB ghost automatically selected. If the user disables the PB ghost,
then their future PBs also shouldn't automatically appear."* So the PB box is
remembered per target, and while it is ticked each new PB replaces the old one
without any other change.
"""
from __future__ import annotations

import json
import logging
from time import monotonic

SELECTION_KEY = "ghost_templates"
CANDIDATE_CACHE_S = 4.0
MISSING_REASON = "The selected ghost was removed. Choose another saved ghost."
#: Ghosts drawn at once; the native channel's `GIPC_GHOSTS`.
MAX_GHOSTS = 16
DEFAULT_CHOICE = {"pb": True, "templates": []}
log = logging.getLogger("sm64.ghost")


def _native_candidate(native, target, template):
    if native is None:
        return {"available": False,
                "reason_text": "Mario playback is not available in this window."}
    try:
        return native.probe(target, template)
    except Exception:  # noqa: BLE001 - one unreadable recording must not hide other choices
        log.debug("ghost candidate %s unavailable", template.id, exc_info=True)
        return {"available": False,
                "reason_text": "This ghost's saved Mario poses could not be read."}


def checked_template_id(value):
    if value is not None and (type(value) is not int or value <= 0):
        raise ValueError("Choose a saved ghost or follow the input template.")
    return value


def checked_choice(value) -> dict:
    """`{"pb": bool, "templates": [saved ghost ids]}`, or one readable refusal."""
    if not isinstance(value, dict) or set(value) - {"pb", "templates"}:
        raise ValueError("Choose which ghosts to show.")
    pb = value.get("pb", True)
    templates = value.get("templates", [])
    if type(pb) is not bool or not isinstance(templates, list):
        raise ValueError("Choose which ghosts to show.")
    for number in templates:
        checked_template_id(number)
        if number is None:
            raise ValueError("Choose a saved ghost.")
    if len(set(templates)) != len(templates):
        raise ValueError("Each saved ghost can be shown once.")
    if len(templates) + pb > MAX_GHOSTS:
        raise ValueError(f"Show at most {MAX_GHOSTS} ghosts at once.")
    return {"pb": pb, "templates": list(templates)}


def _stored_choice(value):
    if type(value) is int and value > 0:
        # Before 2026-09-22 a target held ONE picked recording.
        return {"pb": True, "templates": [value]}
    try:
        return checked_choice(value)
    except ValueError:
        return None


def chosen_ghosts(choice, library, target):
    """The ghosts that play, PB first, and the ticked ids that no longer exist.

    With the PB box ticked but no PB saved yet and nothing else ticked, the
    active input template plays, as it did before PBs became ghosts.
    """
    kind, key, strat = target["kind"], target["entity_key"], target.get("strat")
    ghosts, missing = [], []
    pb = library.pb_for(kind, key, strat) if choice["pb"] else None
    if pb is not None:
        ghosts.append(pb)
    for number in choice["templates"]:
        try:
            template = library.get(number)
        except LookupError:
            missing.append(number)
            continue
        if (template.kind, template.entity_key) != (kind, key):
            missing.append(number)
        elif all(template.origin != other.origin for other in ghosts):
            ghosts.append(template)
    if not ghosts and choice["pb"] and not choice["templates"]:
        fallback = library.template_for(kind, key, strat)
        if fallback is not None:
            ghosts.append(fallback)
    return ghosts, missing


def target_key(target):
    return json.dumps([target["kind"], target["entity_key"], target.get("strat")],
                      separators=(",", ":"))


def selected_template(templates, kind, entity_key, strat, template_id=None):
    if template_id is None:
        return templates.active_for(kind, entity_key, strat)
    checked_template_id(template_id)
    try:
        template = templates.get(template_id)
    except LookupError:
        return None
    return template if (template.kind, template.entity_key) == (kind, entity_key) else None


class GhostSelection:
    def __init__(self):
        self._choices = {}
        self._loaded = False
        self._cache = None

    def load(self, database):
        if self._loaded or database is None:
            return
        stored = database.get_state(SELECTION_KEY, {})
        if isinstance(stored, dict):
            self._choices = {key: choice for key, value in stored.items()
                             if isinstance(key, str) and (choice := _stored_choice(value)) is not None}
        self._loaded = True

    def chosen(self, target):
        choice = self._choices.get(target_key(target)) if target else None
        return {"pb": choice["pb"], "templates": list(choice["templates"])} if choice else dict(
            DEFAULT_CHOICE, templates=[])

    def choose(self, target, choice):
        key = target_key(target)
        if choice == DEFAULT_CHOICE:
            self._choices.pop(key, None)
        else:
            self._choices[key] = checked_choice(choice)
        return {name: dict(value) for name, value in self._choices.items()}

    def candidates(self, target, templates, video_of, native):
        from sm64_events.ghost.source import attempt_id_of

        if target is None:
            return []
        rows = templates.list_for(target["kind"], target["entity_key"])
        signature = (target_key(target), tuple(
            (row.id, row.name, row.origin, row.strat_tag, row.created_utc) for row in rows))
        now = monotonic()
        if self._cache is not None:
            key, until, candidates = self._cache
            if key == signature and now < until:
                return [dict(row) for row in candidates]
        candidates = []
        for row in rows:
            video = video_of(target, row.id)
            pose = _native_candidate(native, target, row)
            candidates.append({"template_id": row.id, "name": row.name,
                               "attempt_id": attempt_id_of(row.origin),
                               "strat": row.strat_tag, "created_utc": row.created_utc,
                               "native_available": bool(pose["available"]),
                               "native_reason_text": pose["reason_text"],
                               "whole_available": bool(video["available"]),
                               "whole_reason_text": video["reason_text"]})
        self._cache = signature, now + CANDIDATE_CACHE_S, candidates
        return [dict(row) for row in candidates]

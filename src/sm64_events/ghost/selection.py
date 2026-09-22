"""A ghost choice belongs to the practice target, not its input comparison."""
from __future__ import annotations

import json
import logging
from time import monotonic

SELECTION_KEY = "ghost_templates"
CANDIDATE_CACHE_S = 4.0
MISSING_REASON = "The selected ghost was removed. Choose another saved ghost."
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
            self._choices = {key: value for key, value in stored.items()
                             if isinstance(key, str) and type(value) is int and value > 0}
        self._loaded = True

    def chosen(self, target):
        return self._choices.get(target_key(target)) if target else None

    def choose(self, target, template_id):
        key = target_key(target)
        if template_id is None:
            self._choices.pop(key, None)
        else:
            self._choices[key] = template_id
        return dict(self._choices)

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

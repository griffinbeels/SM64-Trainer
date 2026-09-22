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
DEFAULT_CHOICE = {"pb": True, "attempts": []}
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
    """`{"pb": bool, "attempts": [attempt ids]}`, or one readable refusal.

    A ghost is a recorded ATTEMPT, whichever list offered it (a saved ghost or
    a template's run), so the tick set names attempts.
    """
    if not isinstance(value, dict) or set(value) - {"pb", "attempts"}:
        raise ValueError("Choose which ghosts to show.")
    pb = value.get("pb", True)
    attempts = value.get("attempts", [])
    if type(pb) is not bool or not isinstance(attempts, list):
        raise ValueError("Choose which ghosts to show.")
    for number in attempts:
        if type(number) is not int or number <= 0:
            raise ValueError("Choose a saved ghost.")
    if len(set(attempts)) != len(attempts):
        raise ValueError("Each saved ghost can be shown once.")
    if len(attempts) + pb > MAX_GHOSTS:
        raise ValueError(f"Show at most {MAX_GHOSTS} ghosts at once.")
    return {"pb": pb, "attempts": list(attempts)}


def _stored_choice(value):
    """Stored choices, including the two older shapes: one template id (before
    the tick set) and ticked TEMPLATE ids (its first build). Template ids are
    kept aside and turned into attempts where a library is at hand."""
    if type(value) is int and value > 0:
        return {"pb": True, "attempts": [], "templates": [value]}
    if isinstance(value, dict) and "templates" in value and "attempts" not in value:
        templates = value.get("templates")
        if type(value.get("pb")) is bool and isinstance(templates, list) and all(
                type(number) is int and number > 0 for number in templates):
            return {"pb": value["pb"], "attempts": [], "templates": list(templates)}
        return None
    try:
        return checked_choice(value)
    except ValueError:
        return None


def upgraded_choice(choice, library):
    """Replace legacy ticked template ids with their runs' attempt ids."""
    from sm64_events.ghost.source import attempt_id_of

    if not choice.get("templates"):
        return {"pb": choice["pb"], "attempts": list(choice["attempts"])}
    attempts = list(choice["attempts"])
    for number in choice["templates"]:
        try:
            attempt = attempt_id_of(library.get(number).origin)
        except LookupError:
            continue
        if attempt is not None and attempt not in attempts:
            attempts.append(attempt)
    return {"pb": choice["pb"], "attempts": attempts[:MAX_GHOSTS - choice["pb"]]}


def chosen_ghosts(choice, library, target):
    """The ghosts that play, PB first, and ticked attempts no longer offered.

    With the PB box ticked but no PB saved yet and nothing else ticked, the
    active input template plays, as it did before PBs became ghosts.
    """
    kind, key, strat = target["kind"], target["entity_key"], target.get("strat")
    ghosts, missing = [], []
    pb = library.pb_for(kind, key, strat) if choice["pb"] else None
    if pb is not None:
        ghosts.append(pb)
    offered = {ghost.attempt_id: ghost for ghost in library.ghosts_for(kind, key)}
    for number in choice["attempts"]:
        if number not in offered:
            missing.append(number)
        elif all(offered[number].origin != other.origin for other in ghosts):
            ghosts.append(offered[number])
    if not ghosts and choice["pb"] and not choice["attempts"]:
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

    def chosen(self, target, library=None):
        """This target's tick set; legacy template ticks are upgraded (and the
        upgrade kept) when a library is given."""
        key = target_key(target) if target else None
        choice = self._choices.get(key) if key else None
        if choice is None:
            return dict(DEFAULT_CHOICE, attempts=[])
        if choice.get("templates") and library is not None:
            choice = self._choices[key] = upgraded_choice(choice, library)
        return {"pb": choice["pb"], "attempts": list(choice["attempts"])}

    def choose(self, target, choice):
        key = target_key(target)
        if choice == DEFAULT_CHOICE:
            self._choices.pop(key, None)
        else:
            self._choices[key] = checked_choice(choice)
        return self.stored()

    def untick(self, attempt_id):
        for key, choice in list(self._choices.items()):
            if attempt_id in choice.get("attempts", []):
                self._choices[key] = {**choice, "attempts": [n for n in choice["attempts"] if n != attempt_id]}

    def stored(self):
        return {name: dict(value) for name, value in self._choices.items()}

    def candidates(self, target, library, video_of, native):
        """Every run this target can race except his PB (the PB box is its own
        row), named by time, each qualified for both Show modes."""
        if target is None:
            return []
        ghosts = library.ghosts_for(target["kind"], target["entity_key"])
        signature = (target_key(target), tuple((g.attempt_id, g.frames, g.strat_tag) for g in ghosts))
        now = monotonic()
        if self._cache is not None:
            key, until, candidates = self._cache
            if key == signature and now < until:
                return [dict(row) for row in candidates]
        candidates = []
        for ghost in ghosts:
            video = video_of(target, ghost)
            pose = _native_candidate(native, target, ghost)
            candidates.append({"attempt_id": ghost.attempt_id, "frames": ghost.frames,
                               "strat": ghost.strat_tag,
                               "native_available": bool(pose["available"]),
                               "native_reason_text": pose["reason_text"],
                               "pose_gaps": pose.get("pose_gaps", 0),
                               "whole_available": bool(video["available"]),
                               "whole_reason_text": video["reason_text"]})
        self._cache = signature, now + CANDIDATE_CACHE_S, candidates
        return [dict(row) for row in candidates]

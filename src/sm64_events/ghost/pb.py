"""THE PB GHOST: his current personal best is the ghost he races by default.

His words, 2026-09-22: *"Any new PB should become a ghost immediately. If the
user unmarks something as a PB, it should be removed as a ghost."* So the PB
ghost is DERIVED from the pbs table on every resolve, never stored: saving a PB
makes it the answer, undoing (or hiding) that PB makes the previous save the
answer again, with nothing to keep in step.

It is not an input template. Templates also drive the input comparison, and a
template he imported from another player must keep driving it after he sets a
PB. So the PB reaches the ghost through `GhostLibrary`: `pb_for` is the PB box
in the ghost picker (`selection.py::chosen_ghosts`), and `active_for` -- the
one-recording question whole-picture readers ask -- answers with the PB first
and the active input template second.
"""
from __future__ import annotations

from dataclasses import dataclass

PB_NAME = "Your PB"


@dataclass(frozen=True)
class AttemptGhost:
    """One recorded attempt raced as a ghost: his PB, a saved ghost, or a
    template's run. Shaped like `inputs/templates.py::Template` where the
    ghost readers read one (`id`, `origin`, `kind`, `entity_key`).

    `id` is the NEGATED attempt id: every real template id is positive, so an
    attempt ghost can never be mistaken for a template row.
    `frames` is the run's time on its own clock (IGT for a star, RTA for a
    segment; the PB's saved clock for a PB), which is how the picker names it.
    """
    attempt_id: int
    kind: str
    entity_key: str
    strat_tag: str | None
    frames: int | None = None
    pb: bool = False

    @property
    def id(self) -> int:
        return -self.attempt_id

    @property
    def origin(self) -> str:
        return f"attempt:{self.attempt_id}"

    @property
    def name(self) -> str:
        return PB_NAME if self.pb else f"attempt #{self.attempt_id}"


def current_pb_ghost(db, kind: str, entity_key: str, strat: str | None) -> AttemptGhost | None:
    """The latest visible PB save for this target and strategy, any timer.

    Latest save wins across IGT and RTA: the newest PB he validated is the run
    he wants to race, whichever clock he graded it on. Segments are RTA-only.
    """
    lookup = getattr(db, "current_pb", None)  # a db-less or state-only boot has no PBs
    if lookup is None:
        return None
    if kind == "star":
        course, star = (int(part) for part in entity_key.split("-"))
        where = {"course_id": course, "star_id": star}
        modes = ("igt", "rta")
    elif kind == "segment":
        where = {"course_id": None, "star_id": None, "segment_id": int(entity_key)}
        modes = ("rta",)
    else:
        return None
    rows = [row for mode in modes
            if (row := lookup(timer_mode=mode, strat_tag=strat, **where)) is not None
            and row.get("attempt_id")]
    if not rows:
        return None
    row = max(rows, key=lambda item: item["id"])
    return AttemptGhost(attempt_id=row["attempt_id"], kind=kind, entity_key=entity_key,
                        strat_tag=row.get("strat_tag"), frames=row["frames"], pb=True)


class GhostLibrary:
    """Every ghost a target can race, as the ghost sees them: his PB, his saved
    ghosts (`ghost/saved.py`), and the runs his templates were marked from.

    `attempt_of(attempt_id)` returns the projected attempt row or raises; it
    names each run by its own time.
    """

    def __init__(self, templates, db, attempt_of=None):
        self._templates = templates
        self._db = db
        self._attempt_of = attempt_of

    def active_for(self, kind, entity_key, strat):
        return self.pb_for(kind, entity_key, strat) or self.template_for(kind, entity_key, strat)

    def template_for(self, kind, entity_key, strat):
        return self._templates.active_for(kind, entity_key, strat)

    def pb_for(self, kind, entity_key, strat):
        return current_pb_ghost(self._db, kind, entity_key, strat)

    def get(self, template_id):
        return self._templates.get(template_id)

    def list_for(self, kind, entity_key):
        return self._templates.list_for(kind, entity_key)

    def ghosts_for(self, kind, entity_key) -> list[AttemptGhost]:
        """Saved ghosts, then template runs, newest first; one per attempt."""
        from sm64_events.ghost.saved import saved_for
        from sm64_events.ghost.source import attempt_id_of

        ids = {number: None for number in
               (saved_for(self._db, kind, entity_key) if hasattr(self._db, "get_state") else [])}
        for template in self._templates.list_for(kind, entity_key):
            number = attempt_id_of(template.origin)
            if number is not None and ids.get(number) is None:
                ids[number] = template.strat_tag
        return [self.attempt_ghost(number, kind, entity_key, strat) for number, strat in ids.items()]

    def attempt_ghost(self, attempt_id, kind, entity_key, strat=None) -> AttemptGhost:
        row = None
        if self._attempt_of is not None:
            try:
                row = self._attempt_of(attempt_id)
            except (LookupError, RuntimeError, ValueError):
                row = None
        frames = None
        if row is not None:
            frames = getattr(row, "rta_frames" if kind == "segment" else "igt_frames", None)
        return AttemptGhost(attempt_id=attempt_id, kind=kind, entity_key=entity_key,
                            strat_tag=getattr(row, "strat_tag", strat) if row is not None else strat,
                            frames=frames)

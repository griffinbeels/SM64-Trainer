"""THE PB GHOST: his current personal best is the ghost he races by default.

His words, 2026-09-22: *"Any new PB should become a ghost immediately. If the
user unmarks something as a PB, it should be removed as a ghost."* So the PB
ghost is DERIVED from the pbs table on every resolve, never stored: saving a PB
makes it the answer, undoing (or hiding) that PB makes the previous save the
answer again, with nothing to keep in step.

It is not an input template. Templates also drive the input comparison, and a
template he imported from another player must keep driving it after he sets a
PB. So the PB reaches the ghost through `GhostLibrary`: `pb_for` is the PB box
in the ghost picker (`selection.py::chosen_ghosts`). With that box ticked and
no PB saved yet, the active input template supplies a fallback.
"""
from __future__ import annotations

from dataclasses import dataclass

PB_NAME = "Your PB"


def attempt_id_of(origin: str) -> int | None:
    """The local attempt behind a template; imported/authored origins have none."""
    prefix, sep, rest = origin.partition(":")
    return int(rest) if prefix == "attempt" and sep and rest.isdigit() else None


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


def current_pb_ghost(db, kind: str, entity_key: str, strat: str | None, *,
                     running: str | None = None, ranks=None) -> AttemptGhost | None:
    """The latest visible PB save for this target and strategy, any timer.

    Latest save wins across IGT and RTA: the newest PB he validated is the run
    he wants to race, whichever clock he graded it on. Segments are RTA-only.
    With the `running` ROM named, the PB slot and whether its run may play
    follow the strategy's region scope (`ranks/region_scope.py`).
    """
    from sm64_events.ranks import region_scope
    from sm64_events.tracking.views import pb_rom

    lookup = getattr(db, "current_pb", None)  # a db-less or state-only boot has no PBs
    if lookup is None:
        return None
    if kind == "star":
        course, star = (int(part) for part in entity_key.split("-"))
        where = {"course_id": course, "star_id": star}
        modes = ("igt", "rta")
        ek = f"star:{course}:{star}"
    elif kind == "segment":
        where = {"course_id": None, "star_id": None, "segment_id": int(entity_key)}
        modes = ("rta",)
        ek = f"segment:{int(entity_key)}"
    else:
        return None
    slot = ({"game_version": running, "region_scope": region_scope.scope_of(ek, strat, ranks)}
            if running in region_scope.VERSIONS else {})
    rows = [row for mode in modes
            if (row := lookup(timer_mode=mode, strat_tag=strat, **where, **slot)) is not None
            and row.get("attempt_id")]
    if not rows:
        return None
    row = max(rows, key=lambda item: item["id"])
    if slot and pb_rom(row) is not None and not region_scope.serves(slot["region_scope"], running, pb_rom(row)):
        return None
    return AttemptGhost(attempt_id=row["attempt_id"], kind=kind, entity_key=entity_key,
                        strat_tag=row.get("strat_tag"), frames=row["frames"], pb=True)


def eligible_attempt(attempt, identity) -> bool:
    """Population admission requires judged complete evidence.

    Local saves with unknown validity, ROM or star timing remain privately
    playable; those facts cannot certify a population recording. A 100-coin
    star reattributed from the segment engine retains that engine's known clock
    span instead of pretending it began at a star reset.
    """
    return bool(getattr(attempt, "strat_tag", None)) and eligible_capture(attempt, identity)


def eligible_capture(attempt, identity) -> bool:
    """A complete certificate may retain poses before its strategy is chosen.

    This grants no population membership. Admission still requires a strategy,
    its ceiling and subdivision checks, and complete recorded inputs.
    """
    from sm64_events.inputs.service import entity_key_of
    from sm64_events.tracking.caveats import pb_blocked_by, save_state_run

    if (identity.clock not in ("igt", "rta") or identity.game_version not in ("us", "jp")
            or attempt is None or getattr(attempt, "valid", None) is not True
            or attempt.outcome != "success" or attempt.cleared or save_state_run(attempt)
            or pb_blocked_by(attempt) or getattr(attempt, "timed_by", None) == "imported"
            or identity.clock == "igt" and getattr(attempt, "timed_by", None) != "igt"
            or getattr(attempt, "game_version", None) != identity.game_version
            or entity_key_of(attempt) != (identity.kind, identity.entity_key)):
        return False
    start, end = getattr(attempt, "native_clock_frame", None), getattr(attempt, "closed_frame", None)
    frames = getattr(attempt, f"{identity.clock}_frames", None)
    if (type(start) is not int or type(end) is not int or start < 0 or end < start
            or not getattr(attempt, "native_clock_utc", None) or type(frames) is not int or frames <= 0):
        return False
    native_kind = getattr(attempt, "native_clock_kind", None)
    if identity.kind == "segment":
        return native_kind == "segment"
    if native_kind == "segment":
        return 1 <= attempt.course_id <= 15 and attempt.star_id == 6
    return native_kind == "star" and getattr(attempt, "timed_at", None) == "xcam"


def local_attempt_source(attempt) -> dict | None:
    """Local owner lineage, never an identity claimed by a private import.

    Session and start time distinguish attempt numbers reused by fresh profiles.
    Population admission derives this from its verified projected attempt; these
    fields must not be accepted as evidence in a future remote provider.
    """
    number = getattr(attempt, "id", None)
    session = getattr(attempt, "session_id", None)
    started = getattr(attempt, "started_utc", None)
    if type(number) is not int or number <= 0 or type(session) is not int or session <= 0 or not started:
        return None
    return {"session_id": session, "attempt_id": number, "started_utc": started}


def saved_pb_basis(db, ranks, identity) -> dict | None:
    """The current strategy's saved PB, graded on the selected Practice scope.

    Resolve the latest visible slot through the canonical practice lookup. A
    deliberately slower save replaces its predecessor; an undo reveals the
    predecessor through database visibility. Historical minima never participate.
    Local legacy and imported saves remain legitimate rank anchors; strict
    population certificates are a separate contract. The rank survives expired
    pose files and incompatible ROMs. Playback needs a compatible local owner,
    and the native provider decides whether its pose files can be prepared.
    """
    from sm64_events.ghost.adaptive import rank_frames, region_serves
    from sm64_events.inputs.service import entity_key_of
    from sm64_events.tracking.views import current_pbs_by_strat, pb_rom

    if db is None or ranks is None or not identity.strategy or identity.game_version not in ("us", "jp"):
        return None
    if identity.kind == "segment":
        key = ("segment", int(identity.entity_key), identity.clock, identity.strategy)
    elif identity.kind == "star":
        course, star = map(int, identity.entity_key.split("-"))
        key = (course, star, identity.clock, identity.strategy)
    else:
        return None
    # The slot the running ROM sees: its own PB for a regional strategy, the
    # faster ROM's for a shared one (`ranks/region_scope.py`).
    row = current_pbs_by_strat(db.pbs(), running=identity.game_version, ranks=ranks).get(key)
    if row is None or type(row.get("frames")) is not int or row["frames"] <= 0:
        return None
    progress = rank_frames(ranks, identity, row["frames"], version=pb_rom(row))
    if progress is None:
        return None
    number = row.get("attempt_id")
    attempt = db.attempt(number) if type(number) is int and number > 0 else None
    source, ghost = local_attempt_source(attempt), None
    # The PB ghost is the slot's own run. A run whose ROM nobody recorded is
    # one the slot already counts for this ROM ("same for both", like an
    # unversioned import), so it keeps playing -- the reported Wild Blue PB;
    # a known ROM plays where the strategy's region scope serves it.
    played_on = getattr(attempt, "game_version", None)
    if (attempt is not None and entity_key_of(attempt) == (identity.kind, identity.entity_key)
            and getattr(attempt, "strat_tag", None) in (None, identity.strategy)
            and (played_on is None or region_serves(identity, identity.strategy, played_on, ranks))):
        ghost = AttemptGhost(number, identity.kind, identity.entity_key,
                             row["strat_tag"], row["frames"], pb=True)
    return {"frames": row["frames"], **progress, "attempt_id": row["attempt_id"],
            "pb_id": row["id"], "strategy": row["strat_tag"], "current_strategy": identity.strategy,
            "game_version": pb_rom(row), "source": source, "ghost": ghost, "scope": identity.scope}


class GhostLibrary:
    """Every ghost a target can race, as the ghost sees them: his PB, his saved
    ghosts (`ghost/saved.py`), and the runs his templates were marked from.

    `attempt_of(attempt_id)` returns the projected attempt row or raises; it
    names each run by its own time.
    """

    def __init__(self, templates, db, attempt_of=None, portable=None, *, running=None, ranks=None):
        self._templates = templates
        self._db = db
        self._attempt_of = attempt_of
        self._portable = portable
        # The ROM the emulator runs and the standards that judge region scope:
        # the PB ghost is the running ROM's PB slot (`current_pb_ghost`).
        self._running = running
        self._ranks = ranks

    @property
    def generation(self):
        return getattr(self._portable, "generation", None)

    def imports_for(self, kind, entity_key):
        return self._portable.imports_for(kind, entity_key) if self._portable is not None else []

    def imported(self, import_id):
        if self._portable is None:
            raise LookupError("That imported ghost is no longer available.")
        return self._portable.get(import_id)


    def template_for(self, kind, entity_key, strat):
        return self._templates.active_for(kind, entity_key, strat)

    def pb_for(self, kind, entity_key, strat):
        return current_pb_ghost(self._db, kind, entity_key, strat,
                                running=self._running, ranks=self._ranks)

    def get(self, template_id):
        if isinstance(template_id, str):
            return self.imported(template_id)
        return self._templates.get(template_id)

    def list_for(self, kind, entity_key):
        return self._templates.list_for(kind, entity_key)

    def ghosts_for(self, kind, entity_key) -> list[AttemptGhost]:
        """Saved ghosts, then template runs, newest first; one per attempt."""
        from sm64_events.ghost.saved import saved_for

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

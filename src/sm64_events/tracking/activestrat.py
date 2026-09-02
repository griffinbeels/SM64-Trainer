"""Which strategy is an entity being PRACTISED with -- one answer, one door.

Everything that shows or acts on "the active strategy" asks here: the
practice card (its PB tag, its gate chip, the `◀ you` marker), the
quick-select medals, the route candidates' medals, AND the PB save/undo
door in `tracking/service.py`. Until 2026-08-22 the same rule was restated
at six call sites inside `tracking/views.py` -- each picking the Bowser
reds/pipe family reject itself -- plus a seventh in `active_strat_for` for
the service, and the route candidates applied the tombstone but never the
family reject. A second reading of "which strategy is active" is how the
API starts accepting a save the button refuses, which is the exact failure
`tracking/pbaction.py` exists to prevent; so the rule lives here, takes
IDENTITY (a star, a segment, an attempt) and never ingredients, and
`tests/test_single_source.py` fences the tombstone read to this file.

Two ingredients, both resolved once per `ActiveStrats`:

* the TOMBSTONES (`deleted_strats` in the KV): a fully deleted strategy must
  never surface as active -- the dropdowns no longer offer it;
* the Bowser reds/pipe FAMILY: a Bowser course's 8-Red-Coins star practices
  as two things worth timing -- the grab alone (" (Star)" strategies) or the
  whole reds-then-pipe run (" (Pipe)" strategies) -- and BOTH ladders live on
  the star entity, so a star must never read a " (Pipe)" name as its active
  strategy and the paired segment must never read a " (Star)" one. Nothing
  stopped a pre-2026-07-30 pick from landing on the wrong side before the
  family toggle existed to keep them apart; masking it to None self-heals on
  the next pick from the now family-filtered dropdown, no data migration.
"""
from __future__ import annotations

from sm64_events.memory.addresses import COURSE_BY_LEVEL
from sm64_events.ranks.standards import entity_key
from sm64_events.tracking.segments import start_levels

STAR_FAMILY_SUFFIX = " (Star)"
PIPE_FAMILY_SUFFIX = " (Pipe)"
_REDS_PIPE_SEED_PREFIX = "seg:reds->pipe:"


def reds_pipe_segments(seg_rows: list[dict]) -> tuple[dict[int, int], dict[int, str]]:
    """The Bowser reds/pipe pairing: (course_id -> segment_id,
    segment_id -> the paired star's entity_key).

    Measured (`rank_standards.seed.json`): star:16/17/18:0 carry paired
    "X (Star)"/"X (Pipe)" strategy names, and the `seg:reds->pipe:<abbrev>`
    definition that actually records the Pipe-family attempts has no rank
    entity of its own. So grading the Pipe family means pairing the STAR's
    ladder with the SEGMENT's own (rta) history -- this is the resolver every
    grading call site borrows the star's entity_key from, and the ONLY place
    that decides which segment that is.

    Matched by seed_key prefix, never start_levels alone: the legacy
    exclusive "no reds" pipe-only segment (`seg:<abbrev>-pipe`) starts in the
    SAME level and would be indistinguishable otherwise."""
    by_course: dict[int, int] = {}
    grading_ek: dict[int, str] = {}
    for row in seg_rows:
        if not (row.get("seed_key") or "").startswith(_REDS_PIPE_SEED_PREFIX):
            continue
        for level in start_levels(row["start_triggers"]):
            course = COURSE_BY_LEVEL.get(level)
            if course is not None:
                by_course[course] = row["id"]
                grading_ek[row["id"]] = entity_key(course, 0)
                break
    return by_course, grading_ek


def masked_strat(strat, deleted_names, reject_suffix=None):
    """The primitive: a strategy NAME as every surface must read it, or None.
    Tombstoned -> None; carrying the OTHER family's suffix -> None. Callers
    outside this module go through `ActiveStrats`, which picks both
    ingredients from identity -- this stays public only for the one reader
    (the practice target) that carries its own strategy name."""
    if strat and strat in deleted_names:
        return None
    if reject_suffix and strat and strat.endswith(reject_suffix):
        return None
    return strat


class ActiveStrats:
    """The active strategy of any entity, from identity alone.

    Built once per session-view build (which already holds every ingredient)
    or once per PB command in `service.py` (`from_db`, one KV read and one
    segment_defs read -- a click, not a hot path). Reads the two LIVE
    strategy maps rather than the db so a pick made this event is already in
    force."""

    def __init__(self, strat_by_star: dict, strat_by_segment: dict,
                 deleted_strats: dict, reds_pipe_by_course: dict[int, int],
                 reds_pipe_grading_ek: dict[int, str]):
        self._by_star = strat_by_star
        self._by_segment = strat_by_segment
        self._deleted = deleted_strats
        self._reds_pipe_by_course = reds_pipe_by_course
        self._reds_pipe_grading_ek = reds_pipe_grading_ek

    @classmethod
    def from_db(cls, db, strat_by_star: dict, strat_by_segment: dict) -> "ActiveStrats":
        by_course, grading_ek = reds_pipe_segments(db.segment_defs())
        return cls(strat_by_star, strat_by_segment,
                   db.get_state("deleted_strats", {}), by_course, grading_ek)

    def mask(self, strat, ek: str):
        """Tombstone-only masking of a name the caller already holds -- the
        practice target carries its own `strat_tag` rather than reading the
        strategy maps, so it cannot go through `for_star`/`for_segment`."""
        return masked_strat(strat, self._deleted.get(ek, []))

    def for_star(self, course_id: int, star_id: int) -> str | None:
        reject = (PIPE_FAMILY_SUFFIX
                  if star_id == 0 and course_id in self._reds_pipe_by_course
                  else None)
        return masked_strat(self._by_star.get((course_id, star_id)),
                            self._deleted.get(entity_key(course_id, star_id), []),
                            reject)

    def for_segment(self, segment_id: int) -> str | None:
        reject = (STAR_FAMILY_SUFFIX
                  if segment_id in self._reds_pipe_grading_ek else None)
        return masked_strat(self._by_segment.get(segment_id),
                            self._deleted.get(entity_key(None, None, segment_id), []),
                            reject)

    def for_attempt(self, attempt) -> str | None:
        """None for an attempt with no entity (the unassigned list), which
        `pbaction.pb_action` refuses before it ever asks about a strategy."""
        if attempt.segment_id is not None:
            return self.for_segment(attempt.segment_id)
        if attempt.course_id is None:
            return None
        return self.for_star(attempt.course_id, attempt.star_id)

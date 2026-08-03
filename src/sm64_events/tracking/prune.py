"""The startup prune of UNLABELLED attempts — the rule, as a pure function.

An attempt is *unlabelled* when nothing recorded on it says what it was
practice FOR: it names no star and no segment at all (the "Unassigned
attempts" block of the practice log), or it names one but carries no
strategy. Either way there is no way to look at the row later and know what
it was, which is the whole of the user's reasoning for deleting them
(task 0076): "we should assume that the user probably won't remember them,
and probably doesn't care about them."

Runs ONCE per server start, over the attempts a previous session left
behind, and never over the session in progress — the rows he is making
right now are exactly the ones he still remembers. His words: "this is only
done at STARTUP because in this scenario, the user probably has restarted
their session / it's a new day, so anything they cared about was already
saved and stored."

**Anything he deliberately SAVED is protected**, which is the same sentence
read forwards: a saved PB or a saved replay clip IS the act of caring, so
the attempt behind it is not a row he has forgotten. Both protections are
load-bearing rather than defensive, measured against his live journal before
this shipped:

- 34 `pbs` rows sit on unlabelled attempts and 16 of those are the CURRENT
  PB for their star or segment. `db.delete_orphaned_pbs()` runs on every
  re-projection and HARD-deletes a pb row whose attempt no longer exists —
  and the `pbs` table is not journal-derived, so unlike the prune itself
  that deletion could never be undone.
- 5 of 15 saved clips on disk are for unlabelled attempts. A clip is found
  by `attempt_<id>_*.mp4` (replay/service.py), so an attempt that stops
  existing leaves its file unreachable from the UI forever.

The prune is a COMPENSATING EVENT carrying explicit ids, not a rule
re-evaluated on every replay — see `projection.replay`. Deciding once and
journaling the answer is what makes a past prune immutable: were the rule
re-run at replay time, deleting a PB row later would silently widen a prune
that already happened.
"""
from __future__ import annotations

from collections.abc import Iterable

#: Journal event type carrying `{"attempt_ids": [...]}`. Applied
#: retroactively on replay, exactly like `data_wiped`.
PRUNE_EVENT = "attempts_pruned"


def unlabelled(attempt) -> bool:
    """Does this attempt fail to say what it was practice for?

    The unassigned test is `segment_id is None and course_id is None`, the
    same one `views.build_session_view` uses to route a row into the
    "Unassigned attempts" block — a star row always carries a course, a
    segment row always carries a segment id.
    """
    if attempt.segment_id is None and attempt.course_id is None:
        return True
    return not attempt.strat_tag


def prunable_ids(attempts: Iterable, protected: set[int]) -> list[int]:
    """The attempt ids this prune should drop, oldest first.

    `protected` is every id the user deliberately saved something for; see
    the module docstring for why an unprotected prune is unrecoverable.
    """
    return [a.id for a in attempts
            if a.id not in protected and unlabelled(a)]

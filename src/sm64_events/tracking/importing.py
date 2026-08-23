"""Deciding which brought-in times become personal bests.

An IMPORTED TIME is a personal best the trainer never watched him set — typed
by hand, lifted off a runner's Ultimate Sheet column, or read out of a linked
sheet. Every source produces the same
`ImportCandidate` and lands through the same rule here, so a sixth source is
a parser rather than a feature. What lands is a real attempt row (see
`IMPORT_EVENT`) carrying its personal best.

The rule is IMPROVEMENT, and it is what makes the whole thing safe to press
twice: a candidate lands only when it BEATS the current best for that target
and that strategy, so re-importing costs nothing and can never move a personal
best backwards.

Two details are load-bearing rather than tidy:

  * The comparison is per STRATEGY, not per star. A first time on a strategy he
    has never run must land even while he holds a faster best on another
    strategy for the same star — `db.current_pb` is strategy-blind unless it is
    given a `strat_tag`, and that argument is the whole difference.
  * The comparison also counts what THIS batch has already landed. Rows are
    inserted in order and the latest row wins (`views.current_pbs_by_strat`), so
    a batch holding the same target twice would otherwise leave the SLOWER row
    current — the exact regression this rule exists to prevent.

Pure: no database, no I/O, no clock. The caller supplies the lookup.
"""
from dataclasses import dataclass, field
from typing import Callable

from sm64_events.core import timefmt

# The journal event one landed time becomes. The PROJECTOR turns it into the
# attempt row he sees — "It should show the new entry in the practice log as an
# entry row... it then affords us all of the functionality of a practice log
# entry row (deleting, undoing, etc)" (2026-08-22) — so the row is rebuilt from
# the journal on every replay like every other attempt, rather than being an
# attempts-table insert that the next reproject would drop.
IMPORT_EVENT = "time_imported"


@dataclass(frozen=True)
class ImportCandidate:
    """One brought-in time, whatever produced it.

    `game_version` is the ROM this time was SET on ("us"/"jp"), or None when
    the source does not say — then it grades on the running version, which is
    what every time stored before this feature does."""
    entity_key: str
    strat_tag: str
    time_cs: int
    game_version: str | None = None
    timer_mode: str = "igt"


@dataclass
class ImportPlan:
    """`landing` pairs each surviving candidate with its frame count;
    `summary` counts the whole batch for the one sentence the UI shows."""
    landing: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)


def decide(candidates, current_frames: Callable) -> ImportPlan:
    """Which candidates land, and the count of what did not.

    `current_frames(entity_key, strat_tag, timer_mode) -> int | None` is the
    player's current best for exactly that combination, in game frames."""
    landing, already_faster, unmappable, unstrategised = [], 0, 0, 0
    landed_best: dict[tuple, int] = {}
    for candidate in candidates:
        if not candidate.entity_key or candidate.time_cs <= 0:
            unmappable += 1
            continue
        if not candidate.strat_tag:
            # A time with NO strategy still lands, and is counted so it is
            # never silent. It was refused until typed names existed, when
            # every source (the sheet, the card's own picker) always had one —
            # but most people writing down a gold write the star and the time
            # and nothing else, and refusing those would reject the bulk of a
            # real sheet. The store already allows it: such a row shows as a
            # personal best and never GRADES, because `current_pbs_by_strat`
            # cannot attribute it, and `tracking/caveats.py`'s `unattributed`
            # mark is what says so where the click lands.
            unstrategised += 1
        # The frame is the real unit; centiseconds are only how the timer
        # prints. Rounding UP is the conservative direction — it never credits
        # him with a time the timer could not display.
        frames = timefmt.frame_at_or_after(candidate.time_cs)
        key = (candidate.entity_key, candidate.strat_tag, candidate.timer_mode)
        existing = landed_best.get(key)
        if existing is None:
            existing = current_frames(*key)
        if existing is not None and existing <= frames:
            already_faster += 1
            continue
        landed_best[key] = frames
        landing.append((candidate, frames))
    return ImportPlan(
        landing=landing,
        summary={"found": len(candidates), "imported": len(landing),
                 "already_faster": already_faster, "unmappable": unmappable,
                 # Counted, not hidden: a best with no strategy shows a time
                 # and no rank, and the player deserves to know how many of
                 # theirs arrived that way.
                 "without_strategy": unstrategised})

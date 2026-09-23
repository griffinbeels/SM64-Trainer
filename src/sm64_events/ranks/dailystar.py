"""The Daily Star: one Scorecard entry per calendar day, the same for everyone.

Task 0111, his words: "any user using the app on the same date should have the
same practice suggestion" and "we rotate through every single star in the
overall scoreboard". No server decides it -- the local calendar DATE alone
picks the entry, the way a daily-seed game does, so two installs agree as long
as they run the same `DECK`.

The rotation is a shuffled deck. Every cycle of `len(DECK)` days deals each
entry exactly once, in an order seeded by the cycle number, repaired so two
consecutive days never land in the same course. Day 1 is `EPOCH` and deals his
own example first ("EVERYONE today will practice Whomp's Fortress star Into the
Wild Blue").

`DECK` is frozen here rather than read from the Scorecard at runtime: a card
change must not silently reshuffle every future day for one app version and
not another. `tests/test_daily_star.py` fails when the Overall card's default
entries and this deck disagree, so that change is a deliberate edit.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date
from functools import lru_cache

from sm64_events.memory.addresses import COURSE_NAMES

__all__ = ["EPOCH", "FIRST_KEY", "DECK", "DailyEntry", "daily_entry",
           "cycle_order", "course_of"]

EPOCH = date(2026, 9, 23)
FIRST_KEY = "star:2:2"          # Whomp's Fortress, Shoot into the Wild Blue

# The Overall Scorecard's default cells in card order: each course's 100c star
# folded into its companion's cell (`star:C:6`), the seven secret-stage stars,
# the three Bowser reds each followed by its fight. The castle's Toad and MIPS
# stars sit out, as they do on the card by default. A fight is named by its
# SEED key (`seg:bowser-N`), the only handle that is the same on every install.
DECK: tuple[str, ...] = (
    "star:1:0", "star:1:1", "star:1:2", "star:1:6", "star:1:4", "star:1:5",
    "star:2:0", "star:2:1", "star:2:2", "star:2:6", "star:2:4", "star:2:5",
    "star:3:0", "star:3:1", "star:3:2", "star:3:6", "star:3:4", "star:3:5",
    "star:4:0", "star:4:1", "star:4:6", "star:4:3", "star:4:4", "star:4:5",
    "star:5:0", "star:5:1", "star:5:2", "star:5:6", "star:5:4", "star:5:5",
    "star:6:0", "star:6:6", "star:6:2", "star:6:3", "star:6:4", "star:6:5",
    "star:7:0", "star:7:1", "star:7:2", "star:7:3", "star:7:6", "star:7:5",
    "star:8:0", "star:8:1", "star:8:2", "star:8:3", "star:8:4", "star:8:6",
    "star:9:0", "star:9:1", "star:9:6", "star:9:3", "star:9:4", "star:9:5",
    "star:10:0", "star:10:1", "star:10:2", "star:10:3", "star:10:6", "star:10:5",
    "star:11:0", "star:11:1", "star:11:2", "star:11:3", "star:11:6", "star:11:5",
    "star:12:0", "star:12:1", "star:12:6", "star:12:3", "star:12:4", "star:12:5",
    "star:13:0", "star:13:1", "star:13:2", "star:13:3", "star:13:6", "star:13:5",
    "star:14:0", "star:14:1", "star:14:2", "star:14:6", "star:14:4", "star:14:5",
    "star:15:0", "star:15:6", "star:15:2", "star:15:3", "star:15:4", "star:15:5",
    "star:19:0", "star:19:1", "star:24:0", "star:21:0", "star:22:0", "star:20:0",
    "star:23:0", "star:16:0", "seg:bowser-1", "star:17:0", "seg:bowser-2", "star:18:0",
    "seg:bowser-3",
)

# Bowser N's fight happens in the stage whose reds star is `star:(15+N):0`.
_FIGHT_COURSE_BASE = 15


def course_of(key: str) -> int:
    """The course an entry is played in; a fight counts as its Bowser stage,
    so the reds star and its fight are never dealt on consecutive days."""
    if key.startswith("seg:bowser-"):
        return _FIGHT_COURSE_BASE + int(key[len("seg:bowser-"):])
    return int(key.split(":")[1])


def _first_valid(order: list[str], start: int, avoid: int) -> int | None:
    for index in range(start, len(order)):
        if course_of(order[index]) != avoid:
            return index
    return None


def _spread(order: list[str], previous_course: int | None) -> list[str]:
    """Swap entries forward until no two neighbours share a course. Each swap
    pulls a later entry back, so a position once settled is never revisited."""
    for index in range(len(order)):
        avoid = course_of(order[index - 1]) if index else previous_course
        if avoid is None or course_of(order[index]) != avoid:
            continue
        swap = _first_valid(order, index + 1, avoid)
        if swap is None:
            # The tail ran out of other courses; move this entry back to the
            # earliest slot where it touches neither neighbour's course.
            key = order.pop(index)
            for slot in range(1, index):
                if course_of(order[slot - 1]) != course_of(key) != course_of(order[slot]):
                    order.insert(slot, key)
                    break
            else:
                order.insert(index, key)
            continue
        order[index], order[swap] = order[swap], order[index]
    return order


@lru_cache(maxsize=64)
def _own_order(cycle: int) -> tuple[str, ...]:
    """Cycle `cycle` from its own seed alone. A string seed hashes the same
    on every Python build, which is what keeps installs in agreement."""
    order = list(DECK)
    random.Random(f"sm64-daily-star/{cycle}").shuffle(order)
    if cycle == 0:
        order.remove(FIRST_KEY)
        order.insert(0, FIRST_KEY)
        return tuple([FIRST_KEY] + _spread(order[1:], course_of(FIRST_KEY)))
    return tuple(_spread(order, None))


def cycle_order(cycle: int) -> tuple[str, ...]:
    """The deck as cycle `cycle` deals it, forever: any cycle number works.

    Only the FIRST card may move to meet the previous cycle, and it never
    touches the last card, so a cycle's last card is its own order's last
    card. That keeps every cycle one lookup away from its neighbour rather
    than a chain back to Day 1 -- the prototype recursed through every
    earlier cycle and a date far enough ahead would overflow the stack
    (round 1, his ask: "continues to cycle FOREVER, for the rest of time")."""
    order = list(_own_order(cycle))
    if cycle == 0:
        return tuple(order)
    avoid = course_of(_own_order(cycle - 1)[-1])
    if course_of(order[0]) != avoid:
        return tuple(order)
    for index in range(1, len(order) - 1):
        course = course_of(order[index])
        if course in (avoid, course_of(order[0])) \
                or course_of(order[index - 1]) == course_of(order[index + 1]):
            continue
        return tuple([order[index]] + order[:index] + order[index + 1:])
    return tuple(order)


@dataclass(frozen=True)
class DailyEntry:
    date: date
    day: int                 # Day 1 is EPOCH; days before it count down from 0
    key: str

    @property
    def course_name(self) -> str:
        return COURSE_NAMES.get(course_of(self.key), "")


def daily_entry(day: date) -> DailyEntry:
    offset = (day - EPOCH).days
    cycle, position = divmod(offset, len(DECK))
    return DailyEntry(date=day, day=offset + 1,
                      key=cycle_order(cycle)[position])

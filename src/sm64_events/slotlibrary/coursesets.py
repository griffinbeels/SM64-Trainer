"""Course sets: which kept states go into the slots for a level, or for one star of it.

A set is an ordered list of up to nine kept states; position n goes to slot n, and slots past the end of the
list keep what they hold. A state may be written whatever the game is doing, so that entering a course can
fill the slots. Usamune's own load refuses a state whose gMarioObject, level or area differ from the running
game, so every placement reports whether those match, and never changes the slot's word to make them match:
in the simulation a patched word crashes the game on the load frame.

Before the first write, every occupied slot is captured. A failure there writes nothing.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from sm64_events.slotlibrary.store import Damaged, KeptState, SlotLibrary, write_atomic
from sm64_events.slotlibrary.usamune import (
    CURRENT_AREA_POINTER,
    SEGMENT_BYTES,
    SEGMENT_TABLE,
    SLOT_COUNT,
    GameMemory,
    Refusal,
    SlotFacts,
    WriteFailed,
    check_cartridge,
    live_facts,
    routine_fingerprint,
    same_segments,
    slot_facts,
    slot_range,
    write_slot_bytes,
)
from sm64_events.slotlibrary.watcher import capture_all, read_slot

SETS_VERSION = 1


@dataclass(frozen=True)
class Placement:
    slot: int
    state_id: str
    written: bool            # False when the slot already held these exact bytes, or on a dry run
    mario_matches: bool      # Usamune refuses the load when this is False: re-enter the level and try again
    place_matches: bool      # level and area; Usamune refuses the load when this is False too
    pointers_match: bool | None  # the level's load addresses against the save's; None when the save has no record

    @property
    def loadable_now(self) -> bool:
        return self.mario_matches and self.place_matches


@dataclass(frozen=True)
class InstallReport:
    placements: tuple[Placement, ...]
    captured: tuple[KeptState, ...]
    failed: tuple[int, str] | None = None    # the slot whose write failed; slots after it were not attempted


def _pointers_match(memory: GameMemory, state: KeptState) -> bool | None:
    context = state.context
    if context is None:
        return None
    return (int.from_bytes(memory.read(CURRENT_AREA_POINTER, 4), "big") == context.current_area
            and same_segments(memory.read(SEGMENT_TABLE, SEGMENT_BYTES), context.segments))


def _checked_states(memory: GameMemory, library: SlotLibrary,
                    placements: Mapping[int, str]) -> list[tuple[int, KeptState, bytes, SlotFacts]]:
    """Everything that could refuse, before anything is captured or written."""
    fingerprint = routine_fingerprint(memory.read)
    loaded = []
    for slot, identity in sorted(placements.items()):
        slot_range(slot)
        try:
            state, data = library.get(identity), library.read_bytes(identity)
        except Damaged as error:
            raise Refusal(str(error)) from error
        if state.fingerprint != fingerprint:
            raise Refusal(f"state {identity} came from another Usamune build")
        loaded.append((slot, state, data, slot_facts(data)))
    return loaded


def place_states(memory: GameMemory, library: SlotLibrary, placements: Mapping[int, str],
                 *, dry_run: bool = False) -> InstallReport:
    """Checks everything, captures every occupied slot, then writes each state into its slot."""
    check_cartridge(memory)
    loaded = _checked_states(memory, library, placements)
    captured = () if dry_run else capture_all(memory, library)
    live = live_facts(memory)
    done: list[Placement] = []
    for slot, state, data, facts in loaded:
        written = not dry_run and read_slot(memory, slot) != data
        if written:
            try:
                write_slot_bytes(memory, slot, data)
            except WriteFailed as error:
                return InstallReport(tuple(done), captured, (slot, str(error)))
        done.append(Placement(slot, state.id, written, facts.mario_object == live.mario_object,
                              (facts.level, facts.area) == (live.level, live.area), _pointers_match(memory, state)))
    return InstallReport(tuple(done), captured)


def _well_formed(entry: object) -> bool:
    return (isinstance(entry, dict) and type(entry.get("level")) is int
            and (entry.get("star") is None or isinstance(entry.get("star"), str))
            and isinstance(entry.get("states"), list) and all(isinstance(s, str) for s in entry["states"]))


class CourseSets:
    """One versioned file. A file it cannot read stops it, so it can never overwrite his sets with nothing."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def all(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            stored = json.loads(self.path.read_bytes())
        except (OSError, ValueError, RecursionError) as error:
            raise Damaged(f"{self.path.name}: {error}") from error
        if (not isinstance(stored, dict) or stored.get("version") != SETS_VERSION
                or not isinstance(stored.get("sets"), list) or not all(_well_formed(e) for e in stored["sets"])):
            raise Damaged(f"{self.path.name}: not a version {SETS_VERSION} file")
        return stored["sets"]

    def assign(self, library: SlotLibrary, level: int, star: str | None, states: Sequence[str]) -> None:
        """Replace the set for (level, star). An empty list removes it. The kept states stay in the library."""
        if len(states) > SLOT_COUNT:
            raise Refusal(f"Usamune has {SLOT_COUNT} slots, this set has {len(states)} states")
        for identity in states:
            library.get(identity)
        sets = [entry for entry in self.all() if (entry["level"], entry["star"]) != (level, star)]
        if states:
            sets.append({"level": level, "star": star, "states": list(states)})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(self.path, json.dumps({"version": SETS_VERSION, "sets": sets}, indent=1).encode("utf-8"))

    def lookup(self, level: int, star: str | None) -> list[str] | None:
        """The star's own set, else the level's. None when neither exists."""
        sets = {(entry["level"], entry["star"]): entry["states"] for entry in self.all()}
        found = sets.get((level, star)) or sets.get((level, None))
        return list(found) if found else None


def install_for_course(memory: GameMemory, library: SlotLibrary, sets: CourseSets, level: int, star: str | None,
                       *, dry_run: bool = False) -> InstallReport | None:
    """What a course entry calls. None when this course has no set, and then nothing was touched."""
    states = sets.lookup(level, star)
    if states is None:
        return None
    return place_states(memory, library, dict(enumerate(states)), dry_run=dry_run)

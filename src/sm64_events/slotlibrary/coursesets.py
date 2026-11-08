"""Course sets: which kept states go into the slots for a level, or for one star of it.

A set is an ordered list of up to nine kept states; position n goes to slot n, and slots past the end of the
list keep what they hold. A state may be written whatever the game is doing, so that entering a course can
fill the slots. Usamune's own load refuses a state whose gMarioObject, level or area differ from the running
game, so every placement reports whether those match. Every state's bytes go through `relocate.to_live_place`
after any `Transform`: Mario moves to the running game's place inside the slot data, and the kept state never
changes. Patching only the occupied word crashes the game on the load frame in the simulation. Griffin's rule
(2026-11-05): Ghost Handoff writes data only, inside slot ranges, and never adds or patches code in the game or
Usamune. A state the other Usamune build made (US in a JP game, or back) goes through `translate.for_build`
before anything else, with its place's atlas; its placement says so in words.

Before the first write, every occupied slot is captured. A failure there writes nothing.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from sm64_events.slotlibrary.atlas import AtlasStore
from sm64_events.slotlibrary.relocate import to_live_place
from sm64_events.slotlibrary.store import Damaged, KeptState, SlotLibrary, write_atomic
from sm64_events.slotlibrary.translate import Translation, for_build
from sm64_events.slotlibrary.usamune import (
    SEGMENT_BYTES,
    SLOT_COUNT,
    Build,
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
Transform = Callable[[KeptState, bytes], bytes]  # slot data in, slot data to write out; the kept state never changes


@dataclass(frozen=True)
class Placement:
    slot: int
    state_id: str
    written: bool            # False when the slot already held these exact bytes, or on a dry run
    mario_matches: bool      # Usamune refuses the load when this is False: re-enter the level and try again
    place_matches: bool      # level and area; Usamune refuses the load when this is False too
    pointers_match: bool | None  # the level's load addresses against the save's; None when the save has no record
    translated_from: str | None = None   # the other build the state came from ("us" or "jp"), when it was translated
    words: str = ""                      # what translating it changed, in plain words

    @property
    def loadable_now(self) -> bool:
        return self.mario_matches and self.place_matches


@dataclass(frozen=True)
class InstallReport:
    placements: tuple[Placement, ...]
    captured: tuple[KeptState, ...]
    failed: tuple[int, str] | None = None    # the slot whose write failed; slots after it were not attempted


def _pointers_match(memory: GameMemory, state: KeptState, build: Build) -> bool | None:
    context = state.context
    if context is None:
        return None
    return (int.from_bytes(memory.read(build.current_area_pointer, 4), "big") == context.current_area
            and same_segments(memory.read(build.segment_table, SEGMENT_BYTES), context.segments))


def _translated(memory: GameMemory, build: Build, state: KeptState, data: bytes,
                atlases: AtlasStore | None) -> Translation:
    """A state the other build made, as the running build's (slotlibrary/translate.py); a pointer with no place
    there refuses it."""
    if atlases is None:
        raise Refusal(f"state {state.id} is from Usamune {state.build.upper()}, and nothing here can translate it")
    translation = for_build(memory, build, data, state.build, atlases.get(state.level, state.area))
    if translation.unmapped:
        raise Refusal(f"state {state.id}: {len(translation.unmapped)} pointers have no place in the "
                      f"{build.version.upper()} game")
    return translation


def _checked_states(memory: GameMemory, library: SlotLibrary, placements: Mapping[int, str],
                    transform: Transform | None, build: Build, atlases: AtlasStore | None,
                    ) -> list[tuple[int, KeptState, bytes, SlotFacts, Translation | None]]:
    """Everything that could refuse, before anything is captured or written."""
    fingerprint = routine_fingerprint(memory.read, build)
    loaded = []
    for slot, identity in sorted(placements.items()):
        slot_range(slot)
        try:
            state, data = library.get(identity), library.read_bytes(identity)
        except Damaged as error:
            raise Refusal(str(error)) from error
        translation = None
        if state.fingerprint != fingerprint:
            if state.build is None or state.build == build.version:
                raise Refusal(f"state {identity} came from another Usamune build")
            translation = _translated(memory, build, state, data, atlases)
            data = translation.data
        if transform is not None:
            data = transform(state, data)
        data = to_live_place(memory, data, build)
        loaded.append((slot, state, data, slot_facts(data), translation))
    return loaded


def place_states(memory: GameMemory, library: SlotLibrary, placements: Mapping[int, str],
                 *, dry_run: bool = False, transform: Transform | None = None,
                 atlases: AtlasStore | None = None) -> InstallReport:
    """Checks everything, captures every occupied slot, then writes each state into its slot. A transform runs
    with the checks, so its refusal writes nothing, and the report describes the bytes it returned. A state from
    the other Usamune build is translated first, with the atlas `atlases` holds or builds for its place."""
    build = check_cartridge(memory)
    loaded = _checked_states(memory, library, placements, transform, build, atlases)
    captured = () if dry_run else capture_all(memory, library, build)
    live = live_facts(memory, build)
    done: list[Placement] = []
    for slot, state, data, facts, translation in loaded:
        written = not dry_run and read_slot(memory, slot) != data
        if written:
            try:
                write_slot_bytes(memory, build, slot, data, mario_place=(
                    facts.mario_object if facts.mario_object == live.mario_object else None))
            except WriteFailed as error:
                return InstallReport(tuple(done), captured, (slot, str(error)))
        done.append(Placement(slot, state.id, written, facts.mario_object == live.mario_object,
                              (facts.level, facts.area) == (live.level, live.area),
                              True if translation else _pointers_match(memory, state, build),
                              translation.source if translation else None, translation.words() if translation else ""))
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
                       *, dry_run: bool = False, atlases: AtlasStore | None = None) -> InstallReport | None:
    """What a course entry calls. None when this course has no set, and then nothing was touched."""
    states = sets.lookup(level, star)
    if states is None:
        return None
    return place_states(memory, library, dict(enumerate(states)), dry_run=dry_run, atlases=atlases)

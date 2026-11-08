"""Course sets: which kept states go into which Usamune slots for a course, and for each star of it.

Griffin's words (2026-11-08): "for every single star, we should be able to have custom save states that override
the default stage-level save states (if there's a slot set)." A set maps slots (1-9) to kept states: the course
default, and one per star. The star's own slots override the default slot by slot (`CourseSets.resolved`); slots
no set names keep what they hold. slotlibrary/keeper.py decides which set applies and when it is written.

A state may be written whatever the game is doing. Usamune's own load refuses a state whose gMarioObject, level or area differ from the running
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
import threading
from collections.abc import Callable, Mapping
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
                 atlases: AtlasStore | None = None, ours: Mapping[int, str] | None = None) -> InstallReport:
    """Checks everything, captures every occupied slot, then writes each state into its slot. A transform runs
    with the checks, so its refusal writes nothing, and the report describes the bytes it returned. A state from
    the other Usamune build is translated first, with the atlas `atlases` holds or builds for its place.
    ours: slot index -> hash of the bytes the Trainer itself last wrote there; those are a placed state, not a
    save of his, so the capture skips them (keeper.SlotLedger)."""
    build = check_cartridge(memory)
    loaded = _checked_states(memory, library, placements, transform, build, atlases)
    captured = () if dry_run else capture_all(memory, library, build, ours)
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


SETS_VERSION = 2
SLOTS = range(1, SLOT_COUNT + 1)          # Usamune's own slot numbers, as its menu shows them
DEFAULT = "default"


def _slot_map(raw: object) -> dict[int, str] | None:
    """{slot number: state id} from its stored form, or None when it is not one."""
    if not isinstance(raw, dict):
        return None
    out = {}
    for slot, identity in raw.items():
        if not (isinstance(slot, str) and slot.isdigit() and int(slot) in SLOTS and isinstance(identity, str)):
            return None
        out[int(slot)] = identity
    return out


def _from_v1(entries: object) -> dict:
    """Version 1 held {level, star (a string or null), states [ordered]} per set. Each becomes its course's
    default (star null) or that star's set, position n going to slot n; a level outside every course, or a star
    that is not a number, cannot be placed in version 2 and is left out."""
    from sm64_events.memory.addresses import course_for_level
    if not isinstance(entries, list):
        raise ValueError("not a list of sets")
    courses: dict = {}
    for entry in entries:
        if not (isinstance(entry, dict) and type(entry.get("level")) is int and isinstance(entry.get("states"), list)
                and all(isinstance(s, str) for s in entry["states"])):
            raise ValueError("a set is malformed")
        course, star = course_for_level(entry["level"]), entry.get("star")
        if course is None or not (star is None or isinstance(star, str) and star.isdigit()):
            continue
        record = courses.setdefault(str(course), {DEFAULT: {}, "stars": {}})
        target = record[DEFAULT] if star is None else record["stars"].setdefault(str(int(star)), {})
        target.update({str(n): identity for n, identity in zip(SLOTS, entry["states"], strict=False)})
    return courses


class CourseSets:
    """For each course: the course default, and one set per star, each a map of Usamune's slots (1-9) to kept
    states. A star's set overrides the course default slot by slot (`resolved`). One versioned file
    (<library>/course-sets.json); a file it cannot read stops it, so it can never overwrite his sets with nothing.
    Version 1 (ordered lists keyed by level) is read and converted, and written back as version 2."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            stored = json.loads(self.path.read_bytes())
            if not isinstance(stored, dict):
                raise ValueError("not an object")
            if stored.get("version") == 1:
                return _from_v1(stored.get("sets"))
            if stored.get("version") != SETS_VERSION or not isinstance(stored.get("courses"), dict):
                raise ValueError(f"not a version {SETS_VERSION} file")
            for course, record in stored["courses"].items():
                if not (course.isdigit() and isinstance(record, dict) and _slot_map(record.get(DEFAULT)) is not None
                        and isinstance(record.get("stars"), dict)
                        and all(star.isdigit() and _slot_map(slots) is not None
                                for star, slots in record["stars"].items())):
                    raise ValueError(f"course {course} is malformed")
            return stored["courses"]
        except (OSError, ValueError, RecursionError, AttributeError) as error:
            raise Damaged(f"{self.path.name}: {error}") from error

    def _write(self, courses: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(self.path, json.dumps({"version": SETS_VERSION, "courses": courses}, indent=1).encode("utf-8"))

    def slots(self, course: int, star: int | None) -> dict[int, str]:
        """One set: the course default when star is None."""
        record = self._read().get(str(course), {})
        raw = record.get(DEFAULT, {}) if star is None else record.get("stars", {}).get(str(star), {})
        return _slot_map(raw) or {}

    def resolved(self, course: int, star: int | None) -> dict[int, tuple[str, str]]:
        """slot -> (state id, "star" | "course"): the course default, with the star's own slots over it."""
        found = {slot: (identity, "course") for slot, identity in self.slots(course, None).items()}
        if star is not None:
            found.update({slot: (identity, "star") for slot, identity in self.slots(course, star).items()})
        return found

    def named(self, course: int) -> set[str]:
        """Every kept state any set of this course names."""
        record = self._read().get(str(course), {})
        maps = [record.get(DEFAULT, {}), *record.get("stars", {}).values()]
        return {identity for raw in maps for identity in (_slot_map(raw) or {}).values()}

    def courses(self) -> dict:
        return self._read()

    def put(self, library: SlotLibrary, course: int, star: int | None, slot: int, identity: str) -> None:
        """This kept state at this slot of this set, replacing what the set held there."""
        if slot not in SLOTS:
            raise Refusal(f"Usamune has slots {SLOTS.start} to {SLOTS.stop - 1}, not {slot}")
        library.get(identity)
        with self._lock:
            courses = self._read()
            record = courses.setdefault(str(course), {DEFAULT: {}, "stars": {}})
            target = record[DEFAULT] if star is None else record["stars"].setdefault(str(star), {})
            target[str(slot)] = identity
            self._write(courses)

    def remove(self, course: int, star: int | None, slot: int) -> bool:
        """Takes this slot out of this set; the kept state stays in the library. False when it named nothing."""
        with self._lock:
            courses = self._read()
            record = courses.get(str(course))
            if record is None:
                return False
            target = record.get(DEFAULT, {}) if star is None else record.get("stars", {}).get(str(star), {})
            if target.pop(str(slot), None) is None:
                return False
            if star is not None and not target:
                record["stars"].pop(str(star), None)
            if not record.get(DEFAULT) and not record.get("stars"):
                courses.pop(str(course))
            self._write(courses)
            return True

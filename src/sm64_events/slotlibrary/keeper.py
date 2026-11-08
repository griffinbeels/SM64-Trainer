"""Star sets in the running game: which set applies now, writing it when that changes, and taking his saves into it.

Griffin's words (2026-11-08): "when the user loads into a course for the first time in a session, they haven't
selected a star ... So, we load the default set of save states for that course for them. ... Then, when the user
grabs a star or selects a star, we now know which one they're trying to practice, so we can load their
star-specific save states. ... They should also persist across resets. As the user swaps between different stars,
the different save states should automatically load into memory".

The context is (the course Mario stands in, a star or none). Entering a course takes the practice target's star when
that star is in this course, else no star: the course default. Picking a star target, or grabbing a star, in this
course switches to that star. A context change writes every slot the resolved set names (coursesets.place_states:
every occupied slot captured first, translation and relocation as for any write), and empties a slot that holds
bytes the Trainer itself wrote for another set of this course. Anything else is left as it is. A new save of his
into slot N joins the current context's set at N. Nothing is written while no set names anything.

The ledger tells the Trainer's bytes from his: the hash of what it last wrote into each slot and the kept state that
came from. Relocation and translation make new bytes, so a slot's own hash never names its kept state.

Every pass runs on the slot refit's one worker thread with its one reader (refit.SlotRefitter), under `lock`; the
Ghost Lab's write takes the same lock, so no two writers meet in a slot, and a spawn refit never races a context
write.

The strip also shows which slot he loaded last in this context: a `state_loaded` event marks a load, and the next
watch reads the slot Usamune's load read (usamune.load_slot). That slot keeps the mark while it holds the state
he loaded and the context stays. A state made from a Scene viewer frame shows that frame's picture (the Slot
Library keeps it; server/scene_moments.py takes it at the write).
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from types import SimpleNamespace

from sm64_events.memory.addresses import course_for_level, course_name, star_name
from sm64_events.slotlibrary.atlas import AtlasStore
from sm64_events.slotlibrary.coursesets import SLOTS, CourseSets, InstallReport, place_states
from sm64_events.slotlibrary.store import Damaged, KeptState, SlotLibrary, state_id
from sm64_events.slotlibrary.usamune import (
    SLOT_COUNT,
    GameMemory,
    Refusal,
    WriteFailed,
    check_cartridge,
    empty_slot,
    is_occupied,
    live_facts,
    load_slot,
)
from sm64_events.slotlibrary.watcher import keep, read_slot

log = logging.getLogger("sm64.slotlibrary")

STAR_SIGNALS = frozenset({"target_changed", "star_collected"})
LOAD_SIGNAL = "state_loaded"
PICTURE_ROUTE = "/api/scene-lab/slot-sets/pictures/{id}"     # server/scene_lab_api.py serves it


@dataclass(frozen=True)
class Context:
    course: int
    star: int | None             # None: the course default alone

    @property
    def entity_key(self) -> str | None:
        """The practice target's key in the spelling the ghost and input layers use ("2-3"); None for no star."""
        if self.star is None:
            return None
        from sm64_events.inputs.service import entity_key_of
        return entity_key_of(SimpleNamespace(course_id=self.course, star_id=self.star, segment_id=None))[1]


def picture_url(library: SlotLibrary, identity: str | None) -> str | None:
    """Where the page reads a kept state's frame picture; None when it has none."""
    if identity is None:
        return None
    try:
        return PICTURE_ROUTE.format(id=identity) if library.picture_path(identity) is not None else None
    except Refusal:
        return None


def empty_entry(slot: int) -> dict:
    return {"slot": slot, "set": None, "state": None, "translated": None, "under": None, "picture": None,
            "loaded_last": False}


def empty_view(game: dict | None = None) -> dict:
    """The strip's answer outside a course, and on a server that keeps no star sets."""
    return {"context": None, "game": game, "slots": [empty_entry(slot) for slot in SLOTS]}


class SlotLedger:
    """For each slot (index 0-8), the kept state the Trainer last wrote there and the hash of the bytes written."""

    def __init__(self):
        self._slots: dict[int, tuple[str | None, str]] = {}

    def wrote(self, index: int, state: str | None, data: bytes) -> None:
        self._slots[index] = (state, state_id(data))

    def forget(self, index: int) -> None:
        self._slots.pop(index, None)

    def ours(self, index: int, data: bytes) -> bool:
        entry = self._slots.get(index)
        return entry is not None and entry[1] == state_id(data)

    def state_in(self, index: int, data: bytes) -> str | None:
        """The kept state these bytes are, when the Trainer wrote them; None when he has saved over them since."""
        entry = self._slots.get(index)
        return entry[0] if entry is not None and entry[1] == state_id(data) else None

    def hashes(self) -> dict[int, str]:
        return {index: digest for index, (_state, digest) in self._slots.items()}


@dataclass
class ApplyReport:
    context: Context | None
    written: list[int] = field(default_factory=list)        # slot numbers (1-9) now holding their set's state
    emptied: list[int] = field(default_factory=list)        # slot numbers that held another set's state
    refused: list[tuple[int, str]] = field(default_factory=list)   # slot number, why it was not written
    captured: int = 0                                        # his saves kept before the write


class SetKeeper:
    """The context, the ledger and the sets of the running game. Not thread-safe by itself: callers hold `lock`."""

    def __init__(self, library: SlotLibrary, sets: CourseSets, atlases: AtlasStore | None,
                 target: Callable[[], tuple | None] = lambda: None,
                 on_change: Callable[[dict], None] = lambda payload: None):
        self.library, self.sets, self.atlases = library, sets, atlases
        self.target, self.on_change = target, on_change
        self.lock = threading.RLock()
        self.ledger = SlotLedger()
        self.context: Context | None = None      # where the game is now, as the last pass read it
        self.applied: Context | None = None      # the context the slots were last written for
        self.build: str | None = None            # the running game's version, as the last pass read it
        self._course: int | None = None
        self._hint: tuple[int, int] | None = None
        self._seen: dict[int, str | None] | None = None   # None: the next watch only takes stock
        self._load_seen = False                            # a state_loaded came; the next watch reads the slot
        self._loaded: tuple[Context, int, str | None] | None = None   # the context, slot (1-9) and state loaded

    # -- what decides the context ---------------------------------------------------------------------

    def observe(self, event) -> bool:
        """A star target pick or a star grab: remembered as the star to switch to. True when it may change the
        context (the caller then schedules a pass). A load is only noted: the next watch reads which slot it was."""
        kind = getattr(event, "type", None)
        if kind == LOAD_SIGNAL:
            with self.lock:
                self._load_seen = True
            return False
        if kind not in STAR_SIGNALS:
            return False
        payload = getattr(event, "payload", None) or {}
        course, star = payload.get("course_id"), payload.get("star_id")
        if payload.get("kind") == "segment" or type(course) is not int or type(star) is not int:
            return False
        with self.lock:
            self._hint = (course, star)
        return True

    def locate(self, memory: GameMemory, build) -> Context | None:
        """The context now. Entering a course reads the target; a remembered pick or grab in this course switches
        the star; anywhere else (the castle, a menu, no Mario) is no context."""
        live = live_facts(memory, build)
        if not live.mario_object:
            return self.context                   # between levels or loading: nothing to decide yet
        course = course_for_level(live.level)
        if course is None:
            self._course, self._hint, self.context = None, None, None
            return None
        if course != self._course:
            self._course = course
            target = self.target()
            star = target[2] if target and target[0] == "star" and target[1] == course else None
            self.context = Context(course, star)
        if self._hint is not None:
            if self._hint[0] == course:
                self.context = Context(course, self._hint[1])
            self._hint = None
        return self.context

    # -- writing a context's set ------------------------------------------------------------------------

    def apply(self, memory: GameMemory, *, dry_run: bool = False, context: Context | None = None) -> ApplyReport | None:
        """Writes the resolved set when the context differs from the one last written. None when nothing changed.
        context: write this one now, whatever the game says (the command line's set-install)."""
        build = check_cartridge(memory)
        self.build = build.version
        if context is not None:
            self.context = context
        else:
            context = self.locate(memory, build)
            if context is None or context == self.applied:
                return None
        report = ApplyReport(context)
        resolved = self.sets.resolved(context.course, context.star)
        if not dry_run:
            self._empty_others(memory, context, resolved, report)
        placements = {slot - 1: identity for slot, (identity, _which) in resolved.items()}
        if placements:
            self._place(memory, placements, report, dry_run)
        if not dry_run:
            self.applied = context
        log.info("Star sets: %s; wrote slots %s, emptied %s, refused %s.", self.words(context), report.written,
                 report.emptied, [slot for slot, _why in report.refused])
        self.on_change(self.view())
        return report

    def _empty_others(self, memory: GameMemory, context: Context, resolved: dict, report: ApplyReport) -> None:
        named = self.sets.named(context.course)
        for index in range(SLOT_COUNT):
            if index + 1 in resolved:
                continue
            data = read_slot(memory, index)
            if is_occupied(data) and self.ledger.state_in(index, data) in named:
                try:
                    empty_slot(memory, index)
                except WriteFailed as failed:
                    report.refused.append((index + 1, str(failed)))
                    continue
                self.ledger.forget(index)
                self._remember_seen(index, None)
                report.emptied.append(index + 1)

    def _place(self, memory: GameMemory, placements: dict[int, str], report: ApplyReport, dry_run: bool) -> None:
        """All at once; when one refuses, each on its own, so the rest are still written."""
        results: list[InstallReport] = []
        try:
            results.append(self._place_some(memory, placements, dry_run))
        except Refusal:
            for index, identity in sorted(placements.items()):
                try:
                    results.append(self._place_some(memory, {index: identity}, dry_run))
                except Refusal as refused:
                    report.refused.append((index + 1, str(refused)))
        for result in results:
            report.captured += len(result.captured)
            for placed in result.placements:
                report.written.append(placed.slot + 1)
                if not dry_run:
                    data = read_slot(memory, placed.slot)
                    self.ledger.wrote(placed.slot, placed.state_id, data)
                    self._remember_seen(placed.slot, data)
            if result.failed:
                report.refused.append((result.failed[0] + 1, result.failed[1]))

    def _place_some(self, memory: GameMemory, placements: dict[int, str], dry_run: bool) -> InstallReport:
        return place_states(memory, self.library, placements, dry_run=dry_run, atlases=self.atlases,
                            ours=self.ledger.hashes())

    # -- his saves ----------------------------------------------------------------------------------------

    def _remember_seen(self, index: int, data: bytes | None) -> None:
        if self._seen is not None:
            self._seen[index] = state_id(data) if data is not None and is_occupied(data) else None

    def forget_game(self) -> None:
        """Another game, or none: the slots are unknown again, so the next watch takes stock before it assigns."""
        self._seen, self.applied, self.context, self._course = None, None, None, None
        self._loaded, self._load_seen = None, False
        self.ledger = SlotLedger()

    def _note_load(self, memory: GameMemory, build, context: Context | None) -> bool:
        """After a state_loaded: the slot Usamune's load read, and the state the set names there. True when the
        strip's answer changed."""
        if not self._load_seen:
            return False
        self._load_seen = False
        index = load_slot(memory, build)
        if context is None or index is None:
            return False
        identity, _which = self.sets.resolved(context.course, context.star).get(index + 1, (None, None))
        self._loaded = (context, index + 1, identity)
        log.debug("Star sets: %s; he loaded slot %d.", self.words(context), index + 1)
        return True

    def watch(self, memory: GameMemory) -> list[tuple[int, KeptState]]:
        """His new saves: a slot whose bytes changed and are not the Trainer's joins the library, and the current
        context's set at that slot when it is a state of this course. The first watch of a game only takes stock:
        what the slots held before is not a save he just made."""
        build = check_cartridge(memory)
        self.build = build.version
        context = self.locate(memory, build)
        loaded = self._note_load(memory, build, context)
        stock = self._seen is None
        seen = self._seen = self._seen or {}
        assigned = []
        for index in range(SLOT_COUNT):
            data = read_slot(memory, index)
            digest = state_id(data) if is_occupied(data) else None
            if digest == seen.get(index, ...) or digest is None or self.ledger.ours(index, data):
                seen[index] = digest
                continue
            if read_slot(memory, index) != data:
                continue                                  # Usamune is writing it now; the next watch sees it whole
            seen[index] = digest
            if stock:
                continue
            try:
                state = keep(memory, self.library, data, build)[0]
            except (Refusal, OSError, Damaged) as error:
                log.warning("Star sets: slot %d was not kept: %s", index + 1, error)
                continue
            self.ledger.wrote(index, state.id, data)
            if context is not None and course_for_level(state.level) == context.course:
                self.sets.put(self.library, context.course, context.star, index + 1, state.id)
                assigned.append((index + 1, state))
        if assigned:
            log.info("Star sets: %s took his saves in slots %s.", self.words(context), [slot for slot, _ in assigned])
        if assigned or loaded:
            self.on_change(self.view())
        return assigned

    def claim(self, index: int, state: str, data: bytes, course: int | None, star: int | None) -> str | None:
        """A state the Trainer just wrote into slot `index` for (course, star): it joins that set there.
        Returns "star", "course", or None when no course was named or known."""
        self.ledger.wrote(index, state, data)
        self._remember_seen(index, data)
        if course is None and self.context is not None:
            course, star = self.context.course, self.context.star
        if course is None:
            return None
        self.sets.put(self.library, course, star, index + 1, state)
        self.on_change(self.view())
        return "course" if star is None else "star"

    # -- what the page reads ------------------------------------------------------------------------------

    @staticmethod
    def words(context: Context | None) -> str:
        if context is None:
            return "no course"
        name = course_name(context.course)
        return name if context.star is None else f"{name}, {star_name(context.course, context.star)}"

    def _state_json(self, identity: str) -> dict | None:
        from sm64_events.tracking.eventlabel import place_name
        try:
            state = self.library.get(identity)
        except (Refusal, Damaged):
            return None
        return {"id": state.id, "name": state.name, "level": state.level, "area": state.area,
                "place": place_name(state.level, state.area), "build": state.build, "source": state.source,
                "first_seen_utc": state.first_seen_utc}

    def view(self) -> dict:
        """The Ghost Lab strip's answer: the context and Usamune's nine slots as the resolved set names them."""
        context, build = self.context, self.build
        game = None if build is None else {"build": build}
        if context is None:
            return empty_view(game)
        resolved = self.sets.resolved(context.course, context.star)
        default = self.sets.slots(context.course, None)
        loaded = self._loaded if self._loaded is not None and self._loaded[0] == context else None
        slots = []
        for slot in SLOTS:
            identity, which = resolved.get(slot, (None, None))
            state = self._state_json(identity) if identity else None
            under = default.get(slot) if which == "star" and default.get(slot) != identity else None
            slots.append({"slot": slot, "set": which, "state": state,
                          "translated": None if state is None or build is None or state["build"] is None
                          else state["build"] != build,
                          "under": self._state_json(under) if under else None,
                          "picture": picture_url(self.library, identity) if state is not None else None,
                          "loaded_last": loaded is not None and loaded[1:] == (slot, identity)})
        return {"context": {"course_id": context.course, "course": course_name(context.course),
                            "star_id": context.star,
                            "star": None if context.star is None else star_name(context.course, context.star),
                            "entity_key": context.entity_key},
                "game": game, "slots": slots}

    # -- the page's edits ---------------------------------------------------------------------------------

    def move(self, slot: int, to: str) -> dict:
        """The slot's resolved state into the star's set or the course default at the same slot."""
        context = self.context
        if context is None:
            raise Refusal("there is no course now")
        if to == "star" and context.star is None:
            raise Refusal("no star is chosen in this course")
        identity, which = self.sets.resolved(context.course, context.star).get(slot, (None, None))
        if identity is None:
            raise LookupError(f"slot {slot} names no save state")
        if which != to:
            self.sets.put(self.library, context.course, None if to == "course" else context.star, slot, identity)
            self.sets.remove(context.course, context.star if which == "star" else None, slot)
        self.applied = None                       # the next pass writes the resolved set again
        payload = self.view()
        self.on_change(payload)
        return payload

    def remove(self, slot: int, which: str) -> dict:
        context = self.context
        if context is None:
            raise Refusal("there is no course now")
        if which == "star" and context.star is None:
            raise Refusal("no star is chosen in this course")
        if not self.sets.remove(context.course, context.star if which == "star" else None, slot):
            raise LookupError(f"slot {slot} is not in that set")
        payload = self.view()
        self.on_change(payload)
        return payload

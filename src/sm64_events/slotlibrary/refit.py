"""Slot refit: after every spawn, each occupied Usamune slot made for the place the game is in now gets its Mario
moved to the object place the running game just gave him, so Usamune's own load takes it (lab only).

Griffin's words (Ghost Handoff round 1, item 2): "what if we did that automatically when the user resets and
spawns in, so that all the save states are already ready to go, based on the slots the user configured?" A practice
reset keeps the slots in RAM but moves gMarioObject, and Usamune refuses a slot whose Mario word differs. So after
each spawn this rewrites, data only, every occupied slot whose trailer level and area match the game's, through
`relocate.to_live_place` and the one checked write (`usamune.write_slot_bytes`, which reads Mario's place again
before the occupied word goes in). Slots for other places are never touched, and a slot already in place is never
rewritten.

`refit_slots` is one pass. A slot whose bytes changed between the first read and the write is skipped: he may have
just saved over it. A slot whose pointers cannot be read by structure is refused without a write.

`SlotRefitter` is its trigger. The broadcaster's observer only stamps a deadline; one worker thread with its own
reader waits until no spawn event came for SETTLE_SECONDS (a reset journals practice_reset, area_changed and spawned
inside a few frames, so they run once), checks Mario's place reads the same twice, then runs the pass. It never
runs on the poller or the event loop.

What it keeps. Before a slot's bytes are refit they join the Slot Library as captured, unless they are bytes this
refitter wrote itself: those are a move of a state already kept. The library keeps one state per distinct bytes, so
resets add nothing; only a new save of his does. Keeping runs after the writes, so a compress never delays a slot
he is about to load. What it records: one INFO line per pass, and one line per pass in data/slot_refit.jsonl beside
the journal (bounded, read back by tools/what_happened.py). A refit is the Trainer's action, not the game's, so it
is never a journal event.
"""
from __future__ import annotations

import functools
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sm64_events.slotlibrary.keeper import SetKeeper, SlotLedger
from sm64_events.slotlibrary.relocate import Ambiguous, to_live_place
from sm64_events.slotlibrary.store import Damaged, SlotLibrary
from sm64_events.slotlibrary.usamune import (
    SLOT_COUNT,
    Build,
    GameMemory,
    MarioMoved,
    Refusal,
    WriteFailed,
    check_cartridge,
    is_occupied,
    live_facts,
    slot_facts,
    with_live_clocks,
    write_slot_bytes,
)
from sm64_events.slotlibrary.watcher import keep, read_slot

log = logging.getLogger("sm64.slotlibrary")

REFIT, ALREADY_FIT, CHANGED, REFUSED = "refit", "already_fit", "changed", "refused"
OUTCOMES = (REFIT, ALREADY_FIT, CHANGED, REFUSED)
# Why a slot was refused, as a kind: the details name addresses and go to the debug log only.
UNCLEAR, NOT_A_SLOT, MOVED, WRITE, GAME = "unclear", "not_a_slot", "moved", "write", "game"
TRIGGERS = frozenset({"practice_reset", "spawned", "level_changed", "area_changed"})
SETTLE_SECONDS = 0.25        # quiet after the last spawn event: one reset's events land within a few frames
STEADY_SECONDS = 0.05        # Mario's place must read the same across this gap
STEADY_TRIES = 20            # about a second of an unsettled game before the pass gives up
IDLE = ("idle",)             # _quiet's answer when a pass already took every trigger
WATCH_SECONDS = 0.5          # how often the slots are read for his new saves (nine slots: about 5 ms a read)
LOG_NAME = "slot_refit.jsonl"
LOG_MAX_RECORDS, LOG_TRIM_ABOVE_BYTES = 2000, 500_000


@dataclass(frozen=True)
class SlotOutcome:
    slot: int                # Usamune's slot index, 0 to 8
    outcome: str             # one of OUTCOMES
    why: str = ""            # REFUSED only: one of the kinds above


@dataclass(frozen=True)
class RefitReport:
    level: int | None = None
    area: int | None = None
    slots: tuple[SlotOutcome, ...] = ()
    not_ready: str | None = None       # the pass did not run: no game, no Mario, or the game did not settle
    originals: tuple[tuple[int, bytes], ...] = field(default=(), repr=False)   # bytes before each attempted write
    written: tuple[tuple[int, bytes], ...] = field(default=(), repr=False)     # bytes each refit slot now holds
    build: Build | None = field(default=None, repr=False)                      # the running game's, when it ran
    trainers: tuple[int, ...] = field(default=(), repr=False)   # slots whose bytes before the write were the Trainer's

    def count(self, outcome: str) -> int:
        return sum(1 for slot in self.slots if slot.outcome == outcome)


def refit_slots(memory: GameMemory, ledger: SlotLedger | None = None) -> RefitReport:
    """One pass over the nine slots. Writes only slots made for the game's current level and area whose Mario sits
    elsewhere; stops at a Mario that moves during a write, because every later slot would be made for the old place.
    ledger: what the Trainer itself wrote into each slot; a refit slot keeps its kept state there, as new bytes."""
    ledger = ledger if ledger is not None else SlotLedger()
    trainers: list[int] = []
    try:
        build = check_cartridge(memory)
    except Refusal as refused:
        return RefitReport(not_ready=str(refused))
    ledger.use_build(build.version)
    live = live_facts(memory, build)
    if live.mario_object == 0:
        return RefitReport(live.level, live.area, not_ready="Mario is not in a level")
    outcomes: list[SlotOutcome] = []
    originals: list[tuple[int, bytes]] = []
    written: list[tuple[int, bytes]] = []
    for index in range(SLOT_COUNT):
        data = read_slot(memory, index)
        if not is_occupied(data):
            continue
        try:
            facts = slot_facts(data)
        except Refusal as refused:
            log.debug("slot refit: slot %d is not a slot: %s", index, refused)
            outcomes.append(SlotOutcome(index, REFUSED, NOT_A_SLOT))
            continue
        if (facts.level, facts.area) != (live.level, live.area):
            continue
        if facts.mario_object == live.mario_object:
            outcomes.append(SlotOutcome(index, ALREADY_FIT))
            continue
        try:
            fitted = with_live_clocks(memory, build, to_live_place(memory, data, build))
        except Refusal as refused:
            log.debug("slot refit: slot %d refused: %s", index, refused)
            outcomes.append(SlotOutcome(index, REFUSED, UNCLEAR if isinstance(refused, Ambiguous) else GAME))
            continue
        if read_slot(memory, index) != data:
            outcomes.append(SlotOutcome(index, CHANGED))
            continue
        originals.append((index, data))
        if ledger.ours(index, data):
            trainers.append(index)
        outcome = _write_fitted(memory, build, index, data, fitted, live.mario_object, ledger)
        outcomes.append(outcome)
        if outcome.outcome == REFIT:
            written.append((index, fitted))
        elif outcome.why == MOVED:
            break
    return RefitReport(live.level, live.area, tuple(outcomes), originals=tuple(originals), written=tuple(written),
                       build=build, trainers=tuple(trainers))


def _write_fitted(memory: GameMemory, build: Build, index: int, original: bytes, fitted: bytes, mario: int,
                  ledger: SlotLedger) -> SlotOutcome:
    """One refit write; the ledger follows: the slot keeps its kept state as new bytes, or is forgotten."""
    kept_state = ledger.state_in(index, original)
    try:
        write_slot_bytes(memory, build, index, fitted, mario_place=mario)
    except MarioMoved as moved:
        log.debug("slot refit: %s", moved)
        ledger.forget(index)
        return SlotOutcome(index, REFUSED, MOVED)
    except WriteFailed as failed:
        log.debug("slot refit: %s", failed)
        ledger.forget(index)
        return SlotOutcome(index, REFUSED, WRITE)
    ledger.wrote(index, kept_state, fitted)
    return SlotOutcome(index, REFIT)


def log_path() -> Path:
    from sm64_events.core.paths import data_root
    return data_root() / "data" / LOG_NAME


def record(report: RefitReport, *, path: Path, triggers: tuple[str, ...], frame: int | None, seconds: float,
           kept: int, now: datetime | None = None) -> dict:
    """One line per pass, beside the journal. Counts and kinds only, never addresses."""
    from sm64_events.core.uilog import append_bounded
    entry = {"server_utc": (now or datetime.now(UTC)).isoformat(), "frame": frame, "triggers": list(triggers),
             "level": report.level, "area": report.area, "not_ready": report.not_ready,
             **{outcome: report.count(outcome) for outcome in OUTCOMES}, "kept": kept,
             "refused_why": [{"slot": slot.slot + 1, "why": slot.why} for slot in report.slots if slot.outcome == REFUSED],
             "ms": round(seconds * 1000, 1)}
    append_bounded(path, entry, LOG_MAX_RECORDS, LOG_TRIM_ABOVE_BYTES)
    return entry


def describe(report: RefitReport) -> str:
    """The INFO line's words: where, and how many of each."""
    if report.not_ready:
        return f"Slot refit did not run: {report.not_ready}."
    from sm64_events.tracking.eventlabel import place_name
    counts = ", ".join(f"{report.count(outcome)} {outcome.replace('_', ' ')}" for outcome in OUTCOMES)
    return f"Slot refit in {place_name(report.level, report.area)}: {counts}."


class SlotRefitter:
    """The spawn trigger: `observe` is the broadcaster's observer, `start`/`stop` own the one worker thread.

    game() attaches a reader of the worker's own; library may be None (nothing is kept). With a keeper
    (keeper.SetKeeper) the same thread also writes a star set when the context changes, before the refit, and
    watches the slots every `watch_seconds` for his new saves; everything it does to the game runs under the
    keeper's lock. Tests pass a clock-free short settle and call `run_once` and `watch_once` directly."""

    def __init__(self, game: Callable[[], GameMemory], library: SlotLibrary | None, *, log_file: Path | None = None,
                 settle_seconds: float = SETTLE_SECONDS, steady_seconds: float = STEADY_SECONDS,
                 on_report: Callable[[RefitReport], None] | None = None, keeper: SetKeeper | None = None,
                 watch_seconds: float = WATCH_SECONDS):
        self.game, self.library, self.log_file = game, library, log_file
        self.settle_seconds, self.steady_seconds = settle_seconds, steady_seconds
        self.on_report, self.keeper, self.watch_seconds = on_report, keeper, watch_seconds
        self.passes = 0
        self._lock = threading.Lock()
        self._due: float | None = None
        self._triggers: list[str] = []
        self._frame: int | None = None
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._memory: GameMemory | None = None
        self._own_ledger = SlotLedger()          # without a keeper: what this refitter itself wrote

    @property
    def ledger(self) -> SlotLedger:
        return self.keeper.ledger if self.keeper is not None else self._own_ledger

    def observe(self, event) -> None:
        """Bounded work on the event loop: stamp the deadline and wake the worker. A star pick or grab counts too,
        when there is a keeper: it may change which star set belongs in the slots."""
        kind = getattr(event, "type", None)
        if kind not in TRIGGERS and not (self.keeper is not None and self.keeper.observe(event)):
            return
        self.poke(kind, getattr(event, "frame", None))

    def poke(self, kind: str = "edit", frame: int | None = None) -> None:
        """A pass soon: after a spawn, a star pick, or an edit of the sets."""
        with self._lock:
            self._due = time.monotonic() + self.settle_seconds
            if kind not in self._triggers:
                self._triggers.append(kind)
            self._frame = frame
        self._wake.set()

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="slot-refit", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stopping.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self._drop_reader()

    def _run(self) -> None:
        while not self._stopping.is_set():
            woken = self._wake.wait(self.watch_seconds if self.keeper is not None else None)
            self._wake.clear()
            if not woken:
                self._try(self.watch_once, "The slot watch failed; it tries again.")
                continue
            taken = self._quiet()
            if taken is None:
                return
            if taken is IDLE:
                continue
            self._try(functools.partial(self.run_once, *taken), "Slot refit failed; the next spawn tries again.")

    def _try(self, work: Callable[[], object], words: str) -> None:
        try:
            work()
        except Exception:  # noqa: BLE001 - a slot pass never costs tracking; the next one tries again
            log.warning(words, exc_info=True)
            self._drop_reader()

    def _quiet(self):
        """Waits until no trigger came for settle_seconds: the (triggers, frame) it coalesced, IDLE when a pass
        already took them, or None to stop."""
        while True:
            with self._lock:
                if self._due is None:
                    return IDLE
                remaining = self._due - time.monotonic()
                if remaining <= 0:
                    taken = (tuple(self._triggers), self._frame)
                    self._due, self._triggers, self._frame = None, [], None
                    return taken
            if self._stopping.wait(remaining):
                return None

    def _reader(self) -> GameMemory | None:
        if self._memory is None:
            try:
                self._memory = self.game()
            except Refusal:
                return None
            if self.keeper is not None:
                with self.keeper.lock:
                    self.keeper.forget_game()     # a new reader may be a new game: take stock again
        return self._memory

    def _drop_reader(self) -> None:
        memory, self._memory = self._memory, None
        detach = getattr(getattr(memory, "reader", None), "detach", None)
        if detach is not None:
            detach()

    def _steady(self, memory: GameMemory, build: Build) -> bool:
        for _ in range(STEADY_TRIES):
            first = live_facts(memory, build)
            if self._stopping.wait(self.steady_seconds):
                return False
            if first.mario_object != 0 and live_facts(memory, build) == first:
                return True
        return False

    def _pass(self, memory: GameMemory) -> RefitReport:
        try:
            build = check_cartridge(memory)
        except Refusal as refused:
            return RefitReport(not_ready=str(refused))
        if not self._steady(memory, build):
            return RefitReport(not_ready="the game did not settle after the spawn")
        if self.keeper is None:
            return refit_slots(memory, self.ledger)
        with self.keeper.lock:
            self.keeper.apply(memory)
            return refit_slots(memory, self.ledger)

    def watch_once(self) -> list:
        """His new saves into the current star set (keeper.SetKeeper.watch); nothing without a keeper or a game."""
        memory = self._reader()
        if self.keeper is None or memory is None:
            return []
        try:
            with self.keeper.lock:
                return self.keeper.watch(memory)
        except Refusal:
            return []

    def run_once(self, triggers: tuple[str, ...] = (), frame: int | None = None) -> RefitReport:
        started = time.perf_counter()
        memory = self._reader()
        if memory is None:
            report = RefitReport(not_ready="no Project64 with a running game")
        else:
            report = self._pass(memory)
        seconds = time.perf_counter() - started
        kept = self._keep_originals(memory, report) if memory is not None else 0
        self.passes += 1
        log.info("%s", describe(report))
        if self.log_file is not None:
            try:
                record(report, path=self.log_file, triggers=triggers, frame=frame, seconds=seconds, kept=kept)
            except OSError:
                log.warning("The slot refit log could not be written.", exc_info=True)
        if self.on_report is not None:
            self.on_report(report)
        return report

    def _keep_originals(self, memory: GameMemory, report: RefitReport) -> int:
        """His saves, before their first refit, into the library. Bytes the Trainer wrote are a move of a state
        already kept, so they are skipped: a reset adds no file. A kept save stays the state its slot holds."""
        kept = 0
        if self.library is not None and report.build is not None:
            fitted = dict(report.written)
            for index, data in report.originals:
                if index in report.trainers:
                    continue
                try:
                    state, is_new = keep(memory, self.library, data, report.build)
                except (Refusal, OSError, Damaged) as error:
                    log.warning("slot refit: slot %d was not kept: %s", index, error)
                    continue
                kept += is_new
                if index in fitted:
                    self.ledger.wrote(index, state.id, fitted[index])
        return kept

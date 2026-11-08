"""Notices new saves: one step reads all nine slots and keeps any occupied slot whose bytes changed.

`watch_step` holds no state of its own: the caller keeps `WatchResult.seen` and passes it back. All nine slots
cost 4.3 ms to read and 0.3 ms to hash (measured live 2026-11-05), and a new state costs a compress and a disk
write, so this must run on a worker of its own and never on the poller's thread.

A slot the Trainer wrote needs no special case: its bytes are already in the library, so the step reports them
as `known`, never as `captured`. Bytes the library has never seen are kept whoever wrote them.

Usamune may be copying into a slot during the read. A changed slot is read again at once and kept only if both
reads agree; if they differ the slot stays unseen, and the next step tries again.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sm64_events.slotlibrary.store import CAPTURED, Damaged, KeptState, LoadContext, SlotLibrary, state_id
from sm64_events.slotlibrary.usamune import (
    SEGMENT_BYTES,
    SLOT_BYTES,
    SLOT_COUNT,
    Build,
    GameMemory,
    Refusal,
    check_cartridge,
    is_occupied,
    live_facts,
    rom_identity,
    routine_fingerprint,
    slot_address,
    slot_facts,
)

log = logging.getLogger("sm64.slotlibrary")

EMPTY = "empty"
NEVER_SEEN: tuple[str | None, ...] = (None,) * SLOT_COUNT


@dataclass(frozen=True)
class WatchResult:
    seen: tuple[str | None, ...]         # pass this to the next step
    captured: list[tuple[int, KeptState]] = field(default_factory=list)  # new to the library: his new saves
    known: list[tuple[int, str]] = field(default_factory=list)   # changed, but the library already holds these bytes
    emptied: list[int] = field(default_factory=list)
    unsettled: list[int] = field(default_factory=list)   # two reads differed: being written now, retried next step
    rejected: list[tuple[int, str]] = field(default_factory=list)   # not bytes a slot can hold; reported once
    failed: list[tuple[int, str]] = field(default_factory=list)     # could not be kept (disk); retried next step
    not_ready: str | None = None         # the running game has no slots to read


def read_slot(memory: GameMemory, index: int) -> bytes:
    return memory.read(slot_address(index), SLOT_BYTES)


def load_context(memory: GameMemory, data: bytes, build: Build) -> LoadContext | None:
    """The level's load addresses, if the game is still where this slot says it was saved."""
    if live_facts(memory, build) != slot_facts(data):
        return None
    return LoadContext(int.from_bytes(memory.read(build.current_area_pointer, 4), "big"),
                       memory.read(build.segment_table, SEGMENT_BYTES))


def keep(memory: GameMemory, library: SlotLibrary, data: bytes, build: Build) -> tuple[KeptState, bool]:
    """Bytes the running game holds, made by its own build, into the library."""
    context = load_context(memory, data, build)
    return library.add(data, source=CAPTURED, fingerprint=routine_fingerprint(memory.read, build),
                       rom=rom_identity(memory), context=context)


def watch_step(memory: GameMemory, library: SlotLibrary, seen: tuple[str | None, ...]) -> WatchResult:
    try:
        build = check_cartridge(memory)
    except Refusal as refused:
        return WatchResult(seen, not_ready=str(refused))
    result = WatchResult(tuple(seen))
    next_seen = list(seen)
    for index in range(SLOT_COUNT):
        data = read_slot(memory, index)
        digest = state_id(data) if is_occupied(data) else EMPTY
        if digest == seen[index]:
            continue
        if digest == EMPTY:
            next_seen[index] = EMPTY
            if seen[index] is not None:
                result.emptied.append(index)
        elif read_slot(memory, index) != data:
            result.unsettled.append(index)
        elif _keep_changed(memory, library, index, data, build, result):
            next_seen[index] = digest
    return WatchResult(tuple(next_seen), result.captured, result.known, result.emptied, result.unsettled,
                       result.rejected, result.failed)


def _keep_changed(memory: GameMemory, library: SlotLibrary, index: int, data: bytes, build: Build,
                  result: WatchResult) -> bool:
    """Files one changed slot in the result. True when the next step need not look at these bytes again."""
    try:
        state, is_new = keep(memory, library, data, build)
    except Refusal as refused:
        result.rejected.append((index, str(refused)))
        log.warning("slot library: slot %d not kept: %s", index, refused)
        return True
    except (OSError, Damaged) as error:
        result.failed.append((index, str(error)))
        log.warning("slot library: slot %d could not be kept: %s", index, error)
        return False
    if is_new:
        result.captured.append((index, state))
    else:
        result.known.append((index, state.id))
    return True


def capture_all(memory: GameMemory, library: SlotLibrary, build: Build) -> tuple[KeptState, ...]:
    """Keep every occupied slot now, whatever was seen before. Refuses rather than leave one uncaptured:
    its caller is about to overwrite slots. Returns the states that were new to the library."""
    added = []
    for index in range(SLOT_COUNT):
        data = read_slot(memory, index)
        if not is_occupied(data):
            continue
        if read_slot(memory, index) != data:
            raise Refusal(f"slot {index} is being written right now")
        try:
            state, is_new = keep(memory, library, data, build)
        except (OSError, Damaged) as error:
            raise Refusal(f"slot {index} could not be kept: {error}") from error
        if is_new:
            added.append(state)
    return tuple(added)

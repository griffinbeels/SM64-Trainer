"""Write one simulated frame into one Usamune slot of the running game (Ghost Handoff, first live try).

    uv run --with unicorn==2.1.4 --with capstone python tools/scene_lab/handoff_try.py
        --session <tap session> --frame K --slot N [--dry-run] [--write-seed] [--pid P]
    uv run python tools/scene_lab/handoff_try.py --restore <backup> [--dry-run] [--pid P]

Griffin decided the Trainer writes to game memory for this feature (2026-11-04); this tool is the one
place that does it, and only inside one slot's range. The slot comes from Usamune's own save routine in
the simulation (handoff_slot.py), never by hand. Before any write it attaches and refuses unless:
the cartridge is Usamune US, Usamune's save and load routines match the ones the slot was saved by, and
level, area, gMarioObject, the current-area pointer and the segment table all match the slot. The last
two go beyond Usamune's own gate: a slot's pointers mean nothing if the level loaded elsewhere.

The target slot's bytes go to scene-lab/handoff-backups first. The write clears the occupied word (the
trailer's first), writes the rest, writes the occupied word last, then reads all of it back, so a load
pressed during the write finds an empty slot and not half of one. --dry-run does everything but write.
--write-seed also writes gRandomSeed16, outside the slot, once, now: a load does not restore the seed,
so the frames after the load match the recording only if it lands right before the load.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import handoff_slot as slots  # noqa: E402
from sm64_events.core.paths import slot_library_dir  # noqa: E402
from sm64_events.slotlibrary.store import BUILT, KeptState, LoadContext, SlotLibrary  # noqa: E402
from sm64_events.slotlibrary.relocate import Ambiguous, to_live_place  # noqa: E402
from sm64_events.slotlibrary.usamune import (  # noqa: E402
    SEGMENT_BYTES,
    SEGMENT_TABLE,
    GameMemory,
    MarioMoved,
    Refusal,
    WriteFailed,
    attach,
    check_cartridge,
    rom_identity,
    routine_fingerprint,
    same_segments,
    slot_range,
    write_slot_bytes,
)

BACKUP_MAGIC = b"SM64-HANDOFF-SLOT-1\n"


class GameDiffers(Refusal):
    """The running game cannot take this slot. `kinds` names why (build, no-mario, place, mario, loaded, unclear),
    first the one a person should fix first; `seen` holds the game's level and area. Nothing was written."""

    def __init__(self, kinds: list[str], sentences: list[str], live_level: int, live_area: int):
        super().__init__("; ".join(sentences))
        self.kinds = kinds
        self.seen = {"live_level": live_level, "live_area": live_area}


@dataclass(frozen=True)
class SlotImage:
    """What a slot is, and what it needs from the running game."""
    data: bytes
    index: int
    seed: int | None
    segments: bytes
    current_area: int
    fingerprint: str
    source: str

    @property
    def mario_object(self) -> int:
        return struct.unpack_from(">I", self.data, slots.TRAILER_MARIO)[0]

    @property
    def level(self) -> int:
        return struct.unpack_from(">h", self.data, slots.TRAILER_LEVEL)[0]

    @property
    def area(self) -> int:
        return struct.unpack_from(">h", self.data, slots.TRAILER_AREA)[0]


def check_image(image: SlotImage) -> None:
    if len(image.data) != slots.SLOT_BYTES:
        raise Refusal(f"a slot is {slots.SLOT_BYTES} bytes, this one {len(image.data)}")
    if image.mario_object == 0:
        raise Refusal("this slot is empty: its occupied word is zero")


def check_game_matches(memory: GameMemory, image: SlotImage) -> None:
    """Usamune's own load gate, plus the two things that make the slot's pointers mean something."""
    mismatches, kinds = [], []
    if routine_fingerprint(memory.read) != image.fingerprint:
        kinds.append("build")
        mismatches.append("Usamune's save and load code differ from the build this slot came from")
    level = struct.unpack(">h", memory.read(slots.CURR_LEVEL, 2))[0]
    area = struct.unpack(">h", memory.read(slots.CURR_AREA, 2))[0]
    mario = int.from_bytes(memory.read(slots.MARIO_OBJECT, 4), "big")
    if mario == 0:
        kinds.append("no-mario")
        mismatches.append("no Mario: enter the level first")
    if (level, area) != (image.level, image.area):
        kinds.append("place")
        mismatches.append(f"the game is in level {level} area {area}, the slot in level {image.level} area {image.area}")
    if mario != image.mario_object:
        kinds.append("mario")
        mismatches.append(f"gMarioObject is {mario:08x}, the slot's is {image.mario_object:08x}")
    if int.from_bytes(memory.read(slots.CURRENT_AREA_POINTER, 4), "big") != image.current_area:
        kinds.append("loaded")
        mismatches.append("the current-area pointer differs: this level loaded to other addresses")
    if not same_segments(memory.read(SEGMENT_TABLE, SEGMENT_BYTES), image.segments):
        kinds.append("loaded")
        mismatches.append("the segment table differs: this level loaded to other addresses")
    if mismatches:
        raise GameDiffers(kinds, mismatches, level, area)


def backups_folder() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "SM64Trainer" / "scene-lab" / "handoff-backups"


def save_backup(memory: GameMemory, index: int, folder: Path, note: str) -> Path:
    """The slot's current bytes, read back from disk before any write may follow."""
    low, high = slot_range(index)
    data = memory.read(low, high - low)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    meta = {"slot": index, "taken_utc": stamp, "note": note, "fingerprint": routine_fingerprint(memory.read),
            "sha256": hashlib.sha256(data).hexdigest()}
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"slot{index}-{stamp}.bin"
    path.write_bytes(BACKUP_MAGIC + json.dumps(meta).encode("utf-8") + b"\n" + data)
    if read_backup(path)[1] != data:
        raise Refusal(f"the backup at {path} does not read back")
    return path


def read_backup(path: Path) -> tuple[dict, bytes]:
    blob = path.read_bytes()
    if not blob.startswith(BACKUP_MAGIC):
        raise Refusal(f"{path} is not a slot backup")
    line, _, data = blob[len(BACKUP_MAGIC):].partition(b"\n")
    meta = json.loads(line)
    if len(data) != slots.SLOT_BYTES or hashlib.sha256(data).hexdigest() != meta["sha256"]:
        raise Refusal(f"{path} is damaged: its bytes do not match its own hash")
    return meta, data


def keep_built(memory: GameMemory, image: SlotImage, library: SlotLibrary) -> tuple[KeptState, bool]:
    """A built slot joins the Slot Library, so a later watcher reads it as known and not as his new save."""
    return library.add(image.data, source=BUILT, fingerprint=image.fingerprint, rom=rom_identity(memory),
                       name=image.source, context=LoadContext(image.current_area, image.segments))


def at_live_place(memory: GameMemory, image: SlotImage) -> SlotImage:
    """The image with Mario moved inside its data to where the running game keeps him (slotlibrary/relocate.py),
    the same step every writer runs. A slot whose data nothing can read by structure is refused, not guessed."""
    try:
        data = to_live_place(memory, image.data)
    except Ambiguous as unclear:
        level = struct.unpack(">h", memory.read(slots.CURR_LEVEL, 2))[0]
        area = struct.unpack(">h", memory.read(slots.CURR_AREA, 2))[0]
        raise GameDiffers(["unclear"], [str(unclear)], level, area) from unclear
    return image if data == image.data else replace(image, data=data)


def install(memory: GameMemory, image: SlotImage, folder: Path, *, dry_run: bool, write_seed: bool) -> Path:
    """Every check, Mario moved to the live place, the backup, then (unless a dry run) the write. Returns the
    backup. The write refuses, leaving the slot empty, if Mario's place changes before the occupied word."""
    slot_range(image.index)
    check_image(image)
    check_cartridge(memory)
    image = at_live_place(memory, image)
    check_game_matches(memory, image)
    if write_seed and image.seed is None:
        raise Refusal("this slot image carries no seed")
    backup = save_backup(memory, image.index, folder, image.source)
    if not dry_run:
        write_slot_bytes(memory, image.index, image.data, mario_place=image.mario_object)
        if write_seed:
            memory.write(slots.RANDOM_SEED, struct.pack(">H", image.seed))
    return backup


def restore(memory: GameMemory, path: Path, folder: Path, *, dry_run: bool) -> Path:
    """Put a backup back. Only the cartridge and Usamune's build must match: a slot may belong to any level."""
    meta, data = read_backup(path)
    check_cartridge(memory)
    if routine_fingerprint(memory.read) != meta["fingerprint"]:
        raise Refusal("this backup came from another Usamune build")
    undo = save_backup(memory, meta["slot"], folder, f"before restoring {path.name}")
    if not dry_run:
        write_slot_bytes(memory, meta["slot"], data, mario_place=None)   # a backup goes back exactly as it was
    return undo


def build_image(session: str, frame: int, index: int) -> SlotImage:
    slot_range(index)
    built = slots.build_slot(slots.sim.lab_root() / session, frame, index)
    return SlotImage(built.data, index, built.seed, built.segments, built.current_area, built.fingerprint,
                     f"{session} frame {frame}")


class SimulatedGame:
    """GameMemory over the simulation: the rehearsal runs every check and write without a process."""

    def __init__(self, cpu, cartridge: bytes):
        self.cpu, self.head = cpu, cartridge[:0x40]

    def read(self, address: int, size: int) -> bytes:
        at = slots.sim.phys(address)
        return self.cpu.ram[at:at + size].tobytes()

    def write(self, address: int, data: bytes) -> None:
        at = slots.sim.phys(address)
        self.cpu.ram[at:at + len(data)] = list(data)

    def cartridge_header(self) -> bytes:
        return self.head


def rehearse(args) -> int:
    """The whole path in the simulation: build at K, run to J, install through the writer, let Usamune load.
    Mario, the objects and the camera right after the load are compared with frame K's at the same point."""
    image = build_image(args.session, args.frame, args.slot)
    replay = slots.start_replay(slots.sim.lab_root() / args.session)
    cpu = replay.cpu
    results: dict = {}
    kept: dict = {}

    def note_k() -> None:
        kept["natural"] = cpu.ram.copy()

    def write_and_load() -> None:
        game = SimulatedGame(cpu, cpu.rom)
        install(game, image, backups_folder() / "rehearsal", dry_run=False, write_seed=False)
        cpu.load_slot(args.slot)
        groups = slots.group_diff(cpu.ram, kept["natural"], cpu.symbols)
        results.update(summary=slots.summarize(groups), camera=slots.camera_block(cpu) == kept["camera"])

    for frame in replay.stream:
        replay.absorb(frame)
        if frame.index == args.frame:
            def capture() -> None:
                note_k()
                kept["camera"] = slots.camera_block(cpu)
            cpu.pending = capture
        if frame.index == args.rehearse:
            cpu.pending = write_and_load
        replay.step(frame)
        if frame.index == args.rehearse:
            break
    print(f"installed through the writer and loaded at frame {args.rehearse}: right after the load, differences "
          f"from frame {args.frame} {results.get('summary')}; camera block equal {results.get('camera')}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--session")
    parser.add_argument("--frame", type=int)
    parser.add_argument("--slot", type=int)
    parser.add_argument("--restore", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--write-seed", action="store_true")
    parser.add_argument("--pid", type=int)
    parser.add_argument("--rehearse", type=int, metavar="J", help="no process: run the whole path in the simulation, loading at frame J")
    args = parser.parse_args()
    if not args.restore and None in (args.session, args.frame, args.slot):
        parser.error("name --session, --frame and --slot, or --restore")
    try:
        if args.restore:
            undo = restore(attach(args.pid), args.restore, backups_folder(), dry_run=args.dry_run)
            print(f"{'dry run, nothing written' if args.dry_run else 'restored'}; the slot as it was: {undo}")
            return 0
        if args.rehearse is not None:
            return rehearse(args)
        image = build_image(args.session, args.frame, args.slot)
        print(f"slot {image.index}: level {image.level}, area {image.area}, gMarioObject {image.mario_object:08x}")
        game = attach(args.pid)
        backup = install(game, image, backups_folder(), dry_run=args.dry_run, write_seed=args.write_seed)
        if not args.dry_run:
            keep_built(game, image, SlotLibrary(slot_library_dir()))
        print(f"{'dry run, nothing written' if args.dry_run else 'written and read back'}; backup: {backup}")
        return 0
    except (Refusal, WriteFailed) as error:
        print(f"REFUSED: {error}" if isinstance(error, Refusal) else f"FAILED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

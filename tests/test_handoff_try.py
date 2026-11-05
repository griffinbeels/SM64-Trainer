"""The Ghost Handoff writer against a fake game: what it refuses, and what it touches when it does not."""
from __future__ import annotations

import hashlib
import struct
import sys
from pathlib import Path

import pytest

from sm64_events.slotlibrary import usamune
from sm64_events.slotlibrary.store import BUILT, SlotLibrary
from sm64_events.slotlibrary.watcher import NEVER_SEEN, watch_step

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "scene_lab"))
import handoff_slot as slots  # noqa: E402
import handoff_try as tool  # noqa: E402

LEVEL, AREA, MARIO, CURRENT_AREA = 9, 1, 0x8034FFC8, 0x8033B5A0
SEGMENTS = bytes(range(64))
INDEX = 3


def header(name: bytes, country: bytes = b"E") -> bytes:
    raw = bytearray(0x40)
    raw[:4] = bytes.fromhex("80371240")
    raw[0x20:0x20 + len(name)] = name
    raw[0x3B:0x3F] = b"NSM" + country
    return bytes(raw)


USAMUNE_US = header(b"SM64 USAMUNE v1.93u")


class FakeGame:
    """Big-endian RAM with every write logged; nothing here is a real process."""

    def __init__(self):
        self.ram = bytearray(8 << 20)
        self.writes: list[tuple[int, bytes]] = []
        self.corrupt_next_read = False
        self.head = USAMUNE_US
        self.poke(usamune.OS_MEM_SIZE, struct.pack(">I", usamune.EXPANSION_BYTES))
        self.poke(slots.CURR_LEVEL, struct.pack(">h", LEVEL))
        self.poke(slots.CURR_AREA, struct.pack(">h", AREA))
        self.poke(slots.MARIO_OBJECT, struct.pack(">I", MARIO))
        self.poke(slots.CURRENT_AREA_POINTER, struct.pack(">I", CURRENT_AREA))
        self.poke(usamune.SEGMENT_TABLE, SEGMENTS)
        self.poke(slots.SAVE, b"\x11" * 0x400)
        self.poke(slots.LOAD, b"\x22" * 0x400)

    def poke(self, address: int, data: bytes) -> None:
        at = address - 0x80000000
        self.ram[at:at + len(data)] = data

    def read(self, address: int, size: int) -> bytes:
        at = address - 0x80000000
        data = bytes(self.ram[at:at + size])
        if self.corrupt_next_read and size == slots.SLOT_BYTES:
            self.corrupt_next_read = False
            return data[:-1] + bytes([data[-1] ^ 1])
        return data

    def write(self, address: int, data: bytes) -> None:
        self.writes.append((address, bytes(data)))
        self.poke(address, data)

    def cartridge_header(self) -> bytes | None:
        return self.head


def make_image(game: FakeGame, **overrides) -> tool.SlotImage:
    data = bytearray(b"\xAB" * slots.SLOT_BYTES)
    struct.pack_into(">I", data, slots.TRAILER_MARIO, MARIO)
    struct.pack_into(">hh", data, slots.TRAILER_LEVEL, LEVEL, AREA)
    fields = {"data": bytes(data), "index": INDEX, "seed": 0x1234, "segments": SEGMENTS, "current_area": CURRENT_AREA,
              "fingerprint": tool.routine_fingerprint(game.read), "source": "test"}
    return tool.SlotImage(**(fields | overrides))


def old_slot(game: FakeGame) -> bytes:
    data = bytes(i & 0xFF for i in range(slots.SLOT_BYTES))
    game.poke(slots.slot_address(INDEX), data)
    return data


def run(game, image, folder, **flags):
    return tool.install(game, image, folder, dry_run=flags.get("dry_run", False), write_seed=flags.get("write_seed", False))


def set_level(game):
    game.poke(slots.CURR_LEVEL, struct.pack(">h", 16))


def set_area(game):
    game.poke(slots.CURR_AREA, struct.pack(">h", 2))


def set_mario(game):
    game.poke(slots.MARIO_OBJECT, struct.pack(">I", MARIO + 0x260))


def clear_mario(game):
    game.poke(slots.MARIO_OBJECT, bytes(4))


def set_area_pointer(game):
    game.poke(slots.CURRENT_AREA_POINTER, struct.pack(">I", CURRENT_AREA + 16))


def set_segment(game):
    game.poke(usamune.SEGMENT_TABLE + 8, b"\xFF\xFF\xFF\xFF")


def vanilla_rom(game):
    game.head = header(b"SUPER MARIO 64")


def usamune_jp(game):
    game.head = header(b"SM64 USAMUNE v1.93j", b"J")


def no_rom(game):
    game.head = None


def other_usamune_build(game):
    game.poke(slots.LOAD + 8, b"\x00")


def four_meg_console(game):
    game.poke(usamune.OS_MEM_SIZE, struct.pack(">I", 0x400000))


@pytest.mark.parametrize("break_game", [set_level, set_area, clear_mario, set_area_pointer, set_segment,
                                        vanilla_rom, usamune_jp, no_rom, other_usamune_build, four_meg_console])
def test_a_mismatch_refuses_before_any_write_or_backup(break_game, tmp_path):
    game = FakeGame()
    image = make_image(game)
    break_game(game)
    with pytest.raises(tool.Refusal):
        run(game, image, tmp_path)
    assert game.writes == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("index", [-1, usamune.SLOT_COUNT, 200])
def test_a_slot_number_usamune_lacks_refuses(index, tmp_path):
    game = FakeGame()
    with pytest.raises(tool.Refusal):
        run(game, make_image(game, index=index), tmp_path)
    assert game.writes == []


def test_a_wrong_sized_or_empty_image_refuses(tmp_path):
    game = FakeGame()
    with pytest.raises(tool.Refusal):
        run(game, make_image(game, data=bytes(16)), tmp_path)
    empty = bytearray(make_image(game).data)
    struct.pack_into(">I", empty, slots.TRAILER_MARIO, 0)
    with pytest.raises(tool.Refusal):
        run(game, make_image(game, data=bytes(empty)), tmp_path)
    assert game.writes == []


def test_the_gfx_pool_segment_may_differ(tmp_path):
    """Segment 1 alternates every frame, so a slot and the live game can hold either pool."""
    game = FakeGame()
    image = make_image(game)
    game.poke(usamune.SEGMENT_TABLE + 4, b"\xEE\xEE\xEE\xEE")
    run(game, image, tmp_path)
    assert game.writes


def test_writes_stay_inside_the_slot_with_the_occupied_word_last(tmp_path):
    game = FakeGame()
    old_slot(game)
    image = make_image(game)
    run(game, image, tmp_path)
    low, high = slots.slot_address(INDEX), slots.slot_address(INDEX) + slots.SLOT_BYTES
    occupied = low + slots.TRAILER_MARIO
    assert all(low <= address and address + len(data) <= high for address, data in game.writes)
    assert game.writes[0] == (occupied, bytes(4))
    assert game.writes[-1] == (occupied, struct.pack(">I", MARIO))
    assert game.read(low, slots.SLOT_BYTES) == image.data


def test_the_seed_stays_unwritten_without_its_flag(tmp_path):
    game = FakeGame()
    image = make_image(game)
    run(game, image, tmp_path)
    assert not any(address == slots.RANDOM_SEED for address, _ in game.writes)
    run(game, image, tmp_path, write_seed=True)
    assert game.writes[-1] == (slots.RANDOM_SEED, struct.pack(">H", 0x1234))


def test_the_backup_holds_the_old_slot(tmp_path):
    game = FakeGame()
    before = old_slot(game)
    backup = run(game, make_image(game), tmp_path)
    meta, data = tool.read_backup(backup)
    assert data == before
    assert meta["slot"] == INDEX and meta["sha256"] == hashlib.sha256(before).hexdigest()


def test_a_dry_run_backs_up_but_writes_nothing(tmp_path):
    game = FakeGame()
    before = old_slot(game)
    backup = run(game, make_image(game), tmp_path, dry_run=True, write_seed=True)
    assert game.writes == []
    assert tool.read_backup(backup)[1] == before


def test_a_built_slot_joins_the_library_and_is_no_new_save_afterward(tmp_path):
    game = FakeGame()
    image = make_image(game)
    library = SlotLibrary(tmp_path / "library")
    run(game, image, tmp_path / "backups")
    state, is_new = tool.keep_built(game, image, library)
    assert is_new and state.source == BUILT and library.read_bytes(state.id) == image.data
    assert state.context.current_area == CURRENT_AREA and state.context.segments == SEGMENTS
    result = watch_step(game, library, NEVER_SEEN)
    assert result.captured == [] and result.known == [(INDEX, state.id)]


def test_a_failed_read_back_leaves_the_slot_empty(tmp_path):
    game = FakeGame()
    old_slot(game)
    image = make_image(game)
    backup = tool.save_backup(game, INDEX, tmp_path, "first")
    game.corrupt_next_read = True
    with pytest.raises(tool.WriteFailed):
        tool.write_slot_bytes(game, INDEX, image.data, mario_place=None)
    assert game.read(slots.slot_address(INDEX) + slots.TRAILER_MARIO, 4) == bytes(4)
    assert backup.exists()


def test_restore_puts_the_old_slot_back_and_keeps_the_new_one(tmp_path):
    game = FakeGame()
    before = old_slot(game)
    image = make_image(game)
    backup = run(game, image, tmp_path)
    game.writes.clear()
    undo = tool.restore(game, backup, tmp_path, dry_run=False)
    assert game.read(slots.slot_address(INDEX), slots.SLOT_BYTES) == before
    assert tool.read_backup(undo)[1] == image.data
    assert game.writes[-1][0] == slots.slot_address(INDEX) + slots.TRAILER_MARIO


def test_restore_refuses_a_damaged_backup_or_another_rom(tmp_path):
    game = FakeGame()
    old_slot(game)
    backup = run(game, make_image(game), tmp_path)
    game.writes.clear()
    blob = bytearray(backup.read_bytes())
    blob[-1] ^= 1
    damaged = tmp_path / "damaged.bin"
    damaged.write_bytes(blob)
    with pytest.raises(tool.Refusal):
        tool.restore(game, damaged, tmp_path, dry_run=False)
    vanilla_rom(game)
    with pytest.raises(tool.Refusal):
        tool.restore(game, backup, tmp_path, dry_run=False)
    game.head = USAMUNE_US
    other_usamune_build(game)
    with pytest.raises(tool.Refusal):
        tool.restore(game, backup, tmp_path, dry_run=False)
    assert game.writes == []


class FakeProject64:
    """What Project64Game touches of Pj64Memory: RDRAM kept as little-endian words."""

    def __init__(self):
        self.host = bytearray(0x1000)
        self._rdram_base = 0x10000
        self._pm = self
        self.calls: list[tuple[int, int]] = []

    def _read_raw(self, offset: int, size: int) -> bytes:
        return bytes(self.host[offset:offset + size])

    def write_bytes(self, address: int, data: bytes, length: int) -> None:
        self.calls.append((address, length))
        at = address - self._rdram_base
        self.host[at:at + length] = data

    def rom_header(self) -> bytes:
        return USAMUNE_US


def test_project64_bytes_swap_inside_each_word():
    target = FakeProject64()
    game = usamune.Project64Game(target)
    game.write(0x80000100, b"\x01\x02\x03\x04\x05\x06\x07\x08")
    assert bytes(target.host[0x100:0x108]) == b"\x04\x03\x02\x01\x08\x07\x06\x05"
    assert game.read(0x80000100, 8) == b"\x01\x02\x03\x04\x05\x06\x07\x08"
    assert game.read(0x80000102, 2) == b"\x03\x04"


def test_a_project64_halfword_write_keeps_its_neighbor():
    target = FakeProject64()
    game = usamune.Project64Game(target)
    game.write(0x80000200, b"\xAA\xBB\xCC\xDD")
    target.calls.clear()
    game.write(0x80000200, b"\x12\x34")
    assert game.read(0x80000200, 4) == b"\x12\x34\xCC\xDD"
    assert target.calls == [(target._rdram_base + 0x200, 4)]

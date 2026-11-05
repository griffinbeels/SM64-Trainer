"""The Slot Library against a fake game and temp folders: what it keeps, what it refuses, what it touches."""
from __future__ import annotations

import json
import lzma
import struct
import sys
from pathlib import Path

import pytest

from sm64_events.core import paths
from sm64_events.slotlibrary import sharefile, usamune
from sm64_events.slotlibrary.coursesets import CourseSets, install_for_course, place_states
from sm64_events.slotlibrary.store import (
    BUILT,
    CAPTURED,
    IMPORTED,
    MAX_NAME,
    Damaged,
    SlotLibrary,
    state_id,
    unpack,
)
from sm64_events.slotlibrary.usamune import Refusal
from sm64_events.slotlibrary.watcher import EMPTY, NEVER_SEEN, capture_all, watch_step

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "scene_lab"))
import slot_library as cli  # noqa: E402

LEVEL, AREA, MARIO, CURRENT_AREA = 9, 1, 0x8034FFC8, 0x8033B5A0
SEGMENTS = bytes(range(64))
OTHER_MARIO = 0x80347AC8


def cartridge(name: bytes = b"SM64 USAMUNE v1.93u", country: bytes = b"E") -> bytes:
    raw = bytearray(0x40)
    raw[:4] = bytes.fromhex("80371240")
    raw[0x20:0x20 + len(name)] = name
    raw[0x3B:0x3F] = b"NSM" + country
    return bytes(raw)


class FakeGame:
    """Big-endian RAM with every write logged. `on_write` sees the game just before each write lands."""

    def __init__(self):
        self.ram = bytearray(8 << 20)
        self.writes: list[tuple[int, bytes]] = []
        self.head: bytes | None = cartridge()
        self.on_write = None
        self.fail_read_back = False
        self.torn_reads: dict[int, int] = {}
        self.poke(usamune.OS_MEM_SIZE, struct.pack(">I", usamune.EXPANSION_BYTES))
        self.poke(usamune.CURR_LEVEL, struct.pack(">h", LEVEL))
        self.poke(usamune.CURR_AREA, struct.pack(">h", AREA))
        self.poke(usamune.MARIO_OBJECT, struct.pack(">I", MARIO))
        self.poke(usamune.CURRENT_AREA_POINTER, struct.pack(">I", CURRENT_AREA))
        self.poke(usamune.SEGMENT_TABLE, SEGMENTS)
        self.poke(usamune.SAVE, b"\x11" * usamune.FINGERPRINT_BYTES)
        self.poke(usamune.LOAD, b"\x22" * usamune.FINGERPRINT_BYTES)

    def poke(self, address: int, data: bytes) -> None:
        at = address - 0x80000000
        self.ram[at:at + len(data)] = data

    def read(self, address: int, size: int) -> bytes:
        at = address - 0x80000000
        data = bytes(self.ram[at:at + size])
        if self.torn_reads.get(address, 0) > 0:
            self.torn_reads[address] -= 1
            return bytes([data[0] ^ self.torn_reads[address] ^ 0x55]) + data[1:]
        if self.fail_read_back and size == usamune.SLOT_BYTES:
            return data[:-1] + bytes([data[-1] ^ 1])
        return data

    def write(self, address: int, data: bytes) -> None:
        if self.on_write:
            self.on_write(address, data)
        self.writes.append((address, bytes(data)))
        self.poke(address, data)

    def cartridge_header(self) -> bytes | None:
        return self.head

    def save(self, index: int, data: bytes) -> None:
        """Usamune's own save, which the Trainer's write log must not count."""
        self.poke(usamune.slot_address(index), data)

    def slot(self, index: int) -> bytes:
        return bytes(self.ram[usamune.slot_address(index) - 0x80000000:][:usamune.SLOT_BYTES])

    def fingerprint(self) -> str:
        return usamune.routine_fingerprint(self.read)


def make_slot(fill: int, *, mario: int = MARIO, level: int = LEVEL, area: int = AREA) -> bytes:
    data = bytearray(bytes([fill]) * usamune.SLOT_BYTES)
    struct.pack_into(">I", data, usamune.TRAILER_MARIO, mario)
    struct.pack_into(">hh", data, usamune.TRAILER_LEVEL, level, area)
    return bytes(data)


@pytest.fixture
def game():
    return FakeGame()


@pytest.fixture
def library(tmp_path):
    return SlotLibrary(tmp_path / "library")


def keep(library, game, data, **extra):
    return library.add(data, source=extra.pop("source", BUILT), fingerprint=game.fingerprint(), rom="test", **extra)[0]


# ---- what the watcher keeps -------------------------------------------------------------------------------


def test_overwriting_a_slot_keeps_both_states(game, library):
    first, second = make_slot(0xA1), make_slot(0xA2)
    game.save(4, first)
    seen = watch_step(game, library, NEVER_SEEN).seen
    game.save(4, second)
    result = watch_step(game, library, seen)
    assert [slot for slot, _ in result.captured] == [4]
    assert library.read_bytes(state_id(first)) == first
    assert library.read_bytes(state_id(second)) == second


def test_identical_bytes_in_two_slots_are_one_state(game, library):
    game.save(0, make_slot(0xA1))
    game.save(5, make_slot(0xA1))
    result = watch_step(game, library, NEVER_SEEN)
    assert [slot for slot, _ in result.captured] == [0]
    assert [slot for slot, _ in result.known] == [5]
    assert len(library.scan().states) == 1


def test_an_unchanged_slot_is_not_looked_at_twice(game, library):
    game.save(1, make_slot(0xA1))
    seen = watch_step(game, library, NEVER_SEEN).seen
    result = watch_step(game, library, seen)
    assert result.captured == [] and result.known == []
    assert result.seen == seen


def test_an_emptied_slot_stores_nothing(game, library):
    game.save(2, make_slot(0xA1))
    seen = watch_step(game, library, NEVER_SEEN).seen
    game.save(2, bytes(usamune.SLOT_BYTES))
    result = watch_step(game, library, seen)
    assert result.emptied == [2] and result.seen[2] == EMPTY
    assert len(library.scan().states) == 1


def test_a_slot_with_a_zero_word_and_other_bytes_stores_nothing(game, library):
    data = bytearray(make_slot(0xA1))
    struct.pack_into(">I", data, usamune.TRAILER_MARIO, 0)
    game.save(2, bytes(data))
    assert watch_step(game, library, NEVER_SEEN).captured == []
    assert library.scan().states == ()


def test_a_slot_being_copied_is_not_kept_until_it_settles(game, library):
    data = make_slot(0xA1)
    game.save(3, data)
    game.torn_reads[usamune.slot_address(3)] = 2
    torn = watch_step(game, library, NEVER_SEEN)
    assert torn.unsettled == [3] and torn.captured == [] and torn.seen[3] is None
    assert library.scan().states == ()
    assert [slot for slot, _ in watch_step(game, library, torn.seen).captured] == [3]
    assert library.read_bytes(state_id(data)) == data


def test_a_slot_that_cannot_be_real_is_reported_once_and_not_kept(game, library):
    game.save(6, make_slot(0xA1, level=99))
    result = watch_step(game, library, NEVER_SEEN)
    assert [slot for slot, _ in result.rejected] == [6] and library.scan().states == ()
    assert watch_step(game, library, result.seen).rejected == []


def test_the_watcher_reads_no_slots_of_another_game(game, library):
    game.save(1, make_slot(0xA1))
    game.head = cartridge(b"SUPER MARIO 64")
    result = watch_step(game, library, NEVER_SEEN)
    assert result.not_ready and result.seen == NEVER_SEEN and library.scan().states == ()


def test_a_full_disk_retries_on_the_next_step(game, library, monkeypatch):
    game.save(1, make_slot(0xA1))

    def no_room(*_args, **_kwargs):
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr("sm64_events.slotlibrary.store.write_atomic", no_room)
        result = watch_step(game, library, NEVER_SEEN)
    assert [slot for slot, _ in result.failed] == [1] and result.seen[1] is None
    assert [slot for slot, _ in watch_step(game, library, result.seen).captured] == [1]


def test_a_state_records_what_it_is(game, library):
    data = make_slot(0xA1)
    game.save(0, data)
    (_, state), = watch_step(game, library, NEVER_SEEN).captured
    assert (state.level, state.area, state.mario_object) == (LEVEL, AREA, MARIO)
    assert state.id == state_id(data) and state.source == CAPTURED and state.fingerprint == game.fingerprint()
    assert "USAMUNE" in state.rom and state.first_seen_utc.endswith("+00:00")
    assert state.context.current_area == CURRENT_AREA and state.context.segments == SEGMENTS


def test_a_state_saved_elsewhere_records_no_load_addresses(game, library):
    game.save(0, make_slot(0xA1, mario=OTHER_MARIO))
    (_, state), = watch_step(game, library, NEVER_SEEN).captured
    assert state.context is None


def test_a_trainer_write_is_not_a_new_save_unless_its_bytes_are_new(game, library):
    kept = keep(library, game, make_slot(0xB1))
    seen = watch_step(game, library, NEVER_SEEN).seen
    place_states(game, library, {2: kept.id})
    result = watch_step(game, library, seen)
    assert result.captured == [] and result.known == [(2, kept.id)]
    assert library.get(kept.id).source == BUILT
    new_bytes = make_slot(0xB2)
    usamune.write_slot_bytes(game, 3, new_bytes)
    assert [state.id for _, state in watch_step(game, library, result.seen).captured] == [state_id(new_bytes)]


# ---- the store ----------------------------------------------------------------------------------------------


def test_a_kept_state_comes_back_byte_identical_and_small(game, library):
    data = bytes(i * 7 & 0xFF for i in range(usamune.SLOT_BYTES))
    data = data[:usamune.TRAILER_MARIO] + make_slot(0)[usamune.TRAILER_MARIO:usamune.TRAILER_MARIO + 8] + data[usamune.TRAILER_MARIO + 8:]
    state = keep(library, game, data)
    fresh = SlotLibrary(library.root)
    assert fresh.read_bytes(state.id) == data
    assert fresh.get(state.id) == state


def test_renaming_keeps_the_bytes_and_a_later_save_keeps_the_name(game, library):
    data = make_slot(0xA1)
    state = keep(library, game, data)
    library.rename(state.id, "  cannon\tlong jump\n")
    assert library.get(state.id).name == "cannonlong jump"
    assert library.read_bytes(state.id) == data
    again, is_new = library.add(data, source=CAPTURED, fingerprint=game.fingerprint(), rom="x")
    assert not is_new and again.name == "cannonlong jump" and again.source == BUILT


def test_a_name_is_bounded(game, library):
    assert len(library.rename(keep(library, game, make_slot(1)).id, "x" * 500).name) == MAX_NAME


def test_a_changed_byte_on_disk_cannot_be_read_as_the_state(game, library):
    state = keep(library, game, make_slot(0xA1))
    blob = library.root / "states" / f"{state.id}.xz"
    other = bytes(make_slot(0xA2))
    blob.write_bytes(lzma.compress(other, format=lzma.FORMAT_XZ))
    with pytest.raises(Damaged):
        library.read_bytes(state.id)


def test_a_record_from_the_future_is_reported_and_left_alone(game, library):
    state = keep(library, game, make_slot(0xA1))
    record = library.root / "states" / f"{state.id}.json"
    stored = json.loads(record.read_bytes()) | {"version": 99}
    record.write_text(json.dumps(stored), encoding="utf-8")
    before = record.read_bytes()
    scan = library.scan()
    assert scan.states == () and scan.damaged == (state.id,)
    with pytest.raises(Damaged):
        library.add(make_slot(0xA1), source=CAPTURED, fingerprint=game.fingerprint(), rom="x")
    assert record.read_bytes() == before


@pytest.mark.parametrize("bad", ["..\\..\\evil", "abc", "A" * 64, "g" * 64])
def test_an_id_is_never_a_path(library, bad):
    with pytest.raises(Refusal, match="is not a state id"):
        library.get(bad)


def test_the_library_lives_with_the_users_data_not_the_checkout(tmp_path, monkeypatch):
    monkeypatch.delenv("SM64_PRACTICE_ROOT", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert paths.slot_library_dir().is_relative_to(tmp_path)
    profile = tmp_path / "profile"
    monkeypatch.setenv("SM64_PRACTICE_ROOT", str(profile))
    assert paths.slot_library_dir().is_relative_to(profile)


# ---- installing -----------------------------------------------------------------------------------------------


def test_every_old_slot_is_in_the_library_before_the_first_write(game, library):
    """The capture-before-install order, seen from inside the first write."""
    old = {0: make_slot(0xC0), 7: make_slot(0xC7)}
    for slot, data in old.items():
        game.save(slot, data)
    incoming = keep(library, game, make_slot(0xD0))
    held_at_first_write = []

    def check(_address, _data):
        if not held_at_first_write:
            held_at_first_write.append([library.has(state_id(data)) for data in old.values()])

    game.on_write = check
    report = place_states(game, library, {0: incoming.id})
    assert held_at_first_write == [[True, True]]
    assert {state.id for state in report.captured} == {state_id(data) for data in old.values()}
    assert game.slot(0) == make_slot(0xD0) and library.read_bytes(state_id(old[0])) == old[0]


def test_a_capture_that_fails_writes_nothing(game, library, monkeypatch):
    game.save(0, make_slot(0xC0))
    incoming = keep(library, game, make_slot(0xD0))

    def no_room(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("sm64_events.slotlibrary.store.write_atomic", no_room)
    with pytest.raises(Refusal):
        place_states(game, library, {0: incoming.id})
    assert game.writes == [] and game.slot(0) == make_slot(0xC0)


def test_a_slot_being_written_stops_the_capture(game, library):
    game.save(0, make_slot(0xC0))
    game.torn_reads[usamune.slot_address(0)] = 1
    with pytest.raises(Refusal):
        capture_all(game, library)


def test_a_course_set_goes_in_order_and_leaves_later_slots_alone(game, library, tmp_path):
    states = [keep(library, game, make_slot(0xD0 + n)) for n in range(3)]
    game.save(5, make_slot(0xC5))
    sets = CourseSets(tmp_path / "sets.json")
    sets.assign(library, LEVEL, None, [state.id for state in states])
    report = install_for_course(game, library, sets, LEVEL, None)
    assert [placed.slot for placed in report.placements] == [0, 1, 2]
    assert [game.slot(n) for n in range(3)] == [make_slot(0xD0 + n) for n in range(3)]
    assert game.slot(5) == make_slot(0xC5)
    low = usamune.slot_address(0)
    assert all(low <= address and address + len(data) <= usamune.slot_address(2) + usamune.SLOT_BYTES
               for address, data in game.writes)


def test_a_course_entry_with_no_set_touches_nothing(game, library, tmp_path):
    game.save(0, make_slot(0xC0))
    assert install_for_course(game, library, CourseSets(tmp_path / "sets.json"), LEVEL, None) is None
    assert game.writes == [] and library.scan().states == ()


def test_a_stars_set_beats_its_levels_set(game, library, tmp_path):
    level_state, star_state = keep(library, game, make_slot(1)), keep(library, game, make_slot(2))
    sets = CourseSets(tmp_path / "sets.json")
    sets.assign(library, LEVEL, None, [level_state.id])
    sets.assign(library, LEVEL, "wild-blue", [star_state.id])
    assert sets.lookup(LEVEL, "wild-blue") == [star_state.id]
    assert sets.lookup(LEVEL, "other-star") == [level_state.id]
    assert sets.lookup(LEVEL + 1, "wild-blue") is None
    sets.assign(library, LEVEL, "wild-blue", [])
    assert sets.lookup(LEVEL, "wild-blue") == [level_state.id]


def test_a_set_holds_at_most_nine_kept_states(game, library, tmp_path):
    state = keep(library, game, make_slot(1))
    sets = CourseSets(tmp_path / "sets.json")
    with pytest.raises(Refusal):
        sets.assign(library, LEVEL, None, [state.id] * 10)
    with pytest.raises(Refusal):
        sets.assign(library, LEVEL, None, ["0" * 64])
    assert not sets.path.exists()


def test_an_unreadable_sets_file_is_not_overwritten(game, library, tmp_path):
    state = keep(library, game, make_slot(1))
    path = tmp_path / "sets.json"
    path.write_text('{"version": 2, "sets": []}', encoding="utf-8")
    with pytest.raises(Damaged):
        CourseSets(path).assign(library, LEVEL, None, [state.id])
    assert path.read_text(encoding="utf-8") == '{"version": 2, "sets": []}'


def test_a_different_mario_word_is_reported_and_the_bytes_are_never_patched(game, library):
    data = make_slot(0xD1, mario=OTHER_MARIO)
    state = keep(library, game, data)
    report = place_states(game, library, {4: state.id})
    placed = report.placements[0]
    assert placed.written and not placed.mario_matches and not placed.loadable_now and placed.place_matches
    assert game.slot(4) == data
    assert game.read(usamune.slot_address(4) + usamune.TRAILER_MARIO, 4) == struct.pack(">I", OTHER_MARIO)


def test_a_matching_state_reports_it_loads_now(game, library):
    game.save(0, make_slot(0xD1))
    state = watch_step(game, library, NEVER_SEEN).captured[0][1]
    placed = place_states(game, library, {1: state.id}).placements[0]
    assert placed.loadable_now and placed.pointers_match is True
    game.poke(usamune.SEGMENT_TABLE + 8, b"\xFF\xFF\xFF\xFF")
    assert place_states(game, library, {2: state.id}).placements[0].pointers_match is False


def test_another_levels_state_is_written_and_reported(game, library):
    state = keep(library, game, make_slot(0xD1, level=16))
    placed = place_states(game, library, {0: state.id}).placements[0]
    assert placed.written and not placed.place_matches and not placed.loadable_now


def test_a_state_already_in_its_slot_is_not_rewritten(game, library):
    """Re-entering a course must not blank a slot he may be loading."""
    state = keep(library, game, make_slot(0xD1))
    game.save(0, make_slot(0xD1))
    placed = place_states(game, library, {0: state.id}).placements[0]
    assert not placed.written and game.writes == []


def test_a_dry_run_reports_and_writes_nothing(game, library):
    state = keep(library, game, make_slot(0xD1))
    game.save(1, make_slot(0xC1))
    report = place_states(game, library, {0: state.id}, dry_run=True)
    assert not report.placements[0].written and report.captured == () and game.writes == []
    assert not library.has(state_id(make_slot(0xC1)))


@pytest.mark.parametrize("slot", [-1, 9, 200])
def test_a_slot_usamune_lacks_refuses(game, library, slot):
    state = keep(library, game, make_slot(1))
    with pytest.raises(Refusal):
        place_states(game, library, {slot: state.id})
    assert game.writes == []


def test_another_build_or_rom_or_damage_refuses_before_anything_is_captured(game, library):
    game.save(1, make_slot(0xC1))
    state = keep(library, game, make_slot(0xD1))
    other_build = game.fingerprint()
    game.poke(usamune.LOAD + 8, b"\x00")
    assert game.fingerprint() != other_build
    with pytest.raises(Refusal):
        place_states(game, library, {0: state.id})
    assert game.writes == [] and not library.has(state_id(make_slot(0xC1)))
    game.poke(usamune.LOAD + 8, b"\x22")
    game.head = cartridge(b"SUPER MARIO 64")
    with pytest.raises(Refusal):
        place_states(game, library, {0: state.id})
    game.head = cartridge()
    (library.root / "states" / f"{state.id}.xz").write_bytes(b"not xz")
    with pytest.raises(Refusal):
        place_states(game, library, {0: state.id})
    assert game.writes == []


def test_a_failed_write_is_reported_and_stops_the_set(game, library):
    first, second = keep(library, game, make_slot(0xD1)), keep(library, game, make_slot(0xD2))
    game.save(0, make_slot(0xC0))
    capture_all(game, library)
    game.fail_read_back = True
    # The capture's own read pair must still agree; only the writer's read-back differs from the data.
    report = place_states(game, library, {0: first.id, 1: second.id})
    assert report.failed is not None and report.failed[0] == 0 and report.placements == ()
    assert game.read(usamune.slot_address(0) + usamune.TRAILER_MARIO, 4) == bytes(4)
    assert game.read(usamune.slot_address(1) + usamune.TRAILER_MARIO, 4) == bytes(4)
    assert library.read_bytes(state_id(make_slot(0xC0))) == make_slot(0xC0)


# ---- the share file ---------------------------------------------------------------------------------------------


@pytest.fixture
def shared(game, library, tmp_path):
    game.save(0, make_slot(0xE1))
    state = watch_step(game, library, NEVER_SEEN).captured[0][1]
    library.rename(state.id, "the good one")
    return sharefile.export_state(library, state.id, tmp_path / "good.sm64state")


def friend(tmp_path):
    return SlotLibrary(tmp_path / "friend")


def rebuild(blob: bytes, *, version=None, record=None, packed=None, digest=None, extra=b"") -> bytes:
    """A share file with one part replaced and every length made consistent, so the check under test is the one hit."""
    start = len(sharefile.MAGIC) + sharefile._HEADER.size
    old_version, record_size, _packed_size, old_digest = sharefile._HEADER.unpack_from(blob, len(sharefile.MAGIC))
    old_record, old_packed = blob[start:start + record_size], blob[start + record_size:]
    new_record = old_record if record is None else record
    new_packed = old_packed if packed is None else packed
    header = sharefile._HEADER.pack(old_version if version is None else version, len(new_record), len(new_packed),
                                    old_digest if digest is None else digest)
    return sharefile.MAGIC + header + new_record + new_packed + extra


def edited_record(blob: bytes, **changes) -> bytes:
    start = len(sharefile.MAGIC) + sharefile._HEADER.size
    size = sharefile._HEADER.unpack_from(blob, len(sharefile.MAGIC))[1]
    return json.dumps(json.loads(blob[start:start + size]) | changes).encode("utf-8")


def test_a_round_trip_is_byte_identical_and_carries_the_name(game, tmp_path, shared):
    other = friend(tmp_path)
    state, is_new = sharefile.import_state(other, shared, {game.fingerprint()})
    assert is_new and state.source == IMPORTED and state.name == "the good one"
    assert other.read_bytes(state.id) == make_slot(0xE1)
    assert state.context.segments == SEGMENTS
    assert shared.stat().st_size < 20_000


def test_importing_twice_keeps_one_state_with_its_local_name(game, tmp_path, shared):
    other = friend(tmp_path)
    state, _ = sharefile.import_state(other, shared, {game.fingerprint()})
    other.rename(state.id, "mine now")
    again, is_new = sharefile.import_state(other, shared, {game.fingerprint()})
    assert not is_new and again.name == "mine now" and len(other.scan().states) == 1


def test_export_will_not_replace_a_file_or_use_another_suffix(game, library, shared, tmp_path):
    state = keep(library, game, make_slot(0xE2))
    before = shared.read_bytes()
    with pytest.raises(Refusal):
        sharefile.export_state(library, state.id, shared)
    with pytest.raises(Refusal):
        sharefile.export_state(library, state.id, tmp_path / "good.zip")
    assert shared.read_bytes() == before


def test_an_imported_state_installs_through_the_checked_write(game, tmp_path, shared):
    other = friend(tmp_path)
    state, _ = sharefile.import_state(other, shared, {game.fingerprint()})
    game.save(0, bytes(usamune.SLOT_BYTES))
    place_states(game, other, {3: state.id})
    assert game.slot(3) == make_slot(0xE1)


def bomb() -> bytes:
    return lzma.compress(bytes(8 << 20), format=lzma.FORMAT_XZ)


def huge_window() -> bytes:
    filters = [{"id": lzma.FILTER_LZMA2, "dict_size": 1 << 28}]
    return lzma.compress(make_slot(0xE1), format=lzma.FORMAT_XZ, filters=filters)


def short_slot() -> bytes:
    return lzma.compress(make_slot(0xE1)[:-4], format=lzma.FORMAT_XZ)


def trailing_stream() -> bytes:
    return lzma.compress(make_slot(0xE1), format=lzma.FORMAT_XZ) + lzma.compress(b"x", format=lzma.FORMAT_XZ)


def slot_with(**facts) -> bytes:
    return lzma.compress(make_slot(0xE1, **facts), format=lzma.FORMAT_XZ)


BAD_FILES = {
    "wrong magic": lambda blob: b"NOTSTATE!\n" + blob[len(sharefile.MAGIC):],
    "too short": lambda blob: blob[:12],
    "empty": lambda _blob: b"",
    "newer version": lambda blob: rebuild(blob, version=2),
    "version zero": lambda blob: rebuild(blob, version=0),
    "wrong hash": lambda blob: rebuild(blob, digest=bytes(32)),
    "truncated": lambda blob: blob[:-5],
    "trailing bytes": lambda blob: rebuild(blob, extra=b"x"),
    "oversize file": lambda blob: rebuild(blob, extra=bytes(sharefile.MAX_FILE_BYTES)),
    "oversize record": lambda blob: rebuild(blob, record=b" " * (sharefile.MAX_RECORD_BYTES + 1)),
    "header lies about the record": lambda blob: blob[:len(sharefile.MAGIC) + 2] + struct.pack(">I", 0xFFFFFFFF) + blob[len(sharefile.MAGIC) + 6:],
    "header lies about the packed bytes": lambda blob: blob[:len(sharefile.MAGIC) + 6] + struct.pack(">I", 0xFFFFFFFF) + blob[len(sharefile.MAGIC) + 10:],
    "record is not json": lambda blob: rebuild(blob, record=b"\xff\xfe not json"),
    "record is a list": lambda blob: rebuild(blob, record=b"[]"),
    "record nests too deep": lambda blob: rebuild(blob, record=b"[" * sharefile.MAX_RECORD_BYTES),
    "corrupt xz": lambda blob: rebuild(blob, packed=b"\xfd7zXZ\x00garbage"),
    "decompression bomb": lambda blob: rebuild(blob, packed=bomb(), digest=bytes.fromhex(state_id(bytes(8 << 20)))),
    "window beyond the memory limit": lambda blob: rebuild(blob, packed=huge_window()),
    "slot too small": lambda blob: rebuild(blob, packed=short_slot(), digest=bytes.fromhex(state_id(make_slot(0xE1)[:-4]))),
    "two xz streams": lambda blob: rebuild(blob, packed=trailing_stream()),
    "level out of range": lambda blob: rebuild(
        blob, packed=slot_with(level=99), digest=bytes.fromhex(state_id(make_slot(0xE1, level=99))),
        record=edited_record(blob, level=99)),
    "area out of range": lambda blob: rebuild(
        blob, packed=slot_with(area=-1), digest=bytes.fromhex(state_id(make_slot(0xE1, area=-1))),
        record=edited_record(blob, area=-1)),
    "an empty slot": lambda blob: rebuild(
        blob, packed=slot_with(mario=0), digest=bytes.fromhex(state_id(make_slot(0xE1, mario=0))),
        record=edited_record(blob, mario_object=0)),
    "mario outside the pool": lambda blob: rebuild(
        blob, packed=slot_with(mario=0x80000000), digest=bytes.fromhex(state_id(make_slot(0xE1, mario=0x80000000))),
        record=edited_record(blob, mario_object=0x80000000)),
    "record disagrees with the bytes": lambda blob: rebuild(blob, record=edited_record(blob, level=16)),
    "another usamune build": lambda blob: rebuild(blob, record=edited_record(blob, fingerprint="f" * 64)),
    "a fingerprint that is not text": lambda blob: rebuild(blob, record=edited_record(blob, fingerprint=[1])),
    "malformed load addresses": lambda blob: rebuild(blob, record=edited_record(blob, segments_hex="zz")),
}


# Which check each bad file must reach: a refusal from the wrong check would prove nothing about the named one.
REFUSED_BY = {
    "wrong magic": "not a shared save state", "too short": "not a shared save state", "empty": "not a shared save state",
    "newer version": "format 2", "version zero": "format 0", "wrong hash": "do not match its hash",
    "truncated": "length does not match", "trailing bytes": "length does not match", "oversize file": "larger than any",
    "oversize record": "declares more", "header lies about the record": "declares more",
    "header lies about the packed bytes": "declares more", "record is not json": "record cannot be read",
    "record is a list": "record cannot be read", "record nests too deep": "record cannot be read",
    "corrupt xz": "do not unpack", "decompression bomb": "not exactly one slot",
    "window beyond the memory limit": "do not unpack", "slot too small": "not exactly one slot",
    "two xz streams": "not exactly one slot", "level out of range": "not a place in the game",
    "area out of range": "not a place in the game", "an empty slot": "is empty", "mario outside the pool": "object pool",
    "record disagrees with the bytes": "disagrees", "another usamune build": "different Usamune build",
    "a fingerprint that is not text": "different Usamune build", "malformed load addresses": "load addresses",
}


def test_every_bad_file_names_the_check_it_must_reach():
    assert set(REFUSED_BY) == set(BAD_FILES)


@pytest.mark.parametrize("name", BAD_FILES)
def test_import_refuses(name, game, tmp_path, shared):
    bad = tmp_path / "bad.sm64state"
    bad.write_bytes(BAD_FILES[name](shared.read_bytes()))
    other = friend(tmp_path)
    with pytest.raises(Refusal, match=REFUSED_BY[name]):
        sharefile.import_state(other, bad, {game.fingerprint()})
    assert other.scan().states == ()


def test_a_decompression_bomb_never_expands_past_one_slot(monkeypatch):
    real = lzma.LZMADecompressor
    produced = []

    class Spy:
        def __init__(self, *args, **kwargs):
            self.inner = real(*args, **kwargs)

        def decompress(self, *args, **kwargs):
            out = self.inner.decompress(*args, **kwargs)
            produced.append(len(out))
            return out

        def __getattr__(self, name):
            return getattr(self.inner, name)

    monkeypatch.setattr(lzma, "LZMADecompressor", Spy)
    with pytest.raises(Damaged):
        unpack(bomb())
    assert produced == [usamune.SLOT_BYTES + 1]


def test_the_untouched_file_passes_the_helper_that_builds_the_bad_ones(game, tmp_path, shared):
    """Otherwise every refusal above could be the helper breaking the frame, not the check it names."""
    rebuilt = tmp_path / "rebuilt.sm64state"
    rebuilt.write_bytes(rebuild(shared.read_bytes(), record=edited_record(shared.read_bytes())))
    state, _ = sharefile.import_state(friend(tmp_path), rebuilt, {game.fingerprint()})
    assert state.id == state_id(make_slot(0xE1))


def test_an_oversize_file_is_never_read_whole(game, tmp_path, monkeypatch):
    big = tmp_path / "big.sm64state"
    big.write_bytes(bytes(sharefile.MAX_FILE_BYTES * 3))
    asked = []
    real_open = Path.open

    def spying_open(self, *args, **kwargs):
        file = real_open(self, *args, **kwargs)
        real_read = file.read

        class Spy:
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                file.close()

            def read(self, size=-1):
                asked.append(size)
                return real_read(size)

        return Spy()

    monkeypatch.setattr(Path, "open", spying_open)
    with pytest.raises(Refusal):
        sharefile.import_state(friend(tmp_path), big, {"f" * 64})
    assert asked == [sharefile.MAX_FILE_BYTES + 1]


def test_importing_a_folder_refuses(tmp_path):
    with pytest.raises(Refusal):
        sharefile.import_state(friend(tmp_path), tmp_path, {"f" * 64})


# ---- the command line -----------------------------------------------------------------------------------------


@pytest.fixture
def driven(game, monkeypatch):
    monkeypatch.setattr(usamune, "attach", lambda _pid: game)
    return game


def test_the_command_line_drives_the_whole_loop(driven, tmp_path, capsys):
    root = ["--root", str(tmp_path / "lib")]
    other_root = ["--root", str(tmp_path / "other")]
    driven.save(0, make_slot(0xF0))
    assert cli.main([*root, "capture"]) == 0
    identity = state_id(make_slot(0xF0))
    assert identity[:12] in capsys.readouterr().out
    assert cli.main([*root, "rename", identity[:12], "cannon"]) == 0
    assert cli.main([*root, "install", identity[:12], "--slot", "4", "--dry-run"]) == 0
    assert driven.writes == []
    assert cli.main([*root, "install", identity[:12], "--slot", "4"]) == 0
    assert driven.slot(4) == make_slot(0xF0)
    assert cli.main([*root, "set-assign", str(LEVEL), identity[:12]]) == 0
    driven.save(0, bytes(usamune.SLOT_BYTES))
    assert cli.main([*root, "set-install", str(LEVEL)]) == 0
    assert driven.slot(0) == make_slot(0xF0)
    out_file = tmp_path / "cannon.sm64state"
    assert cli.main([*root, "export", identity[:12], str(out_file)]) == 0
    capsys.readouterr()
    assert cli.main([*other_root, "import", str(out_file)]) == 0
    assert "cannon" in capsys.readouterr().out
    assert cli.main([*other_root, "list"]) == 0
    assert "cannon" in capsys.readouterr().out


def test_the_command_line_says_refused_with_a_nonzero_exit(driven, tmp_path, capsys):
    assert cli.main(["--root", str(tmp_path), "install", "abc", "--slot", "1"]) == 2
    assert "REFUSED" in capsys.readouterr().err


def test_import_with_no_game_accepts_only_builds_the_library_holds(driven, tmp_path, monkeypatch, capsys):
    root, other = str(tmp_path / "lib"), str(tmp_path / "other")
    driven.save(0, make_slot(0xF0))
    assert cli.main(["--root", root, "capture"]) == 0
    out_file = tmp_path / "s.sm64state"
    assert cli.main(["--root", root, "export", state_id(make_slot(0xF0))[:12], str(out_file)]) == 0

    def no_game(_pid):
        raise Refusal("no game")

    monkeypatch.setattr(usamune, "attach", no_game)
    capsys.readouterr()
    assert cli.main(["--root", other, "import", str(out_file)]) == 2
    assert "start the game" in capsys.readouterr().err
    assert cli.main(["--root", root, "import", str(out_file)]) == 0

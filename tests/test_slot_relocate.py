"""Moving Mario inside a slot's data so Usamune's own load accepts it: the relocation, and the one write path.

Fake memory only; no process. The simulation's evidence that a relocated slot loads and plays on lives in
tools/scene_lab/handoff_relocate.py (simulation only, 2026-11-05).
"""
from __future__ import annotations

import re
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
sys.path.insert(0, str(ROOT / "tests"))
import handoff_try as tool  # noqa: E402
import test_handoff_try as fakes  # noqa: E402
import test_slot_library as libfakes  # noqa: E402
from sm64_events.slotlibrary import relocate as R  # noqa: E402
from sm64_events.slotlibrary import usamune  # noqa: E402
from sm64_events.slotlibrary.coursesets import place_states  # noqa: E402
from sm64_events.slotlibrary.store import SlotLibrary  # noqa: E402

BODY = R.BODY_START
OBJ = lambda i: R.POOL_START + i * R.OBJECT_SIZE  # noqa: E731
SLOT_MARIO_PLACE, LIVE_PLACE, OTHER_PLACE, WARP_PLACE, WARP_TWIN_PLACE = 200, 50, 7, 30, 120
SLOT_MARIO, LIVE, OTHER = OBJ(SLOT_MARIO_PLACE), OBJ(LIVE_PLACE), OBJ(OTHER_PLACE)
HEAD = BODY + R.LIST_HEADS_IN_BODY
FREE = BODY + R.FREE_LIST_IN_BODY
BEHAVIOR_MARIO, BEHAVIOR_OTHER, BEHAVIOR_ASIDE, BEHAVIOR_WARP = 0x800EE000, 0x800EE040, 0x800EE080, 0x800EB8DC
WARP_PARAMS = 0x000A0000


def put(slot: bytearray, address: int, value: int) -> None:
    struct.pack_into(">I", slot, address - BODY, value)


def word(slot: bytes, address: int) -> int:
    return struct.unpack_from(">I", slot, address - BODY)[0]


def at(index: int, offset: int) -> int:
    return OBJ(index) + offset


def activate(slot: bytearray, index: int, behavior: int, params: int = 0) -> None:
    put(slot, at(index, R.OBJECT_BEHAVIOR), behavior)
    put(slot, at(index, R.OBJECT_BEH_PARAMS), params)
    put(slot, at(index, R.OBJECT_ACTIVE_FLAGS), 0x0101 << 16)


def make_slot(mario: int = SLOT_MARIO, level: int = fakes.LEVEL, area: int = fakes.AREA) -> bytes:
    """A small world: Mario, an object he holds, the object sitting where the game now keeps Mario, a warp object."""
    slot = bytearray(usamune.SLOT_BYTES)
    mario_place = (mario - R.POOL_START) // R.OBJECT_SIZE
    put(slot, BODY + R.MARIO_MARIO_OBJ_OFF, mario)
    put(slot, BODY + R.MARIO_HELD_OBJ_OFF, OTHER)
    put(slot, BODY + R.MARIO_INTERACT_OBJ_OFF, LIVE)
    put(slot, usamune.MARIO_OBJECT, mario)
    put(slot, usamune.MARIO_OBJECT + 8, mario)                   # gCurrentObject
    put(slot, HEAD + 0x60, mario)
    put(slot, HEAD + 0x64, LIVE)
    put(slot, at(mario_place, 0x60), LIVE)
    put(slot, at(mario_place, 0x64), HEAD)
    put(slot, at(LIVE_PLACE, 0x60), HEAD)
    put(slot, at(LIVE_PLACE, 0x64), mario)
    put(slot, at(mario_place, 0x08), LIVE)                      # the draw circle
    put(slot, at(LIVE_PLACE, 0x08), OTHER)
    put(slot, at(OTHER_PLACE, 0x08), mario)
    put(slot, at(OTHER_PLACE, 0x68), mario)                     # parentObj
    put(slot, at(OTHER_PLACE, 0x214), mario)                    # platform
    put(slot, at(OTHER_PLACE, 0xF4), LIVE)                      # a rawData pointer
    put(slot, at(mario_place, 0x50), mario + 0x21C)             # throwMatrix: its own transform
    put(slot, FREE + 0x60, OBJ(60))
    activate(slot, mario_place, BEHAVIOR_MARIO)
    activate(slot, LIVE_PLACE, BEHAVIOR_ASIDE)
    activate(slot, OTHER_PLACE, BEHAVIOR_OTHER)
    activate(slot, WARP_TWIN_PLACE, BEHAVIOR_WARP, WARP_PARAMS)
    struct.pack_into(">I", slot, usamune.TRAILER_MARIO, mario)
    struct.pack_into(">hh", slot, usamune.TRAILER_LEVEL, level, area)
    return bytes(slot)


# --- the relocation itself ------------------------------------------------------------------------

def test_mario_takes_the_live_place_and_every_named_pointer_follows():
    out = R.relocate(make_slot(), [(SLOT_MARIO, LIVE)]).data
    assert word(out, at(LIVE_PLACE, R.OBJECT_BEHAVIOR)) == BEHAVIOR_MARIO
    assert word(out, at(SLOT_MARIO_PLACE, R.OBJECT_BEHAVIOR)) == BEHAVIOR_ASIDE
    assert struct.unpack_from(">I", out, usamune.TRAILER_MARIO)[0] == LIVE
    assert word(out, usamune.MARIO_OBJECT) == LIVE and word(out, usamune.MARIO_OBJECT + 8) == LIVE
    assert word(out, BODY + R.MARIO_MARIO_OBJ_OFF) == LIVE
    assert word(out, BODY + R.MARIO_INTERACT_OBJ_OFF) == SLOT_MARIO, "the object moved aside is followed too"
    assert word(out, BODY + R.MARIO_HELD_OBJ_OFF) == OTHER
    assert word(out, at(OTHER_PLACE, 0x68)) == LIVE and word(out, at(OTHER_PLACE, 0x214)) == LIVE
    assert word(out, at(OTHER_PLACE, 0xF4)) == SLOT_MARIO
    assert word(out, FREE + 0x60) == OBJ(60)
    assert word(out, at(LIVE_PLACE, 0x50)) == LIVE + 0x21C, "a pointer into an object keeps its offset"


def test_the_order_of_every_list_stays_the_same():
    """The object loop and the free list run by links: the world must run in the same order after the move."""
    out = R.relocate(make_slot(), [(SLOT_MARIO, LIVE)]).data
    assert word(out, HEAD + 0x60) == LIVE
    assert word(out, at(LIVE_PLACE, 0x60)) == SLOT_MARIO
    assert word(out, at(SLOT_MARIO_PLACE, 0x60)) == HEAD
    assert word(out, at(LIVE_PLACE, 0x08)) == SLOT_MARIO and word(out, at(OTHER_PLACE, 0x08)) == LIVE


def test_there_and_back_and_in_place_are_the_same_bytes():
    slot = make_slot()
    assert R.relocate(slot, [(SLOT_MARIO, SLOT_MARIO)]).data == slot
    assert R.relocate(R.relocate(slot, [(SLOT_MARIO, LIVE)]).data, [(LIVE, SLOT_MARIO)]).data == slot


@pytest.mark.parametrize("address", [at(OTHER_PLACE, 0x20), BODY + 0x1000, usamune.MARIO_OBJECT + 0x10],
                         ids=["the drawn position", "a global nobody named", "a word after the object globals"])
def test_a_pool_address_no_structure_explains_refuses(address):
    slot = bytearray(make_slot())
    put(slot, address, SLOT_MARIO)
    with pytest.raises(R.Ambiguous):
        R.relocate(bytes(slot), [(SLOT_MARIO, LIVE)])


def test_a_raw_data_pointer_is_reported_as_found_by_value():
    found = R.relocate(make_slot(), [(SLOT_MARIO, LIVE)])
    assert [(index, offset) for index, offset, _behavior in found.raw_data] == [(OTHER_PLACE, 0xF4)]


def test_a_move_names_an_object_where_the_slot_holds_it():
    third = OBJ(90)
    place = R.permutation([(SLOT_MARIO, LIVE), (LIVE, third)])
    assert place[SLOT_MARIO_PLACE] == LIVE_PLACE and place[LIVE_PLACE] == 90 and place[90] == SLOT_MARIO_PLACE


# --- the one write path ---------------------------------------------------------------------------

def live_world(game, *, live: int = LIVE, warp_object: int | None = None, focus: int = 0) -> None:
    """The running game's side: where Mario is, the area's one warp node, the camera's second focus."""
    game.poke(usamune.MARIO_OBJECT, struct.pack(">I", live))
    area, node = 0x80100000, 0x80100100
    game.poke(usamune.CURRENT_AREA_POINTER, struct.pack(">I", area))
    if warp_object is not None:
        game.poke(area + R.AREA_WARP_NODES, struct.pack(">I", node))
        game.poke(node, struct.pack(">III", 0x0A09010A, warp_object, 0))
        game.poke(warp_object + R.OBJECT_BEHAVIOR, struct.pack(">I", BEHAVIOR_WARP))
        game.poke(warp_object + R.OBJECT_BEH_PARAMS, struct.pack(">I", WARP_PARAMS))
    game.poke(R.SECOND_CAMERA_FOCUS, struct.pack(">I", focus))


def install(game, data, tmp_path, **extra):
    current_area = int.from_bytes(game.read(usamune.CURRENT_AREA_POINTER, 4), "big")
    image = fakes.make_image(game, data=data, current_area=current_area, **extra)
    return tool.install(game, image, tmp_path, dry_run=False, write_seed=False), image


def test_a_moved_mario_is_written_relocated_and_usamunes_gate_sees_a_match(tmp_path):
    game = fakes.FakeGame()
    live_world(game)
    install(game, make_slot(), tmp_path)
    written = game.read(usamune.slot_address(fakes.INDEX), usamune.SLOT_BYTES)
    assert written == R.relocate(make_slot(), [(SLOT_MARIO, LIVE)]).data
    facts, live = usamune.slot_facts(written), usamune.live_facts(game)
    assert (facts.mario_object, facts.level, facts.area) == (live.mario_object, live.level, live.area)


def test_a_mario_already_in_place_is_written_byte_for_byte(tmp_path):
    game = fakes.FakeGame()
    live_world(game, live=SLOT_MARIO, warp_object=OBJ(WARP_PLACE))
    install(game, make_slot(), tmp_path)
    assert game.read(usamune.slot_address(fakes.INDEX), usamune.SLOT_BYTES) == make_slot()


def test_the_warp_and_focus_objects_follow_the_live_pointers(tmp_path):
    game = fakes.FakeGame()
    live_world(game, warp_object=OBJ(WARP_PLACE))
    install(game, make_slot(), tmp_path)
    written = game.read(usamune.slot_address(fakes.INDEX), usamune.SLOT_BYTES)
    assert word(written, at(WARP_PLACE, R.OBJECT_BEHAVIOR)) == BEHAVIOR_WARP


class MovingGame(fakes.FakeGame):
    """Mario's place changes in the middle of the write, after the body lands and before the occupied word."""

    def write(self, address: int, data: bytes) -> None:
        super().write(address, data)
        if address == usamune.slot_address(fakes.INDEX):
            self.poke(usamune.MARIO_OBJECT, struct.pack(">I", OBJ(77)))


def test_mario_moving_between_the_check_and_the_write_leaves_the_slot_empty(tmp_path):
    game = MovingGame()
    live_world(game)
    with pytest.raises(usamune.MarioMoved):
        install(game, make_slot(), tmp_path)
    occupied = usamune.slot_address(fakes.INDEX) + usamune.TRAILER_MARIO
    assert game.read(occupied, 4) == bytes(4)
    assert all(usamune.slot_address(fakes.INDEX) <= a < usamune.slot_address(fakes.INDEX) + usamune.SLOT_BYTES
               for a, _d in game.writes)


def test_an_unclear_slot_is_refused_in_its_own_words_and_nothing_is_written(tmp_path):
    game = fakes.FakeGame()
    live_world(game)
    slot = bytearray(make_slot())
    put(slot, at(OTHER_PLACE, 0x20), SLOT_MARIO)
    with pytest.raises(tool.GameDiffers) as refused:
        install(game, bytes(slot), tmp_path)
    assert refused.value.kinds == ["unclear"] and game.writes == [] and not list(tmp_path.iterdir())


def test_a_course_set_moves_mario_through_the_same_step(tmp_path):
    game = libfakes.FakeGame()
    live_world(game)
    library = SlotLibrary(tmp_path / "library")
    state = libfakes.keep(library, game, make_slot())
    placed = place_states(game, library, {4: state.id}).placements[0]
    assert placed.written and placed.mario_matches and placed.loadable_now
    assert game.slot(4) == R.relocate(make_slot(), [(SLOT_MARIO, LIVE)]).data
    assert library.read_bytes(state.id) == make_slot(), "the kept state never changes"


def test_a_course_set_refuses_when_mario_moves_during_its_write(tmp_path):
    game = libfakes.FakeGame()
    live_world(game)
    library = SlotLibrary(tmp_path / "library")
    state = libfakes.keep(library, game, make_slot())

    def moves(address, _data):
        if address == usamune.slot_address(4) + usamune.TRAILER_MARIO + 4:
            game.poke(usamune.MARIO_OBJECT, struct.pack(">I", OBJ(77)))
    game.on_write = moves
    report = place_states(game, library, {4: state.id})
    assert report.failed and report.failed[0] == 4
    assert game.read(usamune.slot_address(4) + usamune.TRAILER_MARIO, 4) == bytes(4)


# --- one write path, by source ----------------------------------------------------------------------

WRITERS = (ROOT / "src", ROOT / "tools")


def sources():
    for folder in WRITERS:
        yield from (path for path in folder.rglob("*.py") if "node_modules" not in path.parts)


def test_every_slot_write_names_the_mario_place_it_was_made_for():
    calls = []
    for path in sources():
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"write_slot_bytes\((?![^)]*def )", text):
            if text[max(0, match.start() - 4):match.start()] == "def ":
                continue
            call = text[match.start():text.index("\n", match.start())]
            calls.append((path.name, call))
    assert calls, "the scan found no writer"
    for name, call in calls:
        assert "mario_place=" in call, f"{name}: {call}"
    unchecked = [(name, call) for name, call in calls if "mario_place=None" in call]
    assert unchecked == [("handoff_try.py", unchecked[0][1])] and "meta[" in unchecked[0][1], \
        "only putting a backup back writes without the Mario check"


def test_both_writers_move_mario_through_the_one_relocation():
    for relative in ("src/sm64_events/slotlibrary/coursesets.py", "src/sm64_events/slotlibrary/refit.py",
                     "tools/scene_lab/handoff_try.py"):
        assert "to_live_place(" in (ROOT / relative).read_text(encoding="utf-8"), relative
    owners = [path.name for path in sources() if re.search(r"^def relocate\(", path.read_text(encoding="utf-8"), re.M)]
    assert owners == ["relocate.py"], owners

"""A start or a slot someone else made, checked against this ROM's own state before anything runs from it
(tools/scene_lab/start_check.py). A synthetic reference and object pool: each hostile pointer is refused with a
sentence, and a pool the game itself made passes. His real starts pass too (measured, 2026-11-08: five takes)."""
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import start_check as sc  # noqa: E402

POOL, LISTS, MARIO = 0x8033D488, 0x8033CBE0, 0x8033B170
SCRIPTS = 0x800EB180
BEHAVIORS = {SCRIPTS + 0x2EC0, SCRIPTS + 0x1000}
GRAPH, COLLISION = 0x80120000, 0x80130000


class Symbols(dict):
    def name_of(self, address):
        return max((name for name, value in self.items() if value <= address), key=lambda name: self[name], default="?")


SYMBOLS = Symbols(gObjectPool=POOL, gObjectListArray=LISTS, gMarioStates=MARIO, gGameVblankHandler=0x8033B048)


def reference() -> sc.Reference:
    ram = np.zeros(8 << 20, np.uint8)
    return sc.Reference(ram, SYMBOLS, [(0x246000, 0x32D560), (0x378800, 0x385F90), sc.usamune_image("us")], BEHAVIORS,
                        (SCRIPTS, SCRIPTS + 0x5AA4), {0, GRAPH}, {0, COLLISION}, {LISTS + i * sc.NODE_BYTES for i in range(16)})


def objects(*changes):
    """Two live objects in list 0 (slots 3 and 7), every field the game would give them; changes: (slot, field, value)."""
    table = np.zeros((sc.OBJECT_COUNT, sc.OBJECT_BYTES // 4), np.int64)
    head = LISTS
    chain = [head, POOL + 3 * sc.OBJECT_BYTES, POOL + 7 * sc.OBJECT_BYTES, head]
    for slot in (3, 7):
        row = table[slot]
        row[sc.FIELD["behavior"] // 4] = SCRIPTS + 0x2EC0
        row[sc.FIELD["command"] // 4] = SCRIPTS + 0x2EC8
        row[sc.FIELD["stack"] // 4] = SCRIPTS + 0x2ED0
        row[sc.FIELD["graph"] // 4] = GRAPH
        row[sc.FIELD["collision"] // 4] = COLLISION
    table[3, sc.FIELD["next"] // 4], table[7, sc.FIELD["next"] // 4] = chain[2], chain[3]
    table[3, sc.FIELD["prev"] // 4], table[7, sc.FIELD["prev"] // 4] = chain[0], chain[1]
    for slot, name, value in changes:
        table[slot, (sc.FIELD[name] if isinstance(name, str) else name) // 4] = value
    heads = [chain[1]] + [LISTS + i * sc.NODE_BYTES for i in range(1, sc.LIST_COUNT)]
    return table, heads


def test_objects_the_game_made_pass():
    table, heads = objects()
    assert sc.check_objects(table, heads, reference()) == []


@pytest.mark.parametrize("change, words", [
    ((3, "behavior", 0x80100000), "a behaviour this game does not have"),
    ((3, "command", 0x80100000), "runs from outside the game's behaviour scripts"),
    ((7, "stack", 0x80150000), "would return outside"),
    ((7, "graph", 0x80150000), "a model this level does not load"),
    ((3, "collision", 0x80150010), "collision this level does not load"),
    ((3, "next", 0x80001000), "leaves the object pool"),
    ((7, "next", POOL + 3 * sc.OBJECT_BYTES), "visits an object twice")])
def test_a_hostile_object_is_refused(change, words):
    table, heads = objects(change)
    reasons = sc.check_objects(table, heads, reference())
    assert any(words in reason for reason in reasons), reasons


def test_a_free_slot_keeps_its_stale_fields_unchecked():
    """The game resets a slot's fields when it spawns into it; only the objects the lists reach run."""
    table, heads = objects((100, "behavior", 0x80100000), (100, "command", 0x80100000))
    assert sc.check_objects(table, heads, reference()) == []


def test_a_command_word_that_is_not_a_pointer_cannot_aim_the_interpreter():
    table, heads = objects((3, "command", 0x41000000))
    assert sc.check_objects(table, heads, reference()) == []


@pytest.mark.parametrize("value, refused", [(0x80246000, True), (0x80403000, True), (0x80205000, True), (0x8038F900, True),
                                            (0x80500010, True), (0x80000000, False), (0x80180000, False), (0x3F800000, False)])
def test_a_word_into_code_or_a_protected_place_is_refused(value, refused):
    """0x80000000 is also the float -0.0; 0x80180000 lies in the main pool (menu code only while a menu runs)."""
    begin = 0x33AF90
    words = np.zeros(0x200, np.int64)
    words[(0x8033B048 & 0x1FFFFFFF) - begin >> 2] = value
    reasons = sc.check_words(words, begin, reference())
    assert bool(reasons) == refused, reasons
    if refused:
        assert "gGameVblankHandler" in reasons[0]


def test_usamunes_image_ends_at_each_builds_own_state_block():
    """The image counts as code up to that build's state block, from memory/layout.py's rows; JP's block starts
    later (0x417C14, its usamune_stop - 1), so JP code between the two bounds is code too. cold_boot.py read the
    same two addresses off Usamune's own code (its "settings"): two sources, one answer."""
    import cold_boot
    for version in ("us", "jp"):
        begin, end = sc.usamune_image(version)
        assert begin == cold_boot.USAMUNE_IMAGE & 0x1FFFFFFF
        assert end == cold_boot.USAMUNE[version]["settings"] & 0x1FFFFFFF
    assert sc.usamune_image("us")[1] < sc.usamune_image("jp")[1]
    jp_code = 0x80417B60                                     # past US's bound, before JP's
    ref = reference()
    ref.code = ref.code[:-1] + [sc.usamune_image("jp")]
    begin = 0x33AF90
    words = np.zeros(0x200, np.int64)
    words[(0x8033B048 & 0x1FFFFFFF) - begin >> 2] = jp_code
    assert sc.check_words(words, begin, ref)                 # refused on JP
    assert sc.check_words(words, begin, reference()) == []   # US: that address is Usamune's state, not code


def test_a_function_pointer_the_game_itself_keeps_passes():
    ref = reference()
    begin = 0x33AF90
    struct.pack_into(">I", ref.ram, 0x33B048, 0x80246100)
    words = np.zeros(0x200, np.int64)
    words[(0x33B048 - begin) >> 2] = 0x80246100
    assert sc.check_words(words, begin, ref) == []

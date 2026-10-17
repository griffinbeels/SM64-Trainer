"""The native ghost's behaviour table names each script by its decomp symbol.

plugin/gfxwrap/ghost_behaviours.h holds each recognised script's segmented
offset on both cartridges. memory/behaviours.py reads the same offsets from
the bundled STROOP maps; the symbol is the identity, so every compiled offset
must be that symbol's offset in that version's map.
"""
from pathlib import Path
import re

from sm64_events.memory import behaviours

ROOT = Path(__file__).resolve().parents[1]
TABLE = ROOT / "plugin/gfxwrap/ghost_behaviours.h"
ROW = re.compile(r'\{Symbol::(\w+), "(bhv\w+)", \{(0x[0-9a-f]{8})u, (0x[0-9a-f]{8})u\}, (\d+)\}')


def rows():
    text = TABLE.read_text(encoding="utf-8")
    found = ROW.findall(text)
    enum = re.search(r"enum class Symbol : uint32_t \{(.*?)\};", text, re.S)[1]
    names = [name.strip() for name in enum.split(",") if name.strip()]
    # Every enum member but `none` has exactly one row, so none is skipped.
    assert [row[0] for row in found] == names[1:], "one parsed row per symbol"
    return found


def test_every_compiled_offset_is_its_symbols_offset_in_that_versions_map():
    wrong = {}
    for _member, symbol, us, jp, _animations in rows():
        for version, offset in (("us", us), ("jp", jp)):
            mapped = behaviours.offsets(version).get(symbol)
            if mapped != int(offset, 16):
                wrong[(symbol, version)] = (offset, hex(mapped) if mapped is not None else None)
    assert not wrong, f"ghost_behaviours.h disagrees with data/behaviours_*.tsv (compiled, map): {wrong}"


def test_jp_moves_exactly_the_four_scripts_the_maps_move():
    moved = {symbol for _member, symbol, us, jp, _animations in rows() if us != jp}
    assert moved == {"bhvBobomb", "bhvBreakableBoxSmall", "bhvMips", "bhvHoot"}


def test_no_offset_names_two_scripts_on_one_cartridge():
    table = rows()
    for column in (2, 3):
        offsets = [row[column] for row in table]
        assert len(set(offsets)) == len(offsets)

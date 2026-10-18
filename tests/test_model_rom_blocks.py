"""The model knows where Usamune JP keeps every ROM block it reads.

renderer/model/assets.c names each block by its US offset (Mario's three, and
every held model's segment from tools/build_model.py HELD_MODELS) and maps it
to the JP cartridge through `jp_blocks`. A block with no JP row would leave a
held kind or Mario undrawn on JP; this pins the table without any ROM. The
bytes at each offset are proven by tests/test_native_model.py, given the ROMs
(SM64_MODEL_JP_ROM), which compares the decoded art across cartridges.
"""
import importlib.util
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
ASSETS = (ROOT / "renderer/model/assets.c").read_text(encoding="utf-8")


def build_model():
    spec = importlib.util.spec_from_file_location("build_model_blocks", ROOT / "tools/build_model.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def defines():
    values = {}
    for name, value in re.findall(r"#define (\w+_BASE) (0x[0-9a-f]+|\d+)u", ASSETS):
        values[name] = int(value, 0)
    return values


def jp_blocks():
    table = re.search(r"jp_blocks\[\] = \{(.*?)\n\};", ASSETS, re.S)[1]
    named = defines()
    rows = {}
    for us, jp, shift in re.findall(r"\{(\w+), (0x[0-9a-f]+)u, (-?0x[0-9a-f]+|0)\}", table):
        rows[named[us] if us in named else int(us.rstrip("u"), 16)] = (int(jp, 16), int(shift, 16))
    return rows


def test_every_block_the_model_reads_has_a_jp_row():
    named = defines()
    used = {named["ANIM_BASE"], named["TEXTURE_BASE"], named["COMMON1_BASE"]}
    for model in build_model().HELD_MODELS.values():
        used.add(model.segment)
        used.update(block for _segment, block in model.texture_segments)
    assert used == set(jp_blocks()), used ^ set(jp_blocks())


def test_jp_blocks_are_distinct_and_inside_the_cartridge():
    rows = jp_blocks()
    targets = [jp for jp, _shift in rows.values()]
    assert len(set(targets)) == len(targets)
    assert all(0 < jp < 8 << 20 for jp in targets)
    # JP's code is smaller than US's: every block sits a little earlier.
    assert all(0 < us - jp < 0x4000 for us, (jp, _shift) in rows.items())


def test_only_the_relocated_group_shifts_and_by_the_us_relocation():
    """US moved Mips's texture 0xF0 later inside group 6 (HELD_MODELS' shift);
    JP keeps it at the decomp address, so its JP shift undoes exactly that."""
    shifted = {us: shift for us, (_jp, shift) in jp_blocks().items() if shift}
    relocated = {model.segment: -model.shift for model in build_model().HELD_MODELS.values() if model.shift}
    assert shifted == relocated == {0x1D8310: -0xF0}

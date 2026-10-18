"""Read a new ROM build's addresses off the ROM images themselves.

    uv run python tools/derive_rom_candidates.py <US Usamune .z64> <other .z64> [--version jp]

Usamune's own globals and the hunted controller struct are named by no symbol
map, so `sync/rom_candidates.py` takes them from here. The method: the same
source compiled for two builds has the same instruction stream with different
addresses in it. Mask every immediate and jump target, align the two streams
(difflib), and pair each `lui`/offset reference -- the US instruction that
reaches a US address names, in the matching instruction of the other build,
that build's address. Two regions are aligned: the vanilla code segment, and
Usamune's payload (found as the stretch of the ROM dense with references to
Usamune's own data page).

The check that it finds the right address and not a neighbour: every symbol
row of memory/layout.py it reaches must agree with STROOP's map for the other
build (`data/symbols_<v>.tsv`). On 2026-10-16 all 18 did, for v1.93u -> v1.93j.

Read-only; prints candidates for every layout row it reaches. The vanilla
alignment takes several minutes. Write the result into
`src/sm64_events/data/rom_candidates_<v>.tsv` by hand -- it is evidence for a
live gate, never a shipped value.
"""
import argparse
import difflib
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sm64_events.memory.behaviours import globals_map  # noqa: E402
from sm64_events.memory.layout import LAYOUT_ROWS, layout_for  # noqa: E402

VANILLA_CODE = (0x1000, 0x110000)     # ROM span of the main code segment, both builds
LOAD_STORE_ADDIU = {9, 32, 33, 35, 36, 37, 40, 41, 43, 49, 53, 57, 61}
LUI = 15
BLOCK = 0x1000


def words(rom: bytes, start: int, end: int) -> list[int]:
    return [struct.unpack_from(">I", rom, at)[0] for at in range(start, end - 3, 4)]


def masked(word: int) -> int:
    op = word >> 26
    if op == 0:
        return word                     # R-type: exact
    if op in (2, 3):
        return op << 26                 # j/jal: target masked
    return word & 0xFFFF0000            # I-type: immediate masked


def s16(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


def pair_references(a: list[int], b: list[int]) -> dict[int, Counter]:
    """US address -> Counter of the other build's address at the matching
    instruction, over every aligned lui/offset pair."""
    matcher = difflib.SequenceMatcher(None, [masked(w) for w in a], [masked(w) for w in b],
                                      autojunk=False)
    pairs: dict[int, Counter] = defaultdict(Counter)
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            ua, ub = a[block.a + k], b[block.b + k]
            if ua >> 26 != LUI:
                continue
            register = (ua >> 16) & 31
            for step in range(1, 10):
                if k + step >= block.size:
                    break
                na, nb = a[block.a + k + step], b[block.b + k + step]
                if (na >> 26) in LOAD_STORE_ADDIU and ((na >> 21) & 31) == register:
                    us = (((ua & 0xFFFF) << 16) + s16(na & 0xFFFF)) & 0xFFFFFFFF
                    other = (((ub & 0xFFFF) << 16) + s16(nb & 0xFFFF)) & 0xFFFFFFFF
                    pairs[us][other] += 1
                if (na >> 26) == LUI and ((na >> 16) & 31) == register:
                    break
    return pairs


def payload_span(rom: bytes, data_page: int) -> tuple[int, int]:
    """The longest run of ROM blocks holding a `lui` of Usamune's data page."""
    dense = []
    for start in range(0, len(rom), BLOCK):
        found = any(w >> 26 == LUI and (w & 0xFFFF) == data_page
                    for w in words(rom, start, min(start + BLOCK, len(rom))))
        dense.append(found)
    best, run_start = (0, 0), None
    for index, hit in enumerate(dense + [False]):
        if hit and run_start is None:
            run_start = index
        elif not hit and run_start is not None:
            if index - run_start > best[1] - best[0]:
                best = (run_start, index)
            run_start = None
    return best[0] * BLOCK, best[1] * BLOCK


NEIGHBOUR_REACH = 0x100


def best(pairs, address):
    """(other build's address, references, how). A field reached only
    through a struct base (a counter read as base + 2) takes the shift of its
    nearest referenced neighbour, and says so."""
    counter = pairs.get(address)
    if counter:
        other, count = counter.most_common(1)[0]
        return other, count, "direct"
    near = [(abs(key - address), key) for key in pairs if abs(key - address) <= NEIGHBOUR_REACH]
    if not near:
        return None, 0, ""
    _, key = min(near)
    other, count = pairs[key].most_common(1)[0]
    return other + (address - key), count, f"via neighbour {key:#x}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("us_rom", type=Path)
    parser.add_argument("other_rom", type=Path)
    parser.add_argument("--version", default="jp")
    args = parser.parse_args()
    us_rom, other_rom = args.us_rom.read_bytes(), args.other_rom.read_bytes()
    us = layout_for("us")
    data_page = us.usamune_overall >> 16
    pairs: dict[int, Counter] = defaultdict(Counter)
    spans = (VANILLA_CODE, payload_span(us_rom, data_page), payload_span(other_rom, data_page))
    print(f"payload: US {spans[1][0]:#x}-{spans[1][1]:#x}, {args.version} "
          f"{spans[2][0]:#x}-{spans[2][1]:#x}", flush=True)
    for a_span, b_span in ((spans[0], spans[0]), (spans[1], spans[2])):
        found = pair_references(words(us_rom, *a_span), words(other_rom, *b_span))
        for key, counter in found.items():
            pairs[key].update(counter)
    symbols = globals_map(args.version)
    agree = disagree = 0
    for row in LAYOUT_ROWS:
        us_value = us.value(row.field)
        if us_value is None or row.field == "behaviour_base":
            continue
        address, count, how = best(pairs, us_value)
        expected = symbols.get(row.symbol) if row.symbol else None
        verdict = ""
        if expected is not None and address is not None:
            verdict = "AGREES with STROOP" if address == expected else f"DISAGREES (STROOP {expected:#x})"
            agree += address == expected
            disagree += address != expected
        shown = (f"{address:#010x} x{count} {how}" if address is not None
                 else "no aligned reference")
        print(f"{row.field:24s} US {us_value:#010x} -> {shown}  {verdict}")
    print(f"symbol rows: {agree} agree, {disagree} disagree")
    return 1 if disagree else 0


if __name__ == "__main__":
    sys.exit(main())

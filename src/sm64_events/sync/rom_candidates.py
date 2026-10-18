"""Candidates read off the ROM images, for the addresses no symbol map names.

STROOP's maps name the decomp's globals, so `data/symbols_<v>.tsv` gives every
symbol row of `memory/layout.py` a candidate. Usamune's own globals and the
hunted controller struct are in no map. For those,
`tools/derive_rom_candidates.py` aligns the code of the two Usamune ROMs --
immediates masked, so the same function lines up however its addresses moved
-- and pairs every `lui`/offset reference: the US instruction that reads a US
address names, in the matching JP instruction, the JP address. On the vanilla
segment that pairing agrees with STROOP's JP map on all 18 symbol rows it
reaches, which is the evidence the method finds the right address and not a
neighbour. Its output is `data/rom_candidates_<v>.tsv` (address, field, how).

A candidate is still only a candidate: the sync runner's gate decides, live,
and only a `verified` verdict is promoted into layout.py.
"""
from functools import cache
from importlib import resources


@cache
def _table(version: str) -> dict[str, tuple[int, str]]:
    try:
        text = (resources.files("sm64_events.data") / f"rom_candidates_{version}.tsv").read_text(
            encoding="utf-8")
    except FileNotFoundError:
        return {}
    rows = {}
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        address, field, how = line.split("\t", 2)
        rows[field] = (int(address, 16), how)
    return rows


def rom_candidate(version: str, field: str) -> int | None:
    entry = _table(version).get(field)
    return entry[0] if entry else None


def rom_derivation(version: str, field: str) -> str | None:
    entry = _table(version).get(field)
    return entry[1] if entry else None

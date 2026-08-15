"""The behaviour-script SYMBOL is the identity; the pointer is per version.

WHY A SYMBOL. A landmark used to be keyed by its behaviour POINTER — the RAM
address of the behaviour script — which is fixed for one ROM and reads as an
identity as long as there is one ROM. There are two. STROOP's linker maps,
fetched 2026-08-15, put `bhvBobomb` at segmented 0x13003174 on US and
0x13003154 on JP, `bhvGoomba` at 0x1300472C and 0x1300470C, and the segment's
own RAM base differs again — so neither the pointer nor the segmented offset
names "a bob-omb" on both ROMs. The decomp symbol does. Every key the tracker
stores (`core/landmark.py`), every caused-moment row
(`addresses.CAUSED_BEHAVIOURS`) and every catalogue name is a symbol now; this
module is the only place a symbol becomes a pointer or a pointer a symbol.

THE DATA is both maps' behaviour segments, held verbatim as package data
(`src/sm64_events/data/behaviours_<v>.tsv`, written by
`tools/import_stroop_maps.py`, resolved by `core/paths.py::bundled_symbol_map`)
so the frozen exe carries them and a disagreement with the source is a diff.
US 536 symbols, JP 532 (JP lacks `bhvPlaysMusicTrackWhenTouched` — the "Music
Touch" object is a US addition — and three unused stubs).

THE BASE — where segment 0x13 sits in RAM — comes from the version's layout
(`memory/layout.py::behaviour_base`), or is passed in by the sync runner
while it is still discovering it. It is found with no human step: Mario's own
object carries `bhvMario`, so `base_from_mario(version, that pointer)` is the
base (`sync/address_gates.py`). US: 0x800EB180, anchored 2026-08-07 on his
bob-omb and confirmed on 8 of 8 touched objects.

NAME CASE IS THE GRAMMAR, by convention with `eventlabel._kind_phrase`: a
COMMON noun ships lowercase and the sentence gives it an article ("Pick up a
bob-omb"); a PROPER noun ships capitalized and stands bare ("Pick up
Bowser"). The name's own first letter is the whole rule — no second table.
`display_name` and its two tables moved here verbatim from
tools/corpus_behaviors.py on 2026-08-15 so the app can name a JP object too.
"""
import re
from collections.abc import Iterator
from functools import lru_cache

from sm64_events.core.paths import bundled_symbol_map

SEGMENT = 0x13000000

# Symbols the shipped catalogue deliberately skips: the decomp's own labels
# for content the real game never spawns, and the map's non-behaviour rows
# (`behavior_data_unused_0`, `unused_1`) that carry no `bhv` prefix at all.
_SKIP_PREFIXES = ("bhvUnused", "bhvStub", "bhvBeta")

# Symbol -> shipped name, where the mechanical split below misses: proper
# nouns that must stand bare, decomp suffixes a player never says ("Boss",
# "Grabbing"), and community spellings.
NAME_OVERRIDES = {
    "bhvBowser": "Bowser",
    "bhvBreakableBoxSmall": "small breakable box",
    "bhvDoorWarp": "warp door",
    "bhvEyerokBoss": "Eyerok",
    "bhvHeaveHo": "heave-ho",
    "bhvKingBobomb": "King Bob-omb",
    "bhvMacroUkiki": "Ukiki",
    "bhvMessagePanel": "sign",
    "bhvMrBlizzard": "Mr. Blizzard",
    "bhvMrI": "Mr. I",
    "bhvPoleGrabbing": "pole",
    "bhvSignOnWall": "wall sign",
    "bhvToadMessage": "Toad",
    "bhvTuxiesMother": "Tuxie's Mother",
    "bhvWhompKingBoss": "Whomp King",
    "bhvWigglerHead": "Wiggler",
}

# Split-word -> shipped form. Words not listed here keep their capitals only
# if they are acronyms or digits; everything else lowercases (the common-noun
# default of the case convention above).
WORD_FORMS = {
    "Bobomb": "bob-omb", "Bowsers": "Bowser's", "Snowmans": "snowman's",
    "Tuxies": "Tuxie's", "Mr": "Mr.", "Mips": "MIPS",
    "Bowser": "Bowser", "Peach": "Peach", "Mario": "Mario", "Toad": "Toad",
    "Yoshi": "Yoshi", "Ukiki": "Ukiki", "Wiggler": "Wiggler",
    "Unagi": "Unagi", "Eyerok": "Eyerok", "Dorrie": "Dorrie",
    "Klepto": "Klepto", "Hoot": "Hoot", "Lakitu": "Lakitu",
    "Bbh": "BBH", "Bitfs": "BitFS", "Bob": "BoB", "Ccm": "CCM",
    "Ddd": "DDD", "Hmc": "HMC", "Jrb": "JRB", "Lll": "LLL", "Rr": "RR",
    "Ssl": "SSL", "Thi": "THI", "Ttm": "TTM", "Wdw": "WDW", "Wf": "WF",
}

_SPLIT = re.compile(r"(?<=[a-z0-9])(?=[A-Z])"
                    r"|(?<=[A-Z])(?=[A-Z][a-z])"
                    r"|(?<=[A-Za-z])(?=[0-9])")

def _read_tsv(name: str) -> dict[str, int]:
    table: dict[str, int] = {}
    for line in bundled_symbol_map(name).read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        address, symbol = line.split("\t")
        table[symbol] = int(address, 16)
    return table


@lru_cache(maxsize=None)
def offsets(version: str) -> dict[str, int]:
    """symbol -> segmented address (0x13xxxxxx) for this version's ROM."""
    return _read_tsv(f"behaviours_{version}.tsv")


@lru_cache(maxsize=None)
def globals_map(version: str) -> dict[str, int]:
    """symbol -> RAM address for the globals memory/layout.py derives from a
    symbol. A CANDIDATE, not a verified value: the sync runner confirms it."""
    return _read_tsv(f"symbols_{version}.tsv")


@lru_cache(maxsize=None)
def _by_offset(version: str) -> dict[int, str]:
    return {offset: symbol for symbol, offset in offsets(version).items()}


def symbols(version: str) -> frozenset[str]:
    return frozenset(offsets(version))


def _base(version: str, base: int | None) -> int | None:
    if base is not None:
        return base
    from sm64_events.memory.layout import layout_for
    return layout_for(version).behaviour_base


def pointer_of(version: str, symbol: str, base: int | None = None) -> int | None:
    """The RAM pointer this version gives `symbol`, or None while the base is
    unknown or the symbol is not in this version's ROM."""
    resolved_base = _base(version, base)
    offset = offsets(version).get(symbol)
    if resolved_base is None or offset is None:
        return None
    return resolved_base + (offset - SEGMENT)


def symbol_of(version: str, pointer: int, base: int | None = None) -> str:
    """`bhvDoor` for a pointer this version's map names; `ptr:800ebc8c` for
    one it does not (nothing is dropped, and a `ptr:` key is a finding the
    sync dashboard shows)."""
    resolved_base = _base(version, base)
    if resolved_base is not None:
        found = _by_offset(version).get(pointer - resolved_base + SEGMENT)
        if found is not None:
            return found
    return f"ptr:{pointer:08x}"


def base_from_mario(version: str, mario_behaviour_pointer: int) -> int:
    """The segment base, from the one object whose symbol is never in doubt:
    Mario's own object carries `bhvMario`."""
    return mario_behaviour_pointer - (offsets(version)["bhvMario"] - SEGMENT)


def display_name(symbol: str) -> str:
    """`bhvWhompKingBoss` -> "Whomp King"; `bhvChainChomp` -> "chain chomp"."""
    if symbol in NAME_OVERRIDES:
        return NAME_OVERRIDES[symbol]
    shaped = []
    for word in _SPLIT.sub(" ", symbol[3:]).split(" "):
        if word in WORD_FORMS:
            shaped.append(WORD_FORMS[word])
        elif word.isupper() or word.isdigit():
            shaped.append(word)
        else:
            shaped.append(word.lower())
    return " ".join(shaped)


def kind_names(version: str) -> Iterator[tuple[str, str]]:
    """(symbol, shipped name) for every behaviour the catalogue names, in
    map order."""
    table = offsets(version)
    for symbol in sorted(table, key=table.__getitem__):
        if not symbol.startswith("bhv") or symbol.startswith(_SKIP_PREFIXES):
            continue
        yield symbol, display_name(symbol)

"""Lift every stored landmark key from a behaviour POINTER to its SYMBOL.

Until 2026-08-15 a landmark key was `level:area:<US pointer>:x,y,z` and a
kind key `kind:<US pointer>` (`core/landmark.py`). The identity is the decomp
symbol now — `6:1:bhvDoor:-1775,0,-824`, `kind:bhvDoor` — because JP moves
every pointer (`memory/behaviours.py`). Every db written before that carries
pointer-form keys in three places: journal payloads (`events.payload`, the
`landmark` object of a `moment_reached`/`warp_entered`), the name catalogue
(`landmark_names.key` and `.seed_key`), and segment definitions (the pinned
`landmark` of a trigger or waypoint clause). This module rewrites all three,
in TEXT, at db open (`storage/db.py::Database.__init__`), so the projector,
the recorder and reconcile all see one key shape.

Idempotent by construction: a key already in symbol form has no 8-hex-digit
third field and does not match; a pointer the US table cannot name becomes
`ptr_800xxxxx`, which is a symbol shape too and never matches again. Every
key stored before this date was written by a US build, so resolving through
the US table is not an assumption — it is what wrote them.

Proof of harmlessness (`tests/test_rekey.py`): the projection of a db before
the rekey equals its projection after, and a second run touches 0 rows.
"""
import re

from sm64_events.memory.behaviours import symbol_of

# `6:1:800ebc8c:` — level, area, an 8-hex-digit pointer, inside a key.
INSTANCE_KEY = re.compile(r"(?<![0-9A-Za-z_])(\d+):(\d+):([0-9a-f]{8}):")
# `kind:` + an 8-hex-digit pointer, not followed by more hex — a kind key.
KIND_KEY = re.compile(r"kind:([0-9a-f]{8})(?![0-9a-fA-Z_])")


def us_symbol_of_pointer(pointer: int) -> str:
    return symbol_of("us", pointer)


def rekey_text(text: str, symbol_of_pointer=us_symbol_of_pointer) -> str:
    """Every pointer-form landmark key in `text` (a JSON payload, a key, a
    seed key), rewritten to symbol form. Text with no such key returns
    unchanged, so callers can compare identity to know whether to write."""
    def instance(match: re.Match) -> str:
        symbol = symbol_of_pointer(int(match.group(3), 16))
        return f"{match.group(1)}:{match.group(2)}:{symbol}:"

    def kind(match: re.Match) -> str:
        return f"kind:{symbol_of_pointer(int(match.group(1), 16))}"

    return KIND_KEY.sub(kind, INSTANCE_KEY.sub(instance, text))

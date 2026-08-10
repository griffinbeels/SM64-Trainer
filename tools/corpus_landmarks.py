"""The shipped LANDMARK CATALOGUE: which door, which pole, which bob-omb.

His ask, 2026-08-05: *"if we already know that a specific door is the door to
HMC, we don't ever need to redefine that… We should be building up each of
these entities for each course/area of the game, and the user can select
whatever scenarios they want to build from there."* So a name lands here once
and every install gets it; nobody re-records what somebody already identified.

TWO LEVELS, and the first is the multiplier. A KIND row names a behaviour
pointer, which is fixed for the ROM, so one row names every pole in the game.
An INSTANCE row names one specific thing, keyed by
`level:area:behaviour:x,y,z` — where the game SPAWNED it, because the pool slot
it happens to occupy changes every time the area reloads
(`memory/addresses.py::OBJECT_HOME_POS` carries that measurement).

HOW A ROW GETS HERE. He names it in the recorder while playing, which writes it
to his own db with `seed_dirty=1`; `uv run python tools/corpus_from_db.py
--landmarks` prints every such name as a row to paste here. That flag did not
exist until 2026-08-07 — this docstring named the tool as the promotion path
for two days while the tool only handled segment definitions, so every name he
typed stayed on his machine and he asked *"are the doors to each specific
course room annotated? Did we miss anything?"* The doc was ahead of the code;
both are true now.

A DERIVATION FROM THE DECOMP WAS TRIED FIRST and did not land, recorded so the
next attempt starts further along rather than repeating it. The castle's course
doors are not in `levels/castle_inside/script.c`'s OBJECT list (that carries
only the star doors and the key doors), not in `areas/1/macro.inc.c`, and
`areas/1/collision.inc.c` has no SPECIAL_OBJECTS section. One real clue came
out of it: his "Courtyard Door" home reads x = -1023, which is exactly the
MIDPOINT of the two door halves the script places at -1100 and -946 — so a
door's `oHome` is the PAIR's centre, not either leaf, and any future
derivation has to average. It needs the raw files rather than a summarised
fetch.

The keys are LONG and that is on purpose — a key you can read is a key you can
check against `tools/probe_objects.py --report` without decoding anything.

THE KIND LEVEL IS GENERATED, since round 8 item 2 (2026-08-07): every behavior
script in the US ROM ships named, derived from STROOP's symbol map through
`corpus_behaviors.py` — the base anchoring, the 8-of-8 validation against his
own play, and the name-case grammar all live there. Nobody hand-names two
dozen kinds any more; what stays hand-written here is the INSTANCE rows, his
own labels from the sessions that found them.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import corpus_behaviors  # noqa: E402


def kind(behaviour: int, name: str) -> dict:
    """Name a whole family, game-wide."""
    return {"seed_key": f"landmark:kind:{behaviour:08x}",
            "key": f"kind:{behaviour:08x}", "name": name}


def at(level: int, area: int, behaviour: int, home: tuple, name: str) -> dict:
    """Name one specific thing, by where the game spawned it."""
    key = f"{level}:{area}:{behaviour:08x}:{home[0]},{home[1]},{home[2]}"
    return {"seed_key": f"landmark:{key}", "key": key, "name": name}


# Behaviour pointers observed in his 2026-08-05 session, BEFORE the symbol
# table arrived. Named then from what Mario was DOING when he touched them —
# the first family always fired ACT_PULLING_DOOR / ACT_PUSHING_DOOR and left
# him in the same area, the second always ended in ACT_WARP_DOOR_SPAWN
# somewhere else — and the decomp agrees: bhvDoor and bhvDoorWarp exactly.
# That agreement is part of the base constant's evidence (corpus_behaviors).
DOOR = 0x800EBC8C
WARP_DOOR = 0x800EBC7C

CASTLE_INSIDE, BASEMENT, LOBBY = 6, 3, 1

# Every behavior script, named — bhvDoor -> "door", bhvBobomb -> "bob-omb",
# bhvBowser -> "Bowser" — so "Pick up an object" can never appear for a thing
# the ROM has a name for.
_KIND_ROWS = tuple(kind(pointer, name)
                   for pointer, name in corpus_behaviors.kind_names())

LANDMARKS: tuple[dict, ...] = _KIND_ROWS + (
    # HIS OWN LABELS, verbatim from the session that found the key: "21/22 are
    # me opening the door to HMC", "23/24 are me opening the door to the moat
    # area leading to the castle grounds", "25/26 is a different door leading
    # to the DDD area".
    at(CASTLE_INSIDE, BASEMENT, DOOR, (1126, -1074, -2661), "HMC Door"),
    at(CASTLE_INSIDE, BASEMENT, DOOR, (717, -1177, -869), "Moat Door"),
    at(CASTLE_INSIDE, BASEMENT, DOOR, (-3097, -1279, 1434), "DDD Door"),
    # ONE physical door, TWO rows, and this is why the area is part of the key:
    # the castle geometry is shared, so the basement side and the lobby side
    # are separate objects at identical coordinates.
    at(CASTLE_INSIDE, BASEMENT, WARP_DOOR, (-1100, -1074, 922),
       "Basement Stairs Door (from the basement)"),
    at(CASTLE_INSIDE, LOBBY, WARP_DOOR, (-1100, -1074, 922),
       "Basement Stairs Door (from the lobby)"),

    # HIS SECOND ROUND OF LABELS, 2026-08-07, promoted with
    # `tools/corpus_from_db.py --landmarks` — he asked *"are the doors to each
    # specific course room annotated? Did we miss anything?"* against a WF star
    # door reading "a door", and the honest answer was no. These are the ones
    # he had already typed by then, verbatim, so a fresh install stops
    # re-answering a question he has answered.
    at(CASTLE_INSIDE, LOBBY, DOOR, (256, 0, -1074), "WF Door"),
    at(CASTLE_INSIDE, LOBBY, DOOR, (-1775, 0, -824), "Left Basement Door"),
    at(CASTLE_INSIDE, LOBBY, DOOR, (-271, 0, -824), "Right Basement Door"),
    at(CASTLE_INSIDE, LOBBY, WARP_DOOR, (-1023, -101, -5170), "Courtyard Door"),
    at(CASTLE_INSIDE, BASEMENT, WARP_DOOR, (7885, -1586, -511),
       "Moat to Castle Grounds Door"),
    at(16, 1, WARP_DOOR, (3292, -511, -2931), "Castle Grounds to Moat Door"),
    at(7, 1, DOOR, (3817, 205, 870), "Maze Door"),
)

# NOT SHIPPED, and this is the one judgement call in the list: his ninth label
# was "Whomp Text" on the Whomp King's dialogue object (24:1:800edd38). It is
# his own shorthand and it is right for him — his row carries seed_dirty=1 and
# keeps it forever — but the KIND catalogue already names that behaviour
# "Whomp King" for every install, which reads better as a shipped default than
# one person's abbreviation. Nothing is lost: seeding an instance name here
# would only change what OTHER people see.

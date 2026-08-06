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
to his own db with `seed_dirty=1`; `tools/corpus_from_db.py` is how a name is
promoted into this table and shipped. Everything below came out of his session
on 2026-08-05, and the three basement doors are the ones he labelled by hand
while the probe was running.

The keys are LONG and that is on purpose — a key you can read is a key you can
check against `tools/probe_objects.py --report` without decoding anything.
"""


def kind(behaviour: int, name: str) -> dict:
    """Name a whole family, game-wide."""
    return {"seed_key": f"landmark:kind:{behaviour:08x}",
            "key": f"kind:{behaviour:08x}", "name": name}


def at(level: int, area: int, behaviour: int, home: tuple, name: str) -> dict:
    """Name one specific thing, by where the game spawned it."""
    key = f"{level}:{area}:{behaviour:08x}:{home[0]},{home[1]},{home[2]}"
    return {"seed_key": f"landmark:{key}", "key": key, "name": name}


# Behaviour pointers observed in his 2026-08-05 session. Named from what Mario
# was DOING when he touched them, not from a symbol table we do not have: the
# first family always fired ACT_PULLING_DOOR / ACT_PUSHING_DOOR and left him in
# the same area, the second always ended in ACT_WARP_DOOR_SPAWN somewhere else.
DOOR = 0x800EBC8C
WARP_DOOR = 0x800EBC7C

CASTLE_INSIDE, BASEMENT, LOBBY = 6, 3, 1

LANDMARKS: tuple[dict, ...] = (
    kind(DOOR, "Door"),
    kind(WARP_DOOR, "Warp Door"),
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
)

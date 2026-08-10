"""WHICH thing in the world a moment happened to — that door, not a door.

A moment used to be `kind + level + ORDINAL`: "the 5th door in the basement".
His ruling, 2026-08-05: *"it's less about the specific order of doors, and more
about WHICH door is being entered… this specific door that happens to be the
5th one you open"*. So the ordinal demotes to a property and the NAME comes
from the game's own object.

THE KEY IS WHERE IT SPAWNED, and the evidence for that is in
`memory/addresses.py::OBJECT_HOME_POS`. Short version: the pool slot is not an
identity (his three basement doors wore slots 3/2/0, then 38/42/44 after a
reload, then 3/2/0 again) and neither is the live position (one SSL bob-omb took
14 of them across 21 grabs). The spawn point held for both.

THIS MODULE IS THE ONLY DOOR onto that key. It takes an identity — never the
ingredients a caller assembles — so two surfaces cannot build the same landmark's
name two different ways, which is the failure `tests/test_single_source.py`
exists to stop.

The AREA is part of the key and is not decoration: one level's two areas can
host the same physical door at the same coordinates, and the castle's
basement↔lobby door does exactly that.

A SCRIPTLESS OBJECT IS KEYED BY WHERE IT STANDS, which is what makes naming a
specific pole possible at all. His push, 2026-08-07: *"we sometimes have
subsections based on grabbing a specific pole... Are we SURE there's no way to
distinguish between poles? Not even their locations? Some stable location that
they always spawn at?"* — and he was right. The game creates poles and trees
with no spawn point, so `home` reads (0,0,0) and every one of them in an area
used to share one key; but such an object never MOVES, so its live position IS
its authored placement. Measured from the 2026-08-05 probe captures: the WF
tree, 6 grabs across TWO area reloads, position (2560, 256, 4608) byte-
identical every time — clean power-of-2 designer coordinates.

There is deliberately NO static-kind allowlist, because the measurement says
one would be a list that rots for nothing. Of every unplaced object those
captures caught through Mario's three engagement pointers, only an EXPLOSION
and a popping STAR ever took more than one position — and no moment fires on
either (`detectors/moment.py`'s registry is doors, textboxes, poles, pickups).
Mario himself never reaches those pointers at all. So the objects that can
carry a name are already the ones that hold still.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Landmark:
    """One specific thing in the world, named by where the game put it."""

    level: int
    area: int
    behaviour: int              # which KIND — a door, a pole, a bob-omb
    home: tuple[int, int, int]  # spawn x/y/z, rounded; the game writes exact
                                # values here and no jitter was observed
    pos: tuple[int, int, int] = (0, 0, 0)   # where it stands right now

    @property
    def where(self) -> tuple[int, int, int]:
        """The coordinate this landmark is known by: its spawn point when the
        game wrote it one, else where it stands (see the module docstring)."""
        return self.home if self.home != (0, 0, 0) else self.pos

    @property
    def key(self) -> str:
        """The stable name this landmark answers to, in every store and payload."""
        return (f"{self.level}:{self.area}:{self.behaviour:08x}"
                f":{self.where[0]},{self.where[1]},{self.where[2]}")

    @property
    def kind_key(self) -> str:
        """The name the whole FAMILY answers to, game-wide.

        A behaviour pointer is fixed for the ROM, so naming "Pole" once names
        every pole in Super Mario 64. That is what makes the catalogue tractable
        by hand: kinds are a couple of dozen rows, and only the instances he
        actually routes on need a name of their own.
        """
        return f"kind:{self.behaviour:08x}"

    @property
    def placed(self) -> bool:
        """Whether a LEVEL SCRIPT put this here, i.e. the game wrote it a spawn
        point. Poles and trees read False and are still nameable — see
        `nameable`, which is the question a rename control asks."""
        return self.home != (0, 0, 0)

    @property
    def nameable(self) -> bool:
        """Whether a name typed here lands on THIS thing and nothing else.

        His ruling, 2026-08-07: *"we should be able to rename ANYTHING"* — and
        since a scriptless static object is keyed by where it stands, almost
        everything now qualifies. What still cannot is an object with NEITHER
        coordinate: the game made it and it is standing at the origin, so its
        key would be shared with every other of its kind in that area and one
        name would land on all of them.
        """
        return self.where != (0, 0, 0)

    def payload(self) -> dict:
        return {"key": self.key, "kind_key": self.kind_key,
                "behaviour": self.behaviour,
                "home": list(self.home), "pos": list(self.pos),
                "placed": self.placed, "nameable": self.nameable}


def landmark_at(snapshot) -> Landmark | None:
    """The landmark Mario is engaged with on this frame, or None."""
    if not snapshot.landmark_behaviour:
        return None
    return Landmark(
        level=snapshot.curr_level,
        area=snapshot.curr_area,
        behaviour=snapshot.landmark_behaviour,
        home=tuple(int(round(axis)) for axis in snapshot.landmark_home),
        pos=tuple(int(round(axis)) for axis in snapshot.landmark_pos),
    )


# --- one thing wearing several keys (round 13 items 2+3) -------------------
#
# A star door is TWO OBJECTS — the game builds it from two halves, each with
# its own spawn point — so one physical door produced two keys and his rename
# stuck to whichever half he happened to push ("it felt like a new landmark").
# Measured 2026-08-08: the 70/50/8-star doors each read as a pair of keys 154
# units apart. His rule IS the mechanism: *"If there are multiple options, and
# I rename them to the same name, they should all collapse to the same
# landmark."* SAME NAME = SAME LANDMARK, within a scope that keeps accidents
# out: only instance keys of the SAME LEVEL and SAME KIND may collapse — a
# pole and a door both named "Checkpoint" stay two things, and a name reused
# in another course never reaches across. The area is deliberately NOT in the
# scope: the basement↔lobby warp door is one physical door standing in two
# areas, and naming both sides alike SHOULD make them one.
#
# The collapse is name-defined — no alias table, no canonical key. Naming is
# the merge gesture, renaming the group moves every member (see the API
# route), and matching asks `same_landmark` instead of key equality.

def group_scope(key: str | None) -> tuple | None:
    """The bucket inside which same-named keys are one landmark, or None for
    a key that never groups (a KIND row names a whole family already; an
    ENTRANCE identity is already one key per entrance)."""
    if not key or ":" not in key:
        return None
    parts = str(key).split(":")
    if len(parts) != 4 or not parts[0].isdigit():
        return None
    level, _area, behaviour, _where = parts
    return (level, behaviour)


def same_landmark(pinned: str | None, key: str | None,
                  names: dict | None) -> bool:
    """Whether a payload's landmark IS the pinned one — key equality, or two
    same-scope instance keys he has given one name."""
    if pinned is None or key is None:
        return False
    if key == pinned:
        return True
    scope = group_scope(pinned)
    if scope is None or scope != group_scope(key):
        return False
    named = (names or {}).get(pinned)
    return named is not None and named == (names or {}).get(key)


def landmark_group(key: str, names: dict) -> list[str]:
    """Every key the catalogue currently reads as one landmark with `key` —
    the set a rename or an erase applies to. Always contains `key` itself."""
    return [key] + [other for other in names
                    if other != key and same_landmark(key, other, names)]

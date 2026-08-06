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
ingredients a caller assembles — so two surfaces cannot build the same entity's
name two different ways, which is the failure `tests/test_single_source.py`
exists to stop.

The AREA is part of the key and is not decoration: one level's two areas can
host the same physical door at the same coordinates, and the castle's
basement↔lobby door does exactly that.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Entity:
    """One specific thing in the world, named by where the game spawned it."""

    level: int
    area: int
    behaviour: int              # which KIND — a door, a pole, a bob-omb
    home: tuple[int, int, int]  # spawn x/y/z, rounded; the game writes exact
                                # values here and no jitter was observed

    @property
    def key(self) -> str:
        """The stable name this entity answers to, in every store and payload."""
        return (f"{self.level}:{self.area}:{self.behaviour:08x}"
                f":{self.home[0]},{self.home[1]},{self.home[2]}")

    @property
    def placed(self) -> bool:
        """False when the GAME made this one mid-play, so it has no name.

        Mario himself, a star popping out of a box, the marker he spawns at —
        a level script never wrote them a spawn point, so several of them share
        one key. Anything that offers to NAME an entity has to refuse these,
        because the name would land on all of them at once.
        """
        return self.home != (0, 0, 0)

    def payload(self) -> dict:
        return {"key": self.key, "behaviour": self.behaviour,
                "home": list(self.home), "placed": self.placed}


def entity_at(snapshot) -> Entity | None:
    """The entity Mario is engaged with on this frame, or None."""
    if not snapshot.entity_behaviour:
        return None
    return Entity(
        level=snapshot.curr_level,
        area=snapshot.curr_area,
        behaviour=snapshot.entity_behaviour,
        home=tuple(int(round(axis)) for axis in snapshot.entity_home),
    )

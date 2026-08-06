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
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Landmark:
    """One specific thing in the world, named by where the game spawned it."""

    level: int
    area: int
    behaviour: int              # which KIND — a door, a pole, a bob-omb
    home: tuple[int, int, int]  # spawn x/y/z, rounded; the game writes exact
                                # values here and no jitter was observed

    @property
    def key(self) -> str:
        """The stable name this landmark answers to, in every store and payload."""
        return (f"{self.level}:{self.area}:{self.behaviour:08x}"
                f":{self.home[0]},{self.home[1]},{self.home[2]}")

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
        """False when the GAME made this one mid-play, so it has no name.

        Mario himself, a star popping out of a box, the marker he spawns at —
        a level script never wrote them a spawn point, so several of them share
        one key. Anything that offers to NAME a landmark has to refuse these,
        because the name would land on all of them at once.
        """
        return self.home != (0, 0, 0)

    def payload(self) -> dict:
        return {"key": self.key, "kind_key": self.kind_key,
                "behaviour": self.behaviour,
                "home": list(self.home), "placed": self.placed}


def landmark_at(snapshot) -> Landmark | None:
    """The landmark Mario is engaged with on this frame, or None."""
    if not snapshot.landmark_behaviour:
        return None
    return Landmark(
        level=snapshot.curr_level,
        area=snapshot.curr_area,
        behaviour=snapshot.landmark_behaviour,
        home=tuple(int(round(axis)) for axis in snapshot.landmark_home),
    )

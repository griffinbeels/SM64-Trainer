"""Is a move between world nodes physically possible, and is it progress?

Reads the SAME `WORLD_EDGES_TWO_WAY`/`WORLD_EDGES_ONE_WAY` tables in
`memory/addresses.py` that already drive the segment builder's dropdown
filtering, and answers the two questions `SegmentEngine` needs to decide
whether an armed segment is still being run (spec
2026-08-01-topological-segment-validity).

THIS REVERSES A DELIBERATE DECISION, knowingly. `segments.py::can_run_from`'s
section comment refuses to consult this table: "a stored def must keep
matching whatever edges the emulator invents... and a check derived from that
table could only ever be tested against the table it came from." The first
half still holds for ARMING -- nothing here gates a start trigger. The second
half is answered by `tools/measure_topology_cancels.py`, which scores these
rules against the real JOURNAL rather than against the table, so a missing
edge shows up as a killed real success instead of a silent agreement.

Lives in `tracking/` rather than `memory/` on purpose: `addresses.py` is the
registry of WHAT THE WORLD IS; this module answers WHAT A MOVE MEANS, which is
a tracking question.
"""
from collections import deque
from functools import lru_cache

from sm64_events.memory.addresses import (LEVEL_CASTLE_INSIDE, node_key,
                                          world_connections)


def node_for(level: int | None, area: int | None) -> str | None:
    """The world-node key for a tracked position, or None when unknown.

    A subarea counts ONLY inside the castle interior (level 6), which is the
    only place `WORLD_EDGES_*` models one. Courses have their own areas -- SSL
    area 2 is the pyramid interior, LLL area 2 the volcano -- and keying on
    (level, area) everywhere read 97.9% of the live journal's settled moves as
    off-graph, against the true 54% (measured 2026-08-01, the design's own
    first pass). That failure is silent and reads exactly like a broken world
    table, which is why it has a test of its own.
    """
    if level is None:
        return None
    return node_key(level, area if level == LEVEL_CASTLE_INSIDE else None)


def graph_node(node_key_string: str | None) -> str | None:
    """A node key from ANY source, re-expressed in the graph's own vocabulary.

    `segments.start_origin` builds its keys straight out of a clause's params,
    so an `area_enter(level=21, area=1)` resolves to `"21:1"` -- a subarea key
    for a level the world graph models as ONE place. `node_for` is the graph's
    rule (subareas only inside the castle interior) and every key that reaches
    `hops`/`between` has to have been through it, or a perfectly real node
    silently answers None and the caller reads that as "no constraint".

    Measured cost of not doing this: the arena retirement rule's first version
    compared a def's `"33:1"` against a stage's `"33"` and retired the arena's
    OWN fight -- the one thing it must never do (2026-08-10).
    """
    if not node_key_string:
        return None
    level, _, area = node_key_string.partition(":")
    if not level.lstrip("-").isdigit():
        return None
    return node_for(int(level), int(area) if area else None)


@lru_cache(maxsize=1)
def graph_nodes() -> frozenset:
    """Every node the world graph knows -- sources and destinations alike.

    A destination-only node (a Bowser arena is entered and never linked out of
    by anything else) is still a place the player can stand, so both sides of
    every edge count.
    """
    successors = _successors()
    nodes = set(successors)
    for destinations in successors.values():
        for level, area in destinations:
            nodes.add(node_key(level, area))
    return frozenset(nodes)


@lru_cache(maxsize=4096)
def between(from_key: str | None, to_key: str | None) -> frozenset:
    """Every node lying on SOME shortest walk from one node to another,
    both ends included -- "is this place on the way?".

    A node is on the way when going through it costs nothing:
    `hops(from, node) + hops(node, to) == hops(from, to)`. SOME rather than
    THE, because the castle offers more than one shortest route to several
    places and a player picking either is not making a detour -- the same
    reason rule 2 in `_flush_move` fires on a strict hop INCREASE and treats
    equal as sideways.

    An empty result means "no answer", never "nowhere": either end unknown, or
    no directed path at all. Callers read that as the unconstrained case, the
    convention `hops` and `segments.step_node` already take.
    """
    span = hops(from_key, to_key)
    if span is None:
        return frozenset()
    on_the_way = {from_key, to_key}
    for node in graph_nodes():
        lead, rest = hops(from_key, node), hops(node, to_key)
        if lead is not None and rest is not None and lead + rest == span:
            on_the_way.add(node)
    return frozenset(on_the_way)


@lru_cache(maxsize=1)
def _successors() -> dict:
    """`world_connections()` rebuilds its map from module constants on every
    call, and both readers below hit it once per position event per armed def.
    Cached for the same reason `addresses.world_regions()` is, and safe for the
    same reason: every input is a module constant, never mutated at runtime."""
    return world_connections()


def _dest(key: str) -> list:
    """A node key back into the `[level, area|None]` shape
    `world_connections()` stores its destinations in."""
    level_str, _, area_str = key.partition(":")
    return [int(level_str), int(area_str) if area_str else None]


def is_legal_move(from_key: str | None, to_key: str | None) -> bool:
    """Could a player actually walk `from_key` -> `to_key`?

    False means the Usamune warp menu (or a savestate load) fabricated the
    edge. Unknown on either side, and a move to the same node, are both True --
    unknown means yes, the convention this codebase takes everywhere, because a
    rule that fired on missing information would cancel every segment on a
    legacy journal that carries no position events at all.
    """
    if from_key is None or to_key is None or from_key == to_key:
        return True
    return _dest(to_key) in _successors().get(from_key, [])


@lru_cache(maxsize=4096)
def hops(from_key: str | None, to_key: str | None) -> int | None:
    """Fewest legal moves from one node to another, or None when either side is
    unknown or no directed path exists.

    Directed: the one-way rows matter. A Bowser arena exits to the castle and
    is never re-entered from it, so `hops("30", "6:1")` is 1 while
    `hops("6:1", "30")` is 2 (out through BitDW's pipe).
    """
    if from_key is None or to_key is None:
        return None
    if from_key == to_key:
        return 0
    successors = _successors()
    seen = {from_key}
    queue = deque([(from_key, 0)])
    while queue:
        node, distance = queue.popleft()
        for level, area in successors.get(node, []):
            neighbour = node_key(level, area)
            if neighbour == to_key:
                return distance + 1
            if neighbour not in seen:
                seen.add(neighbour)
                queue.append((neighbour, distance + 1))
    return None


def entrance_node(destination: int | None) -> str | None:
    """The world NODE holding the entrance that leads to `destination` -- the
    exact place Mario stands when he touches its painting, portal, hole or
    pipe, castle SUBAREA included.

    THE one door for that derivation (task 0081): the corpus authors entrance
    clauses through it, `segments.fires_from` checks arm positions against it,
    and `segments.arm_level`/`start_areas` place a definition that STARTS on
    one -- so the builder, the shipped corpus and the selector cannot disagree
    about where an entrance lives. Read off the world graph, never a hand
    table -- BBH is entered from the courtyard and VCUtM from the grounds, so
    a list would have been wrong on the day it was written and would drift
    again the first time an edge is corrected.

    Four destinations carry a second predecessor that is not castle-side
    (BitDW from the Bowser 1 arena, BitFS from Bowser 2, BitS from Bowser 3,
    HMC from CotMC); the castle REGION node is the one a player walks from, and
    it is unique for all 23 destinations the corpus uses. None when the
    destination is unknown or nothing castle-side reaches it -- the caller
    treats that as "no constraint", the codebase's unknown-means-yes rule.

    The SUBAREA half exists because the castle quick-select row filters on
    `(level, area)` pairs: `entrance_level` alone answers "Castle Inside" for
    all five basement entrances and all the lobby ones alike, which is not a
    row the selector can offer from.
    """
    if destination is None:
        return None
    from sm64_events.memory.addresses import (CASTLE_REGION_NODES,
                                              world_connections)
    region = {node_key(level, area) for level, area in CASTLE_REGION_NODES}
    sources = {node for node, destinations in world_connections().items()
               if node in region
               and any(tuple(step) == (destination, None)
                       for step in destinations)}
    return next(iter(sources)) if len(sources) == 1 else None


def entrance_level(destination: int | None) -> int | None:
    """The LEVEL holding that entrance. `entrance_node` without its subarea,
    derived rather than computed a second way."""
    node = entrance_node(destination)
    return int(node.partition(":")[0]) if node else None


def node_area(node_key_string: str | None) -> int | None:
    """The castle subarea a node names, or None for a node with no subarea."""
    if not node_key_string:
        return None
    _, _, area = node_key_string.partition(":")
    return int(area) if area else None

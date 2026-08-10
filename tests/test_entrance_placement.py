# tests/test_entrance_placement.py
"""A definition that STARTS on an entrance touch is placed where the entrance
is, not where it leads.

Found by merging main into the subsection branch, and named by Griffin before
the merge finished: *"I added functionality that allowed us to detect when we
enter the *portrait* for DDD for the mips clip segment, which is probably a
pretty overarching theme in these types of segments (the event for entering a
course warp, not actually warping into it)."*

He is describing the same shape `moment_reached` has, and it had the same gap.
Every other branch of `arm_level`/`start_areas` was written for a trigger that
fires when Mario ARRIVES somewhere; these two fire while he is still standing
where they name. With no branch, `start_levels`/`start_areas` answer empty, the
selector rows filter on exactly those, and a definition nobody can place is a
definition nobody can pick.

The trap this file exists to pin: the clause's own `to` is the DESTINATION, and
Mario does not reach it for 77 frames. Reading `to` here would offer the
definition inside the course you have not entered yet.
"""
from sm64_events.tracking import topology
from sm64_events.tracking.segments import arm_level, start_areas, start_levels

# Dire, Dire Docks -- Griffin's own case, and the one the MIPS Clip movement
# ends on. Its entrance is the basement portal, so the derived place is
# level 6 (Castle Inside) area 3 (Basement), never level 23.
DDD = 23
CASTLE_INSIDE, BASEMENT = 6, 3


def _touch(destination=DDD):
    return {"type": "entrance_touched", "to": destination}


def test_the_arm_level_is_where_the_entrance_is_not_where_it_leads():
    assert arm_level(_touch()) == CASTLE_INSIDE
    assert arm_level(_touch()) != DDD, (
        "reading the clause's own `to` places the definition in a course "
        "Mario does not reach for another 77 frames")


def test_it_carries_the_subarea_so_the_castle_row_can_offer_it():
    """The level alone answers "Castle Inside" for every basement and lobby
    entrance alike -- `stagebanner.js` filters on (level, area) pairs, so
    without the subarea the row can never match."""
    assert start_areas([_touch()]) == [[CASTLE_INSIDE, BASEMENT]]
    assert start_levels([_touch()]) == [CASTLE_INSIDE]


def test_an_unplaceable_destination_constrains_nothing():
    """Unknown means yes, the codebase's own convention -- a clause with no
    destination, or one nothing castle-side reaches, must not be pinned to a
    place it might not be in."""
    assert arm_level({"type": "entrance_touched"}) is None
    assert start_areas([{"type": "entrance_touched"}]) == []


def test_every_destination_the_corpus_uses_resolves_to_one_place():
    """The derivation is the world graph's, so a corrected edge moves this and
    a hand table would not. Whatever `entrance_node` can answer for, both
    readers must agree with it -- that agreement is the property, not any
    particular list of levels."""
    from sm64_events.memory.addresses import LEVEL_NAMES
    placed = 0
    for destination in LEVEL_NAMES:
        node = topology.entrance_node(destination)
        if node is None:
            assert arm_level(_touch(destination)) is None
            continue
        placed += 1
        assert arm_level(_touch(destination)) == topology.entrance_level(
            destination)
        area = topology.node_area(node)
        expected = [[topology.entrance_level(destination), area]] if area else []
        assert start_areas([_touch(destination)]) == expected
    assert placed >= 20, (
        f"only {placed} destinations resolved to an entrance -- the world "
        "graph lost edges, or CASTLE_REGION_NODES stopped covering the castle")

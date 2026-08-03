from sm64_events.memory.addresses import world_connections
from sm64_events.tracking.topology import hops, is_legal_move, node_for


def test_a_castle_subarea_keeps_its_area_and_a_course_drops_its_own():
    # The world graph models subareas ONLY for the castle interior (level 6).
    # Courses have their own areas -- SSL area 2 is the pyramid interior -- and
    # keying on (level, area) everywhere read 97.9% of real journal moves as
    # off-graph instead of the true 54% (measured 2026-08-01).
    assert node_for(6, 3) == "6:3"
    assert node_for(8, 2) == "8"
    assert node_for(8, None) == "8"
    assert node_for(None, 1) is None


def test_a_real_edge_is_legal_and_a_warp_is_not():
    assert is_legal_move("6:3", "8") is True        # basement -> SSL painting
    assert is_legal_move("6:1", "30") is False      # lobby -> Bowser 1 arena
    assert is_legal_move("24", "8") is False        # WF -> SSL, warp menu only


def test_an_unknown_side_is_always_legal():
    # Unknown means yes: a legacy journal carries no position events, and a
    # rule that fired on missing information would cancel every segment.
    assert is_legal_move(None, "8") is True
    assert is_legal_move("8", None) is True
    assert is_legal_move("8", "8") is True


def test_hop_counts_match_the_shipped_world_table():
    assert hops("6:1", "8") == 2       # lobby -> basement -> SSL
    assert hops("6:3", "8") == 1
    assert hops("30", "8") == 3        # arena -> lobby -> basement -> SSL
    assert hops("6:3", "7") == 1       # basement -> HMC
    assert hops("22", "7") == 2        # inside LLL -> basement -> HMC
    assert hops("8", "8") == 0


def test_hops_is_none_when_unknown_or_unreachable():
    assert hops(None, "8") is None
    assert hops("8", None) is None
    assert hops("8", "999") is None


def test_leaving_bitfs_puts_you_in_the_LOBBY_not_the_basement():
    """The BitFS exit is a movement TRICK he routes on, not a quirk (live
    report 2026-08-02): *"The fastest path to getting to upstairs is actually
    to go Bowser 2 -> Basement -> Re-enter bowser in the fire sea -> Exit to
    lobby -> Upstairs."*  Measured over both journals, taking the SETTLED area:
    BitFS exits landed in the Lobby 11 times and the Basement once.

    It is ONE-WAY. The basement is the real door in; a lobby -> BitFS move is
    the warp menu and must stay off-graph."""
    assert is_legal_move("19", "6:1") is True
    assert is_legal_move("6:1", "19") is False
    assert is_legal_move("6:3", "19") is True       # the real door in, unchanged


def test_entering_bitfs_is_not_walking_away_from_upstairs():
    """What the missing edge COST, stated as the number that decides it.

    `Bowser 2 -> Upstairs` was silently killed the instant he entered the pipe,
    because with BitFS reachable only from the basement it sat 3 hops from
    Upstairs where the basement sat 2 — so Rule 2 read the FASTEST REAL ROUTE
    as a wrong turn. Equal is sideways and tolerated; strictly further is what
    cancels. This is the assertion that has to hold, not the edge itself."""
    assert hops("6:3", "6:2") == 2                  # basement -> lobby -> upstairs
    assert hops("19", "6:2") == 2                   # BitFS   -> lobby -> upstairs
    assert hops("19", "6:2") <= hops("6:3", "6:2")


def test_a_one_way_EXIT_does_not_claim_a_place_that_has_a_real_door():
    """`world_regions` treats one-way rows as undirected on purpose — a Bowser
    arena has no other castle link, so its exit IS its ownership. That was
    only ever true because every one-way row WAS an arena cutscene.

    `(19, _LOBBY)` broke it: BitFS has a real two-way door from the BASEMENT
    (his spec 2026-07-23, "the basement region owns BitFS") and now also exits
    to the lobby, so one undirected pass moved it into the lobby on gameflow
    order — renaming its library group and reordering the origin taxonomy for
    a topology fix that had nothing to do with either. Two-way edges are
    walked FIRST now; one-way rows only claim what is still unowned."""
    from sm64_events.memory.addresses import world_regions
    regions = world_regions()
    assert regions["19"] == "6:3", "BitFS belongs to the basement, its door in"
    assert regions["17"] == "6:1"                  # BitDW off the lobby
    assert regions["21"] == "6:2"                  # BitS off upstairs
    # The arenas still resolve through their one-way exits, which is the case
    # the undirected pass exists for.
    assert regions["30"] == "6:1" and regions["33"] == "6:3"


def test_the_world_graph_is_strongly_connected():
    """Every ordered pair of world nodes is reachable — measured 2026-08-02 at
    **0 unreachable of 992**, over 32 nodes and 67 edges.

    A CHARACTERIZATION test, not TDD: it passed the moment it was written. Its
    teeth were proved by deleting a two-way edge and watching it go red.

    This is the measurement that closed Stage 2 of the topological-validity
    work as a PREMISE ERROR. That stage was scoped as builder-side filtering —
    "only allow the user to choose topologically valid options from each node
    in their segment" — and the castle is hub-and-spoke with two-way spokes, so
    every start/end pair a builder can express is already a valid path. The
    filter had nothing to reject. What replaced it is the path cursor: a
    definition DECLARES its ordered stops, because reachability cannot tell a
    deliberate shortcut from a wrong turn.

    So a failure here is NEWS, not a broken assertion: an edge deletion has
    partitioned the graph, and that is the moment a reachability check becomes
    meaningful again.
    """
    connections = world_connections()
    # Sources are node keys; destinations are [level, area] pairs, so they go
    # through the same resolver the matcher reads positions with.
    nodes = set(connections) | {node_for(level, area)
                                for dests in connections.values()
                                for level, area in dests}
    unreachable = [(source, target) for source in nodes for target in nodes
                   if source != target and hops(source, target) is None]
    assert unreachable == []
    assert len(nodes) * (len(nodes) - 1) > 0, "the graph lost every node"

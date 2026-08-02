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

from sm64_events.memory import addresses as A


def test_star_grab_action_set_contains_all_dance_variants():
    assert A.ACT_STAR_DANCE_EXIT in A.STAR_GRAB_ACTIONS
    assert A.ACT_STAR_DANCE_WATER in A.STAR_GRAB_ACTIONS
    assert A.ACT_STAR_DANCE_NO_EXIT in A.STAR_GRAB_ACTIONS
    assert A.ACT_FALL_AFTER_STAR_GRAB in A.STAR_GRAB_ACTIONS


def test_course_names():
    assert A.course_name(1) == "Bob-omb Battlefield"
    assert A.course_name(15) == "Rainbow Ride"
    assert A.course_name(99) == "Course 99"  # graceful fallback


def test_star_names_main_course():
    assert A.star_name(1, 2) == "Shoot to the Island in the Sky"
    assert A.star_name(1, 6) == "100 Coins"
    assert A.star_name(14, 0) == "Roll into the Cage"


def test_star_names_fallback():
    assert A.star_name(99, 0) == "Star 1"
    assert A.star_name(1, 9) == "Star 10"


def test_star_count_owns_the_seven_star_rule():
    assert A.star_count(1) == 7    # six named + 100 Coins
    assert A.star_count(15) == 7
    assert A.star_count(16) == 1   # Bowser course
    assert A.star_count(19) == 2   # Princess's Secret Slide
    assert A.star_count(0) == 5    # Castle Secret: 3 Toad + 2 MIPS, no 100-coin


def test_castle_secret_star_names_match_the_decomp_flag_order():
    """Ids come from include/save_file.h's SAVE_FLAG_COLLECTED_TOAD_STAR_1..
    _MIPS_STAR_2 under SAVE_FLAG_TO_STAR_FLAG's >>24, cross-checked against
    behaviors/mips.inc.c spawning STAR_INDEX_ACT_4 + oBhvParams2ndByte."""
    assert A.star_name(0, 0) == "Toad Star (Basement)"
    assert A.star_name(0, 1) == "Toad Star (Upstairs)"
    assert A.star_name(0, 2) == "Toad Star (Tippy)"
    assert A.star_name(0, 3) == "MIPS 1st Star"
    assert A.star_name(0, 4) == "MIPS 2nd Star"
    # course 0 has no 100-coin star: the 7-star rule is main-courses-only
    assert A.star_name(0, 6) == "Star 7"


def test_mario_offsets_derive_from_struct_base():
    assert A.MARIO_ACTION == A.MARIO_STRUCT + 0x0C
    assert A.MARIO_ACTION_TIMER == A.MARIO_STRUCT + 0x1A
    assert A.MARIO_NUM_STARS == A.MARIO_STRUCT + 0xAA


def test_course_by_level_is_consistent_with_the_name_tables():
    # Drift guard: every mapped level must name the SAME place as the course
    # it points at. A typo in either table (or a wrong pairing) breaks this
    # before it can silently fail to retire a stale active star.
    for level, course in A.COURSE_BY_LEVEL.items():
        assert level in A.LEVEL_NAMES, level
        assert course in A.COURSE_NAMES, course
        assert A.LEVEL_NAMES[level] == A.COURSE_NAMES[course], (level, course)


def test_course_for_level_returns_none_for_hubs_and_unknown():
    assert A.course_for_level(8) == 8           # SSL level -> SSL course
    assert A.course_for_level(9) == 1           # BoB level -> course 1
    assert A.course_for_level(6) is None        # Castle Inside (hub)
    assert A.course_for_level(16) is None        # Castle Grounds (hub)
    assert A.course_for_level(30) is None        # Bowser 1 Arena (no course)
    assert A.course_for_level(None) is None
    assert A.course_for_level(999) is None       # unknown id

    # Every main course (1-15) is reachable from exactly one level.
    mapped = set(A.COURSE_BY_LEVEL.values())
    assert set(range(1, 16)) <= mapped


# -- world topology (segment-builder dropdown constraints, 2026-07-23) ---------

def test_world_connections_reference_only_registered_levels_and_areas():
    conn = A.world_connections()
    for node_key, destinations in conn.items():
        level_str, _, area_str = node_key.partition(":")
        assert int(level_str) in A.LEVEL_NAMES, node_key
        if area_str:
            assert int(level_str) == A.LEVEL_CASTLE_INSIDE, node_key
            assert int(area_str) in A.CASTLE_AREA_NAMES, node_key
        for dest_level, dest_area in destinations:
            assert dest_level in A.LEVEL_NAMES, (node_key, dest_level)
            if dest_area is not None:
                assert dest_level == A.LEVEL_CASTLE_INSIDE
                assert dest_area in A.CASTLE_AREA_NAMES


def test_world_connections_match_the_user_topology_spec():
    # The 2026-07-23 spec, verbatim: exiting a basement course can ONLY land
    # in the castle basement; each hub region reaches exactly its own stars
    # plus its stated hub/Bowser exits.
    conn = A.world_connections()
    assert conn["22"] == [[6, 3]]          # LLL exits to the basement, nowhere else
    basement = {tuple(d) for d in conn["6:3"]}
    assert {(7, None), (22, None), (8, None), (23, None),    # HMC LLL SSL DDD
            (19, None), (16, None)} <= basement              # BitFS + grounds
    assert not any(lvl in (9, 24, 12, 5, 17, 21) for lvl, _ in basement)
    upstairs = {tuple(d) for d in conn["6:2"]}
    assert {(10, None), (11, None), (36, None), (13, None),  # SL WDW TTM THI
            (14, None), (15, None), (21, None)} <= upstairs  # TTC RR + BitS
    assert not any(lvl in (16, 17, 19, 26) for lvl, _ in upstairs)
    lobby = {tuple(d) for d in conn["6:1"]}
    assert {(9, None), (24, None), (12, None), (5, None),    # BoB WF JRB CCM
            (17, None), (16, None), (26, None)} <= lobby     # BitDW grounds courtyard
    assert not any(lvl in (19, 21) for lvl, _ in lobby)      # other Bowsers unreachable


def test_world_connections_arena_edges_are_directed():
    # Arenas are entered ONLY through their course's pipe; their exits dump
    # Mario back at the course's castle region. The reverse moves don't exist.
    conn = A.world_connections()
    assert [30, None] in conn["17"]                        # BitDW pipe -> B1 arena
    assert [6, 1] in conn["30"]                            # fight exit -> lobby
    assert not any(lvl == 30 for lvl, _ in conn["6:1"])    # lobby can't enter the arena
    assert not any(lvl == 17 for lvl, _ in conn["30"])     # arena never exits into BitDW
    assert [19, None] in conn["23"]                        # DDD sub bay -> BitFS
    assert not any(lvl == 23 for lvl, _ in conn["19"])     # BitFS never exits into DDD


def test_world_regions_assigns_every_level():
    regions = A.world_regions()
    for level in A.LEVEL_NAMES:
        if level == A.LEVEL_CASTLE_INSIDE:
            continue          # the interior is three region nodes, not one
        assert A.node_key(level) in regions, A.LEVEL_NAMES[level]


def test_world_regions_match_the_castle_layout():
    regions = A.world_regions()
    lobby = A.node_key(A.LEVEL_CASTLE_INSIDE, A.AREA_LOBBY)
    basement = A.node_key(A.LEVEL_CASTLE_INSIDE, A.AREA_BASEMENT)
    upstairs = A.node_key(A.LEVEL_CASTLE_INSIDE, A.AREA_UPSTAIRS)
    # the ones a naive level->region guess gets wrong
    assert regions[A.node_key(4)] == A.node_key(A.LEVEL_CASTLE_COURTYARD)  # BBH
    assert regions[A.node_key(18)] == A.node_key(A.LEVEL_CASTLE_GROUNDS)   # VCUtM
    assert regions[A.node_key(28)] == basement          # CotMC, through HMC
    assert regions[A.node_key(A.BOWSER_1_ARENA)] == lobby
    assert regions[A.node_key(A.BOWSER_2_ARENA)] == basement
    assert regions[A.node_key(A.BOWSER_3_ARENA)] == upstairs
    # a region node is its own region
    assert regions[basement] == basement


def test_region_for_a_subarea_less_castle_node_is_the_lobby():
    # `level_enter to=6` with no to_subarea: every castle entry lands in the
    # lobby before settling, so that is where it belongs.
    assert A.region_for_node(A.node_key(A.LEVEL_CASTLE_INSIDE)) == \
        A.node_key(A.LEVEL_CASTLE_INSIDE, A.AREA_LOBBY)
    assert A.region_for_node(None) is None


def test_castle_region_nodes_are_in_gameflow_order():
    assert A.CASTLE_REGION_NODES == (
        (A.LEVEL_CASTLE_GROUNDS, None),
        (A.LEVEL_CASTLE_INSIDE, A.AREA_LOBBY),
        (A.LEVEL_CASTLE_INSIDE, A.AREA_BASEMENT),
        (A.LEVEL_CASTLE_COURTYARD, None),
        (A.LEVEL_CASTLE_INSIDE, A.AREA_UPSTAIRS))


def test_mips_stars_resolve_to_the_basement():
    assert A.CASTLE_SECRET_STAR_AREAS[3] == A.AREA_BASEMENT
    assert A.CASTLE_SECRET_STAR_AREAS[4] == A.AREA_BASEMENT
    assert 0 not in A.CASTLE_SECRET_STAR_AREAS   # Toad stars deliberately absent


def test_node_label_reads_subareas_and_levels():
    assert A.node_label(A.node_key(A.LEVEL_CASTLE_INSIDE, A.AREA_BASEMENT)) == "Basement"
    assert A.node_label(A.node_key(8)) == "Shifting Sand Land"

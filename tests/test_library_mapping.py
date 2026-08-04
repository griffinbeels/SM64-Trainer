from sm64_events.library import mapping


def test_maps_a_main_course_star_by_name():
    assert mapping.map_target("1. Bob-omb Battlefield",
                              "Big Bob-omb on the Summit (JP)") == "star:1:0"
    assert mapping.map_target("1. Bob-omb Battlefield",
                              "Behind Chain Chomp's Gate") == "star:1:5"


def test_a_trailing_annotation_does_not_block_the_match():
    assert mapping.map_target(
        "15. Rainbow Ride",
        "The Big House in the Sky (PAUSE TIME INCLUDED)") == "star:15:1"


def test_a_100c_row_is_the_courses_100_coin_star():
    # Every "+ 100c" row times a run of the course's 100-COIN star ending on
    # the star it names -- the exit-star variant model ranks/standards.py
    # already carries. Four CCM rows therefore share one entity.
    assert mapping.map_target("1. Bob-omb Battlefield",
                              "Find the 8 Red Coins + 100c (US)") == "star:1:6"
    for label in ("Slip Slidin' Away + 100c (JP)",
                  "Slide + 100c No teleporter route (JP)",
                  "Big Penguin Race + 100c (JP)",
                  "Race + 100c atmpas special route (JP)"):
        assert mapping.map_target("4. Cool, Cool Mountain", label) == "star:4:6"
    # ...while the plain row beside them is still the ordinary star.
    assert mapping.map_target("4. Cool, Cool Mountain",
                              "Slip Slidin' Away") == "star:4:0"


def test_an_alternate_strategy_label_does_not_map_to_a_star():
    # Only a target-opening row's label is ever mapped; "Left side strat" is
    # an approach name.
    assert mapping.map_target("1. Bob-omb Battlefield", "Left side strat") is None


def test_secret_stars_are_named_by_their_course():
    assert mapping.map_target("Castle Secret Stars",
                              "Tower of the Wing Cap") == "star:21:0"
    assert mapping.map_target("Castle Secret Stars",
                              "The Secret Aquarium") == "star:24:0"
    assert mapping.map_target("Castle Secret Stars",
                              "The Princess's Secret Slide") == "star:19:0"


def test_bowser_rows_split_three_ways():
    assert mapping.map_target("Bowser Courses",
                              "Bowser in the Dark World Course") == "segment:5"
    assert mapping.map_target("Bowser Courses",
                              "Bowser in the Dark World Battle (JP)") == "segment:8"
    # The sheet says "Red Coins"; our registry names that star "8 Red Coins".
    # Leaving this unmapped dropped every Bowser reds ladder from the rank
    # seed once already (user-reported 2026-07-23).
    assert mapping.map_target("Bowser Courses",
                              "Bowser in the Dark World Red Coins (JP)") == "star:16:0"
    assert mapping.map_target("Bowser Courses",
                              "Bowser in the Sky Battle (120 star file, JP)") == "segment:10"


def test_expected_misses_carry_their_reason():
    assert mapping.map_target(
        "1. Bob-omb Battlefield",
        "BoB RTA (RTA strat, Fadeout, w/ cannon cutscene)") is None
    assert mapping.miss_reason("1. Bob-omb Battlefield",
                               "BoB RTA (RTA strat, Fadeout)") == "stage_rta"


def test_castle_movements_are_recognised_through_their_group():
    # The sheet nests headers: "Castle Movements (Lobby)" then "★ BoB". Only
    # the GROUP says these are movements, so a section-only check calls 113
    # rows unknown.
    assert mapping.miss_reason("★ BoB", "Lobby door (L) - BoB door",
                               group="Castle Movements (Lobby)") == "castle_movement"
    assert mapping.miss_reason("1. Bob-omb Battlefield", "Some New Star") == "unknown"


def test_a_known_non_target_says_why():
    assert mapping.miss_reason("6. Hazy Maze Cave",
                               "Fade of the CotMC entry") == "not_a_target"
    assert all(reason for reason in mapping.KNOWN_NON_TARGETS.values())

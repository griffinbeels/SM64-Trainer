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
    assert A.star_count(0) == 0    # Castle Secret: no named stars


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

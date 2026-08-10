from sm64_events.memory.addresses import COURSE_NAMES, LEVEL_NAMES
from sm64_events.storage.db import EventRow
from sm64_events.tracking.eventlabel import (LABELLABLE_TYPES,
                                             TRIGGER_JOURNAL_TYPES,
                                             label_event, label_level_entry,
                                             level_entry_rows)
from sm64_events.tracking.segments import TRIGGERS

W = "2026-06-11T12:00:00Z"


def jev(id, type, frame, payload=None, session_id=1):
    # local copy of test_segments.py's/test_projection.py's factory
    # (tests/ is not a package, so each test file carries its own).
    return EventRow(id=id, session_id=session_id, seq=id, type=type,
                    frame=frame, wall_time_utc=W, payload=payload or {})


def test_labels_read_like_the_thing_that_happened():
    # NOTE the brief's own sketch expected "...into Castle" here; the real
    # LEVEL_NAMES[6] is "Castle Inside" (addresses.py) — the repo wins.
    assert label_event(jev(1, "level_changed", 0, {"from": 23, "to": 6})) \
        == "Exited Dire, Dire Docks into Castle Inside"
    # NOTE the brief's own sketch paired course_id=15/star_id=1 with
    # course_name="Dire, Dire Docks"/star_name="Board Bowser's Sub" — an
    # internally INCONSISTENT fixture (course 15 is Rainbow Ride; "Board
    # Bowser's Sub" is course 9 star 0). This module derives the sentence
    # from course_id/star_id via the canonical accessors (course_name/
    # star_name), never from the payload's own name strings — see the
    # module docstring's ID-SPACE TRAP section — so the ids below are the
    # ones that actually produce the brief's intended sentence.
    assert label_event(jev(2, "star_collected", 0,
                           {"course_id": 9, "star_id": 0,
                            "course_name": "Dire, Dire Docks",
                            "star_name": "Board Bowser's Sub"})) \
        == "Grabbed Board Bowser's Sub in Dire, Dire Docks"
    assert label_event(jev(3, "warp_entered", 0, {"level": 17, "area": 1})) \
        == "Entered a pipe in Bowser in the Dark World"
    # `level` is part of a real area payload (detectors/area.py names the level
    # AND the settled area) and is what tells a castle room from a course's own
    # subarea -- see _area_changed's docstring.
    assert label_event(jev(4, "area_changed", 0, {"level": 6, "to": 3})) \
        == "Moved into the Basement"
    # Courses have their own areas and nothing names them, so a subarea OUTSIDE
    # the castle names the LEVEL instead of borrowing a castle room's name.
    assert label_event(jev(5, "area_changed", 0, {"level": 22, "to": 2})) \
        == "Moved to another part of Lethal Lava Land"


def test_a_warp_is_called_a_warp_outside_a_bowser_stage():
    """One event type, three things, and calling every one of them a pipe made
    the row he needed unrecognisable -- live report 2026-08-05, reading his own
    Bob-omb Battlefield warp back out of the recorder's list: "it's a warp not
    a pipe... in bowser levels it's a pipe, in every other level it's a warp"."""
    assert label_event(jev(6, "warp_entered", 0, {"level": 9, "area": 1})) \
        == "Entered a warp in Bob-omb Battlefield"


def test_a_course_entrance_is_named_by_where_it_leads():
    """The most specific of the three, and the one a player identifies by its
    DESTINATION rather than by what it looks like: the basement alone hosts
    five entrances, so "a warp in Castle Inside" names none of them."""
    assert label_event(jev(7, "warp_entered", 0,
                           {"level": 6, "area": 3, "to": 23})) \
        == "Touched the Dire, Dire Docks entrance in Castle Inside"


def test_noise_events_are_not_offered_as_boundaries():
    assert label_event(jev(5, "session_started", 0, {})) is None
    # missing `level` entirely -- can't say WHERE, so no label (this is a
    # malformed/legacy-shaped payload, NOT proof that spawned is noise --
    # see test_spawned_labels_when_the_payload_is_real below, and the
    # module docstring's labelling-volume section for why spawned is
    # labelled at all).
    assert label_event(jev(6, "spawned", 0, {"kind": "intro"})) is None
    assert label_event(jev(7, "mario_acted", 0, {})) is None
    assert label_event(jev(8, "rollout", 0, {})) is None
    assert label_event(jev(9, "attempt_completed", 0, {})) is None
    assert label_event(jev(10, "target_changed", 0, {})) is None


def test_spawned_labels_when_the_payload_is_real():
    # spawned is a high-volume type (1,164 / 18,656 in the real journal) but
    # it backs real seeded `spawned` start triggers, so it is NOT excluded
    # wholesale -- only individual malformed rows (above) fail to label.
    assert label_event(jev(11, "spawned", 0, {"level": 16, "kind": "spawn"})) \
        == "Spawned into Castle Grounds"
    assert label_event(jev(12, "spawned", 0, {"level": 16, "kind": "intro"})) \
        == "Started the file in Castle Grounds"


def test_bookkeeping_movement_events_are_not_boundaries():
    # from == to: an establishing/corrective event (detectors/level.py,
    # area.py docstrings) -- bookkeeping, never real movement.
    assert label_event(jev(13, "level_changed", 0, {"from": 9, "to": 9})) is None
    assert label_event(jev(14, "area_changed", 0, {"from": 1, "to": 1})) is None


def test_key_and_reset_labels():
    assert label_event(jev(15, "key_grabbed", 0, {"level": 30, "which": "bitdw"})) \
        == "Grabbed the Bowser 1 key in Bowser 1 Arena"
    assert label_event(jev(16, "key_grabbed", 0, {"level": 34, "which": "grand"})) \
        == "Grabbed the Grand Star in Bowser 3 Arena"
    assert label_event(jev(17, "game_reset", 0, {})) == "Reset the game"
    assert label_event(jev(18, "practice_reset", 0, {"igt_frames_before": 219})) \
        == 'Reset the level after 0\'07"30'
    assert label_event(jev(19, "state_loaded", 0, {"igt_frames_restored": 0})) \
        == 'Loaded a savestate at 0\'00"00'
    # missing igt fields (legacy journal rows) still label -- a reset is
    # itself the recognisable moment even with no duration attached.
    assert label_event(jev(20, "practice_reset", 0, {})) == "Reset the level"
    assert label_event(jev(21, "state_loaded", 0, {})) == "Loaded a savestate"


def test_level_id_and_course_id_are_different_number_spaces():
    """THE TRAP: a level id and a course id are different numbering systems
    that only coincidentally agree at id 15 (Rainbow Ride). id 9 disagrees --
    LEVEL_NAMES[9] is "Bob-omb Battlefield", COURSE_NAMES[9] is "Dire, Dire
    Docks" -- so reading a level_changed payload (which carries LEVEL ids)
    through course naming would produce a plausible WRONG place name here,
    never a crash. Mutation-proved: swapping LEVEL_NAMES for COURSE_NAMES in
    _level_name turns this red (asserted directly below by exercising the
    tables the way a swapped implementation would read them)."""
    assert LEVEL_NAMES[9] != COURSE_NAMES[9]  # the trap actually exists at 9
    assert LEVEL_NAMES[9] == "Bob-omb Battlefield"
    assert COURSE_NAMES[9] == "Dire, Dire Docks"

    label = label_event(jev(22, "level_changed", 0, {"from": 16, "to": 9}))
    assert label == "Exited Castle Grounds into Bob-omb Battlefield"
    # if _level_name read COURSE_NAMES instead of LEVEL_NAMES, this would
    # read "Exited Castle Grounds into Dire, Dire Docks" -- the wrong,
    # plausible-looking place. Pin against the WRONG value too, so this
    # test does not merely assert its own hypothesis:
    wrong_label_a_course_lookup_would_produce = \
        f"Exited Castle Grounds into {COURSE_NAMES[9]}"
    assert label != wrong_label_a_course_lookup_would_produce


def test_every_labellable_type_produces_a_clean_sentence():
    """Every type this module claims to label (LABELLABLE_TYPES) must, given
    a well-formed real-shaped payload, produce a string with no leftover
    None/{}/placeholder -- the leak this whole feature exists to prevent
    from reaching a human-facing picker."""
    rows = [
        jev(30, "level_changed", 0, {"from": 23, "to": 6}),
        jev(31, "area_changed", 0, {"from": 1, "to": 3}),
        jev(32, "star_collected", 0, {"course_id": 9, "star_id": 0}),
        jev(33, "warp_entered", 0, {"level": 17, "area": 1}),
        jev(34, "key_grabbed", 0, {"level": 30, "which": "bitdw"}),
        jev(35, "spawned", 0, {"level": 16, "kind": "spawn"}),
        jev(36, "practice_reset", 0, {"igt_frames_before": 900}),
        jev(37, "state_loaded", 0, {"igt_frames_restored": 450}),
        jev(38, "game_reset", 0, {}),
        jev(39, "moment_reached", 0, {"kind": "door_open", "ordinal": 5,
                                      "level": 4, "area": 1,
                                      "action": 0x00001320}),
    ]
    assert {r.type for r in rows} == set(LABELLABLE_TYPES)
    for row in rows:
        label = label_event(row)
        assert label is not None, row.type
        assert isinstance(label, str) and label.strip() == label
        assert "None" not in label
        assert "{" not in label and "}" not in label


def test_every_trigger_type_has_a_labellable_event_shape():
    """A trigger the timeline cannot produce is a segment the user cannot
    record. TRIGGER_JOURNAL_TYPES maps each TriggerType KEY (segments.py's
    matcher vocabulary: level_enter/level_exit/area_enter/...) to the journal
    event TYPE(S) that can actually fire it (read off each TriggerType's own
    match lambda in TRIGGERS by hand); this test asserts every one of those
    journal types is labellable.

    This is deliberately NOT the brief's literal sketch
    `set(LABELLABLE_TYPES) >= {t.key for t in TRIGGERS.values()}` -- trigger
    KEYS and journal event TYPES are two different vocabularies (the matcher
    vocabulary vs. the wire format) and label_event never dispatches on a
    string like "level_enter" or "reset_game", only on real event types like
    "level_changed" or "game_reset". Comparing them directly would demand
    LABELLABLE_TYPES literally contain trigger keys it does not and never
    will. TRIGGER_JOURNAL_TYPES is the correction, and the `set(...) ==
    set(TRIGGERS)` line keeps IT complete: a new TriggerType with no entry
    there fails this test immediately, same guarantee the brief's sketch
    wanted, checked against the real vocabulary instead of a mismatched one.

    TRIGGER_JOURNAL_TYPES lives in eventlabel.py, not here, ONE DOOR shared
    with test_api.py's default-view sole-route test — a second hand-written
    copy of this exact mapping is how the task-11 revision's wrong
    `attempt_anchor` def-use count first got in (hand-copied from a
    docstring instead of re-derived against the corpus)."""
    assert set(TRIGGER_JOURNAL_TYPES) == set(TRIGGERS)
    for trigger_key, journal_types in TRIGGER_JOURNAL_TYPES.items():
        assert journal_types <= LABELLABLE_TYPES, trigger_key


# -- one row per arrival ------------------------------------------------------

def _entry_fixture():
    """A real course entry, in the shape his own journal has it (ids 2204-2209):
    the level edge, three area edges as the byte settles, the reload's anchor,
    then the spawn. `LEVEL_LOAD_TAIL_FRAMES` is 60 and the load's last edge
    lands well inside it."""
    return [
        jev(1, "level_changed", 1000, {"from": 24, "to": 8}),
        jev(2, "area_changed", 1000, {"level": 8, "from": 1, "to": 1}),
        jev(3, "area_changed", 1010, {"level": 8, "from": 1, "to": 2}),
        jev(4, "area_changed", 1020, {"level": 8, "from": 2, "to": 1}),
        jev(5, "practice_reset", 1030, {}),
        jev(6, "spawned", 1032, {"level": 8, "kind": "spawn"}),
    ]


def test_a_course_entry_draws_one_row_and_it_names_the_place():
    """His report, 2026-08-06: "there are two spawn in events in SSL". They are
    the LOAD walking the area byte, and the row that actually names the arrival
    was the one filtered out of the default view."""
    settled, entry_spawns = level_entry_rows(_entry_fixture())
    assert settled == {2, 3, 4}, "every settling edge belongs to the load"
    assert entry_spawns == {6: 8}
    assert label_level_entry(8) == "Started Shifting Sand Land"


def test_an_entry_with_no_spawn_keeps_its_last_area_edge():
    """Journal ids 2172-2178: he entered a level and walked back out before the
    load ever reached a spawn. Promoting only spawns would delete that arrival
    from the list, so the last settled edge speaks when nothing else can."""
    rows = _entry_fixture()[:4]
    settled, entry_spawns = level_entry_rows(rows)
    assert settled == {2, 3}, "the LAST edge survives to name where he landed"
    assert entry_spawns == {}


def test_a_move_past_the_load_tail_is_the_players_own():
    """The pyramid door. Same event type, outside the measured window, and it
    is the one row on this surface he actually wants to point at."""
    rows = _entry_fixture() + [
        jev(7, "area_changed", 1000 + 61, {"level": 8, "from": 1, "to": 2})]
    settled, _ = level_entry_rows(rows)
    assert 7 not in settled


def test_a_menu_warp_pauses_inside_the_load_and_still_draws_one_row():
    """His report, 2026-08-07: *"the 'moved to another part of lethal lava
    land' at the beginning BEFORE the 'Started Lethal Lava Land' is me warping
    from the previous stage… These are basically garbage events, and reads as
    noise events before the 'start' event."*

    The exact shape of his journal ids 25830-25835 (HMC -> LLL through the
    Usamune menu): the level edge, two area edges on that frame, then the
    load's LAST area edge **4,131 frames later**, with the anchor reporting
    4,187 paused. `LEVEL_LOAD_TAIL_FRAMES` is 60 and was measured on WALKED
    entries, so a raw-frame tail expires mid-load and the no-spawn branch
    promotes one of the load's own edges as the arrival — which is the row he
    called garbage."""
    rows = [
        jev(1, "level_changed", 2688629, {"from": 7, "to": 22}),
        jev(2, "area_changed", 2688629, {"level": 22, "from": 1, "to": 1}),
        jev(3, "area_changed", 2688629, {"level": 22, "from": 1, "to": 2}),
        jev(4, "area_changed", 2692760, {"level": 22, "from": 2, "to": 1}),
        jev(5, "practice_reset", 2692760, {"paused_frames_before": 4187}),
        jev(6, "spawned", 2692760, {"level": 22, "kind": "spawn"}),
    ]
    settled, entry_spawns = level_entry_rows(rows)
    assert settled == {2, 3, 4}, (
        "every edge the LOAD walked belongs to the load, however long he sat "
        "in the menu while it happened")
    assert entry_spawns == {6: 22}, "the spawn is the arrival, and the only one"


def test_a_load_ends_at_its_arrival_so_a_later_reset_is_its_own():
    """The bound on the pause credit, and the measurement that forced it: with
    the load left open, each later pause extended it again and a whole session
    of resets collapsed into one load — SIX arrivals vanished from the two real
    journals, most with no surviving row beside them. A load ends when it
    arrives."""
    rows = [
        jev(1, "level_changed", 1000, {"from": 24, "to": 8}),
        jev(2, "area_changed", 1000, {"level": 8, "from": 1, "to": 1}),
        jev(3, "practice_reset", 1010, {"paused_frames_before": 0}),
        jev(4, "spawned", 1012, {"level": 8, "kind": "spawn"}),
        # A reset minutes later, after a long pause. Its own arrival.
        jev(5, "area_changed", 9000, {"level": 8, "from": 1, "to": 1}),
        jev(6, "practice_reset", 9000, {"paused_frames_before": 900}),
        jev(7, "spawned", 9000, {"level": 8, "kind": "spawn"}),
    ]
    _, entry_spawns = level_entry_rows(rows)
    assert entry_spawns == {4: 8, 7: 8}, (
        "the reset's own spawn is an arrival of its own — swallowing it into "
        "the first load is what deleted six of them")


def test_an_establishing_level_row_opens_no_load():
    """`detectors/level.py` journals `from == to` bookkeeping on every session
    start (his journal ids 2135, 2155, 2167). Reading one as an arrival opens a
    60-frame window over moves the player made himself -- so this fixture is
    the shape that can tell the difference: an establishing row, its own
    establishing area row, and then a real walk deeper in, all inside the tail
    a load would have claimed."""
    rows = [jev(1, "level_changed", 500, {"from": 8, "to": 8}),
            jev(2, "area_changed", 500, {"level": 8, "from": 1, "to": 1}),
            jev(3, "area_changed", 520, {"level": 8, "from": 1, "to": 2}),
            jev(4, "spawned", 530, {"level": 8, "kind": "spawn"})]
    settled, entry_spawns = level_entry_rows(rows)
    assert not settled, "no load happened, so nothing here belongs to one"
    assert not entry_spawns, "a respawn is not an arrival"


def test_a_death_respawn_inside_the_tail_is_not_a_second_arrival():
    """Only the FIRST spawn of a load names it."""
    rows = _entry_fixture() + [
        jev(7, "spawned", 1040, {"level": 8, "kind": "spawn"})]
    _, entry_spawns = level_entry_rows(rows)
    assert list(entry_spawns) == [6]


# -- an arrival is a SPAWN, not a level edge -----------------------------------
# His report, 2026-08-06: he warped from Shifting Sand Land to Shifting Sand
# Land through the Usamune menu and got "Moved to another part of Shifting Sand
# Land" -- *"I would expect it to show me the 'Started Shifting Sand Land'…
# I think when we're spawning, that's the event that should show"*. A same-level
# menu warp moves no level byte, so the old rule never opened a load.
#
# Every fixture below is a shape read off his own journal, named by its ids.

def test_a_menu_warp_to_the_same_level_is_an_arrival():
    """Journal ids 2343-2345: area 2 -> 1, an anchor 80 frames paused, a spawn,
    all on one frame, and not a level edge in sight."""
    rows = [jev(1, "area_changed", 900, {"level": 8, "from": 1, "to": 2}),
            jev(2, "area_changed", 1000, {"level": 8, "from": 2, "to": 1}),
            jev(3, "practice_reset", 1000, {"paused_frames_before": 80}),
            jev(4, "spawned", 1000, {"level": 8, "kind": "spawn"})]
    settled, entry_spawns = level_entry_rows(rows)
    assert entry_spawns == {4: 8}
    assert 2 in settled, "the load's own edge is what the spawn speaks for"
    assert 1 not in settled, "the walk INTO the pyramid was his own move"


def test_walking_back_out_of_a_subarea_is_not_an_arrival():
    """Journal ids 2307-2309 inverted -- the volcano door, 3 frames paused.
    The destination area cannot separate this from a retry (anchors.py records
    the same limit), so the PAUSE streak is what does: a walked load pauses 0-3
    frames and a menu warp 13+. Without this clause every walk out of the
    volcano would announce "Started Lethal Lava Land"."""
    rows = [jev(1, "area_changed", 900, {"level": 22, "from": 1, "to": 2}),
            jev(2, "area_changed", 1000, {"level": 22, "from": 2, "to": 1}),
            jev(3, "practice_reset", 1000, {"paused_frames_before": 3}),
            jev(4, "spawned", 1000, {"level": 22, "kind": "spawn"})]
    settled, entry_spawns = level_entry_rows(rows)
    assert not entry_spawns and not settled


def test_a_reset_in_place_is_an_arrival():
    """Journal ids 2347-2348: nothing moved but the clock, and that IS a start
    -- it is the boundary a subsection would be defined from."""
    rows = [jev(1, "area_changed", 900, {"level": 8, "from": 1, "to": 1}),
            jev(2, "practice_reset", 1000, {"paused_frames_before": 0}),
            jev(3, "spawned", 1000, {"level": 8, "kind": "spawn"})]
    _, entry_spawns = level_entry_rows(rows)
    assert entry_spawns == {3: 8}


def test_a_reset_INSIDE_a_subarea_is_not_an_arrival():
    """Journal ids 2297-2298: he restarted inside the LLL volcano. He did not
    start Lethal Lava Land, and saying so would be the row telling the truth in
    the wrong words -- `COURSE_START_AREA` is the discriminator."""
    rows = [jev(1, "area_changed", 900, {"level": 22, "from": 1, "to": 2}),
            jev(2, "practice_reset", 1000, {"paused_frames_before": 0}),
            jev(3, "spawned", 1000, {"level": 22, "kind": "spawn"})]
    _, entry_spawns = level_entry_rows(rows)
    assert not entry_spawns


def test_a_warp_DEEPER_is_still_a_move_and_not_an_arrival():
    """Journal ids 2340-2342, the pyramid door: a load, a spawn, and the row he
    wants to point at is still "Moved to another part of Shifting Sand Land"."""
    rows = [jev(1, "area_changed", 1000, {"level": 8, "from": 1, "to": 2}),
            jev(2, "practice_reset", 1000, {"paused_frames_before": 3}),
            jev(3, "spawned", 1000, {"level": 8, "kind": "spawn"})]
    settled, entry_spawns = level_entry_rows(rows)
    assert not entry_spawns and not settled


# -- a warp INSIDE a course is not an entrance --------------------------------
# His report, 2026-08-06: *"'Touched the Bob-omb Battlefield entrance in Bob-omb
# Battlefield' is *really* actually a warp. This is warp detection."* Three
# recent rows read `to == level` -- ids 2330/2327 in BoB, 2306/2288 in LLL --
# and every one is an intra-course warp, which never leaves the course.

def test_a_warp_that_lands_in_its_own_level_is_not_an_entrance():
    assert label_event(jev(8, "warp_entered", 0,
                           {"level": 9, "area": 1, "to": 9})) \
        == "Entered a warp in Bob-omb Battlefield"


def test_a_named_warp_reads_as_the_thing_he_named():
    """The landmark work reached moments and never reached warps, so his two
    BoB warps were the same sentence twice and neither could be renamed."""
    row = jev(9, "warp_entered", 0,
              {"level": 9, "area": 1, "to": 9,
               "landmark": {"key": "9:1:aabbccdd:10,0,20",
                            "kind_key": "kind:aabbccdd"}})
    assert label_event(row, {"9:1:aabbccdd:10,0,20": "Cannon Warp"}) \
        == "Entered the Cannon Warp in Bob-omb Battlefield"


def test_a_named_KIND_covers_every_warp_of_that_family_at_once():
    """The catalogue's case convention IS the grammar (corpus_behaviors.py):
    a lowercase kind name is a common noun and gets an article."""
    row = jev(10, "warp_entered", 0,
              {"level": 9, "area": 1, "to": 9,
               "landmark": {"key": "9:1:aabbccdd:10,0,20",
                            "kind_key": "kind:aabbccdd"}})
    assert label_event(row, {"kind:aabbccdd": "hole"}) \
        == "Entered a hole in Bob-omb Battlefield"


def test_an_entrance_out_of_the_level_still_reads_by_its_destination():
    """The regression guard for the clause above: naming a warp must not cost
    the entrance its own, more specific sentence."""
    assert label_event(jev(11, "warp_entered", 0,
                           {"level": 6, "area": 3, "to": 23,
                            "landmark": {"key": "6:3:11:1,2,3"}}),
                       {"6:3:11:1,2,3": "DDD Portal"}) \
        == "Touched the Dire, Dire Docks entrance in Castle Inside"


# -- the two moment kinds added 2026-08-06 ------------------------------------
# *"We don't detect poles / trees when I would expect this to be there"* (a
# screenshot of Mario hugging the BoB tree beside an empty recorder) and *"When
# I grab a bob-omb in a level, I want to be able to detect WHEN i grabbed them.
# The frame I managed to successfully grab them."* Both are one registry row
# plus its action set, which is what the registry exists for.

def test_a_pole_grab_reads_as_one():
    assert label_event(jev(12, "moment_reached", 0,
                           {"kind": "pole_grab", "level": 9, "ordinal": 1})) \
        == "Grab a pole in Bob-omb Battlefield"


def test_a_named_pole_drops_the_generic_noun():
    row = jev(13, "moment_reached", 0,
              {"kind": "pole_grab", "level": 9, "ordinal": 3,
               "landmark": {"key": "9:1:bb:1,2,3"}})
    assert label_event(row, {"9:1:bb:1,2,3": "BoB Tree"}) \
        == "Grab the BoB Tree in Bob-omb Battlefield"


def test_a_pickup_keeps_its_verb_when_the_thing_is_named():
    """"Pick up an object" is the only registry label whose article is "an",
    and missing it reads "Pick up an object the Bob-omb"."""
    row = jev(14, "moment_reached", 0,
              {"kind": "pickup", "level": 9, "ordinal": 1,
               "landmark": {"key": "9:1:cc:4,5,6", "kind_key": "kind:cc"}})
    assert label_event(row, {"9:1:cc:4,5,6": "Bob-omb"}) \
        == "Pick up the Bob-omb in Bob-omb Battlefield"
    assert label_event(row, {"kind:cc": "bob-omb"}) \
        == "Pick up a bob-omb in Bob-omb Battlefield"


def test_a_proper_noun_kind_stands_bare():
    """The other half of the case convention: a capitalized kind name is a
    proper noun and takes NO article — "Pick up Bowser", never "a Bowser"
    (round 8 item 2; the seeded catalogue ships Bowser's tail this way)."""
    row = jev(15, "moment_reached", 0,
              {"kind": "pickup", "level": 33, "ordinal": 1,
               "landmark": {"key": "33:1:dd:0,0,0", "kind_key": "kind:dd"}})
    assert label_event(row, {"kind:dd": "Bowser"}) \
        == "Pick up Bowser in Bowser 2 Arena"


def test_a_kind_named_row_drops_the_ordinal():
    """With every behavior in the ROM named, keeping the ordinal on
    kind-named rows would put two number formats on almost every row — his
    screenshot: "Pick up an object (#5)" beside the repeat counter's "(3)".
    The repeat counter is the one number left; a row naming NOTHING keeps
    the ordinal, where it is still the only discriminator."""
    payload = {"kind": "door_open", "level": 6, "ordinal": 5,
               "landmark": {"key": "6:3:ee:1,2,3", "kind_key": "kind:ee"}}
    named = label_event(jev(16, "moment_reached", 0, payload),
                        {"kind:ee": "door"})
    assert named == "Open a door in Castle Inside"
    unnamed = label_event(jev(17, "moment_reached", 0, payload), {})
    assert "(#5)" in unnamed

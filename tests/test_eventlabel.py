from sm64_events.memory.addresses import COURSE_NAMES, LEVEL_NAMES
from sm64_events.storage.db import EventRow
from sm64_events.tracking.eventlabel import (LABELLABLE_TYPES,
                                             TRIGGER_JOURNAL_TYPES,
                                             label_event)
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
    assert label_event(jev(4, "area_changed", 0, {"to": 3})) \
        == "Moved into the Basement"


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

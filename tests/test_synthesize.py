from sm64_events.memory.addresses import COURSE_NAMES, LEVEL_NAMES
from sm64_events.storage.db import EventRow
from sm64_events.tracking.segments import TRIGGERS
from sm64_events.tracking.synthesize import (_NOT_SYNTHESIZABLE,
                                             _SYNTH_PARAMS, _place_name,
                                             clause_for, suggest_name,
                                             synthesize)

W = "2026-06-11T12:00:00Z"


def jev(id, type, frame, payload=None, session_id=1):
    # local copy of test_eventlabel.py's/test_segments.py's factory (tests/
    # is not a package, so each test file carries its own).
    return EventRow(id=id, session_id=session_id, seq=id, type=type,
                    frame=frame, wall_time_utc=W, payload=payload or {})


# --- the asymmetry (the trap this whole task exists to avoid) -----------

def test_a_level_change_becomes_an_exit_at_the_start_and_an_entry_at_the_end():
    # The SAME event means different things at the two ends, and getting this
    # backwards produces a def whose start and end are satisfied by one event
    # -- the unfireable shape that shipped broken in 2026-07-24.
    ev = jev(1, "level_changed", 0, {"from": 23, "to": 6})
    assert clause_for(ev, "start") == {"type": "level_exit", "from": 23}
    assert clause_for(ev, "end") == {"type": "level_enter", "to": 6}


def test_a_start_exit_is_unpinned_so_it_matches_every_real_exit():
    # 50 of the 51 seeded level_exit clauses omit `to` because every real
    # course exit lands in the castle; pinning it here would make a recorded
    # movement match one landing only.
    assert "to" not in clause_for(jev(1, "level_changed", 0,
                                      {"from": 23, "to": 6}), "start")


def test_an_end_entry_stays_pinned():
    clause = clause_for(jev(2, "level_changed", 0, {"from": 23, "to": 6}),
                        "end")
    assert clause == {"type": "level_enter", "to": 6}


def test_a_bookkeeping_level_change_synthesizes_nothing():
    # from == to is an establishing/corrective event (detectors/level.py),
    # never real movement -- same shape eventlabel.py refuses to label.
    ev = jev(3, "level_changed", 0, {"from": 9, "to": 9})
    assert clause_for(ev, "start") is None
    assert clause_for(ev, "end") is None


# --- star grabs -----------------------------------------------------------

def test_a_star_grab_pins_the_course_and_the_star():
    assert clause_for(jev(1, "star_collected", 0,
                          {"course_id": 15, "star_id": 1}), "end") \
        == {"type": "star_grabbed", "course": 15, "star": 1}


def test_a_star_grab_can_also_be_a_start_clause():
    # No structural rule stops a star_grabbed clause from being a START
    # trigger (a castle secret star opening the way) -- only the seeded
    # corpus's own authoring convention says "start, never end", which this
    # module does not bake in as a restriction.
    assert clause_for(jev(1, "star_collected", 0,
                          {"course_id": 0, "star_id": 3}), "start") \
        == {"type": "star_grabbed", "course": 0, "star": 3}


def test_a_malformed_star_grab_synthesizes_nothing():
    assert clause_for(jev(1, "star_collected", 0, {"course_id": 9}),
                      "end") is None


# --- the single-field types (warp/key/spawn) ------------------------------

def test_warp_key_and_spawn_each_pin_their_level():
    assert clause_for(jev(1, "warp_entered", 0,
                          {"level": 17, "area": 1}), "start") \
        == {"type": "warp_entered", "level": 17}
    assert clause_for(jev(2, "key_grabbed", 0,
                          {"level": 30, "which": "bitdw"}), "end") \
        == {"type": "key_grabbed", "level": 30}
    assert clause_for(jev(3, "spawned", 0,
                          {"level": 16, "kind": "spawn"}), "start") \
        == {"type": "spawned", "level": 16}


def test_a_missing_level_synthesizes_nothing():
    assert clause_for(jev(1, "spawned", 0, {"kind": "intro"}), "start") \
        is None


# --- area_changed ----------------------------------------------------------

def test_an_area_change_pins_level_area_and_source():
    ev = jev(1, "area_changed", 0, {"from": 1, "level": 6, "to": 3})
    assert clause_for(ev, "start") == {"type": "area_enter", "level": 6,
                                       "area": 3, "from": 1}


def test_a_transient_area_source_is_not_pinned():
    # Every castle entry passes through the lobby TRANSIENTLY before settling
    # -- area_enter's own match lambda (segments.py) unconditionally rejects
    # a pinned `from` when the source was transient. Pinning one here would
    # record a clause that can never match the very shape it came from.
    ev = jev(1, "area_changed", 0, {"from": 1, "level": 6, "to": 3,
                                    "from_transient": True})
    assert clause_for(ev, "start") == {"type": "area_enter", "level": 6,
                                       "area": 3}


def test_a_bookkeeping_area_change_synthesizes_nothing():
    ev = jev(1, "area_changed", 0, {"from": 1, "level": 6, "to": 1})
    assert clause_for(ev, "start") is None


# --- reset_game (no params needed at all) ---------------------------------

def test_game_reset_needs_no_payload():
    assert clause_for(jev(1, "game_reset", 0, {}), "start") \
        == {"type": "reset_game"}
    assert clause_for(jev(2, "game_reset", 0, {}), "end") \
        == {"type": "reset_game"}


# --- attempt_anchor: labellable, but NOT synthesizable --------------------

def test_attempt_anchor_cannot_be_synthesized_from_a_bare_row():
    # practice_reset/state_loaded payloads carry no level/course at all --
    # the matcher resolves attempt_anchor's required `level` from LIVE
    # MatchContext, not from the event's own payload (segments.py). A bare
    # row in isolation has no way to recover that, so this returns None for
    # both roles rather than guessing a level.
    assert clause_for(jev(1, "practice_reset", 0,
                          {"igt_frames_before": 100}), "start") is None
    assert clause_for(jev(2, "state_loaded", 0,
                          {"igt_frames_restored": 0}), "end") is None


def test_a_type_this_module_never_heard_of_synthesizes_nothing():
    assert clause_for(jev(1, "mario_acted", 0, {}), "start") is None


# --- registry completeness -------------------------------------------------

def test_every_trigger_type_has_a_synthesis_row_or_is_explicitly_unreachable():
    assert set(_SYNTH_PARAMS) | set(_NOT_SYNTHESIZABLE) == set(TRIGGERS)


# --- refusing an unfireable pair -------------------------------------------

def test_picking_the_same_row_for_both_ends_is_refused_as_unfireable():
    # 7 -> 6 is ONE level_changed event; using it as BOTH ends means the def
    # would arm and close on the identical tick -- segments.py's documented
    # COROLLARY (a def whose start and end are satisfied by the SAME event is
    # unfireable: it arms on the very event that should close it and can
    # never record an attempt). The check reads the rows' own ids -- directly
    # observable ground truth, no world-topology model needed.
    row = jev(1, "level_changed", 0, {"from": 7, "to": 6})
    assert synthesize(row, row) is None


def test_two_different_rows_with_the_same_shape_are_not_refused():
    # Mutation-proof for the check above: it must read ROW IDENTITY, not
    # clause content. Two DIFFERENT journal ids -- two distinct occurrences
    # of "left level 7 into level 6" -- are a normal, fireable pair even
    # though their payloads happen to match; an implementation that compared
    # the built CLAUSES instead of the ids would wrongly refuse this too.
    start_row = jev(10, "level_changed", 0, {"from": 7, "to": 6})
    end_row = jev(11, "level_changed", 500, {"from": 7, "to": 6})
    assert synthesize(start_row, end_row) == (
        {"type": "level_exit", "from": 7},
        {"type": "level_enter", "to": 6})


def test_two_different_events_synthesize_a_normal_pair():
    # The design's working case from the real-journal backtest: exiting
    # somewhere into the castle, then later entering somewhere else -- two
    # SEPARATE level_changed events, not the same-tick shape above.
    start_row = jev(20, "level_changed", 0, {"from": 7, "to": 6})
    end_row = jev(21, "level_changed", 500, {"from": 6, "to": 17})
    assert synthesize(start_row, end_row) == (
        {"type": "level_exit", "from": 7},
        {"type": "level_enter", "to": 17})


def test_synthesize_refuses_when_either_end_cannot_be_built():
    start_row = jev(30, "level_changed", 0, {"from": 7, "to": 6})
    unusable_end = jev(31, "spawned", 500, {"kind": "intro"})  # no level
    assert synthesize(start_row, unusable_end) is None


# --- suggest_name ------------------------------------------------------

def test_the_suggested_name_reads_like_the_movement():
    # NOTE the brief's own sketch expected "DDD -> BitFS", built from an
    # abbreviation table that does not exist anywhere in this codebase
    # (tools/corpus_movements.py's short names are hand-written strings, not
    # derived from an id) and would be an instant second source of truth for
    # place names beside LEVEL_NAMES/COURSE_NAMES. The suggestion only has to
    # be recognisable -- the user renames it -- so this reads the canonical
    # full names instead.
    assert suggest_name({"type": "level_exit", "from": 23},
                        {"type": "level_enter", "to": 19}) \
        == "Dire, Dire Docks → Bowser in the Fire Sea"


def test_suggest_name_covers_a_star_grab_and_a_placeless_clause():
    assert suggest_name({"type": "reset_game"},
                        {"type": "star_grabbed", "course": 9, "star": 0}) \
        == "Reset → Dire, Dire Docks (Board Bowser's Sub)"


def test_suggest_name_falls_back_to_anywhere_for_an_unpinned_clause():
    assert _place_name({"type": "key_grabbed"}) == "Anywhere"


# --- the id-space trap (level ids vs course ids) --------------------------

def test_place_name_reads_a_star_through_course_names_never_level_names():
    """THE TRAP: LEVEL_NAMES[9] is "Bob-omb Battlefield" but COURSE_NAMES[9]
    is "Dire, Dire Docks" -- the two numbering systems only coincidentally
    agree at id 15 (Rainbow Ride), so pin the check where they disagree.
    Mutation-proved: an implementation that read LEVEL_NAMES for a
    star_grabbed clause's `course` would produce this exact WRONG, plausible
    name instead of crashing."""
    assert LEVEL_NAMES[9] != COURSE_NAMES[9]
    name = _place_name({"type": "star_grabbed", "course": 9, "star": 0})
    assert name.startswith("Dire, Dire Docks")
    wrong_name_a_level_lookup_would_produce = LEVEL_NAMES[9]
    assert not name.startswith(wrong_name_a_level_lookup_would_produce)


def test_place_name_reads_a_level_exit_through_level_names_never_course_names():
    """The mirror trap: a level_exit/level_enter clause carries a LEVEL id.
    id 24 disagrees too -- LEVEL_NAMES[24] is "Whomp's Fortress",
    COURSE_NAMES[24] is "The Secret Aquarium"."""
    assert LEVEL_NAMES[24] != COURSE_NAMES[24]
    name = _place_name({"type": "level_exit", "from": 24})
    assert name == "Whomp's Fortress"
    assert name != COURSE_NAMES[24]

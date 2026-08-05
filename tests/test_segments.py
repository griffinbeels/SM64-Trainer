import dataclasses
import re
from dataclasses import replace

import pytest

from sm64_events.memory.addresses import COURSE_NAMES, LEVEL_NAMES
from sm64_events.storage.db import EventRow
from sm64_events.tracking import segments as segments_module
from sm64_events.tracking.segments import (SEGMENT_ATTEMPT_OFFSET,
                                           arms_ambiently,
                                           card_step_labels,
                                           card_waiting_for_sentence,
                                           clause_sentence,
                                           course_groups, level_groups,
                                           GUARDS, TRIGGERS, MatchContext,
                                           hundred_coin_entity,
                                           merge_definitions, SegmentDef,
                                           SegmentEngine, split_definition,
                                           origin_taxonomy, origin_view,
                                           start_origin, validate_definition,
                                           vocab)

W = "2026-06-11T12:00:00Z"


def jev(id, type, frame, payload=None, session_id=1):
    # local copy of test_projection.py's factory (tests/ is not a package)
    return EventRow(id=id, session_id=session_id, seq=id, type=type,
                    frame=frame, wall_time_utc=W, payload=payload or {})


def test_validate_accepts_a_seed_shaped_definition():
    validate_definition({
        "name": "LBLJ",
        "start_triggers": [{"type": "level_enter", "to": 6, "from": 16}],
        "end_triggers": [{"type": "level_enter", "to": 17}],
        "guards": []})  # no raise


def test_validate_rejects_unknown_trigger_type():
    with pytest.raises(ValueError, match="unknown trigger type"):
        validate_definition({"name": "x",
                             "start_triggers": [{"type": "nope"}],
                             "end_triggers": [{"type": "spawned"}],
                             "guards": []})


def test_validate_rejects_missing_required_param():
    with pytest.raises(ValueError, match="level_enter"):
        validate_definition({"name": "x",
                             "start_triggers": [{"type": "level_enter"}],
                             "end_triggers": [{"type": "spawned"}],
                             "guards": []})


def test_vocab_lists_triggers_guards_and_level_enum():
    v = vocab()
    keys = {t["key"] for t in v["triggers"]}
    assert {"level_enter", "level_exit", "area_enter", "warp_entered",
            "key_grabbed", "star_grabbed", "spawned",
            "attempt_anchor"} <= keys
    assert v["levels"]["17"] == "Bowser in the Dark World"
    assert {g["key"] for g in v["guards"]} == {"prev_level", "prev_level_not",
                                               "star_count_min",
                                               "star_count_max",
                                               "min_time", "max_time",
                                               "last_star_grabbed",
                                               "last_star_attempted",
                                               "in_active_route"}


def test_vocab_ships_connections_and_flow_annotations():
    # The builder constrains the level_enter/level_exit dropdowns to world-
    # possible moves (2026-07-23): vocab carries the topology successor map
    # plus per-param `flow` annotations telling the UI which sibling param
    # constrains which ("dest" filters by the source's successors, "source"
    # by the destination's predecessors). UI-only — validation stays
    # permissive (the Usamune warp menu can fabricate any edge).
    v = vocab()
    # LLL reaches the basement by its own door and the lobby by the pause menu
    # (live-verified 2026-08-02; tests/test_topology.py owns that rule).
    assert v["connections"]["22"] == [[6, 1], [6, 3]]
    level_enter = next(t for t in v["triggers"] if t["key"] == "level_enter")
    assert level_enter["params"]["to"]["flow"] == {
        "role": "dest", "peer": "from", "peer_subarea": "from_subarea"}
    assert level_enter["params"]["from"]["flow"] == {
        "role": "source", "peer": "to", "peer_subarea": "to_subarea"}
    assert level_enter["params"]["to_subarea"]["flow"]["role"] == "dest"
    level_exit = next(t for t in v["triggers"] if t["key"] == "level_exit")
    assert level_exit["params"]["from"]["flow"]["role"] == "source"
    assert level_exit["params"]["to"]["flow"]["role"] == "dest"
    assert level_exit["params"]["from_subarea"]["flow"]["role"] == "source"


# ---------------------------------------------------------------------------
# card_waiting_for_sentence (Task 6, spec 2026-07-28-multi-step-segments):
# plain language for what an armed def is waiting for next, read as an
# imperative step for the practice card's "Waiting for" line. Its editor-
# voice twin, waiting_for_sentence, and its four dedicated tests here were
# deleted Task 7 (2026-07-28) once the function lost its last caller in
# `src/` — see segments.py's card_waiting_for_sentence docstring.
# ---------------------------------------------------------------------------

WF_TO_SSL_WAYPOINT = SegmentDef(
    id=21, name="WF -> SSL", enabled=True,
    start_triggers=[{"type": "level_exit", "from": 24}],
    waypoints=[[{"type": "area_enter", "level": 6, "area": 3}]],
    end_triggers=[{"type": "level_enter", "to": 8}], guards=[])

def test_card_waiting_for_sentence_reads_the_next_unconsumed_waypoint():
    sentence = card_waiting_for_sentence(WF_TO_SSL_WAYPOINT, 0)
    assert sentence == "Enter Castle Inside Basement"


def test_card_waiting_for_sentence_falls_back_to_the_end_trigger_once_consumed():
    sentence = card_waiting_for_sentence(WF_TO_SSL_WAYPOINT, 1)
    assert sentence == "Enter Shifting Sand Land"


def test_card_waiting_for_sentence_is_not_the_editors_voice():
    # The bug this function exists for: editor voice shown under a "Waiting
    # for" label reads as broken English ("Waiting for You enter level
    # Shifting Sand Land"). The card sentence must never start with the
    # editor's second-person phrasing.
    card = card_waiting_for_sentence(WF_TO_SSL_WAYPOINT, 1)
    assert card == "Enter Shifting Sand Land"
    assert not card.startswith("You ")


def test_every_trigger_template_resolves_cleanly():
    """Guard for the clause renderer behind card_waiting_for_sentence: every
    TriggerType's card_template (or template, its fallback -- fix round 1,
    2026-07-28: card_template may differ from template), filled with every
    param IT declares, must leave no literal "{token}" behind, and every
    param name the template mentions must be one this trigger actually has
    in its own `params` dict. Fails the day a new trigger type's template
    typos a param name, rather than a user seeing a brace on the practice
    card. (This used to also probe the editor-voice template through
    waiting_for_sentence; deleted Task 7, 2026-07-28, alongside that
    function -- spec.template's own placeholder names are still checked
    below since it is card_template's fallback and so still load-bearing.)"""
    kind_samples = {"level": 6, "subarea": 1, "course": 1, "star": 0}
    for spec in TRIGGERS.values():
        card_named = set(re.findall(r"\{(\w+)\}", spec.card_template or spec.template))
        assert card_named <= set(spec.params), \
            f"{spec.key}: card_template names {card_named - set(spec.params)}, " \
            "which is not one of its own params"
        clause = {"type": spec.key}
        for name, meta in spec.params.items():
            clause[name] = kind_samples[meta["kind"]]
        d = SegmentDef(id=1, name="probe", enabled=True, guards=[],
                       start_triggers=[], end_triggers=[clause])
        card_sentence = card_waiting_for_sentence(d, 0)
        assert "{" not in card_sentence and "}" not in card_sentence, \
            f"{spec.key}: leftover template token in card phrasing {card_sentence!r}"
        assert spec.card_label in card_sentence


def test_every_card_fallback_param_resolves_cleanly_when_unset():
    """The generic loop above always fills EVERY declared param, so it can
    never exercise a card_fallbacks entry -- that only fires when a param is
    LEFT unset. This is the "same probe proving the guard can fail" for
    card_template's one new mechanism: build a clause omitting exactly the
    fallback-bearing params (every other declared param still filled), and
    assert the fallback text appears with no leftover token. Runs for every
    TriggerType that declares a fallback today (just star_grabbed), so a
    future type gets the same coverage for free rather than a bespoke test."""
    kind_samples = {"level": 6, "subarea": 1, "course": 1, "star": 0}
    fallback_specs = [s for s in TRIGGERS.values() if s.card_fallbacks]
    assert fallback_specs, "no TriggerType declares card_fallbacks -- update this probe"
    for spec in fallback_specs:
        clause = {"type": spec.key}
        for name, meta in spec.params.items():
            if name not in spec.card_fallbacks:
                clause[name] = kind_samples[meta["kind"]]
        d = SegmentDef(id=1, name="probe", enabled=True, guards=[],
                       start_triggers=[], end_triggers=[clause])
        card_sentence = card_waiting_for_sentence(d, 0)
        assert "{" not in card_sentence and "}" not in card_sentence, \
            f"{spec.key}: leftover template token with fallback params unset: " \
            f"{card_sentence!r}"
        for name, fallback_text in spec.card_fallbacks.items():
            assert fallback_text in card_sentence, \
                f"{spec.key}: fallback {fallback_text!r} for {name!r} missing " \
                f"from {card_sentence!r}"


def test_star_grabbed_card_phrase_names_the_star_and_course():
    # Fix round 1, 2026-07-28: the shared editor template read as a visible
    # artifact on the card ("Grab the star in Dire, Dire Docks, star Board
    # Bowser's Sub"). course=9/star=0 is Dire, Dire Docks' "Board Bowser's
    # Sub" -- the real names, not synthesized for this test.
    d = SegmentDef(id=1, name="probe", enabled=True, guards=[],
                   start_triggers=[],
                   end_triggers=[{"type": "star_grabbed", "course": 9, "star": 0}])
    assert card_waiting_for_sentence(d, 0) == "Grab Board Bowser's Sub in Dire, Dire Docks"


def test_star_grabbed_card_phrase_falls_back_when_the_star_is_unset():
    # Same course, no specific star: the object of the sentence must not
    # vanish along with the unset param.
    d = SegmentDef(id=1, name="probe", enabled=True, guards=[],
                   start_triggers=[],
                   end_triggers=[{"type": "star_grabbed", "course": 9}])
    assert card_waiting_for_sentence(d, 0) == "Grab a star in Dire, Dire Docks"


def test_card_fallback_is_per_param_not_a_blanket_rule():
    # The mechanism must be selective (only params LISTED in card_fallbacks
    # render unconditionally), not "every unset param on a card renders
    # something" -- course_grabbed's `course` has no fallback entry, so a
    # star-only clause still prunes it the ordinary way, exactly like the
    # editor voice.
    d = SegmentDef(id=1, name="probe", enabled=True, guards=[],
                   start_triggers=[],
                   end_triggers=[{"type": "star_grabbed", "star": 0}])
    sentence = card_waiting_for_sentence(d, 0)
    assert sentence == "Grab Star 1"   # no course -> generic star name, no "in"
    assert "in " not in sentence


# ---------------------------------------------------------------------------
# clause_sentence (Task 13, spec 2026-07-28-multi-step-segments): a public
# entry point onto _render_clause for callers OUTSIDE tracking/ (the
# synthesize-preview API endpoint behind the timeline picker). Same
# card_label/card_template rendering card_waiting_for_sentence uses, so a
# synthesized-but-unsaved clause reads in the identical voice a saved one
# would -- one line, no second template walk.
# ---------------------------------------------------------------------------

def test_clause_sentence_matches_card_waiting_for_sentence_for_the_same_clause():
    # Pin the two against EACH OTHER, not just against a hardcoded string --
    # a future divergence (clause_sentence growing its own branch) shows up
    # here rather than only in a stale literal.
    clause = {"type": "level_exit", "from": 23}
    d = SegmentDef(id=1, name="probe", enabled=True, guards=[],
                   start_triggers=[], end_triggers=[clause])
    assert clause_sentence(clause) == card_waiting_for_sentence(d, 0)


def test_clause_sentence_renders_a_pinned_level_enter():
    assert clause_sentence({"type": "level_enter", "to": 19}) \
        == "Enter Bowser in the Fire Sea"


def test_start_level_set_classifies_level_bound_defs():
    # Segment-target retirement (projection.py) needs "which levels can this
    # segment START from": a set when EVERY start trigger pins a level, None
    # when any trigger is location-free (can start anywhere -> never retire).
    from sm64_events.tracking.segments import start_level_set
    assert start_level_set([{"type": "level_enter", "to": 30},
                            {"type": "attempt_anchor", "level": 30}]) == {30}
    assert start_level_set([{"type": "level_enter", "to": 6},
                            {"type": "star_grabbed"}]) is None
    assert start_level_set([{"type": "level_exit", "from": 8}]) is None  # dest unknown
    assert start_level_set([{"type": "level_exit", "from": 8, "to": 6}]) == {6}
    assert start_level_set([]) is None


def test_start_level_set_unions_waypoint_levels():
    # Fix (whole-branch review 2026-07-24): a multi-level segment's waypoint
    # steps must count toward "levels this segment can occupy", not just its
    # start triggers — otherwise a waypoint re-entering an earlier level (SL
    # -> HMC re-enters SL at 10) reads as "outside the segment" and the
    # projector wrongly retires the practice target mid-sequence.
    from sm64_events.tracking.segments import start_level_set
    assert start_level_set(
        [{"type": "level_exit", "from": 10, "to": 16}],
        [[{"type": "level_enter", "to": 10}],
         [{"type": "level_exit", "from": 10, "to": 16}]]) == {16, 10}
    # A waypoint clause with an unknowable arm level (level_exit with no
    # `to`) makes the whole set unknowable, same as a start trigger would.
    assert start_level_set(
        [{"type": "level_exit", "from": 10, "to": 16}],
        [[{"type": "level_exit", "from": 10}]]) is None
    # Omitting the waypoints argument entirely reproduces today's result —
    # existing callers (and this def's own defaults) are unaffected.
    assert start_level_set(
        [{"type": "level_exit", "from": 10, "to": 16}]) == {16}


def test_string_clause_raises_value_error_not_500():
    with pytest.raises(ValueError, match="must be a dict"):
        validate_definition({"name": "x", "start_triggers": ["level_enter"],
                             "end_triggers": [{"type": "spawned"}], "guards": []})


def test_non_list_guards_raises_value_error():
    with pytest.raises(ValueError, match="guards must be a list"):
        validate_definition({"name": "x",
                             "start_triggers": [{"type": "spawned"}],
                             "end_triggers": [{"type": "spawned"}],
                             "guards": "not a list"})


def test_all_db_seeds_pass_validate_definition(tmp_path):
    """Registry/seed agreement: seeds live as JSON in db.py MIGRATIONS while
    the vocabulary lives here — this is the only gate that catches a rename
    on either side."""
    from sm64_events.storage.db import Database
    db = Database(tmp_path / "t.db")
    defs = db.segment_defs()
    assert len(defs) == 10
    for d in defs:
        # `waypoints` is projected too (fold-in 2026-07-24): without it a
        # malformed seeded waypoint list was invisible to the only gate that
        # checks seeds against the vocabulary.
        validate_definition({k: d[k] for k in
                             ("name", "start_triggers", "end_triggers",
                              "waypoints", "guards")})


# ---------------------------------------------------------------------------
# Task 10: SegmentEngine FSM tests
# ---------------------------------------------------------------------------

LBLJ = SegmentDef(id=1, name="LBLJ", enabled=True,
                  start_triggers=[{"type": "level_enter", "to": 6, "from": 16}],
                  end_triggers=[{"type": "level_enter", "to": 17}], waypoints=[],
                  guards=[])
PIPE = SegmentDef(id=5, name="BitDW Pipe Entry", enabled=True,
                  start_triggers=[{"type": "level_enter", "to": 17},
                                  {"type": "attempt_anchor", "level": 17}],
                  end_triggers=[{"type": "warp_entered", "level": 17}],
                  waypoints=[], guards=[])


def ctx(level=None, prev_level=None, num_stars=None, area=None,
        route_segments=None, target_segment=None):
    return MatchContext(level=level, prev_level=prev_level,
                        num_stars=num_stars, area=area,
                        route_segments=route_segments,
                        target_segment=target_segment)


def lblj_arm(engine, jid=10, frame=1000):
    return engine.feed(jev(jid, "level_changed", frame,
                           {"from": 16, "to": 6}), ctx(level=6, prev_level=16))


def test_arm_then_end_is_a_success_with_rta_delta():
    e = SegmentEngine([LBLJ])
    lblj_arm(e)
    closed, _ = e.feed(jev(11, "level_changed", 1085, {"from": 6, "to": 17}),
                       ctx(level=17, prev_level=6))
    [a] = closed
    assert a.outcome == "success" and a.segment_id == 1
    assert a.rta_frames == 85 and a.igt_frames is None
    assert a.course_id is None and a.star_id is None
    assert a.id == 10 + SEGMENT_ATTEMPT_OFFSET * 1
    assert a.anchor_type == "level_changed"


def test_armed_items_carries_the_live_arm_for_each_armed_id():
    e = SegmentEngine([LBLJ])
    lblj_arm(e, frame=1000)
    items = e.armed_items()
    assert set(items) == {1}
    assert items[1].start_frame == 1000


def test_armed_items_returns_a_copy_not_the_live_dict():
    # Same defensive-copy contract as armed_ids() — a caller mutating what
    # it got back must never reach engine-private state.
    e = SegmentEngine([LBLJ])
    lblj_arm(e, frame=1000)
    items = e.armed_items()
    items.clear()
    assert e.armed_ids() == {1}


def test_definition_returns_the_loaded_def_by_id():
    e = SegmentEngine([LBLJ])
    assert e.definition(1) is LBLJ


def test_definition_returns_none_for_an_unknown_id():
    e = SegmentEngine([LBLJ])
    assert e.definition(999) is None


B3 = SegmentDef(id=10, name="Bowser 3", enabled=True,
                start_triggers=[{"type": "level_enter", "to": 34},
                                {"type": "attempt_anchor", "level": 34}],
                end_triggers=[{"type": "key_grabbed", "level": 34}],
                waypoints=[], guards=[])


def test_grab_close_records_usamune_igt_not_wall_frame_delta():
    # A segment ending on a grab (key_grabbed / star_collected) records the
    # event's authoritative Usamune IGT as its time — the wall-frame delta is
    # one display-tick short and counts paused frames (live report
    # 2026-06-12: Bowser 3 read 0'46"23, Usamune showed 0'46"26).
    e = SegmentEngine([B3])
    e.feed(jev(50, "level_changed", 788707, {"from": 6, "to": 34}),
           ctx(level=34, prev_level=6))
    closed, _ = e.feed(
        jev(51, "key_grabbed", 790094,  # wall delta would be 790094-788707=1387
            {"level": 34, "which": "grand", "igt_frames": 1388,
             "igt": "0'46\"26", "igt_source": "result"}),
        ctx(level=34))
    [a] = closed
    assert a.outcome == "success" and a.segment_id == 10
    assert a.rta_frames == 1388        # Usamune's IGT, not the 1387 wall delta
    assert a.igt_frames is None        # segments stay RTA-only to UI/PB


def test_restart_anchors_rearm_without_recording_a_row():
    e = SegmentEngine([LBLJ])
    lblj_arm(e, jid=10, frame=1000)
    # walk out (silent disarm), walk back in (fresh arm at the new frame)
    closed, _ = e.feed(jev(11, "level_changed", 1200, {"from": 6, "to": 16}),
                       ctx(level=16, prev_level=6))
    assert closed == []
    lblj_arm(e, jid=12, frame=1300)
    closed, _ = e.feed(jev(13, "level_changed", 1390, {"from": 6, "to": 17}),
                       ctx(level=17, prev_level=6))
    assert closed[0].rta_frames == 90


def test_rearm_on_start_refire_restarts_the_timer():
    e = SegmentEngine([PIPE])
    e.feed(jev(20, "level_changed", 2000, {"from": 6, "to": 17}),
           ctx(level=17, prev_level=6))
    e.feed(jev(21, "practice_reset", 2500, {"igt_frames_before": 100}),
           ctx(level=17))                       # closes reset AND re-arms
    closed, _ = e.feed(jev(22, "warp_entered", 2600, {"level": 17, "area": 1,
                                                      "action": 0x1300}),
                       ctx(level=17))
    assert closed[0].rta_frames == 100          # timed from the reset, not entry


def test_practice_reset_closes_as_reset_then_rearms_via_attempt_anchor():
    e = SegmentEngine([PIPE])
    e.feed(jev(30, "level_changed", 3000, {"from": 6, "to": 17}),
           ctx(level=17, prev_level=6))
    closed, _ = e.feed(jev(31, "practice_reset", 3200,
                           {"igt_frames_before": 50}), ctx(level=17))
    [a] = closed
    assert a.outcome == "reset" and a.rta_frames == 200
    assert a.anchor_type == "level_changed"     # the attempt that FAILED was armed by entry


def test_afk_reset_discards_the_row_but_still_rearms():
    e = SegmentEngine([PIPE])
    e.feed(jev(40, "level_changed", 4000, {"from": 6, "to": 17}),
           ctx(level=17, prev_level=6))
    closed, _ = e.feed(jev(41, "practice_reset", 4500,
                           {"paused_frames_before": 200}), ctx(level=17))
    assert closed == []                          # AFK discard
    closed, _ = e.feed(jev(42, "warp_entered", 4600, {"level": 17, "area": 1,
                                                      "action": 0x1300}),
                       ctx(level=17))
    assert closed[0].rta_frames == 100           # re-armed by the reset anyway


def test_death_and_game_reset_close_with_their_outcomes():
    e = SegmentEngine([LBLJ])
    lblj_arm(e)
    closed, _ = e.feed(jev(11, "death", 1050, {"cause": "standing"}),
                       ctx(level=6))
    assert closed[0].outcome == "death"
    assert closed[0].outcome_detail == "standing"
    lblj_arm(e, jid=12, frame=2000)
    closed, _ = e.feed(jev(13, "game_reset", 2100, {}), ctx())
    assert closed[0].outcome == "hard_reset"


def test_foreign_level_change_disarms_silently():
    e = SegmentEngine([LBLJ])
    lblj_arm(e)
    closed, _ = e.feed(jev(11, "level_changed", 1500, {"from": 6, "to": 27}),
                       ctx(level=27, prev_level=6))
    assert closed == []
    closed, _ = e.feed(jev(12, "level_changed", 1600, {"from": 27, "to": 17}),
                       ctx(level=17, prev_level=27))
    assert closed == []                          # was not armed anymore


def test_establishing_level_event_from_equals_to_never_arms():
    e = SegmentEngine([LBLJ])
    closed, _ = e.feed(jev(10, "level_changed", 1000, {"from": 6, "to": 6}),
                       ctx(level=6, prev_level=6))
    assert e.armed_ids() == set()


def test_guards_reevaluate_on_every_arm():
    guarded = SegmentDef(id=2, name="g", enabled=True,
                         start_triggers=[{"type": "level_enter", "to": 6}],
                         end_triggers=[{"type": "level_enter", "to": 17}],
                         waypoints=[],
                         guards=[{"type": "prev_level", "level": 16}])
    e = SegmentEngine([guarded])
    e.feed(jev(10, "level_changed", 1000, {"from": 26, "to": 6}),
           ctx(level=6, prev_level=26))          # guard fails: from courtyard
    assert e.armed_ids() == set()
    e.feed(jev(11, "level_changed", 1100, {"from": 16, "to": 6}),
           ctx(level=6, prev_level=16))
    assert e.armed_ids() == {2}


def test_prev_level_not_guard_blocks_only_the_named_source():
    # Negated companion of prev_level (user request 2026-07-23): "reset for
    # LBLJ, but NOT when I just came out of Bowser in the Dark World".
    g = GUARDS["prev_level_not"]
    assert g.phase == "arm"
    assert g.check({"type": "prev_level_not", "level": 17},
                   ctx(level=6, prev_level=17)) is False
    assert g.check({"type": "prev_level_not", "level": 17},
                   ctx(level=6, prev_level=16)) is True
    # Unknown history PASSES — the guard's job is to block a KNOWN source,
    # and failing closed would leave every session's first arm dead
    # (opposite of prev_level / last_star_*, which fail closed on None).
    assert g.check({"type": "prev_level_not", "level": 17},
                   ctx(level=6, prev_level=None)) is True


def test_prev_level_not_guard_gates_arming():
    guarded = SegmentDef(id=2, name="g", enabled=True,
                         start_triggers=[{"type": "attempt_anchor", "level": 6}],
                         end_triggers=[{"type": "level_enter", "to": 17}],
                         waypoints=[],
                         guards=[{"type": "prev_level_not", "level": 17}])
    e = SegmentEngine([guarded])
    e.feed(jev(10, "practice_reset", 1000, {"level": 6, "mario_acted": True}),
           ctx(level=6, prev_level=17))          # came from BitDW: no arm
    assert e.armed_ids() == set()
    e.feed(jev(11, "practice_reset", 1100, {"level": 6, "mario_acted": True}),
           ctx(level=6, prev_level=16))
    assert e.armed_ids() == {2}


def test_validate_accepts_prev_level_not():
    validate_definition({
        "name": "LBLJ",
        "start_triggers": [{"type": "attempt_anchor", "level": 6}],
        "end_triggers": [{"type": "level_enter", "to": 17}],
        "guards": [{"type": "prev_level_not", "level": 17}]})  # no raise


def test_negative_rta_discards_and_disarms():
    e = SegmentEngine([LBLJ])
    lblj_arm(e, frame=5000)
    closed, _ = e.feed(jev(11, "level_changed", 100, {"from": 6, "to": 17}),
                       ctx(level=17, prev_level=6))
    assert closed == []
    assert e.armed_ids() == set()


def test_armed_disarmed_notices_for_live_broadcast():
    e = SegmentEngine([LBLJ])
    _, notices = lblj_arm(e)
    assert notices == [{"event": "segment_armed", "segment_id": 1,
                        "name": "LBLJ", "frame": 1000}]
    _, notices = e.feed(jev(11, "level_changed", 1500, {"from": 6, "to": 27}),
                        ctx(level=27, prev_level=6))
    assert notices[0]["event"] == "segment_disarmed"


def test_realistic_game_reset_records_hard_reset_with_unknowable_rta():
    # game_reset frames are boot-range (< 120, lifecycle.py) BY DEFINITION,
    # so the delta from any real arm frame is negative — the row must still
    # exist, with the time marked unknowable.
    e = SegmentEngine([LBLJ])
    lblj_arm(e)                                  # armed at frame 1000
    closed, _ = e.feed(jev(11, "game_reset", 50, {}), ctx())
    [a] = closed
    assert a.outcome == "hard_reset"
    assert a.rta_frames is None
    assert e.armed_ids() == set()


def test_session_started_while_armed_disarms_silently():
    e = SegmentEngine([LBLJ])
    lblj_arm(e)
    closed, notices = e.feed(jev(11, "session_started", 0, {}), ctx())
    assert closed == []
    assert e.armed_ids() == set()
    assert notices == [{"event": "segment_disarmed", "segment_id": 1,
                        "name": "LBLJ", "frame": 0}]


def test_state_loaded_closes_as_reset():
    e = SegmentEngine([PIPE])
    e.feed(jev(20, "level_changed", 2000, {"from": 6, "to": 17}),
           ctx(level=17, prev_level=6))
    closed, _ = e.feed(jev(21, "state_loaded", 2300,
                           {"igt_frames_restored": 0}), ctx(level=17))
    [a] = closed
    assert a.outcome == "reset" and a.rta_frames == 300
    assert e.armed_ids() == {5}                  # re-armed via attempt_anchor


def test_two_defs_armed_by_same_event_get_disjoint_ids():
    second = SegmentDef(id=2, name="Second", enabled=True,
                        start_triggers=[{"type": "level_enter", "to": 6,
                                         "from": 16}],
                        end_triggers=[{"type": "level_enter", "to": 17}],
                        waypoints=[], guards=[])
    e = SegmentEngine([LBLJ, second])
    _, notices = lblj_arm(e)
    assert e.armed_ids() == {1, 2}
    assert [n["event"] for n in notices] == ["segment_armed",
                                             "segment_armed"]
    closed, _ = e.feed(jev(11, "level_changed", 1085, {"from": 6, "to": 17}),
                       ctx(level=17, prev_level=6))
    assert len(closed) == 2
    by_def = {a.segment_id: a for a in closed}
    assert (by_def[2].id - by_def[1].id) == SEGMENT_ATTEMPT_OFFSET * (2 - 1)


def test_success_emits_disarmed_notice():
    e = SegmentEngine([LBLJ])
    lblj_arm(e)
    _, notices = e.feed(jev(11, "level_changed", 1085, {"from": 6, "to": 17}),
                        ctx(level=17, prev_level=6))
    assert notices == [{"event": "segment_disarmed", "segment_id": 1,
                        "name": "LBLJ", "frame": 1085}]


def test_afk_constant_matches_projection():
    # the segment-side AFK threshold mirrors the star side — if projection's
    # constant moves, this is the gate that catches the drift
    from sm64_events.tracking.projection import PAUSE_DISCARD_FRAMES
    from sm64_events.tracking.segments import _AFK_PAUSE_FRAMES
    assert _AFK_PAUSE_FRAMES == PAUSE_DISCARD_FRAMES


def test_guard_failing_refire_keeps_original_arm():
    guarded = SegmentDef(id=3, name="g", enabled=True,
                         start_triggers=[{"type": "star_grabbed"}],
                         end_triggers=[{"type": "level_enter", "to": 17}],
                         waypoints=[],
                         guards=[{"type": "star_count_max", "n": 5}])
    e = SegmentEngine([guarded])
    e.feed(jev(30, "star_collected", 3000, {"course_id": 1, "star_id": 1}),
           ctx(level=9, num_stars=5))            # guard passes: armed
    assert e.armed_ids() == {3}
    e.feed(jev(31, "star_collected", 3500, {"course_id": 1, "star_id": 2}),
           ctx(level=9, num_stars=6))            # guard fails: NO re-arm, NO disarm
    assert e.armed_ids() == {3}
    closed, _ = e.feed(jev(32, "level_changed", 4000, {"from": 9, "to": 17}),
                       ctx(level=17, prev_level=9))
    assert closed[0].rta_frames == 1000          # timed from the ORIGINAL arm


# ---------------------------------------------------------------------------
# Load-echo guard (live gate 2026-06-12)
# Usamune resets IGT on every level load, so the anchor detector emits a
# synthetic practice_reset on the SAME global-timer frame as the level entry
# that armed the segment.  A same-frame anchor must be ignored completely.
# ---------------------------------------------------------------------------

def test_load_echo_anchor_does_not_close_a_fresh_arm():
    """Castle-entry LBLJ: practice_reset at frame 1000 == arm frame 1000
    is a load echo and must NOT close or disarm the segment."""
    e = SegmentEngine([LBLJ])
    e.feed(jev(10, "level_changed", 1000, {"from": 16, "to": 6}),
           ctx(level=6, prev_level=16))
    closed, notices = e.feed(jev(11, "practice_reset", 1000,
                                  {"igt_frames_before": 64}), ctx(level=6))
    assert closed == []
    assert e.armed_ids() == {1}
    disarmed = [n for n in notices if n["event"] == "segment_disarmed"]
    assert disarmed == []


def test_lblj_full_walk_with_load_echoes_records_one_clean_success():
    """Full LBLJ walk: castle-entry echo at 1000, BitDW entry echo at 1085.
    Only one closed attempt (success, rta 85); no reset rows."""
    e = SegmentEngine([LBLJ])
    # Castle entry arms LBLJ
    e.feed(jev(10, "level_changed", 1000, {"from": 16, "to": 6}),
           ctx(level=6, prev_level=16))
    # Load echo — same frame as arm — must be ignored
    e.feed(jev(11, "practice_reset", 1000, {"igt_frames_before": 64}),
           ctx(level=6))
    # BitDW entry closes LBLJ with success (end trigger)
    closed1, _ = e.feed(jev(12, "level_changed", 1085, {"from": 6, "to": 17}),
                         ctx(level=17, prev_level=6))
    # BitDW load echo — LBLJ is already disarmed; PIPE not in this engine
    closed2, _ = e.feed(jev(13, "practice_reset", 1085,
                             {"igt_frames_before": 64}), ctx(level=17))
    all_closed = closed1 + closed2
    assert len(all_closed) == 1
    [a] = all_closed
    assert a.outcome == "success" and a.rta_frames == 85


def test_attempt_anchor_segment_load_echo_keeps_armed_without_junk_row():
    """BitDW pipe entry (attempt_anchor segment): level entry at 2000 arms;
    practice_reset at frame 2000 (load echo) must not close it.
    A subsequent warp_entered at 2100 must succeed with rta 100."""
    e = SegmentEngine([PIPE])
    e.feed(jev(20, "level_changed", 2000, {"from": 6, "to": 17}),
           ctx(level=17, prev_level=6))
    # Load echo — same frame
    closed, _ = e.feed(jev(21, "practice_reset", 2000,
                            {"igt_frames_before": 64}), ctx(level=17))
    assert closed == []
    assert e.armed_ids() == {5}
    # Real end trigger
    closed, _ = e.feed(jev(22, "warp_entered", 2100,
                            {"level": 17, "area": 1, "action": 0x1300}),
                        ctx(level=17))
    assert len(closed) == 1
    assert closed[0].outcome == "success" and closed[0].rta_frames == 100


def test_real_reset_frames_later_still_closes():
    """Guard the guard: a practice_reset that lands at a DIFFERENT frame
    from the arm frame is a real player reset and must close the segment."""
    e = SegmentEngine([LBLJ])
    e.feed(jev(10, "level_changed", 1000, {"from": 16, "to": 6}),
           ctx(level=6, prev_level=16))
    closed, _ = e.feed(jev(11, "practice_reset", 1179,
                            {"igt_frames_before": 30}), ctx(level=6))
    assert len(closed) == 1
    assert closed[0].outcome == "reset" and closed[0].rta_frames == 179


# ---------------------------------------------------------------------------
# Save-prompt echo guard (live report 2026-06-12)
# Exiting a course WITH a star pops the "SAVE & CONTINUE?" course-complete
# screen.  Selecting an option reloads and resets Usamune's IGT, firing a
# practice_reset frames later (idle Mario, no position change) that is
# neither co-frame, a door, nor AFK — so it slips through every echo shape
# and wrongly closes the armed segment.  The anchor detector stamps
# save_pending=True when the save menu was seen this anchor period; such an
# anchor is involuntary and must be INVISIBLE to the engine (the user wants
# the segment to run through the save — "INCLUDING the save prompt").
# ---------------------------------------------------------------------------

def test_save_prompt_anchor_is_echo_segment_stays_armed():
    """MIPS Clip arms on the HMC exit (level 7→6, basement).  The save-and-
    continue reload ~169 frames later carries save_pending=True → no row, the
    segment stays armed, and the eventual DDD entry succeeds with rta timed
    from the original HMC exit (proving the timer ran through the save)."""
    mips = SegmentDef(id=2, name="MIPS Clip", enabled=True,
                      start_triggers=[{"type": "level_exit",
                                       "from": 7, "to": 6}],
                      end_triggers=[{"type": "level_enter", "to": 23}],
                      waypoints=[], guards=[])
    e = SegmentEngine([mips])
    # arm on the HMC exit (basement, area 3)
    e.feed(jev(10, "level_changed", 762510, {"from": 7, "to": 6}),
           ctx(level=6, prev_level=7, area=3))
    # co-frame load echo at the exit tick — already ignored
    e.feed(jev(11, "practice_reset", 762510,
               {"igt_frames_before": 727, "paused_frames_before": 3}),
           ctx(level=6, area=3))
    assert e.armed_ids() == {2}
    # save-and-continue reload 169 frames later: idle Mario, same area, the
    # anchor detector flagged the save menu this period → echo, no closure
    closed, _ = e.feed(
        jev(12, "practice_reset", 762679,
            {"igt_frames_before": 158, "paused_frames_before": 0,
             "action": 0x0C400201, "prev_action": 0x0C400201,
             "save_pending": True}),
        ctx(level=6, area=3))
    assert closed == [], "save-prompt reset must not close the segment"
    assert e.armed_ids() == {2}, "segment must remain armed through the save"
    # MIPS clip eventually reaches DDD — success timed from the original arm
    closed, _ = e.feed(jev(13, "level_changed", 769934, {"from": 6, "to": 23}),
                       ctx(level=23, prev_level=6))
    assert len(closed) == 1
    assert closed[0].outcome == "success"
    assert closed[0].rta_frames == 769934 - 762510


def test_reset_without_save_pending_still_closes():
    """The save_pending gate is opt-in: an ordinary player reset (no
    save_pending key, or False) still records its reset row."""
    mips = SegmentDef(id=2, name="MIPS Clip", enabled=True,
                      start_triggers=[{"type": "level_exit",
                                       "from": 7, "to": 6}],
                      end_triggers=[{"type": "level_enter", "to": 23}],
                      waypoints=[], guards=[])
    e = SegmentEngine([mips])
    e.feed(jev(10, "level_changed", 762510, {"from": 7, "to": 6}),
           ctx(level=6, prev_level=7, area=3))
    closed, _ = e.feed(
        jev(12, "practice_reset", 762679,
            {"igt_frames_before": 158, "action": 0x0C400201,
             "save_pending": False}),
        ctx(level=6, area=3))
    assert len(closed) == 1
    assert closed[0].outcome == "reset" and closed[0].rta_frames == 169


# ---------------------------------------------------------------------------
# Cross-area relocation (live report 2026-06-13, supersedes the 2026-06-12
# "stay armed through a cross-area door" behaviour): crossing to a DIFFERENT
# castle area (the lobby<->upstairs star door, a basement door, a warp) means
# Mario left the segment's start position, so it disarms with NO row and ONLY
# the new area's segment is armed. A SAME-area door fires no area_changed and
# still keeps the segment armed (intra-area echo, below).
# ---------------------------------------------------------------------------

def test_cross_area_change_disarms_lobby_segment():
    """LBLJ armed in the lobby (area 1); area_changed 1->3 (crossing to the
    basement) disarms it as a relocation — no reset row — and the co-frame load
    echo changes nothing."""
    e = SegmentEngine([LBLJ])
    e.feed(jev(10, "level_changed", 1000, {"from": 16, "to": 6}),
           ctx(level=6, prev_level=16, area=1))
    e.feed(jev(11, "practice_reset", 1000, {"igt_frames_before": 64}),
           ctx(level=6, area=1))
    assert e.armed_ids() == {1}
    # cross-area change: Mario left the lobby -> relocation disarm, NO row
    closed, _ = e.feed(
        jev(12, "area_changed", 1200, {"level": 6, "from": 1, "to": 3}),
        ctx(level=6, area=3))
    assert closed == [], "relocation records no reset row"
    assert e.armed_ids() == set(), "left the lobby area -> disarmed"
    closed, _ = e.feed(jev(13, "practice_reset", 1200, {}), ctx(level=6, area=3))
    assert closed == [] and e.armed_ids() == set()


def test_real_reset_after_intra_area_door_still_closes():
    """An intra-area door (SAME area, no area_changed) keeps the segment armed
    via the door echo; a real player reset afterward closes it as a reset,
    rta 400 (from the original arm @1000)."""
    e = SegmentEngine([LBLJ])
    e.feed(jev(10, "level_changed", 1000, {"from": 16, "to": 6}),
           ctx(level=6, prev_level=16, area=1))
    e.feed(jev(11, "practice_reset", 1000, {"igt_frames_before": 64}),
           ctx(level=6, area=1))
    # intra-area door echo (door action, NO area change) — ignored, stays armed
    e.feed(jev(12, "practice_reset", 1200, {"action": 0x00001322}),
           ctx(level=6, area=1))
    assert e.armed_ids() == {1}
    # real reset 200 frames later — must close
    closed, _ = e.feed(jev(13, "practice_reset", 1400,
                            {"igt_frames_before": 30}), ctx(level=6, area=1))
    assert len(closed) == 1
    assert closed[0].outcome == "reset" and closed[0].rta_frames == 400


def test_cross_area_warp_swaps_segments_no_double_arm():
    """THE LIVE REPORT (2026-06-13): moving/warping between the lobby and the
    upstairs must leave EXACTLY the destination's segment armed, never both.
    LBLJ arms in the lobby (area 1), BitS Entry upstairs (area 2)."""
    lblj = SegmentDef(id=1, name="LBLJ", enabled=True,
        start_triggers=[{"type": "attempt_anchor", "level": 6, "area": 1}],
        end_triggers=[{"type": "level_enter", "to": 17}], waypoints=[],
        guards=[])
    bits = SegmentDef(id=2, name="BitS", enabled=True,
        start_triggers=[{"type": "area_enter", "level": 6, "area": 2}],
        end_triggers=[{"type": "level_enter", "to": 21}], waypoints=[],
        guards=[])
    e = SegmentEngine([lblj, bits])
    # in the lobby, a reset arms LBLJ
    e.feed(jev(10, "practice_reset", 1000, {"action": 0x0C400201}),
           ctx(level=6, area=1))
    assert e.armed_ids() == {1}
    # cross the star door to the upstairs: area_changed 1->2 + co-frame echo
    e.feed(jev(11, "area_changed", 1100, {"level": 6, "from": 1, "to": 2}),
           ctx(level=6, area=2))
    assert e.armed_ids() == {2}, "LBLJ disarmed, only BitS armed upstairs"
    e.feed(jev(12, "practice_reset", 1100,
               {"igt_frames_before": 0, "mario_acted": True}),
           ctx(level=6, area=2))
    assert e.armed_ids() == {2}, "co-frame echo doesn't re-arm LBLJ"
    # warp back to the lobby: area_changed 2->1 + a menu-warp reset (high pause)
    e.feed(jev(13, "area_changed", 1300, {"level": 6, "from": 2, "to": 1}),
           ctx(level=6, area=1))
    assert e.armed_ids() == set(), "BitS disarmed leaving the upstairs"
    e.feed(jev(14, "practice_reset", 1300,
               {"paused_frames_before": 30, "action": 0x0C400201}),
           ctx(level=6, area=1))
    assert e.armed_ids() == {1}, "menu warp to the lobby arms only LBLJ"


def test_cross_area_warp_into_door_spawn_arms_idle_segment():
    """Warping to the lobby lands Mario in ACT_WARP_DOOR_SPAWN (a door echo),
    but it is a cross-area RELOCATION, so the idle lobby segment still arms
    (live report 2026-06-13: LBLJ never re-armed after warping to the lobby
    because every landing reset was door-echo-suppressed)."""
    lblj = SegmentDef(id=1, name="LBLJ", enabled=True,
        start_triggers=[{"type": "attempt_anchor", "level": 6, "area": 1}],
        end_triggers=[{"type": "level_enter", "to": 17}], waypoints=[],
        guards=[])
    e = SegmentEngine([lblj])
    # warp upstairs -> lobby: area edge 2->1, then a door-spawn landing reset
    e.feed(jev(10, "area_changed", 2000, {"level": 6, "from": 2, "to": 1}),
           ctx(level=6, area=1))
    closed, notices = e.feed(
        jev(11, "practice_reset", 2000,
            {"action": 0x1322, "prev_action": 0x1322, "frames_since_door": 0,
             "paused_frames_before": 67}),
        ctx(level=6, area=1))
    assert e.armed_ids() == {1}, "cross-area warp landing arms the lobby segment"
    assert [n["event"] for n in notices] == ["segment_armed"]


def test_intra_area_door_spawn_echo_does_not_arm_idle_segment():
    """The same door-spawn reset WITHOUT a co-frame area edge is an involuntary
    intra-area door echo — it must NOT arm an idle segment (only a real reset
    or a cross-area relocation does)."""
    lblj = SegmentDef(id=1, name="LBLJ", enabled=True,
        start_triggers=[{"type": "attempt_anchor", "level": 6, "area": 1}],
        end_triggers=[{"type": "level_enter", "to": 17}], waypoints=[],
        guards=[])
    e = SegmentEngine([lblj])
    e.feed(jev(10, "practice_reset", 2000,
               {"action": 0x1322, "prev_action": 0x1322,
                "frames_since_door": 0}),
           ctx(level=6, area=1))
    assert e.armed_ids() == set(), "intra-area door echo must not arm"


# ---------------------------------------------------------------------------
# Intra-area door echo (live gate 2026-06-12, finding 3)
# Same area on both sides of the door → no area_changed → _last_transition_frame
# guard cannot see it.  Classified instead by action in DOOR_ACTIONS.
# ---------------------------------------------------------------------------

def test_intra_area_door_echo_does_not_close():
    """seq 23-31 replay: LBLJ armed at lobby entry (16→6 @92855, co-frame
    load echo already ignored); player crosses the small lobby door toward
    the basement stairs — SAME area on both sides (no area_changed) — and a
    synthetic practice_reset fires @93025 with action=ACT_WARP_DOOR_SPAWN.
    Must not close the segment.  level_changed 6→17 @93100 → success rta 245."""
    e = SegmentEngine([LBLJ])
    # arm via castle entry @92855
    e.feed(jev(10, "level_changed", 92855, {"from": 16, "to": 6}),
           ctx(level=6, prev_level=16))
    # co-frame load echo at arm tick — already ignored by _last_transition_frame
    e.feed(jev(11, "practice_reset", 92855, {"igt_frames_before": 64}),
           ctx(level=6))
    assert e.armed_ids() == {1}
    # intra-area door echo: NO area_changed fired, but igt reset with door action
    closed, _ = e.feed(
        jev(12, "practice_reset", 93025,
            {"igt_frames_before": 128, "action": 0x00001322}),
        ctx(level=6))
    assert closed == [], "intra-area door echo must not close the segment"
    assert e.armed_ids() == {1}, "segment must remain armed after door echo"
    # end trigger fires — success timed from original arm @92855
    closed, _ = e.feed(jev(13, "level_changed", 93100, {"from": 6, "to": 17}),
                       ctx(level=17, prev_level=6))
    assert len(closed) == 1
    assert closed[0].outcome == "success" and closed[0].rta_frames == 245


def test_real_reset_with_gameplay_action_still_closes():
    """A practice_reset whose action is a regular gameplay action (idle =
    L-press default) is a genuine player reset and must close the segment."""
    e = SegmentEngine([LBLJ])
    e.feed(jev(10, "level_changed", 1000, {"from": 16, "to": 6}),
           ctx(level=6, prev_level=16))
    # real L-reset: action is ACT_IDLE (0x0C400201), not a door action
    closed, _ = e.feed(
        jev(11, "practice_reset", 1200, {"action": 0x0C400201}),
        ctx(level=6))
    assert len(closed) == 1
    assert closed[0].outcome == "reset" and closed[0].rta_frames == 200


def test_historical_anchor_without_action_field_closes():
    """Historical journal events have no 'action' key in the payload.
    .get('action') returns None → None not in DOOR_ACTIONS → conservative
    close behaviour (real reset) is preserved for old events."""
    e = SegmentEngine([LBLJ])
    e.feed(jev(10, "level_changed", 1000, {"from": 16, "to": 6}),
           ctx(level=6, prev_level=16))
    # no "action" key — historical event
    closed, _ = e.feed(
        jev(11, "practice_reset", 1200, {"igt_frames_before": 30}),
        ctx(level=6))
    assert len(closed) == 1
    assert closed[0].outcome == "reset" and closed[0].rta_frames == 200


# ---------------------------------------------------------------------------
# prev_action discriminator (live race fix 2026-06-12)
# A Usamune L-reset respawns Mario at the level entrance in
# ACT_WARP_DOOR_SPAWN (0x1322).  If the anchor poll catches the IGT drop one
# tick late, a REAL reset carries the door action as curr.mario_action and
# was incorrectly eaten as a door echo.
#
# Discriminator: a genuine door crossing is ALWAYS preceded by the door open
# animation — prev_action in DOOR_ACTIONS (inputs locked during door anim).
# An L-reset's prev_action is the gameplay action when the reset was pressed.
# ---------------------------------------------------------------------------

def test_lreset_respawning_at_door_still_closes():
    """THE RACE CASE: LBLJ armed @1000; L-reset fires while the poll catches
    Mario already in ACT_WARP_DOOR_SPAWN (0x1322) — curr action is a door
    action but prev was freefall (0x04000440).  Must close as reset rta 200.
    (Red before the fix — this is the live intermittent-miss bug.)"""
    e = SegmentEngine([LBLJ])
    lblj_arm(e)
    closed, _ = e.feed(
        jev(11, "practice_reset", 1200,
            {"action": 0x00001322, "prev_action": 0x04000440}),
        ctx(level=6))
    assert len(closed) == 1
    assert closed[0].outcome == "reset" and closed[0].rta_frames == 200


def test_door_crossing_prev_action_is_echo():
    """A door crossing where prev_action itself is in DOOR_ACTIONS (inputs
    were already locked on the previous poll tick) → genuine door echo, not
    a player reset.  Segment must stay armed."""
    e = SegmentEngine([LBLJ])
    lblj_arm(e)
    closed, _ = e.feed(
        jev(11, "practice_reset", 1200,
            {"action": 0x00001322, "prev_action": 0x00001321}),
        ctx(level=6))
    assert closed == []
    assert e.armed_ids() == {1}


def test_intra_area_door_echo_with_prev_action_stays_echo():
    """Existing intra-area door test shape updated to carry the realistic
    prev_action=0x1321 (door-open anim on the previous tick).  Still green —
    segment must remain armed and succeed at rta 245."""
    e = SegmentEngine([LBLJ])
    e.feed(jev(10, "level_changed", 92855, {"from": 16, "to": 6}),
           ctx(level=6, prev_level=16))
    e.feed(jev(11, "practice_reset", 92855, {"igt_frames_before": 64}),
           ctx(level=6))
    assert e.armed_ids() == {1}
    # intra-area door echo: prev tick was PULLING_DOOR (0x1321) — door anim
    closed, _ = e.feed(
        jev(12, "practice_reset", 93025,
            {"igt_frames_before": 128,
             "action": 0x00001322, "prev_action": 0x00001321}),
        ctx(level=6))
    assert closed == [], "intra-area door echo must not close the segment"
    assert e.armed_ids() == {1}
    closed, _ = e.feed(jev(13, "level_changed", 93100, {"from": 6, "to": 17}),
                       ctx(level=17, prev_level=6))
    assert len(closed) == 1
    assert closed[0].outcome == "success" and closed[0].rta_frames == 245


# ---------------------------------------------------------------------------
# Non-warp door recency echo (live gate 2026-06-12, journal seq 26)
# NON-WARP doors (ACT_PULLING/PUSHING_DOOR 0x1320/0x1321) end the Usamune
# section AFTER the animation: the IGT reset arrives 1-5 frames later when
# Mario is already idle/landing — neither prev_action nor action carries door
# context at that point.  The frames_since_door recency field bridges the gap.
# ---------------------------------------------------------------------------

def test_nonwarp_door_section_reset_is_echo():
    """THE SEQ-26 REGRESSION: LBLJ armed @1000; non-warp door was crossed
    ~1296 (ACT_PUSHING_DOOR 0x0C400201→0x1321); Usamune resets the section
    IGT 4 frames later @1300 when Mario is already in FREEFALL_LAND — no door
    action in prev or curr.  frames_since_door=4 is the recency discriminator.
    Must NOT close the segment (must stay armed).
    (Red before fix — this is the live-gate seq-26 bug.)"""
    e = SegmentEngine([LBLJ])
    lblj_arm(e, jid=10, frame=1000)
    # Non-warp door reset: Mario in FREEFALL_LAND, prev=IDLE, but frames_since_door=4
    closed, _ = e.feed(
        jev(26, "practice_reset", 1300,
            {"igt_frames_before": 296,
             "action": 0x04000440,        # ACT_FREEFALL
             "prev_action": 0x0C400201,   # ACT_IDLE — not in DOOR_ACTIONS
             "frames_since_door": 4}),
        ctx(level=6))
    assert closed == [], "non-warp door section reset must not close the segment"
    assert e.armed_ids() == {1}, "segment must remain armed"


def test_reset_long_after_door_still_closes():
    """Same door crossing but frames_since_door=200 (well outside the echo
    window) → genuine player L-reset → outcome reset, rta 400."""
    e = SegmentEngine([LBLJ])
    lblj_arm(e, jid=10, frame=1000)
    closed, _ = e.feed(
        jev(27, "practice_reset", 1400,
            {"igt_frames_before": 400,
             "action": 0x04000440,
             "prev_action": 0x0C400201,
             "frames_since_door": 200}),
        ctx(level=6))
    assert len(closed) == 1
    assert closed[0].outcome == "reset" and closed[0].rta_frames == 400


def test_historical_anchor_without_frames_since_door_closes():
    """Historical events (no frames_since_door key) keep conservative close
    behaviour — .get() returns None, out-of-window, treated as real reset."""
    e = SegmentEngine([LBLJ])
    lblj_arm(e, jid=10, frame=1000)
    closed, _ = e.feed(
        jev(28, "practice_reset", 1200,
            {"igt_frames_before": 200,
             "action": 0x04000440,
             "prev_action": 0x0C400201}),  # no frames_since_door key
        ctx(level=6))
    assert len(closed) == 1
    assert closed[0].outcome == "reset" and closed[0].rta_frames == 200


@pytest.mark.parametrize("door_action", [
    0x0000132E,  # ACT_UNLOCKING_KEY_DOOR
    0x0000132F,  # ACT_UNLOCKING_STAR_DOOR
    0x00001331,  # ACT_ENTERING_STAR_DOOR
])
def test_star_door_echo_with_prev_action_stays_echo(door_action):
    """THE BITS-ENTRY REGRESSION (live report 2026-06-12): the 30/70-star
    doors and key doors run their own cutscene actions, not PUSH/PULL.  An
    anchor whose prev tick was inside one of those animations is a door echo
    — inputs locked, never a player reset.  Segment must stay armed, no row."""
    e = SegmentEngine([LBLJ])
    lblj_arm(e)
    closed, _ = e.feed(
        jev(11, "practice_reset", 1200,
            {"igt_frames_before": 200,
             "action": 0x04000440,        # already back to gameplay
             "prev_action": door_action}),
        ctx(level=6))
    assert closed == [], "star/key door echo must not close the segment"
    assert e.armed_ids() == {1}, "segment must remain armed"


# ---------------------------------------------------------------------------
# Dialogue/cutscene echo, shape (5) (live journal 2026-06-14, Lakitu Skip)
# A textbox/cutscene engages a time-stop that re-initialises Usamune's overall
# IGT.  On a fresh-file start the intro cutscene ends, control is regained
# (spawned kind="intro"), and ONE frame later Usamune zeroes the overall timer —
# the anchor detector reads that drop as a practice_reset.  It lands a frame
# AFTER the spawn (so it is not co-frame with any transition / arm) and carries
# no door/save context, slipping through every earlier echo shape and closing
# the just-armed Lakitu Skip segment with a bogus ~1-frame "reset" row.
# frames_since_dialog (anchors.py) is the recency discriminator.
# ---------------------------------------------------------------------------

LAKITU = SegmentDef(id=3, name="Lakitu Skip", enabled=True,
                    start_triggers=[{"type": "spawned", "level": 16}],
                    end_triggers=[{"type": "level_enter", "to": 6}],
                    waypoints=[], guards=[])


def test_lakitu_skip_intro_reset_is_echo_segment_stays_armed():
    """THE LAKITU-SKIP REGRESSION (live journal 2026-06-14): spawned(intro,16)
    @1691 arms Lakitu Skip; Usamune re-inits the overall IGT @1692 → a
    practice_reset with frames_since_dialog=1.  It must NOT close the segment;
    the run continues and the eventual castle entry records the real time.
    (Red before fix — the false 0'00\"03 reset the user reported.)"""
    e = SegmentEngine([LAKITU])
    armed = e.feed(jev(108, "spawned", 1691, {"kind": "intro", "level": 16}),
                   ctx(level=16))
    assert e.armed_ids() == {3}, "intro spawn arms Lakitu Skip"
    closed, _ = e.feed(
        jev(111, "practice_reset", 1692,
            {"igt_frames_before": 1481, "mario_acted": True,
             "paused_frames_before": 0, "acted_tracking": True,
             "action": 0x04000440, "prev_action": 0x0C400201,
             "save_pending": False, "frames_since_door": None,
             "frames_since_dialog": 1}),
        ctx(level=16))
    assert closed == [], "intro IGT re-init must not close the segment"
    assert e.armed_ids() == {3}, "segment must remain armed from the spawn"
    # The skip completes by entering the castle — the real, only row.
    done, _ = e.feed(jev(140, "level_changed", 2400, {"from": 16, "to": 6}),
                     ctx(level=6, prev_level=16))
    [a] = done
    assert a.outcome == "success" and a.segment_id == 3
    assert a.rta_frames == 2400 - 1691, "timed from the spawn, not the reset"


def test_dialogue_reset_far_from_textbox_still_closes():
    """The window is opt-in: a real L-reset long after any dialogue
    (frames_since_dialog out of window) still records its reset row."""
    e = SegmentEngine([LAKITU])
    e.feed(jev(108, "spawned", 1691, {"kind": "intro", "level": 16}),
           ctx(level=16))
    closed, _ = e.feed(
        jev(111, "practice_reset", 2000,
            {"igt_frames_before": 309, "mario_acted": True,
             "acted_tracking": True, "action": 0x04000440,
             "prev_action": 0x0C400201, "frames_since_dialog": 309}),
        ctx(level=16))
    assert len(closed) == 1
    assert closed[0].outcome == "reset" and closed[0].rta_frames == 309


def test_historical_anchor_without_frames_since_dialog_closes():
    """Historical events (no frames_since_dialog key) keep conservative close
    behaviour — .get() returns None, out-of-window, treated as a real reset."""
    e = SegmentEngine([LAKITU])
    e.feed(jev(108, "spawned", 1691, {"kind": "intro", "level": 16}),
           ctx(level=16))
    closed, _ = e.feed(
        jev(111, "practice_reset", 1900,
            {"igt_frames_before": 209, "mario_acted": True,
             "acted_tracking": True, "action": 0x04000440,
             "prev_action": 0x0C400201}),  # no frames_since_dialog key
        ctx(level=16))
    assert len(closed) == 1
    assert closed[0].outcome == "reset" and closed[0].rta_frames == 209


# ---------------------------------------------------------------------------
# Anchor closure re-arm (live-gate amendment 2026-06-12)
# A practice_reset/state_loaded that CLOSES an armed segment must also
# RE-ARM the same segment at the anchor frame — the practice-loop
# continuation.  Usamune L-reset respawns Mario at the level's last entrance,
# which is the segment's start position (lobby door for LBLJ, HMC exit for
# MIPS), so timing from the anchor equals a fresh start-trigger arm.
# ---------------------------------------------------------------------------

def test_second_reset_also_records():
    """Live regression (2026-06-12 report: grounds→lobby, reset, reset again —
    second reset recorded nothing, armed chip dark).
    LBLJ armed via level_changed 16→6 @1000 (+ load echo @1000 ignored);
    real reset @1300 → row 1 (reset rta 300), segment still armed;
    real reset @1600 → row 2 (reset rta 300), segment still armed;
    success end @1800 → row 3 (success rta 200, timed from second reset)."""
    e = SegmentEngine([LBLJ])
    # arm
    e.feed(jev(10, "level_changed", 1000, {"from": 16, "to": 6}),
           ctx(level=6, prev_level=16))
    # load echo — ignored
    e.feed(jev(11, "practice_reset", 1000, {"igt_frames_before": 64}),
           ctx(level=6))
    assert e.armed_ids() == {1}

    # first real reset
    closed1, notices1 = e.feed(
        jev(12, "practice_reset", 1300, {"action": 0x0C400201}),
        ctx(level=6))
    assert len(closed1) == 1
    assert closed1[0].outcome == "reset" and closed1[0].rta_frames == 300
    assert e.armed_ids() == {1}, "segment must stay armed after first reset"
    # no armed/disarmed notices: attempt boundary, not a state change
    assert [n["event"] for n in notices1
            if n["event"] in ("segment_armed", "segment_disarmed")] == []

    # second real reset — the live-regression case (was yielding no row)
    closed2, notices2 = e.feed(
        jev(13, "practice_reset", 1600, {"action": 0x0C400201}),
        ctx(level=6))
    assert len(closed2) == 1, "second reset must record a row (was the bug)"
    assert closed2[0].outcome == "reset" and closed2[0].rta_frames == 300
    assert e.armed_ids() == {1}, "segment must stay armed after second reset"
    assert [n["event"] for n in notices2
            if n["event"] in ("segment_armed", "segment_disarmed")] == []

    # success end — timed from the second reset at 1600
    closed3, _ = e.feed(
        jev(14, "level_changed", 1800, {"from": 6, "to": 17}),
        ctx(level=17, prev_level=6))
    assert len(closed3) == 1
    assert closed3[0].outcome == "success" and closed3[0].rta_frames == 200


def test_anchor_continuation_emits_no_notices():
    """The closing anchor (a real practice_reset) must produce zero
    segment_armed / segment_disarmed notices — it is an attempt boundary,
    not a state change."""
    e = SegmentEngine([LBLJ])
    lblj_arm(e, jid=10, frame=1000)
    _, notices = e.feed(
        jev(11, "practice_reset", 1300, {"action": 0x0C400201}),
        ctx(level=6))
    state_notices = [n["event"] for n in notices
                     if n["event"] in ("segment_armed", "segment_disarmed")]
    assert state_notices == []


def test_afk_anchor_rebases_without_row():
    """AFK discard (paused_frames_before >= 150): no row recorded, but the
    segment is re-armed at the AFK anchor frame.  A subsequent end trigger
    times from the AFK anchor, not the original arm."""
    e = SegmentEngine([LBLJ])
    lblj_arm(e, jid=10, frame=1000)
    # AFK anchor at 1500 (200 paused frames) — no row, still armed
    closed_afk, _ = e.feed(
        jev(11, "practice_reset", 1500,
            {"paused_frames_before": 200, "action": 0x0C400201}),
        ctx(level=6))
    assert closed_afk == [], "AFK anchor must not record a row"
    assert e.armed_ids() == {1}, "segment must stay armed after AFK anchor"
    # success end — timed from the AFK anchor at 1500, not the original arm
    closed, _ = e.feed(
        jev(12, "level_changed", 1700, {"from": 6, "to": 17}),
        ctx(level=17, prev_level=6))
    assert len(closed) == 1
    assert closed[0].outcome == "success" and closed[0].rta_frames == 200


# ---------------------------------------------------------------------------
# Area-scoped attempt_anchor (warp-menu arming, live gate 2026-06-12)
# The Usamune warp menu (06 01 00) deposits Mario at the castle lobby
# entrance — equivalent to the grounds→lobby door — emitting only a
# practice_reset (menu pause → warp → IGT reset; NO level edge), so a
# level_enter-only LBLJ never arms.  The anchor gains an optional "area"
# param; area scoping prevents cross-arming (a basement respawn must not
# arm a lobby-anchored segment).
# ---------------------------------------------------------------------------

LBLJ_V5 = SegmentDef(
    id=1, name="LBLJ", enabled=True,
    start_triggers=[{"type": "level_enter", "to": 6, "from": 16},
                    {"type": "attempt_anchor", "level": 6, "area": 1}],
    end_triggers=[{"type": "level_enter", "to": 17}], waypoints=[], guards=[])


def test_area_scoped_anchor_arms_when_tracked_area_matches():
    """Warp-menu deposit: practice_reset with ctx(level=6, area=1) — the
    lobby-scoped anchor must arm LBLJ."""
    e = SegmentEngine([LBLJ_V5])
    closed, notices = e.feed(
        jev(10, "practice_reset", 1000, {"action": 0x0C400201}),
        ctx(level=6, area=1))
    assert closed == []
    assert e.armed_ids() == {1}
    assert [n["event"] for n in notices] == ["segment_armed"]


def test_area_scoped_anchor_does_not_arm_in_other_area():
    """Basement guard: ctx(level=6, area=3) must NOT arm the lobby-anchored
    segment — area scoping prevents cross-arming."""
    e = SegmentEngine([LBLJ_V5])
    e.feed(jev(10, "practice_reset", 1000, {"action": 0x0C400201}),
           ctx(level=6, area=3))
    assert e.armed_ids() == set()


def test_area_scoped_anchor_unknown_area_does_not_arm():
    """Legacy journals (no area events): ctx.area is None — the scoped
    anchor conservatively does not arm."""
    e = SegmentEngine([LBLJ_V5])
    e.feed(jev(10, "practice_reset", 1000, {"action": 0x0C400201}),
           ctx(level=6))
    assert e.armed_ids() == set()


def test_anchor_without_area_param_matches_any_area():
    """Compat: an attempt_anchor WITHOUT the area param (all other seeds)
    keeps matching regardless of ctx.area."""
    e = SegmentEngine([PIPE])
    e.feed(jev(20, "practice_reset", 2000, {"action": 0x0C400201}),
           ctx(level=17, area=2))
    assert e.armed_ids() == {5}


# ---------------------------------------------------------------------------
# Menu-warp pause gate (live-gate amendment 2026-06-12)
# Usamune menu warps (e.g. 06-01-00) cross areas and emit an area_changed
# co-frame with their anchor.  The transition-echo guard would previously
# classify the anchor as a load echo (ev.frame == _last_transition_frame),
# keeping the segment armed with a STALE start_frame — so success rta was
# measured from the original arm minutes earlier.
#
# Discriminator (journal-proven): menu warps pass through the pause menu —
# paused_frames_before 13/18/29/890 observed in live logs.  Walked load
# echoes (level entries, area doors) carry 0-3.  A deliberate menu action
# is never an involuntary load echo.
#
# Fix: the transition-co-frame shape only suppresses if
# paused_frames_before <= _MENU_PAUSE_FRAMES (5).  Above that threshold the
# anchor is REAL → close the stale attempt + re-arm at the warp frame.
# ---------------------------------------------------------------------------

def test_menu_warp_across_areas_rebases_the_attempt():
    """THE REGRESSION: LBLJ armed via level_changed 16→6 @1000 (+co-frame echo
    @1000 paused 3 — stays echo); walked door area_changed 1→3 @1500 + echo
    @1500 paused 2 (still echo, segment stays armed at start_frame 1000);
    then THE MENU WARP: area_changed 3→1 @2000 + practice_reset @2000
    paused_frames_before 18 — co-frame but paused > 5 → REAL anchor → closes
    the stale attempt (reset row, rta 1000) AND re-arms at 2000;
    level_changed 6→17 @2100 → success rta 100 (NOT 1100).

    Red before fix: transition-echo guard eats the warp anchor as a load echo,
    success rta is 1100 (measured from original arm at 1000)."""
    e = SegmentEngine([LBLJ])
    # arm via castle grounds → lobby transition @1000
    e.feed(jev(10, "level_changed", 1000, {"from": 16, "to": 6}),
           ctx(level=6, prev_level=16))
    # co-frame load echo at arm tick (paused 3 — walked entry) — stays echo
    e.feed(jev(11, "practice_reset", 1000, {"paused_frames_before": 3,
                                             "igt_frames_before": 64}),
           ctx(level=6))
    assert e.armed_ids() == {1}
    # walked area door @1500 — echo, segment stays armed at 1000
    e.feed(jev(12, "area_changed", 1500, {"level": 6, "from": 1, "to": 3}),
           ctx(level=6, area=3))
    e.feed(jev(13, "practice_reset", 1500, {"paused_frames_before": 2,
                                             "igt_frames_before": 30}),
           ctx(level=6))
    assert e.armed_ids() == {1}
    # menu warp: area_changed 3→1 @2000 (sets _last_transition_frame=2000)
    e.feed(jev(14, "area_changed", 2000, {"level": 6, "from": 3, "to": 1}),
           ctx(level=6, area=1))
    # anchor @2000 — co-frame, but paused_frames_before 18 > 5 → REAL
    closed, _ = e.feed(jev(15, "practice_reset", 2000, {"paused_frames_before": 18,
                                                          "action": 0x0C400201}),
                       ctx(level=6))
    # must close stale attempt as reset with rta 1000 (2000 - 1000)
    assert len(closed) == 1, f"expected 1 closed attempt, got {len(closed)}"
    assert closed[0].outcome == "reset"
    assert closed[0].rta_frames == 1000
    # segment re-armed at the warp frame 2000
    assert e.armed_ids() == {1}
    assert e._armed[1].start_frame == 2000
    # success times from the warp, not the original arm
    closed2, _ = e.feed(jev(16, "level_changed", 2100, {"from": 6, "to": 17}),
                         ctx(level=17, prev_level=6))
    assert len(closed2) == 1
    assert closed2[0].outcome == "success"
    assert closed2[0].rta_frames == 100


def test_long_menu_warp_rebases_without_row():
    """AFK-length pause during menu warp (paused_frames_before 890 — user
    sat in the menu): no reset row (AFK discard), but segment re-arms at
    the warp frame 2000.  Success times from 2000."""
    e = SegmentEngine([LBLJ])
    lblj_arm(e, jid=10, frame=1000)
    # area_changed sets _last_transition_frame = 2000
    e.feed(jev(11, "area_changed", 2000, {"level": 6, "from": 1, "to": 3}),
           ctx(level=6, area=3))
    # warp anchor co-frame but paused 890 → REAL, AFK → discard (no row)
    closed, _ = e.feed(jev(12, "practice_reset", 2000, {"paused_frames_before": 890,
                                                          "action": 0x0C400201}),
                       ctx(level=6))
    assert closed == [], "AFK-level menu warp must not record a row"
    assert e.armed_ids() == {1}
    assert e._armed[1].start_frame == 2000
    # success times from the warp
    closed2, _ = e.feed(jev(13, "level_changed", 2100, {"from": 6, "to": 17}),
                         ctx(level=17, prev_level=6))
    assert len(closed2) == 1
    assert closed2[0].rta_frames == 100


def test_walked_area_door_with_pause_buffer_stays_echo():
    """Door context (prev_action 0x1321) outranks the pause gate: even with
    paused_frames_before 40 (above _MENU_PAUSE_FRAMES) a door-action anchor
    stays echo.  Segment must remain armed at original start_frame."""
    e = SegmentEngine([LBLJ])
    lblj_arm(e, jid=10, frame=1000)
    # NOT a co-frame reset — different frame, so _last_transition_frame guard
    # is not active.  The intra-area door echo guard (shape c) handles this:
    # prev_action in DOOR_ACTIONS → echo regardless of pause.
    closed, _ = e.feed(
        jev(11, "practice_reset", 1200,
            {"prev_action": 0x1321, "action": 0x00001322,
             "paused_frames_before": 40}),
        ctx(level=6))
    assert closed == [], "door-context anchor must stay echo despite large pause"
    assert e.armed_ids() == {1}
    assert e._armed[1].start_frame == 1000


def test_arm_frame_echo_immune_to_pause():
    """Shape (a) arm-frame echo: co-frame anchor at the same tick as the arm
    must be suppressed UNCONDITIONALLY, even with paused_frames_before 800
    (player was paused on the grounds before entering the lobby).
    No row, stays armed at 3000."""
    e = SegmentEngine([LBLJ])
    e.feed(jev(10, "level_changed", 3000, {"from": 16, "to": 6}),
           ctx(level=6, prev_level=16))
    # co-frame echo at arm tick — large pause, but still a load echo
    closed, _ = e.feed(jev(11, "practice_reset", 3000,
                            {"paused_frames_before": 800,
                             "igt_frames_before": 64}),
                       ctx(level=6))
    assert closed == [], "arm-frame echo must be suppressed regardless of pause"
    assert e.armed_ids() == {1}
    assert e._armed[1].start_frame == 3000


# ---------------------------------------------------------------------------
# Echo anchors are invisible to the ARM phase (live regression 2026-06-12)
# Echo-classified anchors were skipped in the CLOSURE phase but still
# processed by the ARM phase.  Since LBLJ's seeded start triggers include
# attempt_anchor(level=6, area=1), a door's section-reset echo MATCHED it
# and the arm phase REPLACED the _Arm at the door — rebasing
# start_frame/started_utc so the replay (and rta) began at the door instead
# of the segment start.
#
# THE RULE: an echo anchor is involuntary — it must be INVISIBLE to the
# engine entirely: no closure, no continuation re-arm, no arm-phase
# arm/re-arm, for every def.  "Re-arm on start trigger refire" applies to
# player actions only.
# ---------------------------------------------------------------------------

def test_intra_area_door_echo_does_not_rebase_anchor_started_segments():
    """THE REGRESSION: LBLJ_V5 (the seeded shape, with attempt_anchor(6,1));
    arm via level_changed 16→6 @1000 (entry echo anchor @1000 paused 3 —
    invisible); area-1 small door echo anchor @1500 (frames_since_door 4,
    paused 2, gameplay actions) → STILL armed with start 1000 (no row);
    level_changed 6→17 @1800 → success rta 800.
    Red before fix: rta 300 (the echo rebased the arm to the door @1500)."""
    e = SegmentEngine([LBLJ_V5])
    # arm via grounds→lobby entry @1000
    e.feed(jev(10, "level_changed", 1000, {"from": 16, "to": 6}),
           ctx(level=6, prev_level=16, area=1))
    # entry echo anchor @1000 (co-frame, paused 3) — invisible
    e.feed(jev(11, "practice_reset", 1000, {"paused_frames_before": 3,
                                             "igt_frames_before": 64}),
           ctx(level=6, area=1))
    assert e.armed_ids() == {1}
    assert e._armed[1].start_frame == 1000
    # small lobby door (intra-area: NO area_changed): section-reset echo
    # @1500 with gameplay actions and frames_since_door 4 (shape 2b)
    closed, _ = e.feed(
        jev(12, "practice_reset", 1500,
            {"paused_frames_before": 2,
             "frames_since_door": 4,
             "action": 0x04000440,         # ACT_FREEFALL — gameplay
             "prev_action": 0x0C400201}),  # ACT_IDLE — gameplay
        ctx(level=6, area=1))
    assert closed == [], "door echo must not record a row"
    assert e.armed_ids() == {1}
    assert e._armed[1].start_frame == 1000, \
        "echo anchor must not rebase the anchor-started segment (the bug)"
    # success @1800 — rta 800 from the ORIGINAL arm (red-before-fix: 300)
    closed, _ = e.feed(jev(13, "level_changed", 1800, {"from": 6, "to": 17}),
                       ctx(level=17, prev_level=6))
    assert len(closed) == 1
    assert closed[0].outcome == "success" and closed[0].rta_frames == 800


def test_menu_warp_still_rebases_with_anchor_trigger():
    """Guard that the echo hoist didn't break the menu-warp pause gate for
    anchor-started segments: a co-frame anchor with paused 18 (> 5, no door
    context) is REAL → closes the stale attempt (reset rta 1000) AND re-arms
    @2000 (closure-phase continuation; the arm-phase attempt_anchor replace
    stays idempotent for real anchors).  Success @2100 → rta 100."""
    e = SegmentEngine([LBLJ_V5])
    e.feed(jev(10, "level_changed", 1000, {"from": 16, "to": 6}),
           ctx(level=6, prev_level=16, area=1))
    assert e.armed_ids() == {1}
    # menu warp: area_changed 3→1 @2000, then the anchor co-frame paused 18
    e.feed(jev(11, "area_changed", 2000, {"level": 6, "from": 3, "to": 1}),
           ctx(level=6, area=1))
    closed, _ = e.feed(
        jev(12, "practice_reset", 2000,
            {"paused_frames_before": 18, "action": 0x0C400201}),
        ctx(level=6, area=1))
    assert len(closed) == 1
    assert closed[0].outcome == "reset" and closed[0].rta_frames == 1000
    assert e.armed_ids() == {1}
    assert e._armed[1].start_frame == 2000
    closed, _ = e.feed(jev(13, "level_changed", 2100, {"from": 6, "to": 17}),
                       ctx(level=17, prev_level=6))
    assert len(closed) == 1
    assert closed[0].outcome == "success" and closed[0].rta_frames == 100


# ---------------------------------------------------------------------------
# Position-gated anchor closures — segment swap (live report 2026-06-12)
# Each _Arm remembers the MatchContext (level, area) where it armed: the
# segment's start position.  A real anchor AT that position is the practice
# loop (reset row + re-arm in place, unchanged).  A real anchor SOMEWHERE
# ELSE (Usamune menu warp / savestate into another area) is a RELOCATION:
# the player is moving, not practicing — NO reset row, the segment disarms
# (its start conditions no longer hold), and whatever def is anchored at the
# destination arms fresh.  None on either side = unknown (legacy journals)
# → conservative match, the pre-area continuation behavior.
# ---------------------------------------------------------------------------

BITS_ENTRY = SegmentDef(
    id=2, name="BITS Entry", enabled=True,
    start_triggers=[{"type": "attempt_anchor", "level": 6, "area": 2}],
    end_triggers=[{"type": "level_enter", "to": 31}], waypoints=[], guards=[])


def test_menu_warp_to_other_area_swaps_armed_segments():
    """THE LIVE REPORT: LBLJ armed at the lobby; Usamune menu warp to
    Upstairs (area 2).  LBLJ must disarm WITHOUT a reset row (moving ≠
    a failed attempt) and BITS Entry must arm fresh — most recently armed
    segment becomes the only armed one."""
    e = SegmentEngine([LBLJ_V5, BITS_ENTRY])
    # warp-menu deposit at the lobby arms LBLJ via attempt_anchor(6, 1)
    e.feed(jev(10, "practice_reset", 1000, {"action": 0x0C400201}),
           ctx(level=6, area=1))
    assert e.armed_ids() == {1}
    # menu warp upstairs: the area change relocates LBLJ out (no row)...
    closed, notices11 = e.feed(
        jev(11, "area_changed", 2000, {"level": 6, "from": 1, "to": 2}),
        ctx(level=6, area=2))
    assert closed == []
    assert e.armed_ids() == set(), "LBLJ relocated out on the area change"
    assert [(n["event"], n["segment_id"]) for n in notices11] == [
        ("segment_disarmed", 1)]
    # ...and the co-frame warp anchor arms BITS Entry upstairs
    closed, notices12 = e.feed(
        jev(12, "practice_reset", 2000,
            {"paused_frames_before": 18, "action": 0x0C400201}),
        ctx(level=6, area=2))
    assert closed == [], "relocation must not record a reset row"
    assert e.armed_ids() == {2}, "BITS Entry in"
    assert [(n["event"], n["segment_id"]) for n in notices12] == [
        ("segment_armed", 2)]
    # BITS Entry times from the warp frame
    closed, _ = e.feed(jev(13, "level_changed", 2300, {"from": 6, "to": 31}),
                       ctx(level=31, prev_level=6))
    assert len(closed) == 1
    assert closed[0].outcome == "success"
    assert closed[0].segment_id == 2 and closed[0].rta_frames == 300


def test_establishing_area_event_pins_arm_position():
    """level_enter arms while ctx.area is still the PREVIOUS level's area
    (the area detector establishes the new level's area one event later on
    the same tick — main.py order).  The co-frame establishing area_changed
    must pin the arm position: a later same-area L-reset is a retry (row +
    re-arm), a cross-area menu warp is a relocation (no row, disarm)."""
    e = SegmentEngine([LBLJ_V5])
    # entering the castle: ctx.area=2 is the stale pre-entry area
    e.feed(jev(10, "level_changed", 1000, {"from": 16, "to": 6}),
           ctx(level=6, prev_level=16, area=2))
    # co-frame establishing area event: the lobby is area 1
    e.feed(jev(11, "area_changed", 1000, {"level": 6, "from": 2, "to": 1}),
           ctx(level=6, area=1))
    # L-reset at the lobby: same position → practice-loop retry
    closed, _ = e.feed(
        jev(12, "practice_reset", 1500, {"action": 0x0C400201}),
        ctx(level=6, area=1))
    assert len(closed) == 1
    assert closed[0].outcome == "reset" and closed[0].rta_frames == 500
    assert e.armed_ids() == {1}
    # menu warp upstairs: the area change IS the relocation → disarm, NO row
    closed, notices = e.feed(
        jev(13, "area_changed", 2000, {"level": 6, "from": 1, "to": 2}),
        ctx(level=6, area=2))
    assert closed == [], "cross-area warp must not record a reset row"
    assert e.armed_ids() == set()
    assert [n["event"] for n in notices] == ["segment_disarmed"]
    # the co-frame load echo changes nothing (already relocated out)
    closed, _ = e.feed(
        jev(14, "practice_reset", 2000,
            {"paused_frames_before": 18, "action": 0x0C400201}),
        ctx(level=6, area=2))
    assert closed == [] and e.armed_ids() == set()


def test_afk_length_menu_warp_relocation_also_disarms():
    """Relocation does not branch on pause length: an AFK-length menu
    pause (890 frames) warping to another area still disarms with no row."""
    e = SegmentEngine([LBLJ_V5])
    e.feed(jev(10, "practice_reset", 1000, {"action": 0x0C400201}),
           ctx(level=6, area=1))
    e.feed(jev(11, "area_changed", 2000, {"level": 6, "from": 1, "to": 2}),
           ctx(level=6, area=2))
    closed, _ = e.feed(
        jev(12, "practice_reset", 2000,
            {"paused_frames_before": 890, "action": 0x0C400201}),
        ctx(level=6, area=2))
    assert closed == []
    assert e.armed_ids() == set()


# ---------------------------------------------------------------------------
# No-op closures + warp ping-pong (live feedback 2026-06-12)
# A reset/warp where Mario never acted since the last anchor is reset spam,
# not a failed attempt — no row (mirrors the star-side no-op discard,
# acted_tracking-gated so historical journals keep recording).  And warping
# back and forth between two segment starts must always leave EXACTLY the
# destination's segment armed — never both.
# ---------------------------------------------------------------------------

BITS_AREA = SegmentDef(
    id=3, name="BitS Entry (area-armed)", enabled=True,
    start_triggers=[{"type": "area_enter", "level": 6, "area": 2}],
    end_triggers=[{"type": "level_enter", "to": 21}], waypoints=[], guards=[])


def test_unacted_same_position_anchor_discards_the_row():
    """Warp-to-own-start spam without ever moving (acted_tracking True,
    mario_acted False): no reset row, but the arm still rebases to the
    anchor frame (timer restarts at the warp)."""
    e = SegmentEngine([LBLJ_V5])
    e.feed(jev(10, "practice_reset", 1000, {"action": 0x0C400201,
                                            "acted_tracking": True,
                                            "mario_acted": True}),
           ctx(level=6, area=1))
    assert e.armed_ids() == {1}
    closed, _ = e.feed(
        jev(11, "practice_reset", 1400,
            {"paused_frames_before": 20, "action": 0x0C400201,
             "acted_tracking": True, "mario_acted": False}),
        ctx(level=6, area=1))
    assert closed == [], "no-op reset must not record a row"
    assert e.armed_ids() == {1}
    assert e._armed[1].start_frame == 1400


def test_acted_same_position_anchor_still_records():
    """Companion: with mario_acted True the same anchor records normally."""
    e = SegmentEngine([LBLJ_V5])
    e.feed(jev(10, "practice_reset", 1000, {"action": 0x0C400201}),
           ctx(level=6, area=1))
    closed, _ = e.feed(
        jev(11, "practice_reset", 1400,
            {"paused_frames_before": 20, "action": 0x0C400201,
             "acted_tracking": True, "mario_acted": True}),
        ctx(level=6, area=1))
    assert len(closed) == 1
    assert closed[0].outcome == "reset" and closed[0].rta_frames == 400


def test_warp_ping_pong_never_double_arms():
    """THE LIVE REPORT: back-and-forth lobby<->upstairs menu warps without
    moving.  After EVERY warp exactly the destination's segment is armed
    (never both), and zero rows are recorded.  Uses the production def
    shapes: LBLJ arms via attempt_anchor(6,1), BitS Entry arms via
    area_enter(6,2) — whose arm-frame co-frame anchor is a shape-(1) echo."""
    e = SegmentEngine([LBLJ_V5, BITS_AREA])
    rows = []

    def warp(jid, frame, to_area):
        frm = 2 if to_area == 1 else 1
        closed, _ = e.feed(jev(jid, "area_changed", frame,
                               {"level": 6, "from": frm, "to": to_area}),
                           ctx(level=6, area=to_area))
        rows.extend(closed)
        closed, _ = e.feed(jev(jid + 1, "practice_reset", frame,
                               {"paused_frames_before": 18,
                                "action": 0x0C400201,
                                "acted_tracking": True,
                                "mario_acted": False}),
                           ctx(level=6, area=to_area))
        rows.extend(closed)

    # warp-menu deposit at the lobby arms LBLJ
    e.feed(jev(10, "practice_reset", 1000, {"action": 0x0C400201}),
           ctx(level=6, area=1))
    assert e.armed_ids() == {1}
    for i, (frame, area) in enumerate([(2000, 2), (3000, 1), (4000, 2),
                                       (5000, 1), (6000, 2), (7000, 1)]):
        warp(20 + 2 * i, frame, area)
        expect = {3} if area == 2 else {1}
        assert e.armed_ids() == expect, \
            f"after warp #{i + 1} to area {area}: {e.armed_ids()}"
    assert rows == [], "no-move warps must record zero rows"


# ---------------------------------------------------------------------------
# Registry templates (vocab contract): every trigger/guard carries a sentence
# template whose placeholders must match its params exactly — a typo or
# duplicate must fail CI, not render a broken builder row.
# ---------------------------------------------------------------------------

def test_every_trigger_and_guard_template_matches_its_params():
    """A template typo must fail CI, not render a broken builder row. A
    zero-param entry (e.g. in_active_route) has nothing to interpolate, so
    only params-bearing entries are held to the non-empty rule."""
    for reg in (TRIGGERS, GUARDS):
        for t in reg.values():
            if t.params:
                assert t.template.strip(), f"{t.key}: empty template"
            found = re.findall(r"\{(\w+)\}", t.template)
            assert len(found) == len(set(found)), (
                f"{t.key}: duplicated placeholder in template")
            placeholders = set(found)
            assert placeholders == set(t.params), (
                f"{t.key}: template placeholders {placeholders}"
                f" != params {set(t.params)}")


def test_vocab_serializes_templates():
    v = vocab()
    by_key = {t["key"]: t for t in v["triggers"]}
    assert by_key["level_enter"]["template"] == (
        "{to} {to_subarea} coming from {from} {from_subarea}")
    assert by_key["attempt_anchor"]["label"] == (
        "Practice reset / savestate load")
    assert all("template" in t for t in v["triggers"] + v["guards"])


def test_vocab_course_and_star_enums():
    v = vocab()
    assert v["courses"]["2"] == "Whomp's Fortress"
    assert v["stars"]["2"][2] == "Shoot into the Wild Blue"
    assert v["stars"]["1"][6] == "100 Coins"    # main courses: 100-coin star at star_id 6
    assert len(v["stars"]["1"]) == 7
    assert v["stars"]["16"] == ["8 Red Coins"]  # Bowser course: one star
    # Castle Secret stars are selectable in the builder (spec 2026-07-24): the
    # route corpus references the Toad/MIPS stars as ordinary star candidates,
    # so a trigger can be scoped to one too.
    assert v["stars"]["0"] == ["Toad Star (Basement)", "Toad Star (Upstairs)",
                               "Toad Star (Tippy)", "MIPS 1st Star",
                               "MIPS 2nd Star"]


# ---------------------------------------------------------------------------
# Castle subarea scoping (spec 2026-06-12, live-corrected 2026-06-13).
# level_enter / level_exit gain a conditional subarea on EACH side (shown only
# when that side is Castle Inside, level 6). Lobby=1, Upstairs=2, Basement=3.
#
# SOURCE subarea (from_subarea) reads from_area off the level edge — Mario was
# settled in that area before leaving, so it is reliable.
#
# DESTINATION subarea (to_subarea) CANNOT be read off the edge: the castle
# loads area 1 (lobby) first, then warps Mario to the real area a poll later
# on the same game frame (live journal 2026-06-13). So the engine DEFERS a
# destination-subarea trigger into _pending, tracks the settling co-frame
# area_changed, and arms once the frame advances iff the SETTLED area matches.
#
# area_enter restricts its region to the castle hubs {6,16,26} with an optional
# subarea (unchanged area_changed semantics).
# ---------------------------------------------------------------------------


def _seg(**triggers):
    return SegmentDef(id=1, name="x", enabled=True,
                      end_triggers=[{"type": "spawned"}], waypoints=[],
                      guards=[], **triggers)


def test_level_exit_to_subarea_arms_when_destination_area_settles():
    # THE LIVE REPORT (2026-06-13): "exit HMC into Basement" never matched
    # because to_area read the transient lobby (1) on the level edge. It must
    # arm when the co-frame area settles into the basement (3) — promptly, on
    # the real-edge lobby->basement warp.
    e = SegmentEngine([_seg(start_triggers=[
        {"type": "level_exit", "from": 7, "to": 6, "to_subarea": 3}])])
    e.feed(jev(10, "level_changed", 1000, {"from": 7, "to": 6, "from_area": 1}),
           ctx(level=6))
    assert e.armed_ids() == set(), "deferred: destination not settled yet"
    e.feed(jev(11, "area_changed", 1000, {"level": 6, "from": 1, "to": 1}),
           ctx(level=6, area=1))            # transient lobby (establishing)
    assert e.armed_ids() == set(), "transient lobby — still deferred"
    e.feed(jev(12, "area_changed", 1000, {"level": 6, "from": 1, "to": 3}),
           ctx(level=6, area=3))            # real-edge settle into the basement
    assert e.armed_ids() == {1}, "prompt arm on the definitive settle"


def test_level_exit_to_subarea_does_not_arm_when_settling_elsewhere():
    # same basement trigger, but the entry settles in the lobby (no warp to 3)
    e = SegmentEngine([_seg(start_triggers=[
        {"type": "level_exit", "from": 7, "to": 6, "to_subarea": 3}])])
    e.feed(jev(10, "level_changed", 1000, {"from": 7, "to": 6, "from_area": 1}),
           ctx(level=6))
    e.feed(jev(11, "area_changed", 1000, {"level": 6, "from": 1, "to": 1}),
           ctx(level=6, area=1))            # stays in the lobby
    e.feed(jev(20, "area_changed", 1100, {"level": 6, "from": 1, "to": 1}),
           ctx(level=6, area=1))            # frame advances -> drop
    assert e.armed_ids() == set()


def test_level_enter_to_subarea_lobby_arms_promptly_on_entry():
    # lobby destination: area is 1 throughout, so the only co-frame event is the
    # establishing 1->1. It must arm ON ENTRY (live report 2026-06-13: LBLJ's
    # grounds->lobby armed too late — only when the player left — and whiffed).
    e = SegmentEngine([_seg(start_triggers=[
        {"type": "level_enter", "to": 6, "to_subarea": 1, "from": 16}])])
    e.feed(jev(10, "level_changed", 1000, {"from": 16, "to": 6, "from_area": 1}),
           ctx(level=6))
    assert e.armed_ids() == set(), "deferred until the area settles"
    e.feed(jev(11, "area_changed", 1000, {"level": 6, "from": 1, "to": 1}),
           ctx(level=6, area=1))            # establishing lobby settle
    assert e.armed_ids() == {1}, "armed on entry, not at a later event"


def test_lobby_subarea_retracts_when_entry_settles_to_basement():
    # a Lobby destination (no source filter) provisionally arms on the transient
    # lobby load, then RETRACTS the instant the entry settles into the basement.
    e = SegmentEngine([_seg(start_triggers=[
        {"type": "level_enter", "to": 6, "to_subarea": 1}])])
    e.feed(jev(10, "level_changed", 1000, {"from": 7, "to": 6, "from_area": 1}),
           ctx(level=6))
    e.feed(jev(11, "area_changed", 1000, {"level": 6, "from": 1, "to": 1}),
           ctx(level=6, area=1))            # transient lobby -> provisional arm
    assert e.armed_ids() == {1}
    e.feed(jev(12, "area_changed", 1000, {"level": 6, "from": 1, "to": 3}),
           ctx(level=6, area=3))            # settles basement -> retract
    assert e.armed_ids() == set()


def test_level_enter_from_subarea_scopes_the_source_area():
    # "enter BitDW coming from Castle Inside upstairs" — source area off the
    # level edge (reliable; arms immediately, no deferral).
    e = SegmentEngine([_seg(start_triggers=[
        {"type": "level_enter", "to": 17, "from": 6, "from_subarea": 2}])])
    e.feed(jev(10, "level_changed", 1000, {"from": 6, "to": 17, "from_area": 1}),
           ctx(level=17))
    assert e.armed_ids() == set(), "left the lobby, not the upstairs"
    e.feed(jev(11, "level_changed", 2000, {"from": 6, "to": 17, "from_area": 2}),
           ctx(level=17))
    assert e.armed_ids() == {1}


def test_level_exit_from_subarea_scopes_the_left_castle_area():
    e = SegmentEngine([_seg(start_triggers=[
        {"type": "level_exit", "from": 6, "from_subarea": 3}])])
    e.feed(jev(10, "level_changed", 1000, {"from": 6, "to": 7, "from_area": 3}),
           ctx(level=7))
    assert e.armed_ids() == {1}


def test_to_subarea_trigger_without_area_events_never_arms():
    # legacy journal: no area_changed follows the level edge -> the destination
    # subarea can't be confirmed -> the deferred entry is dropped, never arms.
    e = SegmentEngine([_seg(start_triggers=[
        {"type": "level_enter", "to": 6, "to_subarea": 3}])])
    e.feed(jev(10, "level_changed", 1000, {"from": 7, "to": 6}), ctx(level=6))
    e.feed(jev(20, "level_changed", 2000, {"from": 6, "to": 7}), ctx(level=7))
    assert e.armed_ids() == set()


def test_deferred_destination_subarea_segment_completes_with_entry_start():
    # The resolved arm behaves like any other for end-matching, and its
    # start_frame is the level ENTRY frame (not the resolve frame) so timing is
    # measured from the crossing.
    e = SegmentEngine([SegmentDef(
        id=1, name="MIPS Clip", enabled=True,
        start_triggers=[{"type": "level_exit", "from": 7, "to": 6,
                         "to_subarea": 3}],
        end_triggers=[{"type": "level_enter", "to": 23}], waypoints=[],
        guards=[])])
    e.feed(jev(10, "level_changed", 1000, {"from": 7, "to": 6, "from_area": 1}),
           ctx(level=6))
    e.feed(jev(11, "area_changed", 1000, {"level": 6, "from": 1, "to": 1}),
           ctx(level=6, area=1))
    e.feed(jev(12, "area_changed", 1000, {"level": 6, "from": 1, "to": 3}),
           ctx(level=6, area=3))
    e.feed(jev(13, "area_changed", 1100, {"level": 6, "from": 3, "to": 3}),
           ctx(level=6, area=3))            # resolve -> armed
    assert e.armed_ids() == {1}
    closed, _ = e.feed(jev(20, "level_changed", 1500,
                           {"from": 6, "to": 23, "from_area": 3}),
                       ctx(level=23))
    assert len(closed) == 1 and closed[0].outcome == "success"
    assert closed[0].rta_frames == 500, "measured from the entry frame (1000)"


def test_area_enter_without_subarea_matches_any_area_in_region():
    # request 3: "enter area Castle Grounds" — region-only, no subarea.
    e = SegmentEngine([_seg(start_triggers=[
        {"type": "area_enter", "level": 16}])])
    e.feed(jev(10, "area_changed", 1000, {"level": 16, "from": 0, "to": 1}),
           ctx(level=16, area=1))
    assert e.armed_ids() == {1}


def test_area_enter_with_subarea_still_scopes_to_that_area():
    e = SegmentEngine([_seg(start_triggers=[
        {"type": "area_enter", "level": 6, "area": 3}])])
    e.feed(jev(10, "area_changed", 1000, {"level": 6, "from": 1, "to": 2}),
           ctx(level=6, area=2))
    assert e.armed_ids() == set(), "entered upstairs, not the basement"
    e.feed(jev(11, "area_changed", 2000, {"level": 6, "from": 2, "to": 3}),
           ctx(level=6, area=3))
    assert e.armed_ids() == {1}


def test_area_enter_coming_from_matches_a_settled_lobby_walk():
    # THE USER SCENARIO (live report 2026-07-23): "enter the basement coming
    # from the lobby" — arm only on a genuine walk through the basement door.
    e = SegmentEngine([_seg(start_triggers=[
        {"type": "area_enter", "level": 6, "area": 3, "from": 1}])])
    e.feed(jev(10, "area_changed", 2000,
               {"level": 6, "from": 1, "to": 3, "from_transient": False}),
           ctx(level=6, area=3))
    assert e.armed_ids() == {1}


def test_area_enter_coming_from_ignores_the_transient_course_exit_lobby():
    # Exiting a basement course re-enters via the TRANSIENT lobby: the castle
    # loads area 1 then warps to 3 on the same frame (detectors/level.py), so
    # the settle event reads from=1 — payload-identical to a real lobby walk.
    # from_transient (detectors/area.py) is the discriminator.
    e = SegmentEngine([_seg(start_triggers=[
        {"type": "area_enter", "level": 6, "area": 3, "from": 1}])])
    e.feed(jev(10, "area_changed", 2000,
               {"level": 6, "from": 1, "to": 3, "from_transient": True}),
           ctx(level=6, area=3))
    assert e.armed_ids() == set(), "course exit must not arm a lobby-walk def"


def test_area_enter_coming_from_scopes_the_source_area():
    e = SegmentEngine([_seg(start_triggers=[
        {"type": "area_enter", "level": 6, "area": 3, "from": 1}])])
    e.feed(jev(10, "area_changed", 2000,
               {"level": 6, "from": 2, "to": 3, "from_transient": False}),
           ctx(level=6, area=3))
    assert e.armed_ids() == set(), "came from the upstairs, not the lobby"


def test_area_enter_coming_from_is_conservative_on_legacy_events():
    # journal events recorded before from_transient existed: .get() -> False
    # -> match (None = unknown -> conservative match, codebase convention)
    e = SegmentEngine([_seg(start_triggers=[
        {"type": "area_enter", "level": 6, "area": 3, "from": 1}])])
    e.feed(jev(10, "area_changed", 2000, {"level": 6, "from": 1, "to": 3}),
           ctx(level=6, area=3))
    assert e.armed_ids() == {1}


def test_area_enter_without_from_still_matches_transient_entries():
    # backward compat: a def NOT scoped by source must keep arming on course
    # exits (the pre-'coming from' behaviour).
    e = SegmentEngine([_seg(start_triggers=[
        {"type": "area_enter", "level": 6, "area": 3}])])
    e.feed(jev(10, "area_changed", 2000,
               {"level": 6, "from": 1, "to": 3, "from_transient": True}),
           ctx(level=6, area=3))
    assert e.armed_ids() == {1}


def test_validate_rejects_level_enter_within_the_same_level():
    # The builder let "enter Castle Inside coming from Castle Inside" be
    # saved, but a within-level move never fires level_changed (live report
    # 2026-07-23) — reject loudly and point at the trigger that CAN say it.
    with pytest.raises(ValueError, match="enter area"):
        validate_definition({
            "name": "x",
            "start_triggers": [{"type": "level_enter", "to": 6, "from": 6}],
            "end_triggers": [{"type": "spawned"}], "guards": []})


def test_validate_rejects_level_exit_within_the_same_level():
    with pytest.raises(ValueError, match="enter area"):
        validate_definition({
            "name": "x",
            "start_triggers": [{"type": "level_exit", "from": 6, "to": 6}],
            "end_triggers": [{"type": "spawned"}], "guards": []})


def test_validate_rejects_area_enter_from_equal_to_area():
    with pytest.raises(ValueError, match="area_enter"):
        validate_definition({
            "name": "x",
            "start_triggers": [{"type": "area_enter", "level": 6,
                                "area": 3, "from": 3}],
            "end_triggers": [{"type": "spawned"}], "guards": []})


def test_validate_accepts_subarea_and_optional_area_params():
    validate_definition({
        "name": "x",
        "start_triggers": [
            {"type": "level_enter", "to": 6, "to_subarea": 1},
            {"type": "level_exit", "from": 6, "from_subarea": 2},
            {"type": "area_enter", "level": 16}],
        "end_triggers": [{"type": "spawned"}], "guards": []})  # no raise


def test_validate_rejects_area_enter_without_region():
    with pytest.raises(ValueError, match="area_enter"):
        validate_definition({"name": "x",
                             "start_triggers": [{"type": "area_enter"}],
                             "end_triggers": [{"type": "spawned"}],
                             "guards": []})


def test_vocab_exposes_region_enum_and_conditional_subareas():
    by_key = {t["key"]: t for t in vocab()["triggers"]}
    ae = by_key["area_enter"]["params"]
    assert ae["level"]["enum"] == [6, 16, 26]
    assert ae["area"]["required"] is False
    assert ae["area"]["only_when"] == {"param": "level", "equals": 6}
    assert ae["from"]["required"] is False
    assert ae["from"]["only_when"] == {"param": "level", "equals": 6}
    le = by_key["level_enter"]["params"]
    assert le["to_subarea"]["only_when"] == {"param": "to", "equals": 6}
    assert le["from_subarea"]["only_when"] == {"param": "from", "equals": 6}
    lx = by_key["level_exit"]["params"]
    assert lx["from_subarea"]["only_when"] == {"param": "from", "equals": 6}
    assert lx["to_subarea"]["only_when"] == {"param": "to", "equals": 6}


def test_reset_game_trigger_matches_game_reset():
    from sm64_events.tracking.segments import TRIGGERS, MatchContext
    t = TRIGGERS["reset_game"]
    class E:  # minimal event
        type = "game_reset"; payload = {}
    assert t.match({"type": "reset_game"}, E(), MatchContext(level=6, prev_level=6, num_stars=0))
    class E2:
        type = "level_changed"; payload = {"from": 1, "to": 2}
    assert not t.match({"type": "reset_game"}, E2(), MatchContext(level=2, prev_level=1, num_stars=0))


def test_vocab_includes_reset_game():
    from sm64_events.tracking.segments import vocab
    assert any(t["key"] == "reset_game" for t in vocab()["triggers"])


def test_time_guards_validate_and_ship_phase_in_vocab():
    validate_definition({
        "name": "WF->Basement",
        "start_triggers": [{"type": "spawned"}],
        "end_triggers": [{"type": "warp_entered", "level": 16}],
        "guards": [{"type": "min_time", "frames": 180},
                   {"type": "max_time", "frames": 600}]})  # no raise
    v = vocab()
    by_key = {g["key"]: g for g in v["guards"]}
    assert by_key["min_time"]["phase"] == "close"
    assert by_key["max_time"]["phase"] == "close"
    assert by_key["prev_level"]["phase"] == "arm"
    assert by_key["min_time"]["params"]["frames"]["kind"] == "seconds"


def test_min_time_requires_frames_param():
    with pytest.raises(ValueError, match="min_time"):
        validate_definition({
            "name": "x",
            "start_triggers": [{"type": "spawned"}],
            "end_triggers": [{"type": "spawned"}],
            "guards": [{"type": "min_time"}]})


def test_max_time_must_exceed_min_time():
    with pytest.raises(ValueError, match="max_time must exceed min_time"):
        validate_definition({
            "name": "x",
            "start_triggers": [{"type": "spawned"}],
            "end_triggers": [{"type": "spawned"}],
            "guards": [{"type": "min_time", "frames": 300},
                       {"type": "max_time", "frames": 150}]})


def test_max_time_rejects_non_positive_frames():
    with pytest.raises(ValueError, match="max_time frames"):
        validate_definition({
            "name": "x",
            "start_triggers": [{"type": "spawned"}],
            "end_triggers": [{"type": "spawned"}],
            "guards": [{"type": "max_time", "frames": -30}]})


def test_min_time_zero_alone_is_valid():
    validate_definition({
        "name": "x",
        "start_triggers": [{"type": "spawned"}],
        "end_triggers": [{"type": "spawned"}],
        "guards": [{"type": "min_time", "frames": 0}]})  # no raise


def test_close_phase_guards_do_not_gate_arming():
    eng = SegmentEngine([SegmentDef(
        id=1, name="s", enabled=True,
        start_triggers=[{"type": "spawned"}],
        end_triggers=[{"type": "warp_entered", "level": 16}],
        waypoints=[],
        guards=[{"type": "min_time", "frames": 180}])])
    eng.feed(jev(1, "spawned", 1000, {"level": 16}),
             MatchContext(level=16, prev_level=None, num_stars=None))
    assert eng.armed_ids() == {1}


def test_time_bounds_reads_guard_rows():
    from sm64_events.tracking.segments import time_bounds
    assert time_bounds([]) == (None, None)
    assert time_bounds([{"type": "min_time", "frames": 180}]) == (180, None)
    assert time_bounds([{"type": "min_time", "frames": 0},
                        {"type": "max_time", "frames": 600},
                        {"type": "prev_level", "level": 16}]) == (0, 600)


def _ctx_ls(grabbed=None, attempted=None):
    return MatchContext(level=16, prev_level=None, num_stars=None,
                        last_star_grabbed=grabbed,
                        last_star_attempted=attempted)


def test_last_star_guards_match_course_and_optional_star():
    g = GUARDS["last_star_grabbed"]
    assert g.phase == "arm"
    assert g.check({"type": "last_star_grabbed", "course": 6},
                   _ctx_ls(grabbed=(6, 0))) is True
    assert g.check({"type": "last_star_grabbed", "course": 6, "star": 3},
                   _ctx_ls(grabbed=(6, 0))) is False
    assert g.check({"type": "last_star_grabbed", "course": 6, "star": 3},
                   _ctx_ls(grabbed=(6, 3))) is True
    # unknown history conservatively FAILS (mirrors star_count_min)
    assert g.check({"type": "last_star_grabbed", "course": 6},
                   _ctx_ls()) is False


def test_last_star_attempted_reads_its_own_field():
    g = GUARDS["last_star_attempted"]
    assert g.check({"type": "last_star_attempted", "course": 6},
                   _ctx_ls(grabbed=(6, 0))) is False   # grab != attempt field
    assert g.check({"type": "last_star_attempted", "course": 6},
                   _ctx_ls(attempted=(6, 4))) is True


def test_last_star_guards_validate_and_appear_in_vocab():
    validate_definition({
        "name": "after WFRR",
        "start_triggers": [{"type": "spawned"}],
        "end_triggers": [{"type": "spawned"}],
        "guards": [{"type": "last_star_grabbed", "course": 6, "star": 4},
                   {"type": "last_star_attempted", "course": 6}]})  # no raise
    by_key = {g["key"]: g for g in vocab()["guards"]}
    assert by_key["last_star_grabbed"]["params"]["course"]["kind"] == "course"
    assert by_key["last_star_grabbed"]["params"]["star"]["required"] is False


# ---------------------------------------------------------------------------
# Task 2: SegmentDef.waypoints, MatchContext scope fields, in_active_route
# guard (spec 2026-07-23-default-routes-foundation)
# ---------------------------------------------------------------------------

def test_segmentdef_defaults_empty_waypoints():
    d = SegmentDef(id=1, name="x", enabled=True,
                   start_triggers=[{"type": "spawned", "level": 16}],
                   end_triggers=[{"type": "level_enter", "to": 6}], guards=[])
    assert d.waypoints == []


def test_matchcontext_defaults_route_fields_none():
    ctx = MatchContext(level=6, prev_level=16, num_stars=0)
    assert ctx.route_segments is None and ctx.target_segment is None


def test_in_active_route_guard_registered_and_validates():
    assert "in_active_route" in GUARDS
    validate_definition({"name": "m",
        "start_triggers": [{"type": "level_exit", "from": 10}],
        "end_triggers": [{"type": "level_enter", "to": 7}],
        "waypoints": [[{"type": "level_enter", "to": 10}]],
        "guards": [{"type": "in_active_route"}]})   # must not raise


def test_vocab_exposes_in_active_route():
    assert any(g["key"] == "in_active_route" for g in vocab()["guards"])


# ---------------------------------------------------------------------------
# Task 3: waypoint sequence matcher (spec 2026-07-23-default-routes-foundation)
# A waypoint-bearing def (SegmentDef.waypoints non-empty) is an ORDERED
# sequence of middle steps between its start and end triggers — e.g. an SL
# clip that exits to Castle Grounds, walks back into SL, exits again, then
# finally enters HMC. Levels: 10 = Snowman's Land, 16 = Castle Grounds,
# 7 = Hazy Maze Cave, 8 = Shifting Sand Land (addresses.py LEVEL_NAMES).
#
# The start trigger AND the def's own second waypoint are both "exit SL to
# Castle Grounds" (from=10, to=16) — a deliberately realistic shape, since a
# route often re-visits its own start condition mid-sequence. The `to`
# constraint on both is load-bearing: without it, a wrong-destination exit
# (e.g. to SSL) would ALSO satisfy the start clause and re-arm instead of
# cancelling (see test_waypoint_cancel_on_wrong_level).
# ---------------------------------------------------------------------------

def _sl_hmc_def():
    return SegmentDef(
        id=99, name="SL->HMC", enabled=True,
        start_triggers=[{"type": "level_exit", "from": 10, "to": 16}],
        end_triggers=[{"type": "level_enter", "to": 7}],
        guards=[],
        waypoints=[[{"type": "level_enter", "to": 10}],
                   [{"type": "level_exit", "from": 10, "to": 16}]])


def test_waypoint_sequence_spans_reentry_single_success():
    """exit SL -> enter SL -> exit SL -> enter HMC == ONE success row, no
    reset/cancel rows anywhere along the way."""
    e = SegmentEngine([_sl_hmc_def()])
    e.feed(jev(10, "level_changed", 1000, {"from": 10, "to": 16}),
           ctx(level=16, prev_level=10))                        # arm: exit SL
    assert e.armed_ids() == {99}
    closed1, _ = e.feed(jev(11, "level_changed", 1100, {"from": 16, "to": 10}),
                       ctx(level=10, prev_level=16))            # waypoint 1
    closed2, _ = e.feed(jev(12, "level_changed", 1200, {"from": 10, "to": 16}),
                       ctx(level=16, prev_level=10))            # waypoint 2
    closed3, _ = e.feed(jev(13, "level_changed", 1300, {"from": 16, "to": 7}),
                       ctx(level=7, prev_level=16))             # end: enter HMC
    assert closed1 == [] and closed2 == []
    assert len(closed3) == 1
    assert closed3[0].outcome == "success" and closed3[0].segment_id == 99
    assert closed3[0].rta_frames == 300


def test_waypoint_cancel_on_midsequence_star_is_silent():
    """A star grab mid-sequence is a task switch, not a route step — silent
    cancel: no row, and the def is no longer armed."""
    e = SegmentEngine([_sl_hmc_def()])
    e.feed(jev(10, "level_changed", 1000, {"from": 10, "to": 16}),
           ctx(level=16, prev_level=10))                        # arm
    e.feed(jev(11, "level_changed", 1100, {"from": 16, "to": 10}),
           ctx(level=10, prev_level=16))                        # waypoint 1
    assert e.armed_ids() == {99}
    closed, _ = e.feed(jev(12, "star_collected", 1150,
                           {"course_id": 10, "star_id": 0, "num_stars": 1}),
                       ctx(level=10, num_stars=1))
    assert closed == []                       # silent abandon: no row
    assert 99 not in e.armed_ids()            # disarmed


def test_waypoint_cancel_on_wrong_level():
    """Exiting SL to the wrong level (SSL, not Castle Grounds) is a misroute:
    it doesn't match the next waypoint, so it falls through to the major-
    action cancel — no row, disarmed (not a re-arm, even though a looser
    start-trigger match would otherwise re-fire here)."""
    e = SegmentEngine([_sl_hmc_def()])
    e.feed(jev(10, "level_changed", 1000, {"from": 10, "to": 16}),
           ctx(level=16, prev_level=10))                        # arm
    e.feed(jev(11, "level_changed", 1100, {"from": 16, "to": 10}),
           ctx(level=10, prev_level=16))                        # waypoint 1
    assert e.armed_ids() == {99}
    closed, _ = e.feed(jev(12, "level_changed", 1200, {"from": 10, "to": 8}),
                       ctx(level=8, prev_level=10))   # exit to SSL, not HMC
    assert closed == []
    assert 99 not in e.armed_ids()


def test_waypoint_death_still_fatal():
    """Death always closes the attempt, regardless of how far the waypoint
    sequence has progressed."""
    e = SegmentEngine([_sl_hmc_def()])
    e.feed(jev(10, "level_changed", 1000, {"from": 10, "to": 16}),
           ctx(level=16, prev_level=10))                        # arm
    closed, _ = e.feed(jev(11, "death", 1050, {"cause": "quicksand"}),
                       ctx(level=16))
    assert len(closed) == 1 and closed[0].outcome == "death"


def test_waypoint_anchor_rewinds_progress_and_records_a_reset_row():
    """A real anchor (not an echo) mid-attempt rewinds `progress` to 0 and
    re-arms IN PLACE at the anchor frame — the practice-retry loop — AND now
    records a RESET row for the attempt that ends there, exactly like the
    plain chain's own anchor-refire reset (round 2, live report 2026-07-30:
    this branch used to record NO row at all — a documented, deliberate,
    but explicitly-unverified gap ("precise relocation-vs-continuation
    nuance is a live-gate VERIFY item") — so the practice log silently
    omitted every retry of a Pipe-mode Bowser run; the user has since
    settled it: he expects the row). The eventual completion still times
    from the anchor, not the original arm — the rewind-in-place relocation
    itself is unchanged, only the missing row is fixed."""
    e = SegmentEngine([_sl_hmc_def()])
    e.feed(jev(10, "level_changed", 1000, {"from": 10, "to": 16}),
           ctx(level=16, prev_level=10))                        # arm
    assert e.armed_ids() == {99}
    closed, _ = e.feed(
        jev(11, "practice_reset", 1300, {"action": 0x0C400201}),
        ctx(level=16))
    assert len(closed) == 1
    assert closed[0].outcome == "reset" and closed[0].segment_id == 99
    assert closed[0].rta_frames == 300, "timed from the original arm to the rewind"
    assert e.armed_ids() == {99}
    assert e._armed[99].progress == 0
    assert e._armed[99].start_frame == 1300
    # replay the sequence from the rewound anchor
    closed, _ = e.feed(jev(12, "level_changed", 1400, {"from": 16, "to": 10}),
                       ctx(level=10, prev_level=16))             # waypoint 1
    assert closed == []
    closed, _ = e.feed(jev(13, "level_changed", 1500, {"from": 10, "to": 16}),
                       ctx(level=16, prev_level=10))             # waypoint 2
    assert closed == []
    closed, _ = e.feed(jev(14, "level_changed", 1600, {"from": 16, "to": 7}),
                       ctx(level=7, prev_level=16))              # end
    assert len(closed) == 1
    assert closed[0].outcome == "success"
    assert closed[0].rta_frames == 300, "timed from the rewind, not the arm"


def test_waypoint_afk_anchor_rebases_without_row():
    """AFK discard (paused_frames_before >= 150) applies to the waypoint
    matcher's own reset row exactly as it does to the plain chain's
    (test_afk_anchor_rebases_without_row) -- a long menu pause immediately
    before the anchor means the player went AFK, not that they retried, so
    no row even though the rewind-in-place still happens."""
    e = SegmentEngine([_sl_hmc_def()])
    e.feed(jev(10, "level_changed", 1000, {"from": 10, "to": 16}),
           ctx(level=16, prev_level=10))                        # arm
    closed_afk, _ = e.feed(
        jev(11, "practice_reset", 1500,
            {"paused_frames_before": 200, "action": 0x0C400201}),
        ctx(level=16))
    assert closed_afk == [], "AFK anchor must not record a row"
    assert e.armed_ids() == {99}, "segment must stay armed after AFK anchor"
    assert e._armed[99].progress == 0 and e._armed[99].start_frame == 1500
    # replay from the AFK anchor -- confirms the rewind itself still happened
    closed, _ = e.feed(jev(12, "level_changed", 1600, {"from": 16, "to": 10}),
                       ctx(level=10, prev_level=16))             # waypoint 1
    closed, _ = e.feed(jev(13, "level_changed", 1700, {"from": 10, "to": 16}),
                       ctx(level=16, prev_level=10))             # waypoint 2
    closed, _ = e.feed(jev(14, "level_changed", 1800, {"from": 16, "to": 7}),
                       ctx(level=7, prev_level=16))              # end
    assert len(closed) == 1
    assert closed[0].outcome == "success" and closed[0].rta_frames == 300


def test_waypoint_session_started_disarms_silently():
    """A session boundary (session_started) disarms an armed waypoint
    segment mid-sequence, exactly like the plain chain's session_started
    handling: no attempt row, but a segment_disarmed notice IS emitted."""
    e = SegmentEngine([_sl_hmc_def()])
    e.feed(jev(10, "level_changed", 1000, {"from": 10, "to": 16}),
           ctx(level=16, prev_level=10))                        # arm
    e.feed(jev(11, "level_changed", 1100, {"from": 16, "to": 10}),
           ctx(level=10, prev_level=16))                        # waypoint 1
    assert e.armed_ids() == {99}
    closed, notices = e.feed(jev(12, "session_started", 0, {}), ctx())
    assert closed == []
    assert e.armed_ids() == set()
    assert notices == [{"event": "segment_disarmed", "segment_id": 99,
                        "name": "SL->HMC", "frame": 0}]


# ---------------------------------------------------------------------------
# Task 4 (spec 2026-07-23-default-routes-foundation): route-scoped arming
# ---------------------------------------------------------------------------

def _guarded_move():
    return SegmentDef(id=42, name="CCM->BitDW", enabled=True,
                      start_triggers=[{"type": "level_exit", "from": 5}],
                      end_triggers=[{"type": "level_enter", "to": 17}],
                      waypoints=[], guards=[{"type": "in_active_route"}])


def test_guarded_def_arms_with_no_route_because_no_route_means_no_filter():
    """REVERSED 2026-08-02 by his own ruling. This test asserted the opposite
    ("does not arm without route") from 2026-07-23 until a live report found
    what it really cost: with the header scope on Overall there is no active
    route, so all 56 castle movements were unpracticable and no surface said
    why. *"if we're in 'Overall' mode, I would expect to see EVERY SINGLE
    OPTION enabled. That is, I can practice ANYTHING."*"""
    e = SegmentEngine([_guarded_move()])
    e.feed(jev(10, "level_changed", 1000, {"from": 5, "to": 16}),  # exit CCM
           ctx(level=16, prev_level=5, route_segments=None))
    assert 42 in e.armed_ids()


def test_a_selected_route_still_narrows_to_its_own_members():
    """The other half, and the reason the guard is not simply deleted: picking
    a route is a deliberate narrowing, so a movement OUTSIDE the active route
    stays unarmed. Only the EMPTY scope means "no filter"."""
    e = SegmentEngine([_guarded_move()])
    e.feed(jev(10, "level_changed", 1000, {"from": 5, "to": 16}),
           ctx(level=16, prev_level=5, route_segments=frozenset({7, 8})))
    assert 42 not in e.armed_ids()


def test_guarded_def_arms_when_in_active_route():
    e = SegmentEngine([_guarded_move()])
    e.feed(jev(10, "level_changed", 1000, {"from": 5, "to": 16}),
           ctx(level=16, prev_level=5, route_segments=frozenset({42})))
    assert 42 in e.armed_ids()


def test_guarded_def_arms_as_target_segment():
    e = SegmentEngine([_guarded_move()])
    e.feed(jev(10, "level_changed", 1000, {"from": 5, "to": 16}),
           ctx(level=16, prev_level=5, route_segments=None, target_segment=42))
    assert 42 in e.armed_ids()


def test_unguarded_def_ignores_route_state():
    d = replace(_guarded_move(), guards=[])
    e = SegmentEngine([d])
    e.feed(jev(10, "level_changed", 1000, {"from": 5, "to": 16}),
           ctx(level=16, prev_level=5, route_segments=None))
    assert 42 in e.armed_ids()


# -- default_strat: the definition's own strategy (spec 2026-07-24) ----------

def test_segment_def_default_strat_defaults_to_none():
    """Defaulted for the same reason waypoints is: a non-default field would
    TypeError every existing SegmentDef(...) construction."""
    assert LBLJ.default_strat is None


def test_validate_accepts_a_default_strat():
    validate_definition({
        "name": "BoB -> WF",
        "start_triggers": [{"type": "level_exit", "from": 9}],
        "end_triggers": [{"type": "level_enter", "to": 24}],
        "guards": [], "default_strat": "Standard"})  # no raise


def test_validate_rejects_a_non_string_default_strat():
    for bad in (7, "", "   ", []):
        with pytest.raises(ValueError, match="default_strat"):
            validate_definition({
                "name": "x", "start_triggers": [{"type": "spawned"}],
                "end_triggers": [{"type": "level_enter", "to": 6}],
                "guards": [], "default_strat": bad})


def test_start_origin_reads_the_source_of_a_level_exit():
    # SSL -> LLL: the seeded exits carry no `to`, and the source is the place
    # a runner names ("coming out of SSL").
    assert start_origin([{"type": "level_exit", "from": 8}]) == "8"


def test_start_origin_reads_the_destination_of_a_level_enter():
    assert start_origin([{"type": "level_enter", "to": 9, "from": 6}]) == "9"


def test_start_origin_reads_area_and_anchor_positions():
    assert start_origin([{"type": "area_enter", "level": 6, "area": 2}]) == "6:2"
    assert start_origin([{"type": "attempt_anchor", "level": 17}]) == "17"
    assert start_origin([{"type": "spawned", "level": 16}]) == "16"


def test_start_origin_prefers_the_clause_that_names_a_subarea():
    # LBLJ arms either way; the anchor knows it is the lobby, the level entry
    # does not.
    assert start_origin([{"type": "level_enter", "to": 6, "from": 16},
                         {"type": "attempt_anchor", "level": 6, "area": 1}]) == "6:1"


def test_start_origin_keeps_the_first_clause_when_places_disagree():
    assert start_origin([{"type": "level_exit", "from": 8},
                         {"type": "level_exit", "from": 22}]) == "8"


def test_start_origin_resolves_a_star_grab_through_its_course():
    assert start_origin([{"type": "star_grabbed", "course": 9, "star": 0}]) == "23"


def test_start_origin_places_the_mips_stars_in_the_basement():
    assert start_origin([{"type": "star_grabbed", "course": 0, "star": 3}]) == "6:3"
    assert start_origin([{"type": "star_grabbed", "course": 0, "star": 4}]) == "6:3"


def test_start_origin_is_none_when_the_rules_carry_no_place():
    assert start_origin([{"type": "reset_game"}]) is None
    assert start_origin([{"type": "key_grabbed"}]) is None
    assert start_origin([{"type": "star_grabbed", "course": 0, "star": 0}]) is None
    assert start_origin([]) is None


def test_hundred_coin_entity_finds_the_grab_as_a_start_clause():
    # The pre-reshape shape (the grab itself IS the start trigger).
    assert hundred_coin_entity(
        [{"type": "star_grabbed", "course": 2, "star": 6}], []) == (2, 6)


def test_hundred_coin_entity_finds_the_grab_as_a_waypoint():
    # The reshaped, currently-seeded shape (course entry starts it, the
    # 100-coin grab is a waypoint mid-sequence) -- SPAN-AGNOSTIC on purpose,
    # same reasoning as the retired _hundred_coin_redirect.
    start_triggers = [{"type": "level_enter", "to": 24},
                      {"type": "attempt_anchor", "level": 24}]
    waypoints = [[{"type": "star_grabbed", "course": 2, "star": 6}]]
    assert hundred_coin_entity(start_triggers, waypoints) == (2, 6)


def test_hundred_coin_entity_ignores_a_different_star():
    # A def whose sequence grabs an ORDINARY star (not the 100-coin one)
    # is not part of this family, whatever else it does.
    assert hundred_coin_entity(
        [{"type": "star_grabbed", "course": 2, "star": 3}], []) is None


def test_hundred_coin_entity_is_none_for_a_plain_movement():
    assert hundred_coin_entity([{"type": "level_exit", "from": 8}], []) is None


def test_hundred_coin_entity_reads_the_course_off_the_matching_clause():
    # Not a hand-written course table -- the (course, 6) pair comes straight
    # off whichever clause matched, so a DDD def answers 9, not 2.
    assert hundred_coin_entity(
        [{"type": "star_grabbed", "course": 9, "star": 6}], []) == (9, 6)


def test_arms_ambiently_is_true_for_a_course_entry_start():
    assert arms_ambiently([{"type": "level_enter", "to": 24},
                          {"type": "attempt_anchor", "level": 24}]) is True


def test_arms_ambiently_is_true_for_an_anchor_alone():
    assert arms_ambiently([{"type": "attempt_anchor", "level": 17}]) is True


def test_arms_ambiently_is_false_for_the_castle_interior():
    # LBLJ's own shape -- level 6 has no course (course_for_level -> None),
    # so entering/anchoring the castle interior is not "a stage".
    assert arms_ambiently([{"type": "level_enter", "to": 6, "from": 16},
                          {"type": "attempt_anchor", "level": 6}]) is False


def test_arms_ambiently_is_false_for_a_leaving_or_grabbing_start():
    assert arms_ambiently([{"type": "level_exit", "from": 8}]) is False
    assert arms_ambiently(
        [{"type": "star_grabbed", "course": 0, "star": 3}]) is False


def test_arms_ambiently_matches_exactly_the_three_families_in_the_real_corpus():
    # Measured, not assumed: 21 of 84 seeded defs (15 hundred-coin + 3
    # reds->pipe + 3 legacy pipe-entry), and specifically NOT LBLJ or any
    # Bowser fight (arms the same way but auto-selects on entry by design --
    # stagebanner.js's ArenaRow -- so an ambient pin there is not a bug).
    import json
    from sm64_events.core.paths import bundled_defaults_seed
    seed = json.loads(bundled_defaults_seed().read_bytes().decode("utf-8"))
    flagged = {s["name"] for s in seed["segments"]
              if arms_ambiently(s["start_triggers"])}
    assert len(flagged) == 21
    assert "LBLJ" not in flagged
    assert "Bowser 1" not in flagged and "Bowser 2" not in flagged \
        and "Bowser 3" not in flagged
    assert "BitDW Pipe Entry" in flagged
    assert "BitDW — 8 Red Coins → Pipe" in flagged
    assert "WF — 100 Coins → Exit" in flagged


def test_origin_view_carries_the_region_and_its_labels():
    view = origin_view("8")
    assert view == {"key": "8", "label": "Shifting Sand Land",
                    "region": "6:3", "region_label": "Basement"}
    anywhere = origin_view(None)
    assert anywhere["key"] is None and anywhere["region"] is None


def test_origin_view_puts_a_subarea_less_castle_start_in_the_lobby():
    # start_origin never emits a bare "6" anymore (see the test below), but a
    # STORED value (an old override, a foreign payload) can still be one —
    # origin_view must keep resolving its region rather than rendering a raw
    # key as a group header (review I1).
    assert origin_view("6")["region"] == "6:1"


def test_a_subarea_less_castle_start_normalizes_to_the_lobby():
    # Not just "has the lobby as its region" — it must BE a lobby place, or it
    # renders as a group header labelled "6" (review I1).
    assert start_origin([{"type": "level_enter", "to": 6}]) == "6:1"


def test_origin_taxonomy_is_ordered_by_gameflow_then_class():
    taxonomy = origin_taxonomy()
    assert [group["key"] for group in taxonomy] == \
        ["16", "6:1", "6:3", "26", "6:2", None]
    lobby = next(group for group in taxonomy if group["key"] == "6:1")
    labels = [place["label"] for place in lobby["children"]]
    # region itself, then Bowser stage + arena, then secret stages, then the
    # main courses in gameflow order
    assert labels[:3] == ["Lobby (in-area starts)",
                          "Bowser in the Dark World", "Bowser 1 Arena"]
    assert labels[3:6] == ["The Princess's Secret Slide",
                           "Tower of the Wing Cap", "The Secret Aquarium"]
    assert labels[6:8] == ["Bob-omb Battlefield", "Whomp's Fortress"]


def test_vocab_ships_the_origin_taxonomy():
    assert vocab()["origins"] == origin_taxonomy()


def test_level_groups_cover_every_level_exactly_once():
    groups = level_groups()
    seen = [level for group in groups for level in group["levels"]]
    assert sorted(seen) == sorted(LEVEL_NAMES), "a level vanished from the picker"
    assert len(seen) == len(set(seen)), "a level is offered twice"


def test_level_groups_read_in_the_librarys_order():
    groups = level_groups()
    assert [group["label"] for group in groups][:5] == [
        "Castle Grounds", "Lobby", "Basement", "Castle Courtyard", "Upstairs"]
    lobby = next(group for group in groups if group["label"] == "Lobby")
    # the castle interior has a node in three regions and takes the first —
    # the same answer region_for_node gives a bare "6"
    assert lobby["levels"][0] == 6
    assert all(6 not in group["levels"] for group in groups
               if group["label"] != "Lobby")


def test_course_groups_put_the_castle_secret_stars_in_other():
    groups = course_groups()
    other = next(group for group in groups if group["label"] == "Other")
    assert 0 in other["courses"]        # course 0 has no level of its own
    seen = [course for group in groups for course in group["courses"]]
    assert sorted(seen) == sorted(COURSE_NAMES)
    assert len(seen) == len(set(seen))


def test_vocab_ships_both_grouped_pickers():
    shipped = vocab()
    assert shipped["level_groups"] == level_groups()
    assert shipped["course_groups"] == course_groups()


def test_vocab_ships_course_by_level():
    # The JS icon chain maps a level to its course; the mapping is domain data
    # and stays server-side rather than being duplicated in the UI.
    shipped = vocab()["course_by_level"]
    assert shipped["9"] == 1        # BoB
    assert shipped["24"] == 2       # WF
    assert all(isinstance(key, str) for key in shipped)   # JSON object keys


# ---------------------------------------------------------------------------
# Arm-position gate (live report 2026-07-27): a Usamune menu warp fabricates a
# level edge the world does not have, so a start trigger can fire with Mario
# standing somewhere the segment cannot be run from.
# ---------------------------------------------------------------------------

# The seeded WF -> SSL movement, verbatim: leave Whomp's Fortress, cross into
# the castle basement, enter Shifting Sand Land.
WF_TO_SSL = SegmentDef(
    id=21, name="WF -> SSL", enabled=True,
    start_triggers=[{"type": "level_exit", "from": 24}],
    waypoints=[[{"type": "area_enter", "level": 6, "area": 3}]],
    end_triggers=[{"type": "level_enter", "to": 8}], guards=[])


def test_a_menu_warp_between_courses_arms_nothing():
    """THE LIVE REPORT (2026-07-27, journal ids 1547-1564). Warping WF -> CCM
    from the Usamune menu is ONE level_changed 24 -> 5 — no castle in between —
    so `level_exit from=24` fired with Mario standing in Cool, Cool Mountain.
    WF -> SSL armed there and nothing disarmed it while the player practised in
    CCM, so the page read "ACTIVE SEGMENT  WF -> SSL  Running" for six
    minutes."""
    e = SegmentEngine([WF_TO_SSL])
    e.feed(jev(1547, "level_changed", 5065014, {"from": 24, "to": 5}),
           ctx(level=5, prev_level=24))
    assert e.armed_ids() == set()
    # and it stays that way through the practice loop that followed
    e.feed(jev(1549, "area_changed", 5065014,
               {"level": 5, "from": 1, "to": 1, "from_transient": True}),
           ctx(level=5))
    e.feed(jev(1552, "practice_reset", 5065066, {"paused_frames_before": 4392}),
           ctx(level=5))
    assert e.armed_ids() == set()


def test_the_same_walk_still_arms_when_it_goes_through_the_castle():
    """The other half of the gate: the REAL exit lands in the castle, which is
    exactly where WF -> SSL has to start. Without this the fix is just a way to
    stop the feature working."""
    e = SegmentEngine([WF_TO_SSL])
    e.feed(jev(1, "level_changed", 1000, {"from": 24, "to": 6}),
           ctx(level=6, prev_level=24))
    assert e.armed_ids() == {21}


def test_a_menu_warp_onto_the_destination_arms_nothing():
    """Warping WF -> SSL is not PERFORMING WF -> SSL: the movement whose route
    you skipped cannot start where it was supposed to end."""
    e = SegmentEngine([WF_TO_SSL])
    e.feed(jev(1, "level_changed", 1000, {"from": 24, "to": 8}),
           ctx(level=8, prev_level=24))
    assert e.armed_ids() == set()


def test_a_castle_exit_into_a_course_still_arms():
    """The gate keys on where a COURSE exit lands, so leaving the castle — the
    one level whose exits are courses — is untouched."""
    e = SegmentEngine([_seg(start_triggers=[{"type": "level_exit", "from": 6}])])
    e.feed(jev(1, "level_changed", 1000, {"from": 6, "to": 7}), ctx(level=7))
    assert e.armed_ids() == {1}


def test_a_placeless_start_trigger_arms_anywhere():
    """A def that never says where it starts keeps arming wherever it fires —
    unknown means yes, as everywhere else in the matcher."""
    e = SegmentEngine([SegmentDef(
        id=1, name="x", enabled=True, waypoints=[], guards=[],
        start_triggers=[{"type": "star_grabbed"}],
        end_triggers=[{"type": "level_enter", "to": 17}])])
    e.feed(jev(1, "star_collected", 1000, {"course_id": 4, "star_id": 0}),
           ctx(level=5))
    assert e.armed_ids() == {1}


# The seeded corpus only ever exercises rule (B) — every one of its movements
# leaves a COURSE, so "the exit landed outside the castle" catches each bad arm
# before the other two rules are consulted. These two defs leave the COURTYARD
# instead: a castle level, whose exits legitimately include a course (BBH), so
# rule (B) is off by construction and (A) and (C) are what is left. Both shapes
# are ones the segment builder can produce.
def _courtyard_to_ssl(waypoint):
    return SegmentDef(id=1, name="Courtyard -> SSL", enabled=True,
                      start_triggers=[{"type": "level_exit", "from": 26}],
                      waypoints=[[waypoint]],
                      end_triggers=[{"type": "level_enter", "to": 8}],
                      guards=[])


def test_an_arm_that_cannot_take_its_next_step_is_refused():
    """Rule (A). Waypoint: cross into the basement — which needs Mario to be
    inside the castle. A warp that drops him in Cool, Cool Mountain leaves the
    sequence with nowhere to go, whatever the end trigger says."""
    definition = _courtyard_to_ssl({"type": "area_enter", "level": 6, "area": 3})
    warped = SegmentEngine([definition])
    warped.feed(jev(1, "level_changed", 1000, {"from": 26, "to": 5}),
                ctx(level=5, prev_level=26))
    assert warped.armed_ids() == set()
    walked = SegmentEngine([definition])
    walked.feed(jev(1, "level_changed", 1000, {"from": 26, "to": 6}),
                ctx(level=6, prev_level=26))
    assert walked.armed_ids() == {1}       # the real exit still arms


def test_an_arm_standing_on_its_own_destination_is_refused():
    """Rule (C). Waypoint: enter the castle — firable from Shifting Sand Land,
    so (A) is satisfied and only "you are already at the finish" can tell that
    warping straight to SSL did not PERFORM the movement to SSL."""
    definition = _courtyard_to_ssl({"type": "level_enter", "to": 6})
    warped = SegmentEngine([definition])
    warped.feed(jev(1, "level_changed", 1000, {"from": 26, "to": 8}),
                ctx(level=8, prev_level=26))
    assert warped.armed_ids() == set()
    walked = SegmentEngine([definition])
    walked.feed(jev(1, "level_changed", 1000, {"from": 26, "to": 4}),
                ctx(level=4, prev_level=26))
    assert walked.armed_ids() == {1}       # BBH is a real courtyard exit


def test_a_reset_started_segment_arms_despite_the_stale_level():
    """The gate's one exemption. The projector holds the PRE-reset level until
    the next level_changed, so at a game_reset ctx.level names where the player
    was, not where the reset put them — reading it would refuse to arm a
    reset-started def whenever the player happened to F1 inside its own
    destination."""
    e = SegmentEngine([SegmentDef(
        id=1, name="from the top", enabled=True, waypoints=[], guards=[],
        start_triggers=[{"type": "reset_game"}],
        end_triggers=[{"type": "level_enter", "to": 9}])])
    e.feed(jev(1, "game_reset", 40, {}), ctx(level=9))   # F1'd inside BoB
    assert e.armed_ids() == {1}


# ---------------------------------------------------------------------------
# Task 1: SegmentDef.match_mode (spec 2026-07-28-multi-step-segments). Pure
# plumbing — the field, its validation, and the editor vocab. No matching
# behaviour changes: every def (waypoint-bearing or plain) still runs the
# armed-branch chain this file already exercises above; a mode's HANDLING is
# added in a later task.
# ---------------------------------------------------------------------------

def test_segmentdef_defaults_to_strict_match_mode():
    # Defaulted for the same reason waypoints is: a non-default field would
    # TypeError every existing SegmentDef(...) construction that omits it.
    d = SegmentDef(id=1, name="x", enabled=True,
                   start_triggers=[{"type": "spawned"}],
                   end_triggers=[{"type": "spawned"}], guards=[])
    assert d.match_mode == "strict"


def test_validate_accepts_all_match_modes():
    for mode in ("strict", "loose", "exclusive"):
        validate_definition({"name": "x", "match_mode": mode,
                             "start_triggers": [{"type": "spawned"}],
                             "end_triggers": [{"type": "spawned"}],
                             "guards": []})  # no raise


def test_validate_rejects_an_unknown_match_mode():
    with pytest.raises(ValueError, match="match_mode"):
        validate_definition({"name": "x", "match_mode": "sloppy",
                             "start_triggers": [{"type": "spawned"}],
                             "end_triggers": [{"type": "spawned"}],
                             "guards": []})


def test_vocab_ships_the_match_modes_for_the_editor():
    modes = vocab()["match_modes"]
    # loose stays position 0 -- segments.js seeds a new definition's default
    # from match_modes[0].key; exclusive (strict plus one more cancel rule)
    # is the most specialized of the three, so it's appended last.
    assert [m["key"] for m in modes] == ["loose", "strict", "exclusive"]
    assert all(m["label"] and m["description"] for m in modes)


# ---------------------------------------------------------------------------
# Task 3: SegmentEngine._feed_loose + the staleness deadline (spec
# 2026-07-28-multi-step-segments). A loose def stays armed through star
# grabs, key grabs and level crossings until its end trigger fires or a
# staleness deadline passes — every test below is one row of _feed_loose's
# precedence table, so a reviewer can check coverage by reading the names.
# ---------------------------------------------------------------------------

LOOSE = SegmentDef(
    id=20, name="DDD -> BitFS (loose)", enabled=True,
    start_triggers=[{"type": "level_exit", "from": 23}],
    end_triggers=[{"type": "level_enter", "to": 19}],
    guards=[], match_mode="loose")


def loose_arm(engine, jid=10, frame=1000):
    return engine.feed(jev(jid, "level_changed", frame, {"from": 23, "to": 6}),
                       ctx(level=6, prev_level=23))


def test_loose_survives_a_star_grab_that_would_cancel_a_strict_def():
    # The rule that made the 100-coin case unwriteable: a strict waypoint def
    # is silently cancelled by ANY star grab.
    e = SegmentEngine([LOOSE])
    loose_arm(e)
    closed, _ = e.feed(jev(11, "star_collected", 1200,
                           {"course_id": 15, "star_id": 1, "igt_frames": 900}),
                       ctx(level=6))
    assert closed == []
    assert 20 in e.armed_ids()


def test_loose_survives_an_off_route_level_crossing():
    e = SegmentEngine([LOOSE])
    loose_arm(e)
    e.feed(jev(11, "level_changed", 1200, {"from": 6, "to": 8}),
           ctx(level=8, prev_level=6))
    assert 20 in e.armed_ids()


def test_loose_closes_a_success_on_its_end_trigger():
    e = SegmentEngine([LOOSE])
    loose_arm(e)
    e.feed(jev(11, "star_collected", 1200,
               {"course_id": 15, "star_id": 1, "igt_frames": 900}),
           ctx(level=6))
    closed, _ = e.feed(jev(12, "level_changed", 1500, {"from": 6, "to": 19}),
                       ctx(level=19, prev_level=6))
    [a] = closed
    assert a.outcome == "success" and a.rta_frames == 500


def test_loose_expires_after_its_staleness_budget_with_no_row():
    e = SegmentEngine([LOOSE])
    loose_arm(e, frame=1000)
    stale = 1000 + segments_module.MIN_BUDGET_FRAMES + 1
    closed, notices = e.feed(jev(11, "area_changed", stale, {"to": 3}),
                             ctx(level=6, area=3))
    assert closed == []                       # silent: stats stay clean
    assert 20 not in e.armed_ids()
    assert [n["event"] for n in notices] == ["segment_disarmed"]


def test_an_expired_arm_cannot_still_record_a_success():
    # Deadline is checked FIRST. An end trigger arriving an hour after the
    # player walked away is not a run.
    e = SegmentEngine([LOOSE])
    loose_arm(e, frame=1000)
    stale = 1000 + segments_module.MIN_BUDGET_FRAMES + 1
    closed, _ = e.feed(jev(11, "level_changed", stale, {"from": 6, "to": 19}),
                       ctx(level=19, prev_level=6))
    assert closed == []


def test_an_expired_arm_cannot_still_record_a_failure():
    e = SegmentEngine([LOOSE])
    loose_arm(e, frame=1000)
    stale = 1000 + segments_module.MIN_BUDGET_FRAMES + 1
    closed, _ = e.feed(jev(11, "death", stale, {"cause": "fall"}), ctx(level=6))
    assert closed == []


def test_loose_still_records_a_death_inside_the_budget():
    e = SegmentEngine([LOOSE])
    loose_arm(e, frame=1000)
    closed, _ = e.feed(jev(11, "death", 1200, {"cause": "fall"}), ctx(level=6))
    [a] = closed
    assert a.outcome == "death"


def test_the_budget_tightens_once_the_segment_has_a_best_time():
    # A definition with history gets FACTOR x its best, not the floor.
    assert segments_module.budget_frames(None) \
        == segments_module.MIN_BUDGET_FRAMES
    assert segments_module.budget_frames(10 ** 6) \
        == segments_module.BUDGET_FACTOR * 10 ** 6
    assert segments_module.budget_frames(1) \
        == segments_module.MIN_BUDGET_FRAMES     # floor wins for a fast one


def test_the_staleness_budget_never_clips_a_realistic_movement():
    # Assert the RANGE, never the number: the constants are measured
    # (tools/measure_budget.py, Task 9) and will be re-measured again once
    # the loose-native corpus grows — a test naming the shipped value turns
    # a re-measurement into a red build (the shipped-default rule in
    # CLAUDE.md). The real journal's forced-loose max was 4244 frames
    # (141.5s); these bounds are wide enough to survive a re-measurement but
    # tight enough to catch a nonsense value (e.g. a floor under a minute,
    # or a factor so small a single retry would expire).
    assert 1800 <= segments_module.MIN_BUDGET_FRAMES <= 18000
    assert 3 <= segments_module.BUDGET_FACTOR <= 20
    # 30s is a fast castle movement; six of them is not an attempt.
    assert segments_module.budget_frames(900) >= 900 * 3


def test_a_loose_def_armed_through_the_deferred_subarea_path_carries_a_deadline():
    # THE GAP this task's brief missed (see task-3-report.md Item 0/1C): a
    # destination-subarea start trigger (to_subarea) can't be confirmed on
    # the level edge — the castle interior loads the transient lobby before
    # the co-frame settle (module docstring's DESTINATION subarea section) —
    # so the engine holds a fresh _Arm in self._pending until the settled
    # area matches, then PROMOTES it via replace(). A loose def armed this
    # way must still carry a deadline: this deferred path is where a large
    # share of the seeded castle movements arm (any destination inside the
    # castle interior — basement, lobby, upstairs), and a def armed through
    # it with deadline_frame=None would never expire.
    d = SegmentDef(id=1, name="x", enabled=True, guards=[], waypoints=[],
                   start_triggers=[{"type": "level_exit", "from": 7, "to": 6,
                                    "to_subarea": 3}],
                   end_triggers=[{"type": "spawned"}], match_mode="loose")
    e = SegmentEngine([d])
    e.feed(jev(10, "level_changed", 1000, {"from": 7, "to": 6, "from_area": 1}),
           ctx(level=6))
    assert e.armed_ids() == set(), "deferred: destination not settled yet"
    e.feed(jev(12, "area_changed", 1000, {"level": 6, "from": 1, "to": 3}),
           ctx(level=6, area=3))            # real-edge settle into the basement
    assert e.armed_ids() == {1}
    assert e._armed[1].deadline_frame == 1000 + segments_module.MIN_BUDGET_FRAMES


def test_a_real_anchor_re_arms_the_loose_def_with_a_fresh_deadline():
    # Item 1C's third _deadline_for call site: "the re-arm inside
    # _feed_loose". A real practice_reset/state_loaded AT the arm position is
    # the practice-retry continuation (closes a "reset" row, re-arms in
    # place) — the fresh arm must get a NEW deadline counted from the anchor
    # frame, not keep the stale one from the original arm.
    e = SegmentEngine([LOOSE])
    loose_arm(e, frame=1000)
    closed, _ = e.feed(jev(11, "practice_reset", 4000, {}), ctx(level=6))
    [a] = closed
    assert a.outcome == "reset"
    assert e._armed[20].deadline_frame == 4000 + segments_module.MIN_BUDGET_FRAMES


def test_a_real_anchor_off_the_arm_position_is_transparent_not_a_relocation_disarm():
    # Live report 2026-07-28: "Bowser 2 -> Upstairs" (loose, arms in the
    # basement leaving the Bowser 2 fight) vanished mid-run — no attempt row,
    # no notice, the split just disappeared once the player reached the
    # castle lobby on the way to Upstairs. _feed_loose had inherited
    # _feed_strict's anchor-elsewhere RELOCATION disarm, which is backwards
    # for a loose def: reaching Upstairs from the basement requires passing
    # BACK through the lobby, so the described route is guaranteed to cross
    # positions it didn't arm at, and the first practice_reset anywhere along
    # the way used to kill the attempt silently.
    #
    # Sequence synthesized from the real journal's shape (session 167/168,
    # ids ~19040-19130) rather than read off data/tracker.db, which is
    # gitignored and absent from a fresh clone (CLAUDE.md). Level 33 =
    # Bowser 2 Arena, level 6 = Castle Inside, level 19 = Bowser in the Fire
    # Sea (BitFS); castle areas 1/2/3 = lobby/Upstairs/basement.
    bowser2_to_upstairs = SegmentDef(
        id=40, name="Bowser 2 -> Upstairs", enabled=True,
        start_triggers=[{"type": "level_exit", "from": 33}],
        end_triggers=[{"type": "area_enter", "level": 6, "area": 2}],
        guards=[], match_mode="loose")
    bits_entry = SegmentDef(
        id=41, name="BitS Entry", enabled=True,
        start_triggers=[{"type": "area_enter", "level": 6, "area": 2}],
        end_triggers=[{"type": "level_enter", "to": 19}],
        guards=[], match_mode="strict")
    e = SegmentEngine([bowser2_to_upstairs, bits_entry])

    # Exit Bowser 2 -> lands in the castle basement; arms the loose movement.
    e.feed(jev(1, "level_changed", 1000, {"from": 33, "to": 6}),
           ctx(level=6, prev_level=33))
    e.feed(jev(2, "area_changed", 1000, {"level": 6, "from": 1, "to": 3}),
           ctx(level=6, area=3))
    assert e.armed_ids() == {40}

    # A reset AT the arm position (still standing in the basement) is a
    # genuine retry — unaffected by this fix, asserted for contrast.
    closed, _ = e.feed(jev(3, "practice_reset", 1050, {}), ctx(level=6, area=3))
    [retry] = closed
    assert retry.outcome == "reset"
    assert 40 in e.armed_ids()

    # Off into BitFS -- the loose def survives the off-route level crossing
    # (already covered elsewhere) and a real anchor taken INSIDE BitFS, far
    # from the basement arm position.
    e.feed(jev(4, "level_changed", 1200, {"from": 6, "to": 19}),
           ctx(level=19, prev_level=6))
    closed, notices = e.feed(jev(5, "practice_reset", 1300, {}), ctx(level=19))
    assert closed == [], "an anchor elsewhere must not close a row"
    assert notices == [], "and must not disarm — the old bug's silent kill"
    assert 40 in e.armed_ids()

    # Back out toward the castle: through the lobby (area 1) -- THE anchor
    # that used to silently kill the segment.
    e.feed(jev(6, "level_changed", 1400, {"from": 19, "to": 6}),
           ctx(level=6, prev_level=19))
    e.feed(jev(7, "area_changed", 1400, {"level": 6, "from": 3, "to": 1}),
           ctx(level=6, area=1))
    closed, notices = e.feed(jev(8, "practice_reset", 1450, {}),
                             ctx(level=6, area=1))
    assert closed == []
    assert notices == [], "the lobby anchor must stay transparent too"
    assert 40 in e.armed_ids(), "the segment must still be armed here"

    # Finally reach Upstairs: the loose def closes success, and BitS Entry
    # (idle strict def, unrelated to this fix) arms on the SAME event.
    closed, notices = e.feed(jev(9, "area_changed", 1500, {"level": 6, "from": 1, "to": 2}),
                             ctx(level=6, area=2))
    [success] = closed
    assert success.outcome == "success" and success.rta_frames == 450
    assert e.armed_ids() == {41}
    assert [n["event"] for n in notices] == ["segment_disarmed", "segment_armed"]


def test_a_plain_loose_defs_own_start_trigger_refiring_while_armed_restarts_visibly():
    # Live audit 2026-07-29 (following up on the finding in the anchor-
    # relocation report above): replaying the user's real session found 13
    # refires of a plain loose def's own start trigger while it was still
    # armed -- e.g. an EARLIER, abandoned Bowser 2 exit had armed the
    # movement, and a later real exit refired the same start trigger,
    # restarting it. The restart is the correct arithmetic (the stale arm
    # hadn't hit its staleness deadline yet) -- what was wrong is that it was
    # completely SILENT: `fresh` is False (the def never left self._armed),
    # so no notice fired and the discarded in-flight arm vanished with no
    # trace. The restart stays; only the silence is fixed.
    e = SegmentEngine([LOOSE])
    loose_arm(e, jid=10, frame=1000)
    # transparent mid-route travel, exactly like any other loose def
    e.feed(jev(11, "star_collected", 1200,
               {"course_id": 15, "star_id": 1, "igt_frames": 900}), ctx(level=6))
    assert 20 in e.armed_ids()
    # the def's OWN start trigger fires again while still armed
    closed, notices = e.feed(jev(12, "level_changed", 5000, {"from": 23, "to": 6}),
                             ctx(level=6, prev_level=23))
    assert closed == [], "no row for the discarded partial"
    assert [n["event"] for n in notices] == ["segment_disarmed", "segment_armed"]
    assert e._armed[20].start_frame == 5000, "restarted from the refire, not the original arm"
    assert e._armed[20].deadline_frame == 5000 + segments_module.MIN_BUDGET_FRAMES


def test_a_waypoint_bearing_defs_refire_while_armed_stays_silent():
    # The boundary the fix above must not cross: a waypoint-bearing def (loose
    # or not) owns its own re-arm through _feed_waypoint's/`_feed_loose`'s own
    # `progress` counter, and the generic re-arm phase must keep skipping it
    # ENTIRELY while armed -- the pre-existing
    # `not (d.waypoints and d.id in self._armed)` guard, which this task was
    # told not to touch. Built LOOSE (not strict) specifically because that
    # guard reads `d.waypoints`, not match_mode, so a loose def carrying
    # waypoints is the sharper boundary case -- _feed_loose owns its armed
    # branch for it exactly as for a plain loose def, but the outer re-arm
    # phase must still treat "has waypoints" as untouchable while armed.
    wp_def = SegmentDef(
        id=45, name="wp", enabled=True,
        start_triggers=[{"type": "level_exit", "from": 23}],
        end_triggers=[{"type": "level_enter", "to": 19}],
        waypoints=[[{"type": "area_enter", "level": 6, "area": 3}]],
        guards=[], match_mode="loose")
    e = SegmentEngine([wp_def])
    e.feed(jev(10, "level_changed", 1000, {"from": 23, "to": 6}),
           ctx(level=6, prev_level=23))
    assert 45 in e.armed_ids()
    closed, notices = e.feed(jev(11, "level_changed", 5000, {"from": 23, "to": 6}),
                             ctx(level=6, prev_level=23))
    assert closed == []
    assert notices == [], "still silent -- waypoints own their own re-arm"
    assert e._armed[45].start_frame == 1000, "untouched by the refire"


# ---------------------------------------------------------------------------
# Third match_mode: "exclusive" (spec 2026-07-28-multi-step-segments). A
# plain (waypoint-free) def that is otherwise Strict, but silently cancels on
# a star or Bowser-key grab -- the shape a pipe-entry skip needs ("enter the
# pipe without going for the 8-red-coin star"), which a plain start/end pair
# can't express through _feed_waypoint without inventing a fake waypoint.
# `_feed_strict` runs both modes; these tests cover its one new branch and
# the contrast against plain Strict that makes the mode meaningful.
# ---------------------------------------------------------------------------

EXCLUSIVE_PIPE = SegmentDef(
    id=31, name="BitDW Pipe Entry (exclusive)", enabled=True,
    start_triggers=[{"type": "level_enter", "to": 17}],
    end_triggers=[{"type": "warp_entered", "level": 17}],
    waypoints=[], guards=[], match_mode="exclusive")

STRICT_PIPE = SegmentDef(
    id=32, name="BitDW Pipe Entry (strict)", enabled=True,
    start_triggers=[{"type": "level_enter", "to": 17}],
    end_triggers=[{"type": "warp_entered", "level": 17}],
    waypoints=[], guards=[], match_mode="strict")


def _arm_pipe(engine, jid=10, frame=1000):
    return engine.feed(jev(jid, "level_changed", frame, {"from": 6, "to": 17}),
                       ctx(level=17, prev_level=6))


def test_exclusive_closes_normally_on_its_end_trigger_with_no_star_grab():
    e = SegmentEngine([EXCLUSIVE_PIPE])
    _arm_pipe(e)
    closed, _ = e.feed(jev(11, "warp_entered", 1200,
                           {"level": 17, "area": 1, "action": 0x1300}),
                       ctx(level=17))
    [a] = closed
    assert a.outcome == "success" and a.segment_id == 31


def test_exclusive_cancels_silently_on_a_star_grab_mid_route():
    # The whole point: grabbing the 8-red-coin star along the way means the
    # attempt wasn't a skip run. No ATTEMPT row -- but the segment_disarmed
    # notice DOES fire, same as every other silent cancel in this file (e.g.
    # test_waypoint_session_started_disarms_silently above): "silent" means
    # no row, not no notice at all.
    e = SegmentEngine([EXCLUSIVE_PIPE])
    _arm_pipe(e)
    closed, notices = e.feed(jev(11, "star_collected", 1150,
                                 {"course_id": 17, "star_id": 6,
                                  "num_stars": 1}),
                             ctx(level=17, num_stars=1))
    assert closed == []
    assert 31 not in e.armed_ids()
    assert notices == [{"event": "segment_disarmed", "segment_id": 31,
                        "name": "BitDW Pipe Entry (exclusive)", "frame": 1150}]


def test_exclusive_cancels_silently_on_a_key_grab_mid_route():
    e = SegmentEngine([EXCLUSIVE_PIPE])
    _arm_pipe(e)
    closed, _ = e.feed(jev(11, "key_grabbed", 1150, {"level": 17}),
                       ctx(level=17))
    assert closed == []
    assert 31 not in e.armed_ids()


def test_strict_survives_a_star_grab_that_would_cancel_an_exclusive_def():
    # The contrast that makes "exclusive" meaningful: the identical shape
    # under plain Strict does NOT cancel on a star grab. _feed_strict has no
    # branch matching star_collected/key_grabbed at all outside the new
    # match_mode == "exclusive" gate, so the event falls through the whole
    # chain untouched and the def stays armed.
    e = SegmentEngine([STRICT_PIPE])
    _arm_pipe(e)
    closed, _ = e.feed(jev(11, "star_collected", 1150,
                           {"course_id": 17, "star_id": 6, "num_stars": 1}),
                       ctx(level=17, num_stars=1))
    assert closed == []
    assert 32 in e.armed_ids()


def test_exclusive_still_disarms_on_an_off_route_level_crossing_like_strict():
    # EXCLUSIVE's one addition is the star/key-grab branch above -- every
    # other Strict rule (here: the plain silent disarm on a level_changed
    # matching neither start nor end) is untouched.
    e = SegmentEngine([EXCLUSIVE_PIPE])
    _arm_pipe(e)
    closed, _ = e.feed(jev(11, "level_changed", 1150, {"from": 17, "to": 6}),
                       ctx(level=6, prev_level=17))
    assert closed == []
    assert 31 not in e.armed_ids()


# --- Task 17: split_definition / merge_definitions --------------------------
# Two pure operations (spec 2026-07-28-multi-step-segments): "WF -> SSL"
# expressible either as one definition or as "WF -> Basement" +
# "Basement -> SSL", chained at the shared boundary. Both are
# non-destructive -- neither mutates its inputs, and split_definition never
# removes the original (definitions arm in PARALLEL, so the whole and its
# halves can all record on the same play).

def test_split_produces_two_chained_definitions():
    wf_ssl = SegmentDef(
        id=1, name="WF -> SSL", enabled=True,
        start_triggers=[{"type": "level_exit", "from": 24}],
        end_triggers=[{"type": "level_enter", "to": 8}],
        waypoints=[[{"type": "area_enter", "level": 6, "area": 3}]],
        guards=[], match_mode="loose")
    first, second = split_definition(
        wf_ssl, mid=[{"type": "area_enter", "level": 6, "area": 3}],
        names=("WF -> Basement", "Basement -> SSL"))
    assert first["name"] == "WF -> Basement"
    assert second["name"] == "Basement -> SSL"
    assert first["start_triggers"] == wf_ssl.start_triggers
    assert first["end_triggers"] == [{"type": "area_enter", "level": 6, "area": 3}]
    assert second["start_triggers"] == [{"type": "area_enter", "level": 6, "area": 3}]
    assert second["end_triggers"] == wf_ssl.end_triggers
    assert first["waypoints"] == [] and second["waypoints"] == []


def test_split_does_not_touch_the_original():
    # Non-destructive by design: definitions arm in parallel, so the whole and
    # its halves can all be armed on one play and all record. Nothing is
    # orphaned by an edit.
    wf_ssl = SegmentDef(
        id=1, name="WF -> SSL", enabled=True,
        start_triggers=[{"type": "level_exit", "from": 24}],
        end_triggers=[{"type": "level_enter", "to": 8}],
        waypoints=[[{"type": "area_enter", "level": 6, "area": 3}]],
        guards=[], match_mode="loose")
    before = dataclasses.asdict(wf_ssl)
    split_definition(wf_ssl, mid=[{"type": "area_enter", "level": 6, "area": 3}],
                     names=("a", "b"))
    assert dataclasses.asdict(wf_ssl) == before


def test_a_split_half_carries_no_seed_key():
    # A new definition derived from a seeded one is NOT that seeded row;
    # inheriting seed_key would make reconcile overwrite it at next startup.
    # (SegmentDef itself carries no seed_key field at all -- only the raw db
    # row dict does -- so there is nothing to accidentally inherit; this
    # pins the OUTPUT contract regardless.)
    wf_ssl = SegmentDef(
        id=1, name="WF -> SSL", enabled=True,
        start_triggers=[{"type": "level_exit", "from": 24}],
        end_triggers=[{"type": "level_enter", "to": 8}],
        waypoints=[[{"type": "area_enter", "level": 6, "area": 3}]],
        guards=[], match_mode="loose")
    first, second = split_definition(
        wf_ssl, mid=[{"type": "area_enter", "level": 6, "area": 3}],
        names=("a", "b"))
    assert first["seed_key"] is None and second["seed_key"] is None


def test_split_inherits_guards_default_strat_match_mode_and_enabled():
    # Deliberate design choice (see split_definition's docstring): unlike
    # merge_definitions, inheriting guards onto a split half is harmless -- a
    # half's rta can only be SHORTER than the whole's, so a time bound copied
    # from the whole can only ever be looser than necessary, never wrong in a
    # way that rejects a valid completion.
    d = SegmentDef(
        id=3, name="WF -> SSL", enabled=True,
        start_triggers=[{"type": "level_exit", "from": 24}],
        end_triggers=[{"type": "level_enter", "to": 8}],
        waypoints=[[{"type": "area_enter", "level": 6, "area": 3}]],
        guards=[{"type": "max_time", "frames": 1000}],
        default_strat="Standard", match_mode="strict")
    first, second = split_definition(
        d, mid=[{"type": "area_enter", "level": 6, "area": 3}],
        names=("WF -> Basement", "Basement -> SSL"))
    for half in (first, second):
        assert half["guards"] == [{"type": "max_time", "frames": 1000}]
        assert half["default_strat"] == "Standard"
        assert half["match_mode"] == "strict"    # inherited, not forced loose
        assert half["enabled"] is True


def test_split_refuses_when_the_first_half_is_unfireable():
    # Reuses lint.py's own proven fixture (tests/test_lint.py): exiting Hazy
    # Maze Cave (level 7) lands directly in the castle basement in ONE
    # level_changed (world_connections()['7'] == [[6, 3], [28, None]]), so a
    # def start=level_exit(from=7) / end=level_enter(to=6) arms and closes on
    # the SAME event and can never legitimately fire. Splitting anything at
    # exactly that mid point produces a first half shaped like it.
    d = SegmentDef(
        id=2, name="HMC -> somewhere", enabled=True,
        start_triggers=[{"type": "level_exit", "from": 7}],
        end_triggers=[{"type": "level_enter", "to": 8}],
        guards=[], match_mode="loose")
    with pytest.raises(ValueError, match="unfireable"):
        split_definition(d, mid=[{"type": "level_enter", "to": 6}],
                         names=("first half", "second half"))


def test_split_refuses_when_the_second_half_is_unfireable():
    # The mirror of the case above -- proves BOTH halves get checked, not
    # just the one the live report happened to name. Here the FIRST half is
    # harmless (attempt_anchor can't collide with a level_exit mid clause);
    # the SECOND half reproduces the exact HMC-exit/castle-basement collision.
    d = SegmentDef(
        id=4, name="x", enabled=True,
        start_triggers=[{"type": "attempt_anchor", "level": 26}],
        end_triggers=[{"type": "level_enter", "to": 6}],
        guards=[], match_mode="loose")
    with pytest.raises(ValueError, match="unfireable"):
        split_definition(d, mid=[{"type": "level_exit", "from": 7}],
                         names=("first half", "second half"))


def test_split_refuses_a_definition_carrying_more_than_one_waypoint():
    """Silent data loss is the failure mode here, not a wrong answer.

    split_definition folds the original's waypoints into the single shared
    boundary `mid`. With 0 or 1 that is exact — and every seeded def is one of
    those (83 with none, 1 with one). But nothing caps the count:
    validate_definition accepts any-length waypoint lists, so a user-authored
    definition can carry several, and the halves would come back missing the
    ones that were not the split point, with nothing raised and nothing said.
    Refuse instead of guessing which side each survivor belongs on.
    """
    d = SegmentDef(
        id=7, name="three-legged trip", enabled=True,
        start_triggers=[{"type": "level_exit", "from": 9}],
        end_triggers=[{"type": "level_enter", "to": 8}],
        waypoints=[[{"type": "area_enter", "level": 6, "area": 1}],
                   [{"type": "area_enter", "level": 6, "area": 3}]],
        guards=[], match_mode="loose")
    with pytest.raises(ValueError, match="waypoints"):
        split_definition(d, mid=[{"type": "area_enter", "level": 6, "area": 1}],
                         names=("first half", "second half"))


def test_split_still_accepts_the_zero_and_one_waypoint_shapes():
    """The refusal above must not have swallowed the cases that DO work —
    a guard that rejects everything passes its own negative test forever."""
    for waypoints in ([], [[{"type": "area_enter", "level": 6, "area": 1}]]):
        d = SegmentDef(
            id=8, name="WF -> SSL", enabled=True,
            start_triggers=[{"type": "level_exit", "from": 24}],
            end_triggers=[{"type": "level_enter", "to": 8}],
            waypoints=waypoints, guards=[], match_mode="loose")
        first, second = split_definition(
            d, mid=[{"type": "area_enter", "level": 6, "area": 1}],
            names=("WF -> Basement", "Basement -> SSL"))
        assert first["end_triggers"] == second["start_triggers"]


def test_merge_spans_both_and_keeps_the_seam_as_a_waypoint():
    wf_to_basement = SegmentDef(
        id=101, name="WF -> Basement", enabled=True,
        start_triggers=[{"type": "level_exit", "from": 24}],
        end_triggers=[{"type": "area_enter", "level": 6, "area": 3}],
        guards=[], match_mode="loose")
    basement_to_ssl = SegmentDef(
        id=102, name="Basement -> SSL", enabled=True,
        start_triggers=[{"type": "area_enter", "level": 6, "area": 3}],
        end_triggers=[{"type": "level_enter", "to": 8}],
        guards=[], match_mode="loose")
    merged = merge_definitions(wf_to_basement, basement_to_ssl, "WF -> SSL")
    assert merged["name"] == "WF -> SSL"
    assert merged["start_triggers"] == wf_to_basement.start_triggers
    assert merged["end_triggers"] == basement_to_ssl.end_triggers
    assert merged["waypoints"] == [basement_to_ssl.start_triggers]
    assert merged["match_mode"] == "loose"
    assert merged["seed_key"] is None
    assert merged["guards"] == []


def test_merge_does_not_touch_either_input():
    wf_to_basement = SegmentDef(
        id=101, name="WF -> Basement", enabled=True,
        start_triggers=[{"type": "level_exit", "from": 24}],
        end_triggers=[{"type": "area_enter", "level": 6, "area": 3}],
        guards=[], match_mode="loose")
    basement_to_ssl = SegmentDef(
        id=102, name="Basement -> SSL", enabled=True,
        start_triggers=[{"type": "area_enter", "level": 6, "area": 3}],
        end_triggers=[{"type": "level_enter", "to": 8}],
        guards=[], match_mode="loose")
    before_first = dataclasses.asdict(wf_to_basement)
    before_second = dataclasses.asdict(basement_to_ssl)
    merge_definitions(wf_to_basement, basement_to_ssl, "WF -> SSL")
    assert dataclasses.asdict(wf_to_basement) == before_first
    assert dataclasses.asdict(basement_to_ssl) == before_second


def test_merge_preserves_each_inputs_own_waypoints_too():
    # The general inverse of split_definition: merging two definitions that
    # are THEMSELVES already multi-step chains must not drop either one's own
    # internal waypoints -- only the new seam is added, in the middle.
    a_to_b = SegmentDef(
        id=1, name="A->B", enabled=True,
        start_triggers=[{"type": "attempt_anchor", "level": 6, "area": 1}],
        end_triggers=[{"type": "area_enter", "level": 6, "area": 3}],
        waypoints=[[{"type": "area_enter", "level": 6, "area": 2}]],
        guards=[], match_mode="loose")
    b_to_c = SegmentDef(
        id=2, name="B->C", enabled=True,
        start_triggers=[{"type": "area_enter", "level": 6, "area": 3}],
        end_triggers=[{"type": "level_enter", "to": 8}],
        waypoints=[[{"type": "warp_entered", "level": 6}]],
        guards=[], match_mode="loose")
    merged = merge_definitions(a_to_b, b_to_c, "A->C")
    assert merged["waypoints"] == [
        [{"type": "area_enter", "level": 6, "area": 2}],   # a_to_b's own step
        [{"type": "area_enter", "level": 6, "area": 3}],   # the new seam
        [{"type": "warp_entered", "level": 6}],            # b_to_c's own step
    ]


def test_merge_refuses_a_pair_that_does_not_meet():
    wf_to_basement = SegmentDef(
        id=101, name="WF -> Basement", enabled=True,
        start_triggers=[{"type": "level_exit", "from": 24}],
        end_triggers=[{"type": "area_enter", "level": 6, "area": 3}],
        guards=[], match_mode="loose")
    ddd_to_bitfs = SegmentDef(
        id=103, name="DDD -> BitFS", enabled=True,
        start_triggers=[{"type": "area_enter", "level": 26}],
        end_triggers=[{"type": "level_enter", "to": 19}],
        guards=[], match_mode="loose")
    with pytest.raises(ValueError, match="do not meet"):
        merge_definitions(wf_to_basement, ddd_to_bitfs, "nope")


def test_merge_refuses_a_pair_meeting_only_by_level_not_subarea():
    """The castle interior is ONE level (6) holding three subareas on a line
    (basement 3 <-> lobby 1 <-> upstairs 2), so a level-only meet check
    accepts seams that do not exist. This pair is drawn from the shipped
    corpus's own shape: three seeded definitions end at area_enter(6, 3) and
    one starts at area_enter(6, 2), so a merge button would offer it.
    """
    ends_in_basement = SegmentDef(
        id=201, name="WF -> Basement", enabled=True,
        start_triggers=[{"type": "level_exit", "from": 24}],
        end_triggers=[{"type": "area_enter", "level": 6, "area": 3}],
        guards=[], match_mode="loose")
    starts_upstairs = SegmentDef(
        id=202, name="Upstairs -> BitS", enabled=True,
        start_triggers=[{"type": "area_enter", "level": 6, "area": 2}],
        end_triggers=[{"type": "level_enter", "to": 21}],
        guards=[], match_mode="loose")
    with pytest.raises(ValueError, match="do not meet"):
        merge_definitions(ends_in_basement, starts_upstairs, name="nope")


def test_merge_accepts_a_pair_meeting_in_the_same_subarea():
    """The companion to the refusal above — a subarea check that rejected
    every castle seam would pass its own negative test forever, and the
    castle is where nearly every seeded movement meets."""
    ends_in_basement = SegmentDef(
        id=203, name="WF -> Basement", enabled=True,
        start_triggers=[{"type": "level_exit", "from": 24}],
        end_triggers=[{"type": "area_enter", "level": 6, "area": 3}],
        guards=[], match_mode="loose")
    starts_in_basement = SegmentDef(
        id=204, name="Basement -> HMC", enabled=True,
        start_triggers=[{"type": "area_enter", "level": 6, "area": 3}],
        end_triggers=[{"type": "level_enter", "to": 7}],
        guards=[], match_mode="loose")
    merged = merge_definitions(ends_in_basement, starts_in_basement,
                               name="WF -> HMC")
    assert merged["start_triggers"] == ends_in_basement.start_triggers
    assert merged["end_triggers"] == starts_in_basement.end_triggers


def test_merge_permits_a_seam_whose_subarea_is_unknown_on_one_side():
    """Unknown means yes, the convention can_run_from already uses at
    runtime: `level_enter to=6` pins no subarea, so it could land anywhere in
    the castle and must not be refused against an Upstairs start."""
    ends_in_castle = SegmentDef(
        id=205, name="BitDW -> Castle", enabled=True,
        start_triggers=[{"type": "level_exit", "from": 17}],
        end_triggers=[{"type": "level_enter", "to": 6}],
        guards=[], match_mode="loose")
    starts_upstairs = SegmentDef(
        id=206, name="Upstairs -> BitS", enabled=True,
        start_triggers=[{"type": "area_enter", "level": 6, "area": 2}],
        end_triggers=[{"type": "level_enter", "to": 21}],
        guards=[], match_mode="loose")
    merged = merge_definitions(ends_in_castle, starts_upstairs, name="ok")
    assert merged["waypoints"][0] == starts_upstairs.start_triggers


def test_merge_permits_an_unknown_arm_position_on_either_side():
    # "unknown means yes", the SAME convention can_run_from already takes at
    # runtime for the identical question: most seeded level_exit clauses omit
    # `to` (arm_level -> None), which must not be misread as "provably
    # unrelated" -- only a CONCRETE, non-overlapping pair is refused.
    unpinned_exit = SegmentDef(
        id=104, name="unpinned exit", enabled=True,
        start_triggers=[{"type": "attempt_anchor", "level": 6, "area": 1}],
        end_triggers=[{"type": "level_exit", "from": 24}],  # arm_level: None
        guards=[], match_mode="loose")
    ssl_entry = SegmentDef(
        id=105, name="ssl entry", enabled=True,
        start_triggers=[{"type": "level_enter", "to": 8}],
        end_triggers=[{"type": "spawned"}],
        guards=[], match_mode="loose")
    merge_definitions(unpinned_exit, ssl_entry, "ok")  # no raise


def test_merge_falls_back_to_loose_when_match_modes_disagree():
    strict_half = SegmentDef(
        id=106, name="strict half", enabled=True,
        start_triggers=[{"type": "level_exit", "from": 24}],
        end_triggers=[{"type": "area_enter", "level": 6, "area": 3}],
        guards=[], match_mode="strict")
    loose_half = SegmentDef(
        id=107, name="loose half", enabled=True,
        start_triggers=[{"type": "area_enter", "level": 6, "area": 3}],
        end_triggers=[{"type": "level_enter", "to": 8}],
        guards=[], match_mode="loose")
    merged = merge_definitions(strict_half, loose_half, "mixed")
    assert merged["match_mode"] == "loose"
    # Agreement is preserved verbatim -- proves the disagreement case above
    # isn't just "always coerce to loose" but a real fallback.
    both_strict_second = replace(strict_half, id=109,
                                 start_triggers=loose_half.start_triggers,
                                 end_triggers=loose_half.end_triggers)
    agree = merge_definitions(strict_half, both_strict_second, "agree")
    assert agree["match_mode"] == "strict"


def test_merge_default_strat_agrees_or_falls_back_to_none():
    a = SegmentDef(id=108, name="a", enabled=True,
                   start_triggers=[{"type": "level_exit", "from": 24}],
                   end_triggers=[{"type": "area_enter", "level": 6, "area": 3}],
                   guards=[], match_mode="loose", default_strat="Standard")
    b_same = SegmentDef(id=109, name="b", enabled=True,
                        start_triggers=[{"type": "area_enter", "level": 6, "area": 3}],
                        end_triggers=[{"type": "level_enter", "to": 8}],
                        guards=[], match_mode="loose", default_strat="Standard")
    b_diff = replace(b_same, default_strat="Alternate")
    assert merge_definitions(a, b_same, "x")["default_strat"] == "Standard"
    assert merge_definitions(a, b_diff, "y")["default_strat"] is None


def test_merge_requires_both_inputs_enabled():
    a = SegmentDef(id=110, name="a", enabled=True,
                   start_triggers=[{"type": "level_exit", "from": 24}],
                   end_triggers=[{"type": "area_enter", "level": 6, "area": 3}],
                   guards=[], match_mode="loose")
    b_disabled = SegmentDef(
        id=111, name="b", enabled=False,
        start_triggers=[{"type": "area_enter", "level": 6, "area": 3}],
        end_triggers=[{"type": "level_enter", "to": 8}],
        guards=[], match_mode="loose")
    assert merge_definitions(a, b_disabled, "z")["enabled"] is False


def test_step_node_reads_the_place_a_clause_leaves_mario_in():
    from sm64_events.tracking.segments import step_node
    assert step_node({"type": "level_enter", "to": 8}) == "8"
    assert step_node({"type": "level_enter", "to": 6,
                      "to_subarea": 3}) == "6:3"
    assert step_node({"type": "area_enter", "level": 6, "area": 3}) == "6:3"
    assert step_node({"type": "level_exit", "from": 22, "to": 6,
                      "to_subarea": 3}) == "6:3"


def test_step_node_answers_none_for_a_clause_that_names_no_place():
    # None = unconstrained, which is what keeps key grabs, pipe entries, star
    # grabs and reset_game out of the topological rules entirely.
    from sm64_events.tracking.segments import step_node
    assert step_node({"type": "key_grabbed", "level": 30}) is None
    assert step_node({"type": "warp_entered", "level": 17}) is None
    assert step_node({"type": "star_grabbed", "course": 2, "star": 0}) is None
    assert step_node({"type": "reset_game"}) is None
    # 52 of the 53 seeded level_exit clauses omit `to`: the definition says
    # nothing about where the player lands, so neither does this.
    assert step_node({"type": "level_exit", "from": 24}) is None


def test_settled_position_skips_the_transient_lobby():
    # Every castle entry loads the lobby (area 1) for one poll before warping
    # to the real area, all on the SAME game frame (detectors/level.py). The
    # engine must judge the LAST position of a frame, or a basement course
    # exit reads as "SSL -> Lobby", which is not an edge at all.
    e = SegmentEngine([LBLJ])
    e.feed(jev(1, "area_changed", 100,
               {"level": 8, "from": 1, "to": 1, "from_transient": False}),
           ctx(level=8))
    e.feed(jev(2, "level_changed", 200, {"from": 8, "to": 6}),
           ctx(level=6, prev_level=8))
    e.feed(jev(3, "area_changed", 200,
               {"level": 6, "from": 1, "to": 1, "from_transient": True}),
           ctx(level=6, area=1))
    e.feed(jev(4, "area_changed", 200,
               {"level": 6, "from": 1, "to": 3, "from_transient": True}),
           ctx(level=6, area=3))
    assert e._settled_node == "8"          # not judged yet: frame 200 is live
    e.feed(jev(5, "mario_acted", 205, {}), ctx(level=6, area=3))
    assert e._settled_node == "6:3"        # the basement, never the lobby


def test_a_reset_forgets_where_mario_was():
    # game_reset carries a boot-range frame and session_started restarts
    # global_timer, so a remembered node from before either would be compared
    # against a frame number that means nothing.
    e = SegmentEngine([LBLJ])
    e.feed(jev(1, "area_changed", 100,
               {"level": 8, "from": 1, "to": 1, "from_transient": False}),
           ctx(level=8))
    e.feed(jev(2, "mario_acted", 105, {}), ctx(level=8))
    assert e._settled_node == "8"
    e.feed(jev(3, "game_reset", 20, {}), ctx(level=8))
    assert e._settled_node is None and e._pending_move is None


# Mirrors the shipped seed row seg:wf->ssl: loose, no waypoints, exits WF and
# ends on entering SSL.
WF_SSL = SegmentDef(id=70, name="WF -> SSL", enabled=True,
                    start_triggers=[{"type": "level_exit", "from": 24}],
                    end_triggers=[{"type": "level_enter", "to": 8}],
                    waypoints=[], guards=[], match_mode="loose")


def _exit_wf_into_the_lobby(e):
    """Arm WF -> SSL the way the real journal does: a level edge into the
    castle plus its co-frame establishing area_changed, then an event on a
    later frame so the move is judged."""
    e.feed(jev(1, "level_changed", 1000, {"from": 24, "to": 6}),
           ctx(level=6, prev_level=24))
    e.feed(jev(2, "area_changed", 1000,
               {"level": 6, "from": 1, "to": 1, "from_transient": True}),
           ctx(level=6, area=1))
    e.feed(jev(3, "mario_acted", 1005, {}), ctx(level=6, area=1))


def test_a_warp_into_an_unreachable_place_cancels_an_armed_segment():
    # Live report 2026-08-01: standing in the Bowser 1 arena, WF -> SSL read
    # as ACTIVE SEGMENT. There is no walk from the castle lobby to that arena
    # -- it is reached only through BitDW's pipe -- so the movement WF -> SSL
    # was measuring cannot still be under way.
    e = SegmentEngine([WF_SSL])
    _exit_wf_into_the_lobby(e)
    assert e.armed_ids() == {70}
    e.feed(jev(4, "level_changed", 2000, {"from": 6, "to": 30}),
           ctx(level=30, prev_level=6))
    e.feed(jev(5, "area_changed", 2000,
               {"level": 30, "from": 1, "to": 1, "from_transient": True}),
           ctx(level=30, area=1))
    closed, notices = e.feed(jev(6, "mario_acted", 2005, {}), ctx(level=30))
    assert e.armed_ids() == set()
    assert closed == []          # the movement never happened: no row
    assert [n["event"] for n in notices] == ["segment_disarmed"]


def test_a_cancel_lands_on_the_clock_with_no_event_to_carry_it():
    """Live report 2026-08-02: he entered Bowser in the Sky from Upstairs with
    `Bowser 2 → WDW` armed, and the selector kept offering it while the card
    called it ACTIVE SEGMENT. The rule was right and the DELIVERY was late —
    `tools/why_cancelled.py` on his own session dated the verdict **832 frames
    (27.7 s)** after the move, and the UI log has the chip on screen for 27.9 s.
    The one-frame defer only advanced when the journal got another event, and
    standing still inside a course journals nothing.

    So the frame itself must be able to deliver it: `settle(frame)`, fed by the
    poller's clock. Nothing else changes — same verdict, same silence (no row).
    """
    e = SegmentEngine([WF_SSL])
    _exit_wf_into_the_lobby(e)
    e.feed(jev(4, "level_changed", 2000, {"from": 6, "to": 30}),
           ctx(level=30, prev_level=6))
    e.feed(jev(5, "area_changed", 2000,
               {"level": 30, "from": 1, "to": 1, "from_transient": True}),
           ctx(level=30, area=1))
    assert e.armed_ids() == {70}      # frame 2000 is still live: not judged yet
    notices = e.settle(2001)          # the clock, with no sixth event
    assert e.armed_ids() == set()
    assert [n["event"] for n in notices] == ["segment_disarmed"]
    # And the defer still holds: settling ON the move's own frame judges nothing,
    # which is what protects the transient lobby.
    again = SegmentEngine([WF_SSL])
    _exit_wf_into_the_lobby(again)
    again.feed(jev(4, "level_changed", 2000, {"from": 6, "to": 30}),
               ctx(level=30, prev_level=6))
    again.feed(jev(5, "area_changed", 2000,
                   {"level": 30, "from": 1, "to": 1, "from_transient": True}),
               ctx(level=30, area=1))
    assert again.settle(2000) == [] and again.armed_ids() == {70}


def test_a_segment_armed_at_the_warp_destination_survives_that_warp():
    # Warping somewhere to practise is the normal loop. The judgement lands a
    # frame after the move, so without the arm-postdates-move exemption the
    # warp into an arena would cancel the very fight it just armed.
    e = SegmentEngine([B3])
    e.feed(jev(1, "area_changed", 1000,
               {"level": 8, "from": 1, "to": 1, "from_transient": False}),
           ctx(level=8))
    e.feed(jev(2, "mario_acted", 1005, {}), ctx(level=8))
    e.feed(jev(3, "level_changed", 2000, {"from": 8, "to": 34}),
           ctx(level=34, prev_level=8))
    e.feed(jev(4, "area_changed", 2000,
               {"level": 34, "from": 1, "to": 1, "from_transient": True}),
           ctx(level=34, area=1))
    assert e.armed_ids() == {10}
    e.feed(jev(5, "mario_acted", 2005, {}), ctx(level=34))
    assert e.armed_ids() == {10}


def test_a_normal_walk_cancels_nothing():
    e = SegmentEngine([WF_SSL])
    _exit_wf_into_the_lobby(e)
    e.feed(jev(4, "area_changed", 2000,
               {"level": 6, "from": 1, "to": 3, "from_transient": False}),
           ctx(level=6, area=3))
    e.feed(jev(5, "mario_acted", 2005, {}), ctx(level=6, area=3))
    assert e.armed_ids() == {70}


# Mirrors the user-created LLL -> HMC, but LOOSE. A STRICT def is already
# silently disarmed by any level_changed matching neither its start nor its
# end, so loose is where this rule earns its keep -- and loose is what all 56
# seeded castle movements are.
LLL_HMC = SegmentDef(
    id=71, name="LLL -> HMC", enabled=True,
    start_triggers=[{"type": "level_exit", "from": 22, "to": 6,
                     "to_subarea": 3}],
    end_triggers=[{"type": "level_enter", "to": 7, "from": 6,
                   "from_subarea": 3}],
    waypoints=[], guards=[], match_mode="loose")


def _exit_lll_into_the_basement(e):
    e.feed(jev(1, "level_changed", 1000, {"from": 22, "to": 6}),
           ctx(level=6, prev_level=22))
    e.feed(jev(2, "area_changed", 1000,
               {"level": 6, "from": 1, "to": 1, "from_transient": True}),
           ctx(level=6, area=1))
    e.feed(jev(3, "area_changed", 1000,
               {"level": 6, "from": 1, "to": 3, "from_transient": True}),
           ctx(level=6, area=3))
    e.feed(jev(4, "mario_acted", 1005, {}), ctx(level=6, area=3))


def _walk_back_into_lll(e):
    e.feed(jev(5, "level_changed", 2000, {"from": 6, "to": 22}),
           ctx(level=22, prev_level=6))
    e.feed(jev(6, "area_changed", 2000,
               {"level": 22, "from": 1, "to": 1, "from_transient": True}),
           ctx(level=22, area=1))
    return e.feed(jev(7, "mario_acted", 2005, {}), ctx(level=22))


def test_walking_back_into_the_place_you_left_cancels_the_segment():
    # Live report 2026-08-01: standing INSIDE LLL, LLL -> HMC read as ACTIVE
    # SEGMENT, "Waiting for Enter Hazy Maze Cave". Basement -> LLL is a
    # perfectly legal edge, so Rule 1 waves it through; what kills it is that
    # HMC went from 1 hop away to 2.
    e = SegmentEngine([LLL_HMC])
    _exit_lll_into_the_basement(e)
    assert e.armed_ids() == {71}
    closed, _ = _walk_back_into_lll(e)
    assert e.armed_ids() == set()
    assert closed == []


def test_a_declared_re_entry_is_progress_not_a_wrong_turn():
    # Griffin's nuance (2026-08-01): sometimes you genuinely enter a stage to
    # use its exit. A route that really does go back through somewhere
    # DECLARES it as a step, and a node the definition names is never a wrong
    # turn.
    declared = replace(LLL_HMC, id=72,
                       waypoints=[[{"type": "level_enter", "to": 22}]])
    e = SegmentEngine([declared])
    _exit_lll_into_the_basement(e)
    assert e.armed_ids() == {72}
    _walk_back_into_lll(e)
    assert e.armed_ids() == {72}


def test_a_route_ending_on_a_grab_is_never_cancelled_for_moving():
    # A loose multi-step route -- upstairs, into BitS, down the pipe to the
    # arena, ending on the key grab. Two exemptions carry it, and neither is a
    # special case: entering BitS is a DECLARED node (its own waypoint), and
    # once that waypoint is consumed the end trigger names no place at all, so
    # the arena hop is unconstrained.
    #
    # Regression guard rather than a mutation-proved rule: the behaviour it
    # rests on -- step_node answering None for a placeless clause -- is proved
    # by test_step_node_answers_none_for_a_clause_that_names_no_place, and the
    # declared-node half is proved by test_a_declared_re_entry_is_progress...
    to_bowser_3 = SegmentDef(
        id=74, name="Upstairs -> Bowser 3", enabled=True,
        start_triggers=[{"type": "area_enter", "level": 6, "area": 2}],
        waypoints=[[{"type": "level_enter", "to": 21}]],
        end_triggers=[{"type": "key_grabbed", "level": 34}],
        guards=[], match_mode="loose")
    e = SegmentEngine([to_bowser_3])
    e.feed(jev(1, "area_changed", 900,
               {"level": 6, "from": 1, "to": 1, "from_transient": False}),
           ctx(level=6, area=1))
    e.feed(jev(2, "mario_acted", 905, {}), ctx(level=6, area=1))
    e.feed(jev(3, "area_changed", 950,
               {"level": 6, "from": 1, "to": 2, "from_transient": False}),
           ctx(level=6, area=2))
    assert e.armed_ids() == {74}
    e.feed(jev(4, "mario_acted", 955, {}), ctx(level=6, area=2))
    # up into BitS: a declared waypoint
    e.feed(jev(5, "level_changed", 1000, {"from": 6, "to": 21}),
           ctx(level=21, prev_level=6))
    e.feed(jev(6, "area_changed", 1000,
               {"level": 21, "from": 1, "to": 1, "from_transient": True}),
           ctx(level=21, area=1))
    e.feed(jev(7, "mario_acted", 1005, {}), ctx(level=21))
    assert e.armed_ids() == {74}
    # down the pipe into the arena: undeclared, and the end names no place
    e.feed(jev(8, "level_changed", 2000, {"from": 21, "to": 34}),
           ctx(level=34, prev_level=21))
    e.feed(jev(9, "area_changed", 2000,
               {"level": 34, "from": 1, "to": 1, "from_transient": True}),
           ctx(level=34, area=1))
    e.feed(jev(10, "mario_acted", 2005, {}), ctx(level=34))
    assert e.armed_ids() == {74}


def test_an_armed_segment_survives_the_transient_lobby_on_a_course_exit():
    # The case that would break everything: a basement course exit reads
    # SSL -> lobby -> basement in the raw stream. Judged raw, "SSL -> Lobby"
    # is not even an edge, and for an UPSTAIRS destination the lobby is closer
    # than the basement (2 hops vs 3) -- so both rules would fire on a move
    # that never happened.
    ssl_to_sl = SegmentDef(
        id=73, name="SSL -> SL", enabled=True,
        start_triggers=[{"type": "spawned", "level": 8}],
        end_triggers=[{"type": "level_enter", "to": 10}],
        waypoints=[], guards=[], match_mode="loose")
    e = SegmentEngine([ssl_to_sl])
    e.feed(jev(1, "area_changed", 900,
               {"level": 8, "from": 1, "to": 1, "from_transient": False}),
           ctx(level=8))
    e.feed(jev(2, "mario_acted", 905, {}), ctx(level=8))
    e.feed(jev(3, "spawned", 950, {"level": 8, "kind": "spawn"}), ctx(level=8))
    assert e.armed_ids() == {73}
    e.feed(jev(4, "level_changed", 1000, {"from": 8, "to": 6}),
           ctx(level=6, prev_level=8))
    e.feed(jev(5, "area_changed", 1000,
               {"level": 6, "from": 1, "to": 1, "from_transient": True}),
           ctx(level=6, area=1))
    e.feed(jev(6, "area_changed", 1000,
               {"level": 6, "from": 1, "to": 3, "from_transient": True}),
           ctx(level=6, area=3))
    e.feed(jev(7, "mario_acted", 1005, {}), ctx(level=6, area=3))
    assert e.armed_ids() == {73}


# Mirrors seg:bowser-1->wf: loose, exits the Bowser 1 arena (which lands in the
# lobby), ends on entering WF.
B1_WF = SegmentDef(id=75, name="Bowser 1 -> WF", enabled=True,
                   start_triggers=[{"type": "level_exit", "from": 30}],
                   end_triggers=[{"type": "level_enter", "to": 24}],
                   waypoints=[], guards=[], match_mode="loose")


def _bowser_1_exit_then_detour_to_bitdw(e):
    """Arm B1 -> WF by exiting the arena into the lobby, then warp to BitDW --
    a legal edge, but 2 hops from WF where the lobby was 1, so Rule 2 cancels."""
    e.feed(jev(1, "area_changed", 900,
               {"level": 30, "from": 1, "to": 1, "from_transient": False}),
           ctx(level=30))
    e.feed(jev(2, "mario_acted", 905, {}), ctx(level=30))
    e.feed(jev(3, "level_changed", 1000, {"from": 30, "to": 6}),
           ctx(level=6, prev_level=30))
    e.feed(jev(4, "area_changed", 1000,
               {"level": 6, "from": 1, "to": 1, "from_transient": True}),
           ctx(level=6, area=1))
    e.feed(jev(5, "mario_acted", 1005, {}), ctx(level=6, area=1))
    assert e.armed_ids() == {75}
    e.feed(jev(6, "level_changed", 2000, {"from": 6, "to": 17}),
           ctx(level=17, prev_level=6))
    e.feed(jev(7, "area_changed", 2000,
               {"level": 17, "from": 1, "to": 1, "from_transient": True}),
           ctx(level=17, area=1))
    e.feed(jev(8, "mario_acted", 2005, {}), ctx(level=17))
    assert e.armed_ids() == set()          # cancelled by the detour


def _walk_back_to_the_lobby(e):
    e.feed(jev(9, "level_changed", 3000, {"from": 17, "to": 6}),
           ctx(level=6, prev_level=17))
    e.feed(jev(10, "area_changed", 3000,
               {"level": 6, "from": 1, "to": 1, "from_transient": True}),
           ctx(level=6, area=1))
    e.feed(jev(11, "mario_acted", 3005, {}), ctx(level=6, area=1))


def test_a_reset_at_the_start_position_brings_a_cancelled_segment_back():
    # Measured case, journal ids 17926-17940 (tools/measure_topology_cancels):
    # armed by a Bowser 1 exit into the lobby, he warped to BitDW for 7s, came
    # back to the lobby, pressed reset AT THE ARM POSITION and ran lobby -> WF
    # in 16s. Redoing the start trigger means redoing the whole fight, so this
    # reset IS how the movement is re-run.
    e = SegmentEngine([B1_WF])
    _bowser_1_exit_then_detour_to_bitdw(e)
    _walk_back_to_the_lobby(e)
    _, notices = e.feed(jev(12, "practice_reset", 3100, {}),
                        ctx(level=6, area=1))
    assert e.armed_ids() == {75}
    assert e.armed_items()[75].start_frame == 3100
    assert [n["event"] for n in notices] == ["segment_armed"]


def test_a_reset_somewhere_else_forfeits_the_comeback_for_good():
    # Griffin 2026-08-01: "if... in the middle of lobby -> wf, I decided to
    # reset to bitdw, I think that's a genuine kill of the segment, because
    # we've now gone out of order... until I get back to Bowser 1 and trigger
    # it from the beginning again."
    e = SegmentEngine([B1_WF])
    _bowser_1_exit_then_detour_to_bitdw(e)
    _walk_back_to_the_lobby(e)
    e.feed(jev(12, "practice_reset", 3100, {}), ctx(level=17))   # reset to BitDW
    assert e.armed_ids() == set()
    # ...and coming back to the lobby and resetting there no longer helps.
    e.feed(jev(13, "practice_reset", 3200, {}), ctx(level=6, area=1))
    assert e.armed_ids() == set()


def test_the_comeback_expires_with_the_staleness_budget():
    # A cancelled arm has no cancel rules left to bound it, so without a clock
    # a movement killed hours ago would re-arm the next time he happened to
    # reset in the same room.
    from sm64_events.tracking.segments import MIN_BUDGET_FRAMES
    e = SegmentEngine([B1_WF])
    _bowser_1_exit_then_detour_to_bitdw(e)
    _walk_back_to_the_lobby(e)
    e.feed(jev(12, "practice_reset", 2005 + MIN_BUDGET_FRAMES + 1, {}),
           ctx(level=6, area=1))
    assert e.armed_ids() == set()


# ---------------------------------------------------------------------------
# path_nodes -- a definition's steps read as an ORDER (spec
# 2026-08-02-strict-path-segments). Second reader of the same data
# declared_nodes reads as a set; see that docstring for why the set was right
# for ITS job and cannot express repetition or direction.
# ---------------------------------------------------------------------------

def _pathdef(waypoints, end, **kw):
    return SegmentDef(id=900, name="path probe", enabled=True,
                      start_triggers=[{"type": "level_exit", "from": 24}],
                      end_triggers=end, waypoints=waypoints, guards=[], **kw)


def test_path_nodes_reads_waypoints_then_the_end_in_order():
    d = _pathdef([[{"type": "area_enter", "level": 6, "area": 3}]],
                 [{"type": "level_enter", "to": 8}])
    assert segments_module.path_nodes(d) == ("6:3", "8")


def test_path_nodes_of_a_waypointless_def_is_just_its_end():
    # This is what tightens the 39 movements that declare no step today: the
    # end trigger is itself a declared place, so the FIRST move after arming
    # is already judged.
    d = _pathdef([], [{"type": "level_enter", "to": 8}])
    assert segments_module.path_nodes(d) == ("8",)


def test_path_nodes_skips_a_step_that_names_no_place():
    # Contributions are SKIPPED, not padded with None -- the cursor must never
    # have to step over a hole.
    d = _pathdef([[{"type": "star_grabbed", "course": 8, "star": 0}]],
                 [{"type": "level_enter", "to": 8}])
    assert segments_module.path_nodes(d) == ("8",)


def test_path_nodes_skips_an_any_of_step_whose_members_disagree():
    # Any-of means "either is fine" and a cursor cannot hold two positions, so
    # the step declines to constrain -- the unknown-means-yes convention this
    # engine takes everywhere.
    d = _pathdef([[{"type": "level_enter", "to": 8},
                   {"type": "level_enter", "to": 22}]],
                 [{"type": "level_enter", "to": 10}])
    assert segments_module.path_nodes(d) == ("10",)


def test_path_nodes_holds_a_repeated_place_twice():
    # THE case declared_nodes structurally cannot express: SSL -> SSL -> LLL.
    # Named as a difference from the set rather than restated as a literal.
    once = _pathdef([[{"type": "level_enter", "to": 8}],
                     [{"type": "area_enter", "level": 6, "area": 3}]],
                    [{"type": "level_enter", "to": 22}])
    assert len(set(segments_module.path_nodes(once))) == len(
        segments_module.path_nodes(once))
    twice = _pathdef([[{"type": "level_enter", "to": 8}],
                      [{"type": "level_enter", "to": 8}]],
                     [{"type": "level_enter", "to": 22}])
    path = segments_module.path_nodes(twice)
    assert len(path) == 3
    assert [i for i, node in enumerate(path) if node == "8"] == [0, 1]
    assert len(set(path)) < len(path)
    assert len(segments_module.declared_nodes(twice)) < len(path)


def test_path_nodes_of_the_hundred_coin_family_is_empty():
    # Built from the SHIPPED seed rather than a hand-made lookalike, so this
    # tracks the corpus instead of a guess about it. An empty path is what
    # keeps all 15 out of the cursor rule with no exemption list.
    import json

    from sm64_events.core.paths import bundled_defaults_seed
    seed = json.loads(bundled_defaults_seed().read_bytes().decode("utf-8"))
    rows = [r for r in seed["segments"]
            if hundred_coin_entity(r["start_triggers"], r["waypoints"])]
    assert rows, "the seed no longer carries a 100-coin family"
    for row in rows:
        d = SegmentDef(id=901, name=row["name"], enabled=True,
                       start_triggers=row["start_triggers"],
                       end_triggers=row["end_triggers"],
                       waypoints=row["waypoints"], guards=row["guards"])
        assert segments_module.path_nodes(d) == ()


# ---------------------------------------------------------------------------
# The path cursor (spec 2026-08-02-strict-path-segments). A deliberate
# shortcut and a runner's mistake are observationally equivalent -- entering
# BitFS during `Bowser 2 -> Upstairs` is the same move whether it is the
# fastest route or a wrong turn. No measurement separates them; only a
# DECLARATION does. Rule 2's hop arithmetic stays for `loose` definitions.
# ---------------------------------------------------------------------------

def _seed_rows():
    import json

    from sm64_events.core.paths import bundled_defaults_seed
    return json.loads(
        bundled_defaults_seed().read_bytes().decode("utf-8"))["segments"]


def _seed_def(row, id):
    return SegmentDef(id=id, name=row["name"], enabled=True,
                      start_triggers=row["start_triggers"],
                      end_triggers=row["end_triggers"],
                      waypoints=row["waypoints"], guards=row["guards"],
                      match_mode=row.get("match_mode", "strict"))


# WF -> SSL as the spec authors it: the Lobby is where the exit PUT him (the
# cursor's implicit start), so only the Basement is declared.
WF_SSL_STRICT = replace(WF_SSL, id=80, match_mode="strict",
                        waypoints=[[{"type": "area_enter",
                                     "level": 6, "area": 3}]])


def _walk(e, jid, frame, node_level, node_area=None, prev_level=None):
    """One settled position change: the level edge (when there is one), its
    co-frame establishing area_changed, then an event on a LATER frame so the
    move is judged -- the shape the real detectors emit.

    Returns everything the three feeds produced, not just the last: an END
    trigger fires on the move's OWN event while the cursor is judged a frame
    later, so a walk that both closes one definition and advances another
    reports both.
    """
    closed, notices = [], []
    events = []
    if prev_level is not None:
        events.append((jev(jid, "level_changed", frame,
                           {"from": prev_level, "to": node_level}),
                       ctx(level=node_level, prev_level=prev_level)))
    events.append((jev(jid + 1, "area_changed", frame,
                       {"level": node_level, "from": 1, "to": node_area or 1,
                        # transient only when a LEVEL entry put him here;
                        # walking between two castle areas is a real move, and
                        # the echo guards read that flag.
                        "from_transient": prev_level is not None}),
                   ctx(level=node_level, area=node_area)))
    events.append((jev(jid + 2, "mario_acted", frame + 5, {}),
                   ctx(level=node_level, area=node_area)))
    for ev, context in events:
        step_closed, step_notices = e.feed(ev, context)
        closed.extend(step_closed)
        notices.extend(step_notices)
    return closed, notices


def test_a_strict_movement_advances_its_cursor_through_a_declared_stop():
    e = SegmentEngine([WF_SSL_STRICT])
    _exit_wf_into_the_lobby(e)
    assert e.armed_ids() == {80}
    _walk(e, 10, 2000, 6, 3)                       # lobby -> basement
    assert e.armed_items()[80].path_index == 1
    closed, _ = e.feed(jev(20, "level_changed", 3000, {"from": 6, "to": 8}),
                       ctx(level=8, prev_level=6))
    assert [a.outcome for a in closed] == ["success"]


def test_a_strict_movement_cancels_on_a_castle_area_that_is_not_its_next_step():
    # The castle's Lobby / Basement / Upstairs are AREAS, so wandering between
    # them was invisible to `_feed_waypoint` and only the hop arithmetic this
    # rule replaces ever caught it.
    e = SegmentEngine([WF_SSL_STRICT])
    _exit_wf_into_the_lobby(e)
    closed, notices = _walk(e, 10, 2000, 6, 2)     # lobby -> UPSTAIRS
    assert e.armed_ids() == set()
    assert closed == []                            # never happened: no row
    assert [n["event"] for n in notices] == ["segment_disarmed"]


# Declares the Lobby as a real mid-route stop, which is what makes walking
# BACK to it a deviation rather than an exempt re-entry: BBH exits to the
# Courtyard, so the Lobby is somewhere he passes THROUGH.
#
# The Lobby step is a subarea-pinned `level_enter`, NOT an `area_enter`:
# arriving from the Courtyard is a LEVEL edge (26 -> 6), and `can_run_from`
# rule (A) refuses to arm a definition whose next required step is an
# `area_enter` in the castle while Mario stands outside it. Both resolve to
# the same node through `step_node`, which is what the cursor reads.
BBH_SSL_STRICT = SegmentDef(
    id=81, name="BBH -> SSL", enabled=True,
    start_triggers=[{"type": "level_exit", "from": 4}],
    waypoints=[[{"type": "level_enter", "to": 6, "to_subarea": 1}],
               [{"type": "area_enter", "level": 6, "area": 3}]],
    end_triggers=[{"type": "level_enter", "to": 8}],
    guards=[], match_mode="strict")


def test_a_strict_movement_cancels_on_walking_back_the_way_it_came():
    # Griffin 2026-08-02: "if I am doing WF -> Basement -> SSL, I would never
    # go from Basement back to Lobby... then I'm trying to practice something
    # else". A SET answers the same in both directions, and the Lobby is a
    # declared member here -- so this is the case only a cursor can judge.
    e = SegmentEngine([BBH_SSL_STRICT])
    e.feed(jev(1, "level_changed", 1000, {"from": 4, "to": 26}),
           ctx(level=26, prev_level=4))
    e.feed(jev(2, "area_changed", 1000,
               {"level": 26, "from": 1, "to": 1, "from_transient": True}),
           ctx(level=26, area=1))
    e.feed(jev(3, "mario_acted", 1005, {}), ctx(level=26, area=1))
    assert e.armed_ids() == {81}
    _walk(e, 10, 2000, 6, 1, prev_level=26)        # courtyard -> lobby
    assert e.armed_items()[81].path_index == 1
    _walk(e, 20, 3000, 6, 3)                       # lobby -> basement
    assert e.armed_items()[81].path_index == 2
    closed, _ = _walk(e, 30, 4000, 6, 1)           # BACK to the lobby
    assert e.armed_ids() == set()
    assert closed == []


def test_a_strict_movement_matches_a_place_it_declares_twice():
    # Griffin 2026-08-02: "If I, for some reason, want to leave SSL, go back
    # into SSL, leave SSL, and go to LLL, I should be able to define a path
    # that specifically matches that order."
    # Started off a WF exit rather than off the Basement it keeps returning
    # to: a start trigger that also matches a step of its own route re-arms on
    # that step and resets the cursor (the authoring caveat in this module's
    # docstring, and the reason the corpus splits such movements).
    ssl_ssl_lll = SegmentDef(
        id=82, name="WF -> SSL -> SSL -> LLL", enabled=True,
        start_triggers=[{"type": "level_exit", "from": 24}],
        waypoints=[[{"type": "area_enter", "level": 6, "area": 3}],
                   [{"type": "level_enter", "to": 8}],
                   [{"type": "level_enter", "to": 6, "to_subarea": 3}],
                   [{"type": "level_enter", "to": 8}],
                   [{"type": "level_enter", "to": 6, "to_subarea": 3}]],
        end_triggers=[{"type": "level_enter", "to": 22}],
        guards=[], match_mode="strict")
    assert segments_module.path_nodes(ssl_ssl_lll).count("8") == 2
    assert segments_module.path_nodes(ssl_ssl_lll).count("6:3") == 3
    e = SegmentEngine([ssl_ssl_lll])
    _exit_wf_into_the_lobby(e)
    assert e.armed_ids() == {82}
    _walk(e, 10, 2000, 6, 3)                       # lobby -> basement
    _walk(e, 20, 3000, 8, prev_level=6)            # into SSL
    _walk(e, 30, 4000, 6, 3, prev_level=8)         # out again
    _walk(e, 40, 5000, 8, prev_level=6)            # and back in
    _walk(e, 50, 6000, 6, 3, prev_level=8)         # and out
    assert e.armed_items()[82].path_index == 5
    closed, _ = e.feed(jev(60, "level_changed", 7000, {"from": 6, "to": 22}),
                       ctx(level=22, prev_level=6))
    assert [a.outcome for a in closed] == ["success"]


def test_a_course_interior_area_change_never_moves_the_cursor():
    # topology.node_for collapses a course's own subareas to the bare level,
    # so SSL's pyramid and LLL's volcano are invisible to the rule -- which is
    # what keeps it out of the 100-coin family STRUCTURALLY, not by exemption.
    ssl_lll = SegmentDef(
        id=83, name="SSL -> LLL", enabled=True,
        start_triggers=[{"type": "area_enter", "level": 6, "area": 3}],
        waypoints=[[{"type": "level_enter", "to": 8}]],
        end_triggers=[{"type": "level_enter", "to": 22}],
        guards=[], match_mode="strict")
    e = SegmentEngine([ssl_lll])
    e.feed(jev(1, "area_changed", 1000,
               {"level": 6, "from": 1, "to": 3, "from_transient": False}),
           ctx(level=6, area=3))
    e.feed(jev(2, "mario_acted", 1005, {}), ctx(level=6, area=3))
    _walk(e, 10, 2000, 8, prev_level=6)
    assert e.armed_items()[83].path_index == 1
    _walk(e, 20, 3000, 8, 2)                       # into the pyramid
    assert e.armed_ids() == {83}
    assert e.armed_items()[83].path_index == 1


def test_a_definition_declaring_no_place_is_never_cancelled_by_one():
    # The shipped 100-coin family: every step is a star grab, so path_nodes is
    # empty and the cursor has nothing to say from the very first move.
    row = next(r for r in _seed_rows()
               if hundred_coin_entity(r["start_triggers"], r["waypoints"])
               and any(c.get("to") == 22 for c in r["start_triggers"]))
    d = _seed_def(row, 84)
    assert segments_module.path_nodes(d) == ()
    e = SegmentEngine([d])
    e.feed(jev(1, "level_changed", 1000, {"from": 6, "to": 22}),
           ctx(level=22, prev_level=6))
    e.feed(jev(2, "area_changed", 1000,
               {"level": 22, "from": 1, "to": 1, "from_transient": True}),
           ctx(level=22, area=1))
    e.feed(jev(3, "mario_acted", 1005, {}), ctx(level=22, area=1))
    assert e.armed_ids() == {84}
    _walk(e, 10, 2000, 22, 2)                      # into the volcano
    _walk(e, 20, 3000, 22, 1)                      # back out
    assert e.armed_ids() == {84}


def test_a_loose_movement_still_takes_the_hop_rule():
    # THE regression guard on the split. One definition, one walk, two modes:
    # loose survives moving to the Basement (it got CLOSER to SSL), strict
    # does not (the Basement is not a step it declared). That difference is
    # exactly why `seg:wf->ssl` gains a `via` when the corpus flips.
    loose = SegmentEngine([WF_SSL])                # no waypoints, loose
    _exit_wf_into_the_lobby(loose)
    _walk(loose, 10, 2000, 6, 3)
    assert loose.armed_ids() == {70}
    strict = SegmentEngine([replace(WF_SSL, id=85, match_mode="strict")])
    _exit_wf_into_the_lobby(strict)
    _walk(strict, 10, 2000, 6, 3)
    assert strict.armed_ids() == set()


def test_one_exit_arms_several_movements_and_walking_prunes_them():
    # Multiple hypothesis tracking, against the SHIPPED corpus: keep every
    # candidate alive and let the walk eliminate them. Five movements arm on
    # one Whomp's Fortress exit; four end off the Lobby and die at the
    # Basement; WF -> SSL is the one left standing.
    rows = [r for r in _seed_rows()
            if any(c.get("type") == "level_exit" and c.get("from") == 24
                   for c in r["start_triggers"])]
    assert len(rows) == 5
    defs = [_seed_def(row, 100 + i) for i, row in enumerate(rows)]
    ssl_id = next(d.id for d in defs if d.end_triggers[0].get("to") == 8)
    e = SegmentEngine(defs)
    _exit_wf_into_the_lobby(e)
    assert len(e.armed_ids()) == 5
    _walk(e, 10, 2000, 6, 3)                       # lobby -> basement
    assert e.armed_ids() == {ssl_id}
    closed, _ = e.feed(jev(20, "level_changed", 3000, {"from": 6, "to": 8}),
                       ctx(level=8, prev_level=6))
    assert [a.outcome for a in closed] == ["success"]


def test_a_shorter_movement_ending_where_a_longer_one_passes_through_records():
    # Two definitions off one exit, sharing no state: arriving at the Basement
    # fires the short one's END and advances the long one's CURSOR. Both bank
    # a time; neither is arbitrated away. No shipped pair has this shape today,
    # which is exactly why nothing else guards it.
    short = SegmentDef(id=86, name="WF -> Basement", enabled=True,
                       start_triggers=[{"type": "level_exit", "from": 24}],
                       end_triggers=[{"type": "area_enter",
                                      "level": 6, "area": 3}],
                       waypoints=[], guards=[], match_mode="strict")
    e = SegmentEngine([short, replace(WF_SSL_STRICT, id=87)])
    _exit_wf_into_the_lobby(e)
    assert e.armed_ids() == {86, 87}
    closed, _ = _walk(e, 10, 2000, 6, 3)
    assert [(a.segment_id, a.outcome) for a in closed] == [(86, "success")]
    assert e.armed_ids() == {87}
    assert e.armed_items()[87].path_index == 1
    closed, _ = e.feed(jev(20, "level_changed", 3000, {"from": 6, "to": 8}),
                       ctx(level=8, prev_level=6))
    assert [(a.segment_id, a.outcome) for a in closed] == [(87, "success")]


# ---------------------------------------------------------------------------
# The step TRACK: the whole route on the card, and the notice that gets a
# cursor move onto the screen (live report 2026-08-02, WF -> SSL).
# ---------------------------------------------------------------------------

def test_step_labels_are_the_places_the_route_passes_through():
    assert card_step_labels(WF_SSL_STRICT) == ["Basement", "SSL"]


def test_a_definition_with_no_waypoints_is_a_one_step_route():
    assert card_step_labels(WF_SSL) == ["SSL"]


def test_every_step_of_a_four_step_route_is_labelled_in_order():
    long_way = SegmentDef(
        id=90, name="Bowser 2 -> BitS", enabled=True,
        start_triggers=[{"type": "level_exit", "from": 33}],
        end_triggers=[{"type": "level_enter", "to": 21}],
        waypoints=[[{"type": "level_enter", "to": 19}],
                   [{"type": "area_enter", "level": 6, "area": 1}],
                   [{"type": "area_enter", "level": 6, "area": 2}]],
        guards=[], match_mode="strict")
    assert card_step_labels(long_way) == ["BitFS", "Lobby", "Upstairs", "BitS"]


def test_an_any_of_clause_set_over_one_courses_stars_reads_as_any_star():
    # The 100-coin exit's shape, and the ONLY multi-member clause set in the
    # shipped corpus: six star_grabbed alternatives meaning "leave with
    # anything". Six star names cannot go on a one-line track, and the rule is
    # written about the clauses (same type, same course) rather than about
    # that family, so a hand-authored def of the same shape reads the same.
    hundred = SegmentDef(
        id=91, name="SSL -- 100 Coins -> Exit", enabled=True,
        start_triggers=[{"type": "level_enter", "to": 8}],
        end_triggers=[{"type": "star_grabbed", "course": 8, "star": s}
                      for s in range(6)],
        waypoints=[[{"type": "star_grabbed", "course": 8, "star": 6}]],
        guards=[], match_mode="strict")
    assert card_step_labels(hundred) == ["100 Coins", "Any star"]


def test_a_placeless_step_falls_back_to_the_registrys_own_chip_noun():
    pipe = SegmentDef(
        id=92, name="BitDW Pipe Entry", enabled=True,
        start_triggers=[{"type": "level_enter", "to": 17}],
        end_triggers=[{"type": "warp_entered", "level": 17}],
        waypoints=[], guards=[], match_mode="strict")
    assert card_step_labels(pipe) == ["Pipe"]


def test_advancing_a_step_says_so_out_loud():
    """THE BUG: the cursor moved to step 2 the frame he entered the Basement
    and the card read step 1 for the next 77 seconds, because a cursor move
    journals nothing and no broadcast carried it. Mutation proof: drop the
    `_progress_notices` call at the bottom of `feed` and this goes red while
    every other segment test stays green -- the engine was always right."""
    e = SegmentEngine([WF_SSL_STRICT])
    _exit_wf_into_the_lobby(e)
    assert e.armed_items()[80].path_index == 0
    _, notices = _walk(e, 10, 2000, 6, 3)          # lobby -> basement
    progress = [n for n in notices if n["event"] == "segment_progress"]
    assert [(n["segment_id"], n["progress"], n["total"]) for n in progress] \
        == [(80, 1, 1)]


def test_standing_still_announces_nothing():
    # The other half of the mutation: a notice on every event would be a
    # refetch storm, and "the cursor moved" is the only claim being made.
    e = SegmentEngine([WF_SSL_STRICT])
    _exit_wf_into_the_lobby(e)
    _, notices = e.feed(jev(30, "mario_acted", 2500, {}), ctx(level=6, area=1))
    assert [n for n in notices if n["event"] == "segment_progress"] == []


# ---------------------------------------------------------------------------
# A PAUSE EXIT is not a retry (live report 2026-08-03).
# ---------------------------------------------------------------------------

BOWSER1_WF = SegmentDef(
    id=32, name="Bowser 1 -> WF", enabled=True,
    start_triggers=[{"type": "level_exit", "from": 30}],
    end_triggers=[{"type": "level_enter", "to": 24}],
    waypoints=[[{"type": "level_enter", "to": 17, "from": 6}],
               [{"type": "level_enter", "to": 6, "to_subarea": 1, "from": 17}]],
    guards=[], match_mode="strict")


def _arm_out_of_bowser_1(e):
    e.feed(jev(1, "level_changed", 1000, {"from": 30, "to": 6, "from_area": 1}),
           ctx(level=6, prev_level=30))
    e.feed(jev(2, "area_changed", 1000,
               {"level": 6, "from": 1, "to": 1, "from_transient": True}),
           ctx(level=6, area=1))
    e.feed(jev(3, "mario_acted", 1010, {}), ctx(level=6, area=1))


def _enter_bitdw(e, frame=2000):
    e.feed(jev(10, "level_changed", frame, {"from": 6, "to": 17, "from_area": 1}),
           ctx(level=17, prev_level=6))
    e.feed(jev(11, "area_changed", frame,
               {"level": 17, "from": 1, "to": 1, "from_transient": True}),
           ctx(level=17, area=1))


def _pause_exit_to_lobby(e, frame=3000, paused=66):
    """The real shape, taken from his journal (ids 4975-4977): the level edge,
    its co-frame area event, then Usamune's own IGT reset on the SAME frame,
    carrying the pause the exit menu cost."""
    e.feed(jev(20, "level_changed", frame, {"from": 17, "to": 6, "from_area": 1}),
           ctx(level=6, prev_level=17))
    e.feed(jev(21, "area_changed", frame,
               {"level": 6, "from": 1, "to": 1, "from_transient": True}),
           ctx(level=6, area=1))
    return e.feed(jev(22, "practice_reset", frame,
                      {"paused_frames_before": paused, "mario_acted": False,
                       "acted_tracking": True, "area": 1, "prev_area": 1}),
                  ctx(level=6, area=1))


def test_a_pause_exit_does_not_rewind_the_step_cursor():
    """Live report: "it briefly flashed step 3 of 3, then it reset", and then
    nothing recorded on reaching WF — a rewound cursor can never reach its own
    end, so both symptoms are this one bug.

    Usamune zeroes its IGT on the pause exit's level load, and that anchor
    carries a LONG pause because the menu was open, so `_anchor_echo`'s
    transition co-frame shape (pause-gated to keep MENU WARPS as real
    boundaries) let it through as a player retry."""
    e = SegmentEngine([BOWSER1_WF])
    _arm_out_of_bowser_1(e)
    _enter_bitdw(e)
    assert e.armed_items()[32].progress == 1
    _pause_exit_to_lobby(e)
    assert e.armed_items()[32].progress == 2, "the pause exit IS step 2"


def test_the_pause_exit_run_then_records_when_it_reaches_the_end():
    e = SegmentEngine([BOWSER1_WF])
    _arm_out_of_bowser_1(e)
    _enter_bitdw(e)
    _pause_exit_to_lobby(e)
    closed, _ = e.feed(jev(30, "level_changed", 4000,
                           {"from": 6, "to": 24, "from_area": 1}),
                       ctx(level=24, prev_level=6))
    assert [a.outcome for a in closed] == ["success"]


def test_a_reset_with_no_move_under_it_still_rewinds():
    """The boundary the fix must not cross: a real L-reset is not co-frame
    with any transition, so it stays the practice-retry loop it has always
    been. Mutation proof for the pair above: drop
    `_arrived_by_a_real_move` from the anchor guard and the two tests above go
    red while this one stays green."""
    e = SegmentEngine([BOWSER1_WF])
    _arm_out_of_bowser_1(e)
    _enter_bitdw(e)
    assert e.armed_items()[32].progress == 1
    e.feed(jev(40, "practice_reset", 2500,
               {"paused_frames_before": 0, "mario_acted": True,
                "acted_tracking": True, "area": 1, "prev_area": 1}),
           ctx(level=17, area=1))
    assert e.armed_items()[32].progress == 0


def test_a_menu_warp_along_a_fabricated_edge_still_rewinds():
    """The other boundary, and the reason shape (3) was pause-gated in the
    first place: a Usamune menu warp is co-frame with a transition too. It
    fabricates an edge, so the world graph tells the two apart — here BitDW to
    UPSTAIRS, which is not a door."""
    e = SegmentEngine([BOWSER1_WF])
    _arm_out_of_bowser_1(e)
    _enter_bitdw(e)
    e.feed(jev(50, "level_changed", 3000, {"from": 17, "to": 6, "from_area": 1}),
           ctx(level=6, prev_level=17))
    e.feed(jev(51, "area_changed", 3000,
               {"level": 6, "from": 1, "to": 2, "from_transient": False}),
           ctx(level=6, area=2))
    e.feed(jev(52, "practice_reset", 3000,
               {"paused_frames_before": 200, "mario_acted": False,
                "acted_tracking": True, "area": 2, "prev_area": 2}),
           ctx(level=6, area=2))
    assert e.armed_items()[32].progress == 0


# ---------------------------------------------------------------------------
# ECHO SHAPE (6) — IN-LEVEL TELEPORTER (task 0082, live demo 2026-08-03).
# The CCM broken bridge and the WDW corner warps relocate Mario inside the SAME
# area: no transition fires for shape (3), no door or dialogue context exists
# for (2)/(5), and Usamune zeroes its overall counter anyway. "these should not
# trigger resets, because they are a legitimate part of the level."
# ---------------------------------------------------------------------------

CCM_RUN = SegmentDef(id=4, name="CCM run", enabled=True,
                     start_triggers=[{"type": "spawned", "level": 5}],
                     end_triggers=[{"type": "level_enter", "to": 6}],
                     waypoints=[], guards=[])


def _bridge_warp_anchor(teleport):
    # Journal ids 23218-23219: the counter zeroes as Mario crosses from
    # ACT_TELEPORT_FADE_OUT to ACT_TELEPORT_FADE_IN, 42 frames after the pad.
    return jev(111, "practice_reset", 227702,
               {"igt_frames_before": 266, "mario_acted": True,
                "paused_frames_before": 2, "acted_tracking": True,
                "action": 0x1337, "prev_action": 0x1336,
                "save_pending": False, "frames_since_door": None,
                "frames_since_dialog": None, "area_load": False,
                "teleport": teleport})


def test_in_level_teleporter_is_echo_segment_stays_armed():
    e = SegmentEngine([CCM_RUN])
    e.feed(jev(108, "spawned", 227434, {"kind": "spawn", "level": 5}),
           ctx(level=5))
    assert e.armed_ids() == {4}, "the course spawn arms the run"
    closed, _ = e.feed(_bridge_warp_anchor(True), ctx(level=5))
    assert closed == [], "the bridge warp must not bank a reset row"
    assert e.armed_ids() == {4}, "and must not rewind or re-arm the segment"
    done, _ = e.feed(jev(140, "level_changed", 228000, {"from": 5, "to": 6}),
                     ctx(level=6, prev_level=5))
    [a] = done
    assert a.outcome == "success"
    assert a.rta_frames == 228000 - 227434, "timed from the spawn, not the warp"


def test_the_same_anchor_without_the_flag_still_closes():
    """Opt-in, exactly like every other echo shape: an ordinary L-reset in the
    same place, carrying the same actions, still records its reset row."""
    e = SegmentEngine([CCM_RUN])
    e.feed(jev(108, "spawned", 227434, {"kind": "spawn", "level": 5}),
           ctx(level=5))
    closed, _ = e.feed(_bridge_warp_anchor(False), ctx(level=5))
    assert len(closed) == 1 and closed[0].outcome == "reset"


def test_historical_anchor_without_the_teleport_key_closes():
    """No key at all (every event journaled before 2026-08-03): .get() returns
    False, so old journals replay with their conservative close behaviour."""
    e = SegmentEngine([CCM_RUN])
    e.feed(jev(108, "spawned", 227434, {"kind": "spawn", "level": 5}),
           ctx(level=5))
    payload = dict(_bridge_warp_anchor(False).payload)
    payload.pop("teleport")
    closed, _ = e.feed(jev(111, "practice_reset", 227702, payload),
                       ctx(level=5))
    assert len(closed) == 1 and closed[0].outcome == "reset"


def test_a_touch_clause_scopes_to_its_destination():
    """The castle basement alone hosts five exits (HMC, LLL, SSL, DDD,
    BitFS). A clause reading only "a warp in the castle" would let walking
    into HMC record a false MIPS Clip success."""
    clause = {"type": "warp_entered", "level": 6, "to": 23}
    spec = segments_module.TRIGGERS["warp_entered"]
    ctx = MatchContext(level=6, prev_level=6, num_stars=0, area=3)
    to_ddd = jev(1, "warp_entered", 1000, {"level": 6, "area": 3, "to": 23})
    to_hmc = jev(2, "warp_entered", 1000, {"level": 6, "area": 3, "to": 7})
    assert spec.match(clause, to_ddd, ctx) is True
    assert spec.match(clause, to_hmc, ctx) is False


def test_a_touch_clause_without_a_destination_still_matches_anything():
    """The three legacy pipe definitions carry `warp_entered level=6` with no
    destination and must keep matching byte-for-byte -- including against a
    HISTORICAL payload that has no `to` key at all."""
    clause = {"type": "warp_entered", "level": 6}
    spec = segments_module.TRIGGERS["warp_entered"]
    ctx = MatchContext(level=6, prev_level=6, num_stars=0, area=3)
    for payload in ({"level": 6, "area": 3, "to": 23},
                    {"level": 6, "area": 3, "to": None},
                    {"level": 6, "area": 3}):
        assert spec.match(clause, jev(1, "warp_entered", 1000, payload),
                          ctx) is True


def test_a_pinned_touch_clause_ignores_a_historical_payload():
    """Forward-only, in the conservative direction: a journal row written
    before 2026-08-04 carries no destination, so it can satisfy only the
    destination-free clause. An old journal must not start matching something
    new on replay."""
    clause = {"type": "warp_entered", "level": 6, "to": 23}
    spec = segments_module.TRIGGERS["warp_entered"]
    ctx = MatchContext(level=6, prev_level=6, num_stars=0, area=3)
    historical = jev(1, "warp_entered", 1000, {"level": 6, "area": 3})
    assert spec.match(clause, historical, ctx) is False


def test_a_touch_step_names_the_place_it_leads_to():
    """THE guard for the constraint on task 0081 (Griffin, 2026-08-04: "our
    topological logic is already working as expected and fine -- this should
    NOT break that").

    step_node answers None for a clause naming no place, and None means
    UNCONSTRAINED. So a touch-ended movement resolving to None would be
    silently exempt from the topological cancel a level_enter-ended one obeys
    -- re-pointing 56 movements onto such a clause would switch the wrong-turn
    rule off for the entire castle corpus with nothing going red."""
    assert segments_module.step_node({"type": "warp_entered", "level": 6, "to": 23}) \
        == segments_module.step_node({"type": "level_enter", "to": 23})
    assert segments_module.step_node({"type": "warp_entered", "level": 6}) is None

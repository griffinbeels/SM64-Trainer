import dataclasses
import re
from dataclasses import replace

import pytest

from sm64_events.memory.addresses import COURSE_NAMES, LEVEL_NAMES
from sm64_events.storage.db import EventRow
from sm64_events.tracking import segments as segments_module
from sm64_events.tracking.segments import (SEGMENT_ATTEMPT_OFFSET,
                                           card_waiting_for_sentence,
                                           clause_sentence,
                                           course_groups, level_groups,
                                           GUARDS, TRIGGERS, MatchContext,
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
    assert v["connections"]["22"] == [[6, 3]]
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


def test_waypoint_anchor_rewinds_progress_and_rearms():
    """A real anchor (not an echo) mid-attempt rewinds `progress` to 0 and
    re-arms IN PLACE at the anchor frame — the practice-retry loop. No row
    is recorded for the rewind itself; the eventual completion times from
    the anchor, not the original arm."""
    e = SegmentEngine([_sl_hmc_def()])
    e.feed(jev(10, "level_changed", 1000, {"from": 10, "to": 16}),
           ctx(level=16, prev_level=10))                        # arm
    assert e.armed_ids() == {99}
    closed, _ = e.feed(
        jev(11, "practice_reset", 1300, {"action": 0x0C400201}),
        ctx(level=16))
    assert closed == [], "waypoint rewind records no row"
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


def test_guarded_def_does_not_arm_without_route():
    e = SegmentEngine([_guarded_move()])
    e.feed(jev(10, "level_changed", 1000, {"from": 5, "to": 16}),  # exit CCM
           ctx(level=16, prev_level=5, route_segments=None))
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


def test_validate_accepts_both_match_modes():
    for mode in ("strict", "loose"):
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
    assert [m["key"] for m in modes] == ["loose", "strict"]
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

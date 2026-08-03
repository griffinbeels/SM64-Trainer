"""tracking/lint.py -- one test per rule, each citing the live report the
rule exists to catch. lint.py's own module docstring carries the mechanism
each rule detects; these tests only prove the fixture triggers it."""
from sm64_events.tracking.segments import SegmentDef
from sm64_events.tracking.lint import lint_definition


def test_flags_a_definition_whose_start_and_end_are_the_same_event():
    # SegmentEngine.feed only runs the CLOSURE handler for an ALREADY-armed
    # def (feed()'s `if arm is not None:` gate) -- a def that isn't armed yet
    # can only ARM on an event, never also close on it. So a start and end
    # pair satisfiable by the SAME level_changed hangs the def "Running"
    # forever the instant that event happens (live report 2026-07-24,
    # DDD -> BitFS, back when the world had a direct 23->19 edge for it).
    # That exact edge was removed 2026-07-27 for being topologically wrong
    # (BitFS is entered from the basement, two level_changed events away --
    # see test_accepts_the_same_pair_when_the_world_has_no_direct_edge below),
    # so this fixture uses a pair the CURRENT topology actually connects in
    # one hop instead: exiting Hazy Maze Cave (level 7) lands directly in the
    # castle basement, `(_BASEMENT, 7)` in memory.addresses.WORLD_EDGES_TWO_WAY
    # -- verified via world_connections()['7'] == [[6, 3], [28, None]].
    d = SegmentDef(id=1, name="x", enabled=True,
                   start_triggers=[{"type": "level_exit", "from": 7}],
                   end_triggers=[{"type": "level_enter", "to": 6}],
                   guards=[])
    findings = lint_definition(d, [])
    assert any(w["rule"] == "unfireable" and w["severity"] == "error"
               for w in findings), findings


def test_accepts_the_same_pair_when_the_world_has_no_direct_edge():
    # DDD -> BitFS through the BASEMENT is the real edge today (the old
    # one-hop 23->19 was removed 2026-07-27 for being wrong -- the live walk
    # is `23 -> 6 (area 3)` THEN a separate `6 -> 19`), so exit(23) and
    # enter(19) can never be the SAME event and this def is fine.
    d = SegmentDef(id=1, name="x", enabled=True,
                   start_triggers=[{"type": "level_exit", "from": 23}],
                   end_triggers=[{"type": "level_enter", "to": 19}],
                   guards=[])
    findings = lint_definition(d, [])
    assert not any(w["rule"] == "unfireable" for w in findings), findings


def test_flags_a_start_trigger_looser_than_a_colliding_waypoint():
    # _feed_waypoint's authoring caveat: a major-action cancel (a misroute --
    # here, exiting DDD to somewhere other than the castle) disarms the def,
    # and the SAME event is re-evaluated against start_triggers in the same
    # feed() call. The waypoint pins the destination (`to=6`); the start
    # trigger doesn't, so the very misroute that should cancel the sequence
    # re-arms it instead, silently, with no row for the abandoned attempt.
    d = SegmentDef(id=1, name="x", enabled=True,
                   start_triggers=[{"type": "level_exit", "from": 23}],
                   end_triggers=[{"type": "level_enter", "to": 8}],
                   waypoints=[[{"type": "level_exit", "from": 23, "to": 6}]],
                   guards=[])
    findings = lint_definition(d, [])
    assert any(w["rule"] == "start_looser_than_waypoint"
               and w["severity"] == "warning" for w in findings), findings


def test_loose_definition_with_the_same_colliding_shape_is_not_flagged():
    # Same start/waypoint shape as test_flags_a_start_trigger_looser_than_a_
    # colliding_waypoint above, but match_mode="loose". feed() routes a loose
    # def to _feed_loose regardless of waypoints (segments.py), and
    # _feed_loose has no major-action cancel -- an off-sequence event is
    # fully transparent, never a disarm-and-re-arm -- so the race this rule
    # warns about (a misroute cancelling _feed_waypoint's sequence, then
    # re-arming on a too-loose start trigger in the same tick) cannot occur
    # for a loose def. Warning here would cite a mechanism that can't fire.
    d = SegmentDef(id=1, name="x", enabled=True,
                   start_triggers=[{"type": "level_exit", "from": 23}],
                   end_triggers=[{"type": "level_enter", "to": 8}],
                   waypoints=[[{"type": "level_exit", "from": 23, "to": 6}]],
                   guards=[], match_mode="loose")
    findings = lint_definition(d, [])
    assert not any(w["rule"] == "start_looser_than_waypoint"
                   for w in findings), findings


def test_strict_definition_with_the_same_colliding_shape_is_still_flagged():
    # The guard must exempt ONLY loose defs -- a strict def with the
    # identical shape (match_mode defaults to "strict") still runs
    # _feed_waypoint and so still carries the real race; the rule must not
    # be neutered for the mode it's actually written for.
    d = SegmentDef(id=1, name="x", enabled=True,
                   start_triggers=[{"type": "level_exit", "from": 23}],
                   end_triggers=[{"type": "level_enter", "to": 8}],
                   waypoints=[[{"type": "level_exit", "from": 23, "to": 6}]],
                   guards=[])
    findings = lint_definition(d, [])
    assert any(w["rule"] == "start_looser_than_waypoint"
               and w["severity"] == "warning" for w in findings), findings


def test_flags_a_definition_that_can_never_arm_anywhere_it_can_be_run_from():
    # can_run_from false at the ONLY level this start trigger can concretely
    # arm at (attempt_anchor's arm_level is deterministic: exactly its own
    # `level` param) -- "reset in the castle lobby, then enter the castle"
    # can never be completed, because you are already there the moment it
    # arms (live report 2026-07-27, WF -> SSL armed inside Cool, Cool
    # Mountain, is the same class of bug: an arm position the segment cannot
    # be run from).
    d = SegmentDef(id=1, name="x", enabled=True,
                   start_triggers=[{"type": "attempt_anchor",
                                    "level": 6, "area": 1}],
                   end_triggers=[{"type": "level_enter", "to": 6}],
                   guards=[])
    findings = lint_definition(d, [])
    assert any(w["rule"] == "unrunnable_arm_position"
               and w["severity"] == "error" for w in findings), findings


def test_flags_a_duplicate_of_an_existing_definition():
    a = SegmentDef(id=1, name="dup-a", enabled=True,
                   start_triggers=[{"type": "level_exit", "from": 24}],
                   end_triggers=[{"type": "level_enter", "to": 8}],
                   guards=[])
    b = SegmentDef(id=2, name="dup-b", enabled=True,
                   start_triggers=[{"type": "level_exit", "from": 24}],
                   end_triggers=[{"type": "level_enter", "to": 8}],
                   guards=[])
    findings = lint_definition(a, [a, b])
    assert any(w["rule"] == "duplicate" and w["severity"] == "warning"
               for w in findings), findings


def test_an_in_progress_level_exit_with_no_from_yet_does_not_crash():
    # Task 16 regression: POST /api/segments/lint runs this rule against the
    # editor's CURRENT form on every edit, including a just-added clause with
    # no level picked yet -- {"type": "level_exit"} with no "from" at all.
    # _one_event_could_satisfy_both used to read start_clause["from"] with a
    # bare subscript and raised KeyError the first time a live in-progress
    # form reached this rule (found by rendering the editor, not by a test).
    d = SegmentDef(id=1, name="x", enabled=True,
                   start_triggers=[{"type": "level_exit"}],
                   end_triggers=[{"type": "level_enter"}],
                   guards=[])
    findings = lint_definition(d, [])   # must not raise
    assert not any(w["rule"] == "unfireable" for w in findings), findings


def test_a_level_exit_with_from_but_no_to_on_the_next_clause_does_not_crash():
    # The second half of the same bug: `next_clause["to"]` subscripted
    # unconditionally once `start_clause["from"]` no longer crashed first --
    # a level_exit with `from` picked but its level_enter partner still
    # showing the "any level" placeholder (to=None).
    d = SegmentDef(id=1, name="x", enabled=True,
                   start_triggers=[{"type": "level_exit", "from": 7}],
                   end_triggers=[{"type": "level_enter"}],
                   guards=[])
    findings = lint_definition(d, [])   # must not raise
    assert not any(w["rule"] == "unfireable" for w in findings), findings


def test_a_known_arm_position_with_a_bare_next_clause_does_not_crash():
    """The THIRD bare subscript, and the one the two tests above cannot reach.

    Both of those start with `level_exit`, whose `arm_level` is None -- so
    `_rule_unrunnable_arm_position` short-circuits before it ever calls
    `fires_from`, and the crash inside it survived Task 16's fix untouched.
    A start clause with a KNOWN arm position (any `area_enter` with a level)
    is what reaches it, and that is an ordinary editor state: pick the start
    clause's level, leave the end clause at the Builder's bare
    `{"type": "level_enter"}` default, wait for the debounced lint POST.

    `fires_from` read `trig["to"]` and raised KeyError -> 500, which the
    editor then rendered raw beside Save -- so lint was silently absent while
    displaying an error, which is worse than either state on its own.
    """
    d = SegmentDef(id=1, name="x", enabled=True,
                   start_triggers=[{"type": "area_enter", "level": 6,
                                    "area": 1}],
                   end_triggers=[{"type": "level_enter"}],
                   guards=[])
    findings = lint_definition(d, [])   # must not raise
    # An unknown destination excludes nothing, so the arm position is
    # runnable rather than refused -- unknown-means-yes, as everywhere else.
    assert not any(w["rule"] == "unrunnable_arm_position" for w in findings), \
        findings


def test_a_clean_definition_produces_no_warnings():
    # WF -> SSL's real shape: exiting WF lands in the lobby (a real edge),
    # which is NOT SSL, so start and end can't coincide; the lobby is also a
    # position the def CAN run from, so nothing here is unconditionally
    # broken; no waypoints, no sibling def to duplicate.
    d = SegmentDef(id=1, name="clean", enabled=True,
                   start_triggers=[{"type": "level_exit", "from": 24}],
                   end_triggers=[{"type": "level_enter", "to": 8}],
                   guards=[])
    assert lint_definition(d, []) == []


def test_a_next_step_that_pins_a_different_SOURCE_cannot_be_the_same_event():
    # Once every course gained a pause exit into the castle (2026-08-02), BBH's
    # own `level_changed 4 -> 6` satisfies both an unpinned `level_exit from=4`
    # and a bare `level_enter to=6` -- a real trap, and the rule is right to
    # flag it. Pinning the waypoint's SOURCE is what separates the two again:
    # one event cannot come from the Courtyard and from BBH at once, and both
    # match lambdas read the same `ev.payload["from"]`.
    colliding = SegmentDef(id=1, name="x", enabled=True,
                           start_triggers=[{"type": "level_exit", "from": 4}],
                           end_triggers=[{"type": "level_enter", "to": 6}],
                           guards=[])
    assert any(w["rule"] == "unfireable"
               for w in lint_definition(colliding, []))
    pinned = SegmentDef(id=1, name="x", enabled=True,
                        start_triggers=[{"type": "level_exit", "from": 4}],
                        end_triggers=[{"type": "level_enter", "to": 6,
                                       "from": 26}],
                        guards=[])
    findings = lint_definition(pinned, [])
    assert not any(w["rule"] == "unfireable" for w in findings), findings

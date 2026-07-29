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

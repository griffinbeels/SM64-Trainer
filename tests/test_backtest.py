from dataclasses import replace

from sm64_events.storage.db import EventRow
from sm64_events.tracking import backtest as backtest_module
from sm64_events.tracking.backtest import CANDIDATE_ID, backtest
from sm64_events.tracking.segments import SEGMENT_ATTEMPT_OFFSET, SegmentDef

W = "2026-06-11T12:00:00Z"


def jev(id, type, frame, payload=None, session_id=1):
    # local copy of test_projection.py's factory (tests/ is not a package)
    return EventRow(id=id, session_id=session_id, seq=id, type=type,
                    frame=frame, wall_time_utc=W, payload=payload or {})


# A plain (no-waypoint) LOOSE definition: leave course 23 (DDD) through the
# sub (level 6, "Castle Inside" in this world model) and arrive at course 19
# (BitFS) -- the exact geometry the module docstring's DDD -> BitFS trap
# names ("through the sub, 23 -> 19"), used here on purpose so the LOOSE vs.
# STRICT contrast below is the real one this feature exists to answer,
# rather than a fabricated pair of triggers. `id=0` deliberately: proves
# backtest() stamps its own id rather than trusting whatever the caller's
# draft object carries (see the id-collision test at the bottom).
LOOSE = SegmentDef(
    id=0, name="DDD exit -> BitFS", enabled=True, guards=[],
    start_triggers=[{"type": "level_exit", "from": 23}],
    end_triggers=[{"type": "level_enter", "to": 19}],
    match_mode="loose")

STRICT_EQUIVALENT = replace(LOOSE, id=30, match_mode="strict")


def test_backtest_counts_what_a_candidate_would_have_fired():
    events = [jev(1, "level_changed", 100, {"from": 23, "to": 6}),
              jev(2, "level_changed", 400, {"from": 6, "to": 19}),
              jev(3, "level_changed", 900, {"from": 23, "to": 6}),
              jev(4, "level_changed", 1100, {"from": 6, "to": 19})]
    rep = backtest(events, LOOSE)
    assert rep.fires == 2
    assert [a["rta_frames"] for a in rep.attempts] == [300, 200]
    assert rep.arms == 2  # arms once per successful walk -- a normal-firing
                          # candidate's arm count and fire count agree


def test_backtest_reports_an_arm_that_never_closed():
    events = [jev(1, "level_changed", 100, {"from": 23, "to": 6})]
    rep = backtest(events, LOOSE)
    assert rep.fires == 0
    assert len(rep.unclosed) == 1 and rep.unclosed[0]["frame"] == 100
    assert rep.arms == 1


def test_backtest_diffs_a_candidate_against_the_definition_it_replaces():
    # A detour through an off-route level (from=6 to=99, matching neither
    # LOOSE/STRICT_EQUIVALENT's start nor end) is transparent to a LOOSE
    # match (segments.py _feed_loose: "everything else is transparent") but
    # silently cancels a STRICT one with no waypoints (_feed_strict: any
    # level_changed that isn't the end and doesn't re-match the start
    # disarms, no row) -- exactly "one walk the loose def catches and the
    # strict one cancels".
    events = [jev(1, "level_changed", 100, {"from": 23, "to": 6}),
              jev(2, "level_changed", 250, {"from": 6, "to": 99}),
              jev(3, "level_changed", 400, {"from": 99, "to": 19})]
    rep = backtest(events, LOOSE, current=STRICT_EQUIVALENT)
    assert rep.gained == 1 and rep.lost == 0
    assert rep.pb_before is None and rep.pb_after == 300


def test_backtest_does_not_mutate_the_events_it_is_given():
    # EventRow is not a dataclass (storage/db.py — plain __slots__ class), so
    # dataclasses.asdict() cannot snapshot it; compare the fields directly.
    events = [jev(1, "level_changed", 100, {"from": 23, "to": 6})]
    before = [(e.id, e.session_id, e.seq, e.type, e.frame, e.wall_time_utc,
              dict(e.payload)) for e in events]
    backtest(events, LOOSE)
    after = [(e.id, e.session_id, e.seq, e.type, e.frame, e.wall_time_utc,
             dict(e.payload)) for e in events]
    assert before == after


def test_candidate_attempt_ids_cannot_collide_with_star_attempt_namespace(monkeypatch):
    # THE TRAP (backtest.py module docstring): a star attempt's id IS the
    # raw journal id of its first event (projection.py caveat 2) -- here
    # that would be 1, the arming level_changed's own journal id. A
    # candidate left at id=0 produces segment attempt ids
    # (arm.jid + SEGMENT_ATTEMPT_OFFSET * id) equal to that SAME raw journal
    # id, landing exactly in the star namespace. backtest() must never let
    # LOOSE's own id=0 through unchanged.
    events = [jev(1, "level_changed", 100, {"from": 23, "to": 6}),
              jev(2, "level_changed", 400, {"from": 6, "to": 19})]
    rep = backtest(events, LOOSE)
    [attempt] = rep.attempts
    assert attempt["id"] == 1 + SEGMENT_ATTEMPT_OFFSET * CANDIDATE_ID
    assert attempt["id"] != 1

    # Mutation proof: force the sentinel back to the dangerous default and
    # watch the exact collision happen.
    monkeypatch.setattr(backtest_module, "CANDIDATE_ID", 0)
    collided = backtest(events, LOOSE)
    [bad_attempt] = collided.attempts
    assert bad_attempt["id"] == 1   # == the raw journal id: the real trap


def test_arms_distinguishes_a_dead_end_from_a_start_that_never_matches():
    # THE AMBIGUITY (backtest.py module docstring): fires=0, unclosed=[] is
    # the SAME report for two opposite diagnoses -- a def whose start trigger
    # never matches anything, and a def that keeps arming off an off-route
    # detour (STRICT_EQUIVALENT's own "not starts" silent-disarm branch,
    # segments.py _feed_strict) without ever reaching its end trigger. `arms`
    # is the only field that tells them apart -- that's the entire point of
    # this test.
    #
    # A self-consistent shuttle walk between level 23 (DDD) and level 6
    # (Castle Inside), never touching 19 (BitFS, STRICT_EQUIVALENT's end):
    # each 23->6 hop matches the LOOSE `level_exit from=23` start clause and
    # arms; each 6->23 hop matches neither that start (from=6, not 23) nor
    # the end (to=23, not 19) and silently disarms (segments.py: a
    # level_changed matching neither is a silent cancel, no row). Ends
    # disarmed -- unclosed is empty at the journal's end, same as if it had
    # never armed at all.
    shuttle = [jev(1, "level_changed", 100, {"from": 23, "to": 6}),   # arms
               jev(2, "level_changed", 200, {"from": 6, "to": 23}),   # off-route: disarms
               jev(3, "level_changed", 300, {"from": 23, "to": 6}),   # arms again
               jev(4, "level_changed", 400, {"from": 6, "to": 23}),   # disarms
               jev(5, "level_changed", 500, {"from": 23, "to": 6}),   # arms a third time
               jev(6, "level_changed", 600, {"from": 6, "to": 23})]   # disarms -- ends idle

    dead_end = backtest(shuttle, STRICT_EQUIVALENT)
    assert dead_end.fires == 0
    assert dead_end.unclosed == []
    assert dead_end.arms == 3   # armed and was disarmed, three separate times

    never_starts = replace(STRICT_EQUIVALENT,
                           start_triggers=[{"type": "level_exit", "from": 777}])
    dead_start = backtest(shuttle, never_starts)
    assert dead_start.fires == 0
    assert dead_start.unclosed == []
    assert dead_start.arms == 0   # never armed at all

    # Without `arms`, these two reports are byte-for-byte identical on the
    # fields that existed before this task -- that identity is the bug.
    assert dead_end.arms != dead_start.arms


def test_arm_count_wiring_is_load_bearing(monkeypatch):
    # Mutation proof: if backtest._run stopped threading the on_notices
    # collector through replay() -- e.g. someone strips the kwarg while
    # refactoring _run, or replay() stops calling it -- the arm count goes
    # silently blind rather than erroring, and the distinguishing test above
    # would pass right back into the bug it exists to catch (both reports
    # read arms=0, indistinguishable again). Simulate exactly that by
    # patching backtest_module.replay with a wrapper that swallows
    # on_notices before delegating to the real one.
    real_replay = backtest_module.replay

    def replay_that_drops_on_notices(events, segments=None, time_filters=None,
                                     **ignored_kwargs):
        return real_replay(events, segments=segments, time_filters=time_filters)

    monkeypatch.setattr(backtest_module, "replay", replay_that_drops_on_notices)

    shuttle = [jev(1, "level_changed", 100, {"from": 23, "to": 6}),
               jev(2, "level_changed", 200, {"from": 6, "to": 23}),
               jev(3, "level_changed", 300, {"from": 23, "to": 6}),
               jev(4, "level_changed", 400, {"from": 6, "to": 23})]
    blinded = backtest(shuttle, STRICT_EQUIVALENT)
    assert blinded.fires == 0 and blinded.unclosed == []
    assert blinded.arms == 0   # WRONG -- it really armed twice; the wiring
                               # this test guards is what makes that visible

from datetime import datetime
from types import SimpleNamespace

from sm64_events.tracking.compilation import (EntityRef, plan_compilation)

STAR = EntityRef(course_id=1, star_id=0)


def _utc(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# Coverage wide enough to contain every span in these tests.
WIDE = (_utc("2026-07-23T00:00:00Z"), _utc("2026-07-23T02:00:00Z"))


def att(**kw):
    base = dict(id=1, course_id=1, star_id=0, segment_id=None, outcome="death",
                cleared=False, igt_frames=None, rta_frames=None,
                started_utc="2026-07-23T00:10:00Z",
                ended_utc="2026-07-23T00:10:05Z")
    base.update(kw)
    return SimpleNamespace(**base)


def plan(attempts, coverage=WIDE, saved=frozenset(), x=5, y=3,
         identity=STAR):
    return plan_compilation(attempts, coverage, set(saved), identity, x, y,
                            pre_pad=3.0, post_pad=2.0)


def test_failures_ordered_by_elapsed_into_the_run():
    early = att(id=1, started_utc="2026-07-23T00:10:00Z",
                ended_utc="2026-07-23T00:10:05Z")   # 5 s in
    late = att(id=2, started_utc="2026-07-23T00:20:00Z",
               ended_utc="2026-07-23T00:20:25Z")    # 25 s in
    p = plan([late, early])
    assert [s.attempt_id for s in p.specs] == [1, 2]
    assert all(s.kind == "failure" for s in p.specs)


def test_ties_break_by_attempt_id():
    a = att(id=7, started_utc="2026-07-23T00:10:00Z",
            ended_utc="2026-07-23T00:10:05Z")
    b = att(id=3, started_utc="2026-07-23T00:30:00Z",
            ended_utc="2026-07-23T00:30:05Z")   # same 5 s elapsed
    p = plan([a, b])
    assert [s.attempt_id for s in p.specs] == [3, 7]


def test_only_failure_outcomes_and_uncleared_included():
    death = att(id=1, outcome="death")
    reset = att(id=2, outcome="reset")
    abandoned = att(id=3, outcome="abandoned")
    cleared = att(id=4, outcome="death", cleared=True)
    success = att(id=5, outcome="success", igt_frames=600)
    hard_reset = att(id=6, outcome="hard_reset")
    p = plan([death, reset, abandoned, cleared, success, hard_reset])
    fails = [s.attempt_id for s in p.specs if s.kind == "failure"]
    assert set(fails) == {1, 2, 3, 6}


def test_entity_filtering_star_vs_segment():
    mine = att(id=1)
    other_star = att(id=2, star_id=1)
    a_segment = att(id=3, course_id=None, star_id=None, segment_id=9)
    p = plan([mine, other_star, a_segment])
    assert [s.attempt_id for s in p.specs] == [1]


def test_failure_outside_coverage_counts_as_aged_out():
    covered = att(id=1)
    tight = (_utc("2026-07-23T00:10:00Z"), _utc("2026-07-23T00:10:06Z"))
    # window is [end-5, end+3] = 00:10:00 .. 00:10:08 — end past coverage
    p = plan([covered], coverage=tight)
    assert p.specs == []
    assert p.aged_out == 1


def test_finale_is_fastest_available_success_in_full():
    slow = att(id=1, outcome="success", igt_frames=900,
               started_utc="2026-07-23T00:40:00Z",
               ended_utc="2026-07-23T00:40:30Z")
    fast = att(id=2, outcome="success", igt_frames=600,
               started_utc="2026-07-23T00:50:00Z",
               ended_utc="2026-07-23T00:50:20Z")
    p = plan([slow, fast])
    assert p.specs[-1].kind == "finale"
    assert p.specs[-1].attempt_id == 2
    assert p.specs[-1].source == "ring"
    assert p.finale_frames == 600
    assert p.no_finale is False


def test_finale_falls_back_to_saved_when_ring_missing():
    # fast run is out of coverage but saved -> finale uses the saved clip
    fast = att(id=2, outcome="success", igt_frames=600,
               started_utc="2025-01-01T00:00:00Z",
               ended_utc="2025-01-01T00:00:20Z")
    p = plan([fast], saved={2})
    assert p.specs[-1].source == "saved"
    assert p.specs[-1].attempt_id == 2
    assert p.finale_frames == 600


def test_no_finale_when_no_success_available():
    fail = att(id=1)
    p = plan([fail])
    assert p.no_finale is True
    assert p.finale_frames is None
    assert p.specs[-1].kind == "failure"


def test_success_without_a_time_is_not_a_finale():
    timeless = att(id=1, outcome="success", igt_frames=None, rta_frames=None)
    p = plan([timeless])
    assert p.no_finale is True
    assert p.specs == []


def test_segment_finale_uses_rta_frames():
    seg = EntityRef(segment_id=9)
    run = att(id=1, course_id=None, star_id=None, segment_id=9,
              outcome="success", igt_frames=None, rta_frames=450,
              started_utc="2026-07-23T00:10:00Z",
              ended_utc="2026-07-23T00:10:15Z")
    p = plan([run], identity=seg)
    assert p.finale_frames == 450


def test_finale_prefers_ring_when_success_is_also_saved():
    # success is BOTH ring-covered (within WIDE) AND in saved_ids -> ring wins
    run = att(id=2, outcome="success", igt_frames=600,
              started_utc="2026-07-23T00:50:00Z",
              ended_utc="2026-07-23T00:50:20Z")
    p = plan([run], saved={2})
    assert p.specs[-1].source == "ring"


def test_coverage_none_ages_out_failures_and_uses_saved_finale():
    fail = att(id=1, outcome="death")
    win = att(id=2, outcome="success", igt_frames=600,
              started_utc="2026-07-23T00:50:00Z",
              ended_utc="2026-07-23T00:50:20Z")
    p = plan([fail, win], coverage=None, saved={2})
    assert p.aged_out == 1
    assert p.failure_count == 0
    assert p.specs[-1].source == "saved"
    assert p.specs[-1].attempt_id == 2


def test_failure_window_exactly_on_coverage_boundary_is_included():
    a = att(id=1, started_utc="2026-07-23T00:10:00Z",
            ended_utc="2026-07-23T00:10:05Z")   # window [00:10:00, 00:10:08]
    exact = (_utc("2026-07-23T00:10:00Z"), _utc("2026-07-23T00:10:08Z"))
    p = plan([a], coverage=exact)
    assert [s.attempt_id for s in p.specs if s.kind == "failure"] == [1]
    assert p.aged_out == 0

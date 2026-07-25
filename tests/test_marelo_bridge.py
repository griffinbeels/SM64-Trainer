from sm64_events.tracking.marelo import (
    entity_ladders, entity_scores, successes_for)
from sm64_events.tracking.projection import Attempt


def att(**overrides):
    fields = dict(id=1, session_id=1, course_id=1, star_id=0, strat_tag="Standard",
                  anchor_type="practice_reset", anchor_frame=0, outcome="success",
                  outcome_detail=None, igt_frames=1500, rta_frames=1500,
                  started_utc="t", ended_utc="t", cleared=False,
                  cleared_reason=None, segment_id=None)
    fields.update(overrides)
    return Attempt(**fields)


class FakeRanks:
    """Stands in for ranks.standards.RankStandards."""
    def __init__(self, data):
        self._data = data

    def ladders(self, key):
        return self._data.get(key, {})

    def clock_for(self, key):
        return "rta" if key.startswith("segment:") else "igt"


RANKS = FakeRanks({
    "star:1:0": {"Fast": {"Mario": 45.0, "Gold": 60.0},
                 "Slow": {"Mario": 50.0, "Gold": 65.0}},
    "segment:5": {"Standard": {"Mario": 10.0, "Gold": 20.0}}})


def test_entity_ladders_are_the_pointwise_best_across_strategies():
    assert entity_ladders(RANKS, ["star:1:0"]) == {
        "star:1:0": {"Mario": 4500, "Gold": 6000}}


def test_unpracticed_entities_are_absent_from_scores_not_zero():
    scores = entity_scores([], RANKS, ["star:1:0"], "pb")
    assert scores == {}          # absent; aggregate() supplies the zero


def test_a_star_is_graded_on_igt_against_the_best_possible_ladder():
    # 1350 frames -> 45.00s displayed -> exactly the best Mario cutoff
    scores = entity_scores([att(igt_frames=1350)], RANKS, ["star:1:0"], "pb")
    assert scores["star:1:0"] == 95.0


def test_mastering_a_slow_strategy_does_not_max_the_entity():
    # 50.00s is Mario on "Slow" but only ~Gold-ish on the best-possible ladder
    scores = entity_scores([att(igt_frames=1500, strat_tag="Slow")],
                           RANKS, ["star:1:0"], "pb")
    assert scores["star:1:0"] < 95.0


def test_the_best_strategy_wins_the_entity_score():
    runs = [att(id=1, igt_frames=1800, strat_tag="Slow"),
            att(id=2, igt_frames=1350, strat_tag="Fast")]
    assert entity_scores(runs, RANKS, ["star:1:0"], "pb")["star:1:0"] == 95.0


def test_segments_are_graded_on_rta():
    run = att(course_id=None, star_id=None, segment_id=5,
              igt_frames=None, rta_frames=300)
    assert entity_scores([run], RANKS, ["segment:5"], "pb")["segment:5"] == 95.0


def test_cleared_failed_and_untagged_runs_never_score():
    for bad in (att(cleared=True), att(outcome="reset"), att(strat_tag=None)):
        assert entity_scores([bad], RANKS, ["star:1:0"], "pb") == {}


def test_avg_modes_grade_the_window_not_the_pb():
    runs = [att(id=1, igt_frames=1350), att(id=2, igt_frames=1650)]
    pb = entity_scores(runs, RANKS, ["star:1:0"], "pb")["star:1:0"]
    avg = entity_scores(runs, RANKS, ["star:1:0"], "avg10")["star:1:0"]
    assert avg < pb


def test_successes_for_emits_a_chronological_feed_with_entity_keys():
    runs = [att(id=1, ended_utc="t1"),
            att(id=2, ended_utc="t2", course_id=None, star_id=None,
                segment_id=5, rta_frames=300)]
    feed = successes_for(runs, RANKS.clock_for)
    assert [(entry["utc"], entry["key"], entry["frames"]) for entry in feed] == [
        ("t1", "star:1:0", 1500), ("t2", "segment:5", 300)]


def test_successes_for_skips_the_rta_zero_reset_race_rows():
    run = att(course_id=None, star_id=None, segment_id=5,
              igt_frames=None, rta_frames=0)
    assert successes_for([run], RANKS.clock_for) == []

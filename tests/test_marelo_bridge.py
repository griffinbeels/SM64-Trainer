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
    # 1350 frames -> 45.00s displayed -> exactly the best Mario cutoff.
    # CHANGED 2026-07-28 (task 0034): pb mode grades the SAVED row, not the
    # attempt directly -- the save is added, the assertion is unchanged.
    scores = entity_scores([att(igt_frames=1350)], RANKS, ["star:1:0"], "pb",
                           [pb(frames=1350, strat_tag="Standard")])
    assert scores["star:1:0"] == 95.0


def test_mastering_a_slow_strategy_does_not_max_the_entity():
    # 50.00s is Mario on "Slow" but only ~Gold-ish on the best-possible ladder.
    # CHANGED 2026-07-28 (task 0034): pb mode grades the saved row.
    scores = entity_scores([att(igt_frames=1500, strat_tag="Slow")],
                           RANKS, ["star:1:0"], "pb",
                           [pb(frames=1500, strat_tag="Slow")])
    assert scores["star:1:0"] < 95.0


def test_the_best_strategy_wins_the_entity_score():
    # CHANGED 2026-07-28 (task 0034): pb mode grades the saved rows, one per
    # strategy -- the entity still takes its best.
    rows = [pb(id=1, strat_tag="Slow", frames=1800),
            pb(id=2, strat_tag="Fast", frames=1350)]
    assert entity_scores([], RANKS, ["star:1:0"], "pb", rows)["star:1:0"] == 95.0


def test_segments_are_graded_on_rta():
    # CHANGED 2026-07-28 (task 0034): pb mode grades the saved row.
    rows = [pb(course_id=None, star_id=None, segment_id=5, timer_mode="rta",
               strat_tag="Standard", frames=300)]
    assert entity_scores([], RANKS, ["segment:5"], "pb", rows)["segment:5"] == 95.0


def test_cleared_failed_and_untagged_runs_never_score():
    # CHANGED 2026-07-28 (task 0034): pb mode no longer reads attempts at
    # all, so this scenario is vacuous under "pb" -- it would pass for ANY
    # attempt now, saved or not, since pb_rows is empty either way. avg10 is
    # where attempt filtering (cleared/outcome/strat_tag) still lives, so
    # that's what this test now exercises.
    for bad in (att(cleared=True), att(outcome="reset"), att(strat_tag=None)):
        assert entity_scores([bad], RANKS, ["star:1:0"], "avg10") == {}


def test_avg_modes_grade_the_window_not_the_pb():
    # CHANGED 2026-07-28 (task 0034): pb mode grades the saved row, not
    # min() over the window -- the save (of the faster run) reproduces the
    # old "pb" value so the comparison is unchanged.
    runs = [att(id=1, igt_frames=1350), att(id=2, igt_frames=1650)]
    pb_score = entity_scores(runs, RANKS, ["star:1:0"], "pb",
                             [pb(frames=1350, strat_tag="Standard")])["star:1:0"]
    avg = entity_scores(runs, RANKS, ["star:1:0"], "avg10")["star:1:0"]
    assert avg < pb_score


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


def pb(**overrides):
    """One row of storage/db.py's `pbs` table, as db.pbs() returns it."""
    fields = dict(id=1, course_id=1, star_id=0, segment_id=None,
                  strat_tag="Fast", timer_mode="igt", frames=1350,
                  attempt_id=1, saved_utc="2026-07-28T00:00:00Z")
    fields.update(overrides)
    return fields


def test_pb_mode_scores_nothing_until_the_run_is_saved_as_a_pb():
    # The live report (task 0034): "the system pre-emptively rewards MARELO
    # points when i achieve an entry that's better than my best time, BUT the
    # problem is that it's pre-emptive because it doesn't wait for me to click
    # 'Save as PB'." A Mario-cutoff attempt with no pb row scores NOTHING.
    runs = [att(igt_frames=1350, strat_tag="Fast")]
    assert entity_scores(runs, RANKS, ["star:1:0"], "pb", []) == {}


def test_pb_mode_scores_the_saved_row():
    runs = [att(igt_frames=1350, strat_tag="Fast")]
    scores = entity_scores(runs, RANKS, ["star:1:0"], "pb",
                           [pb(frames=1350, strat_tag="Fast")])
    assert scores["star:1:0"] == 95.0


def test_undoing_a_pb_takes_the_points_away():
    # undo_pb DELETES the row, so "undone" is simply an absent row here.
    runs = [att(igt_frames=1350, strat_tag="Fast")]
    assert entity_scores(runs, RANKS, ["star:1:0"], "pb", []) == {}


def test_a_later_slower_save_supersedes_a_faster_earlier_one():
    # latest-row-wins is the pbs contract (views._current_pbs' docstring, and
    # the whole reason undo_pb exists) -- NOT fastest-wins. A deliberate save
    # of a slower run is the current PB and MARELO must grade it.
    rows = [pb(id=1, frames=1350, strat_tag="Fast"),
            pb(id=2, frames=1500, strat_tag="Fast")]
    scores = entity_scores([], RANKS, ["star:1:0"], "pb", rows)
    assert scores["star:1:0"] < 95.0


def test_a_pb_on_the_wrong_clock_never_scores():
    # A star grades on igt (FakeRanks.clock_for); an rta pb row is a different
    # measurement and must not be graded against the igt ladder.
    rows = [pb(timer_mode="rta", frames=1350)]
    assert entity_scores([], RANKS, ["star:1:0"], "pb", rows) == {}


def test_an_untagged_pb_cannot_be_attributed_to_a_strategy():
    rows = [pb(strat_tag=None, frames=1350)]
    assert entity_scores([], RANKS, ["star:1:0"], "pb", rows) == {}


def test_the_entity_still_takes_its_best_strategy_in_pb_mode():
    rows = [pb(id=1, strat_tag="Slow", frames=1500),
            pb(id=2, strat_tag="Fast", frames=1350)]
    assert entity_scores([], RANKS, ["star:1:0"], "pb", rows)["star:1:0"] == 95.0


def test_a_segment_pb_is_graded_on_rta():
    rows = [pb(course_id=None, star_id=None, segment_id=5, timer_mode="rta",
               strat_tag="Standard", frames=300)]
    scores = entity_scores([], RANKS, ["segment:5"], "pb", rows)
    assert scores["segment:5"] == 95.0


def test_avg_modes_still_grade_unsaved_attempts():
    # Grading a WINDOW of attempts is what an average mode IS. Passing no pb
    # rows at all must not change avg10 by a hair.
    runs = [att(id=1, igt_frames=1350), att(id=2, igt_frames=1350)]
    with_pbs = entity_scores(runs, RANKS, ["star:1:0"], "avg10", [pb()])
    without = entity_scores(runs, RANKS, ["star:1:0"], "avg10", [])
    assert with_pbs == without != {}

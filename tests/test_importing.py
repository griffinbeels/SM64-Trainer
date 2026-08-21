from sm64_events.core.timefmt import cs_of_frame
from sm64_events.tracking.importing import ImportCandidate, decide


def _candidate(cs, strat="Standard", key="star:1:0"):
    return ImportCandidate(entity_key=key, strat_tag=strat, time_cs=cs)


def test_a_first_time_on_a_strategy_lands():
    plan = decide([_candidate(886)], lambda ek, strat, mode: None)
    assert [frames for _, frames in plan.landing] == [266]
    # The keys this test owns, not the whole dict: a summary another door
    # extends would fail here at MERGE time for a reason unrelated to the rule
    # under test.
    assert plan.summary["found"] == 1
    assert plan.summary["imported"] == 1
    assert plan.summary["already_faster"] == 0
    assert plan.summary["unmappable"] == 0


def test_a_slower_time_is_kept_out_and_counted():
    plan = decide([_candidate(1200)], lambda ek, strat, mode: 266)
    assert plan.landing == []
    assert plan.summary["already_faster"] == 1


def test_an_equal_time_does_not_land():
    """Re-importing the same batch must be free, and equal is not better."""
    plan = decide([_candidate(886)], lambda ek, strat, mode: 266)
    assert plan.landing == []
    assert plan.summary["already_faster"] == 1


def test_a_faster_time_lands_over_an_existing_best():
    plan = decide([_candidate(886)], lambda ek, strat, mode: 400)
    assert [frames for _, frames in plan.landing] == [266]


def test_the_same_strategy_is_compared_not_the_star():
    """A first time on a strategy he has never run must land even though he
    holds a faster best on a DIFFERENT strategy for the same star."""
    asked = {}

    def current(entity_key, strat_tag, timer_mode):
        asked[strat_tag] = True
        return 200 if strat_tag == "LJ" else None

    plan = decide([_candidate(886, strat="OG")], current)
    assert len(plan.landing) == 1
    assert asked == {"OG": True}


def test_an_unreachable_centisecond_rounds_up_never_down():
    """Only 30 of every 100 centisecond values are displayable. 15.01 is not
    one; the honest answer is the next frame, which reads 15.03. Rounding down
    would credit him with a time the timer cannot show."""
    plan = decide([_candidate(1501)], lambda ek, strat, mode: None)
    frames = plan.landing[0][1]
    assert cs_of_frame(frames) == 1503


def test_a_time_with_no_strategy_LANDS_and_is_counted():
    """Refused until the paste door existed, when every source always had a
    strategy. Most people writing down a gold write the star and the time and
    nothing else, and refusing those would reject the bulk of a real paste —
    so it lands, and the count is what keeps it from being silent."""
    plan = decide([_candidate(886, strat="")], lambda ek, strat, mode: None)
    assert [frames for _, frames in plan.landing] == [266]
    assert plan.summary["without_strategy"] == 1
    assert plan.summary["unmappable"] == 0


def test_a_strategy_less_time_is_measured_against_the_blind_best():
    """It can only ever be claimed by the strategy-blind best, so it has to
    beat everything to land."""
    plan = decide([_candidate(886, strat="")], lambda ek, strat, mode: 200)
    assert plan.landing == []
    assert plan.summary["already_faster"] == 1


def test_a_candidate_with_no_entity_is_unmappable():
    plan = decide([_candidate(886, key="")], lambda ek, strat, mode: None)
    assert plan.landing == []
    assert plan.summary["unmappable"] == 1


def test_a_nonpositive_time_is_unmappable():
    plan = decide([_candidate(0)], lambda ek, strat, mode: None)
    assert plan.summary["unmappable"] == 1


def test_the_version_rides_along_with_the_candidate():
    """A JP time graded on a US ladder reads as superhuman, so the version has
    to survive the decision."""
    jp = ImportCandidate(entity_key="star:1:0", strat_tag="Standard",
                         time_cs=886, game_version="jp")
    plan = decide([jp], lambda ek, strat, mode: None)
    assert plan.landing[0][0].game_version == "jp"


def test_a_slower_duplicate_inside_one_batch_cannot_shadow_the_faster_one():
    """Rows land in order and the latest row wins, so a batch holding the same
    target and strategy twice would leave the SLOWER one current. The decision
    has to measure against what the batch has already landed, not only against
    the store."""
    plan = decide([_candidate(886), _candidate(1200)],
                  lambda ek, strat, mode: None)
    assert [frames for _, frames in plan.landing] == [266]
    assert plan.summary["already_faster"] == 1


def test_a_faster_duplicate_later_in_the_batch_still_lands():
    plan = decide([_candidate(1200), _candidate(886)],
                  lambda ek, strat, mode: None)
    assert [frames for _, frames in plan.landing] == [360, 266]

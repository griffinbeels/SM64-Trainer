from sm64_events.ranks.classify import rank_for
from sm64_events.ranks.scoring import (
    SCORE_ANCHORS, best_ladder, defined_tiers, division_for, next_tier_target,
    progression_key, score_for, tier_band, tier_from_score)

# centiseconds, hardest -> easiest (the SSL "Nuts Pless" ladder)
NUTS = {"Mario": 1293, "Grandmaster": 1303, "Master": 1316, "Diamond": 1336,
        "Platinum": 1416, "Gold": 1566, "Silver": 1676}


def test_score_at_each_cutoff_is_that_tiers_anchor():
    for tier, cs in NUTS.items():
        assert score_for(NUTS, cs) == SCORE_ANCHORS[tier]


def test_score_interpolates_linearly_between_cutoffs():
    # midway between Platinum (1416 -> 60) and Gold (1566 -> 45)
    assert score_for(NUTS, 1491) == 52.5


def test_faster_than_the_hardest_tier_extrapolates_and_caps_at_100():
    assert score_for(NUTS, 1283) > 95.0          # 0.10s under the Mario cutoff
    assert score_for(NUTS, 0) == 100.0           # capped, never above


def test_iron_tail_decays_toward_zero_without_reaching_it():
    slow = score_for(NUTS, 5000)
    assert 0.0 < slow < SCORE_ANCHORS["Silver"]
    assert score_for(NUTS, 50000) < slow         # monotone: slower scores less
    assert score_for(NUTS, 10 ** 9) > 0.0        # asymptotic, never 0


def test_empty_ladder_has_no_score():
    assert score_for({}, 1300) is None


def test_defined_tiers_is_hardest_first_and_drops_iron():
    assert defined_tiers({"Gold": 10, "Mario": 5, "Iron": 99}) == ["Mario", "Gold"]


def test_best_ladder_is_the_pointwise_minimum_over_strategies():
    ladders = {"SS":       {"Mario": 12.93, "Gold": 15.66},
               "Leftside": {"Mario": 13.39, "Gold": 15.10, "Silver": 17.0}}
    assert best_ladder(ladders) == {"Mario": 1293, "Gold": 1510, "Silver": 1700}


def test_tier_from_score_only_names_tiers_the_ladder_defines():
    sparse = {"Grandmaster": 1303, "Diamond": 1336}      # no Master
    defined = defined_tiers(sparse)
    # a time between the two cutoffs interpolates through the 80-90 range;
    # a full-table lookup would wrongly call that "Master".
    # 1310, not 1320: the interpolation crosses the Master anchor (80.0) at
    # exactly 1319.5cs, so a probe just past it lands in Diamond on BOTH
    # lookups and proves nothing. This one sits mid-Master.
    score = score_for(sparse, 1310)
    assert 70.0 < score < 90.0
    assert tier_from_score(score, defined) == "Diamond"
    assert tier_from_score(score) == "Master"           # full table, for aggregates


def test_score_and_medal_never_disagree():
    """THE invariant (spec section 4.4)."""
    for ladder in (NUTS, {"Grandmaster": 1303, "Diamond": 1336}, {"Gold": 1566}):
        defined = defined_tiers(ladder)
        for time_cs in range(1200, 2400, 7):
            assert tier_from_score(score_for(ladder, time_cs), defined) == \
                rank_for(ladder, time_cs)


def test_tier_band_spans_to_the_next_harder_defined_tier():
    assert tier_band("Gold") == (45.0, 60.0)
    assert tier_band("Mario") == (95.0, 100.0)
    assert tier_band("Iron") == (0.0, 10.0)
    assert tier_band("Diamond", ["Grandmaster", "Diamond"]) == (70.0, 90.0)


def test_divisions_slice_the_band_with_five_at_the_bottom():
    assert division_for(45.0) == ("Gold", "V")
    assert division_for(59.9) == ("Gold", "I")
    assert division_for(48.0) == ("Gold", "IV")
    assert division_for(0.0) == ("Iron", "V")
    assert division_for(100.0) == ("Mario", "I")       # clamped, not out of range


def test_progression_key_is_monotone_across_tiers_and_divisions():
    assert progression_key("Gold", "V") < progression_key("Gold", "I")
    assert progression_key("Gold", "I") < progression_key("Platinum", "V")
    assert progression_key("Iron", "V") == 0


def test_next_tier_target_is_the_harder_anchor_and_100_at_the_top():
    assert next_tier_target(50.0) == 60.0             # Gold -> Platinum
    assert next_tier_target(96.0) == 100.0            # already Mario
    assert next_tier_target(75.0, ["Grandmaster", "Diamond"]) == 90.0

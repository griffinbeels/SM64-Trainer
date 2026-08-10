from sm64_events.ranks.classify import rank_for
from sm64_events.ranks.scoring import (
    SCORE_ANCHORS, best_ladder, best_ladder_owners, defined_tiers,
    division_for, division_progress,
    next_tier_target, progress_for_time, progression_key, score_for, tier_band,
    tier_from_score, time_for_score)

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


def test_division_progress_mid_division_targets_the_next_division_up():
    # Gold spans [45,60); division IV is [48,51) -- 50.0 sits 2/3 through it.
    progress = division_progress(50.0)
    assert progress["tier"] == "Gold" and progress["division"] == "IV"
    assert abs(progress["fill"] - 2 / 3) < 1e-9
    assert progress["next_tier"] == "Gold" and progress["next_division"] == "III"
    assert progress["next_at"] == 51.0        # the score division III begins at


def test_division_progress_at_division_i_targets_the_next_tier_at_v():
    # 59.9 sits in Gold's top division (I, [57,60)); the next STEP is the
    # next harder tier's bottom division, not a division within Gold.
    progress = division_progress(59.9)
    assert progress["tier"] == "Gold" and progress["division"] == "I"
    assert progress["next_tier"] == "Platinum" and progress["next_division"] == "V"
    assert progress["next_at"] == 60.0        # Platinum's own anchor


def test_division_progress_is_maxed_at_the_top_of_the_full_table():
    progress = division_progress(100.0)
    assert progress == {"tier": "Mario", "division": "I", "fill": 1.0,
                        "next_tier": None, "next_division": None, "next_at": None}


def test_division_progress_is_maxed_at_the_top_of_a_ragged_ladder():
    # This ladder never defines Mario -- topping out at Grandmaster I must
    # NOT claim "next: Mario V" for a tier this ladder doesn't know about.
    defined = ["Grandmaster", "Diamond"]
    progress = division_progress(99.9, defined)
    assert progress["tier"] == "Grandmaster" and progress["division"] == "I"
    assert progress["next_tier"] is None and progress["next_division"] is None
    assert progress["fill"] == 1.0


def test_division_progress_respects_a_ragged_ladders_band_edges():
    # Diamond's band on this 2-tier ladder is [70,90) (its only harder
    # neighbor is Grandmaster, not Master/Diamond's usual full-table one).
    defined = ["Grandmaster", "Diamond"]
    progress = division_progress(85.0, defined)
    assert progress["tier"] == "Diamond" and progress["division"] == "II"
    assert progress["fill"] == 0.75
    assert progress["next_tier"] == "Diamond" and progress["next_division"] == "I"


def test_time_for_score_is_exact_at_every_ladder_anchor():
    for tier, cs in NUTS.items():
        assert time_for_score(NUTS, SCORE_ANCHORS[tier]) == cs


def test_time_for_score_round_trips_through_score_for():
    """THE invariant this function exists for: score_for(L, time_for_score(L,
    s)) == s, for any score score_for could actually produce (round to the
    nearest centisecond, since time_for_score returns an int). Starts at
    1284, not 1200: score_for CAPS at 100.0 for anything faster than 1283cs
    on this ladder (test_faster_than_the_hardest_tier_extrapolates_and_caps_
    at_100 above), so times in the capped region are a many-to-one mapping
    with no single correct inverse -- outside what this test can check."""
    for time_cs in range(1284, 2400, 11):
        score = score_for(NUTS, time_cs)
        recovered_time = time_for_score(NUTS, score)
        assert abs(recovered_time - time_cs) <= 1     # rounding slack
        assert abs(score_for(NUTS, recovered_time) - score) < 0.05


def test_time_for_score_inverts_the_extrapolation_above_the_hardest_tier():
    faster_score = score_for(NUTS, 1283)               # 0.10s under Mario
    assert time_for_score(NUTS, faster_score) == 1283


def test_time_for_score_inverts_the_iron_tail():
    slow_score = score_for(NUTS, 5000)
    assert time_for_score(NUTS, slow_score) == 5000


def test_time_for_score_is_none_for_an_empty_ladder_or_a_zero_score():
    assert time_for_score({}, 50.0) is None
    assert time_for_score(NUTS, 0.0) is None


# ---- progress_for_time: the displayed-centisecond boundary ---------------
#
# The whole app grades DISPLAYED centiseconds (classify.py's docstring), and a
# tier cutoff is inclusive: `time_cs <= cutoff` earns it. A DIVISION edge is
# interpolated, so it lands at a fractional centisecond that time_for_score
# rounds to report -- which is how a banner came to read "0.00s to rank up"
# (live report 2026-07-29). These pin the extension of the tier rule to those
# edges, and the consequence the user actually asked for.

def test_a_time_on_a_division_edge_ranks_up_instead_of_owing_zero():
    """The reported bug, reproduced on this ladder.

    Master III begins at score 84, which this ladder puts at 1310.6cs. A run
    displaying 1311 is a rounding hair SLOWER than that edge, so the score
    curve alone leaves it in Master IV -- owing `1311 - round(1310.6)` = 0.00s
    to a division it has, in displayed time, already reached."""
    edge_cs = time_for_score(NUTS, 84.0)
    assert edge_cs == 1311, "the fixture moved; re-derive the edge before reading on"
    assert division_progress(score_for(NUTS, edge_cs), defined_tiers(NUTS))["division"] == "IV"

    progress = progress_for_time(NUTS, edge_cs)
    assert (progress["tier"], progress["division"]) == ("Master", "III")
    assert progress["fill"] == 0.0                 # freshly into the division
    assert progress["score"] == 84.0               # snapped WITH the division
    assert progress["next_gap_cs"] >= 1


def test_one_centisecond_slower_than_the_edge_still_owes_a_real_gap():
    """The other side of the same boundary -- the rule widens by at most half
    a centisecond and must not swallow a run that genuinely has not got there."""
    progress = progress_for_time(NUTS, time_for_score(NUTS, 84.0) + 1)
    assert (progress["tier"], progress["division"]) == ("Master", "IV")
    assert progress["next_gap_cs"] >= 1


def test_the_gap_is_never_zero_anywhere_on_a_ladder():
    """A gap of 0.00s is not a goal anybody can chase -- and IGT moves a whole
    frame (~3.33cs) at a time, so it is not even reachable. Swept across the
    whole ladder rather than at sampled points, since the failing times are
    exactly the ones nobody would think to pick."""
    for time_cs in range(1200, 4000):
        gap = progress_for_time(NUTS, time_cs)["next_gap_cs"]
        assert gap is None or gap >= 1, f"{time_cs}cs owes {gap}cs"


def test_a_maxed_ladder_owes_nothing_at_all():
    progress = progress_for_time(NUTS, 1)          # faster than Mario, capped
    assert progress["next_tier"] is None and progress["next_gap_cs"] is None


def test_the_returned_division_always_matches_the_returned_score():
    """`progress_for_time` raises `score` with the division when the boundary
    rule fires, so the payload can never contradict itself -- and this is the
    property that keeps two SURFACES agreeing, not just one dict.

    The rank banner shows `progress_for_time`'s own tier/division; the Rank
    tab's breakdown re-derives a division from the MARELO score
    (`server/ranks_api.py::_score_scope` -> `division_for(entity["score"])`,
    fed by `tracking/marelo.py`). Grade the score through the raw curve there
    and the two name different divisions for the same run inside the boundary
    window -- the same shape as the Platinum II / Diamond V split
    docs/architecture.md records. Held structurally by
    tests/test_single_source.py's `score_for` row; measured here."""
    for time_cs in range(1200, 4000, 7):
        progress = progress_for_time(NUTS, time_cs)
        assert division_for(progress["score"], defined_tiers(NUTS)) == \
            (progress["tier"], progress["division"]), time_cs


def test_best_ladder_owners_names_who_sets_each_tier():
    # The pointwise minimum is a ladder no single strategy need own: one way
    # can be fastest at Mario and another set Bronze. "What does it take to
    # rank up overall" is only half an answer without "and by doing what",
    # which is the half that says which strategy to go and practice.
    ladders = {"Sideflip": {"Mario": 10.93, "Gold": 12.00},
               "Owlless": {"Mario": 11.36, "Gold": 11.63}}
    assert best_ladder(ladders) == {"Mario": 1093, "Gold": 1163}
    assert best_ladder_owners(ladders) == {
        "Mario": ["Sideflip"], "Gold": ["Owlless"]}


def test_best_ladder_owners_names_every_winner_of_a_tie():
    # Two ways published to the same centisecond is ordinary, not exotic, and
    # picking one arbitrarily would tell the reader to practice a strategy
    # that is no better than the one beside it.
    ladders = {"A": {"Mario": 10.0}, "B": {"Mario": 10.0}, "C": {"Mario": 10.5}}
    assert best_ladder_owners(ladders) == {"Mario": ["A", "B"]}

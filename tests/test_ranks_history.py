from sm64_events.ranks.history import history_series

GROUPS = [{"need": 1, "candidates": ["a"]}, {"need": 1, "candidates": ["b"]}]


def scorer(key, frames):
    """Fake curve: faster frames score higher, capped at 100."""
    return max(0.0, min(100.0, 6000.0 / frames))


def s(utc, key, frames, strat="Standard"):
    return {"utc": utc, "key": key, "strat": strat, "frames": frames}


def test_one_point_per_success_in_order():
    series = history_series([s("t1", "a", 100), s("t2", "b", 200)],
                            GROUPS, scorer, "pb")
    assert [p["utc"] for p in series] == ["t1", "t2"]


def test_marelo_climbs_as_coverage_and_mastery_grow():
    series = history_series([s("t1", "a", 200), s("t2", "b", 200),
                             s("t3", "a", 100)], GROUPS, scorer, "pb")
    assert series[0]["marelo"] < series[1]["marelo"] < series[2]["marelo"]
    assert series[0]["practiced"] == 1 and series[1]["practiced"] == 2


def test_pb_mode_keeps_the_best_time_not_the_latest():
    series = history_series([s("t1", "a", 100), s("t2", "a", 400)],
                            GROUPS, scorer, "pb")
    assert series[1]["marelo"] == series[0]["marelo"]   # a worse run cannot lower a PB


def test_avg_mode_uses_a_rolling_window_per_strategy():
    runs = [s("t1", "a", 100), s("t2", "a", 300), s("t3", "a", 300)]
    series = history_series(runs, GROUPS, scorer, "avg10")
    # the mean of 100,300 is worse than 100 alone; adding another 300 is worse again
    assert series[0]["marelo"] > series[1]["marelo"] > series[2]["marelo"]


def test_strategies_are_averaged_separately_and_the_best_one_wins():
    runs = [s("t1", "a", 300, "Slow"), s("t2", "a", 100, "Fast"),
            s("t3", "a", 320, "Slow")]
    series = history_series(runs, GROUPS, scorer, "avg10")
    # the Slow strat degrading must not drag down a better Fast average
    assert series[1]["marelo"] == series[2]["marelo"]


def test_points_carry_tier_and_division():
    series = history_series([s("t1", "a", 100)], GROUPS, scorer, "pb")
    # a scored 60.0, b absent -> marelo 30.0 (Silver spans 25-45, band IV is
    # 29-33) -- pinned independently by test_ranks_scoring.py's division_for.
    assert series[0]["tier"] == "Silver" and series[0]["division"] == "IV"


def test_successes_outside_the_scope_are_ignored():
    series = history_series([s("t1", "zzz", 100)], GROUPS, scorer, "pb")
    assert series == []


def test_long_histories_are_decimated_but_keep_the_last_point():
    runs = [s(f"t{i}", "a", 100 + i) for i in range(1000)]
    series = history_series(runs, GROUPS, scorer, "pb", max_points=50)
    assert len(series) == 50            # _decimate returns exactly max_points
    assert series[-1]["utc"] == "t999"


def test_empty_history_is_empty():
    assert history_series([], GROUPS, scorer, "pb") == []

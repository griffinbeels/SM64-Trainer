from sm64_events.library import ladders
from sm64_events.ranks.classify import RANK_NAMES, rank_for


def _spread(low, high, count):
    step = (high - low) / (count - 1)
    return [int(round(low + step * i)) for i in range(count)]


def test_a_thin_row_gets_no_ladder():
    # A feasibility floor, not an accuracy one: below it neighbouring
    # percentiles land on the same observation and the "ladder" is three
    # numbers wearing eight names.
    assert ladders.fit_ladder(_spread(1000, 2000, ladders.MIN_ENTRIES - 1)) == {}
    assert ladders.fit_ladder(_spread(1000, 2000, ladders.MIN_ENTRIES)) != {}


def test_cutoffs_are_strictly_increasing_in_whole_centiseconds():
    # classify.py compares DISPLAYED centiseconds, so two tiers sharing a
    # cutoff is a tier no time can ever earn.
    ladder = ladders.fit_ladder([1200] * 40 + [1300] * 40)   # only two values
    values = [int(round(ladder[r] * 100)) for r in RANK_NAMES if r in ladder]
    assert values == sorted(values)
    assert len(values) == len(set(values)), values


def test_the_fastest_tier_is_near_the_fastest_times():
    times = _spread(2000, 4000, 200)
    ladder = ladders.fit_ladder(times)
    mario = int(round(ladder["Mario"] * 100))
    assert times[0] <= mario <= times[len(times) // 5]
    assert ladder["Bronze"] > ladder["Mario"]


def test_the_model_reproduces_its_own_percentiles():
    times = _spread(1000, 2000, 1001)      # one value per 0.01s, exactly linear
    ladder = ladders.fit_ladder(times)
    for rank, percent in ladders.LADDER_PERCENTILES.items():
        expected = 1000 + (2000 - 1000) * percent / 100
        assert abs(ladder[rank] * 100 - expected) <= 1, (rank, ladder[rank])


def test_a_cutoff_inside_a_real_gap_moves_to_its_slow_edge():
    # A cycle star: everyone either catches it (~10s) or waits one (~13s).
    # A cutoff left in the void between them decides a future time arbitrarily
    # and lets two tiers share one gap, which mints a band nobody can occupy.
    times = sorted([1000 + i for i in range(60)] + [1300 + i for i in range(60)])
    ladder = ladders.fit_ladder(times)
    for rank, seconds in ladder.items():
        centiseconds = int(round(seconds * 100))
        assert not (1060 < centiseconds < 1299), (rank, centiseconds)


def test_every_recorded_time_lands_on_a_real_tier():
    times = _spread(1500, 3000, 300)
    ladder = ladders.fit_ladder(times)
    graded = {rank_for({r: int(round(v * 100)) for r, v in ladder.items()}, t)
              for t in times}
    assert "Mario" in graded and len(graded) >= 6, graded


def test_fit_payload_stamps_rows_and_records_the_model():
    payload = {"targets": [
        {"approaches": [{"name": "a", "entries": [{"time_cs": 1000 + i}
                                                  for i in range(60)]}],
         "subsections": [{"name": "s", "entries": [{"time_cs": 500}] * 3}]}]}
    out = ladders.fit_payload(payload)
    target = out["targets"][0]
    assert "ladder" in target["approaches"][0]
    assert "ladder" not in target["subsections"][0]      # too thin
    assert out["ladder_model"]["fitted_rows"] == 1
    assert out["ladder_model"]["rows_too_thin"] == 1
    assert out["ladder_model"]["source"] == "sheet"
    assert out["ladder_model"]["percentiles"] == dict(ladders.LADDER_PERCENTILES)


def test_refitting_is_idempotent():
    payload = {"targets": [
        {"approaches": [{"name": "a", "entries": [{"time_cs": 1000 + i}
                                                  for i in range(60)]}],
         "subsections": []}]}
    first = ladders.fit_payload(payload)["targets"][0]["approaches"][0]["ladder"]
    second = ladders.fit_payload(payload)["targets"][0]["approaches"][0]["ladder"]
    assert first == second

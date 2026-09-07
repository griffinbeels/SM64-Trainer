from sm64_events.library import ladders
from sm64_events.ranks.classify import RANK_NAMES, rank_for
from sm64_events.core.timefmt import cs_of_frame, frame_at_or_after
from sm64_events.ranks.scoring import time_for_score


def _spread(low, high, count):
    step = (high - low) / (count - 1)
    return [int(round(low + step * i)) for i in range(count)]


def test_every_nonempty_population_gets_every_tier():
    assert ladders.fit_ladder([]) == {}
    for times in ([1000], [1000] * 20, [1000, 1003, 1006], [1200] * 40 + [1300] * 40):
        ladder = ladders.fit_ladder(times)
        assert set(ladder) == set(RANK_NAMES) - {"Iron"}
        frames = [frame_at_or_after(round(t * 100)) for t in ladder.values()]
        assert all(b - a >= 5 for a, b in zip(frames, frames[1:], strict=False))


def test_a_tight_door_extends_slower_while_the_top_stays_near_the_observations():
    ladder = {r: round(t * 100) for r, t in ladders.fit_ladder([246] * 57 + [250]).items()}
    assert time_for_score(ladder, 99) == 246
    assert ladder["Mario"] == 260
    assert ladder["Bronze"] == 376
    assert time_for_score(ladder, 8) == 380


def test_a_real_slow_outlier_participates_in_the_fit():
    ladder = ladders.fit_ladder([246] * 57 + [400])
    assert ladder["Bronze"] == 4.0
    assert ladder["Mario"] < 3.0


def test_dense_data_preserves_the_interior_empirical_quantiles():
    times = _spread(1000, 2000, 1001)
    ladder = ladders.fit_ladder(times)
    # Top pair is fitted jointly around Mario I. Slower interior cutoffs use
    # their measured percentiles when no spacing repair is necessary.
    from sm64_events.core.timefmt import attainable_cs
    for rank in ("Master", "Diamond", "Platinum", "Gold", "Silver", "Bronze"):
        expected = attainable_cs(round(1000 + 10 * ladders.LADDER_PERCENTILES[rank]))
        assert round(ladder[rank] * 100) == expected
    elite = time_for_score({r: round(t * 100) for r, t in ladder.items()}, 99)
    assert 1067 <= elite <= 1070


def test_every_cutoff_is_a_time_the_timer_can_show():
    for low, high in ((1000, 2000), (517, 1013), (12345, 19999)):
        ladder = ladders.fit_ladder(_spread(low, high, 200))
        for seconds in ladder.values():
            cs = round(seconds * 100)
            assert cs_of_frame(frame_at_or_after(cs)) == cs


def test_interpolation_keeps_standards_in_a_gap_for_future_submissions():
    # Missing observations inside a cycle gap do not justify deleting ranks.
    ladder = ladders.fit_ladder([1000] * 50 + [2000] * 50)
    assert len(ladder) == 8
    assert any(1000 < round(t * 100) < 2000 for t in ladder.values())


def test_every_recorded_time_lands_on_a_real_tier():
    times = _spread(1500, 3000, 300)
    ladder = ladders.fit_ladder(times)
    graded = {rank_for({r: round(v * 100) for r, v in ladder.items()}, t) for t in times}
    assert "Mario" in graded and len(graded) >= 6


def test_fit_payload_stamps_rows_and_records_the_model():
    payload = {"targets": [
        {"approaches": [{"name": "a", "entries": [{"time_cs": 1000 + i}
                                                  for i in range(60)]}],
         "subsections": [{"name": "s", "entries": [{"time_cs": 500}] * 3}]}]}
    out = ladders.fit_payload(payload)
    target = out["targets"][0]
    assert "ladder" in target["approaches"][0]
    assert target["subsections"][0]["ladder"] == ladders.fit_ladder([500])
    assert out["ladder_model"]["fitted_rows"] == 2
    assert out["ladder_model"]["rows_too_thin"] == 0
    assert out["ladder_model"]["version"] == ladders.LADDER_MODEL_VERSION
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


def test_a_ladder_is_never_fitted_across_two_rom_versions():
    # A (JP)/(US) pair merges into one approach holding both populations --
    # JRB's stone pillar is 10.80 JP against 14.50 US -- and a ladder fitted
    # across that pile spans a gap no single player can be on both sides of.
    item = {"entries": [{"time_cs": 1080 + i, "version": "jp"} for i in range(40)]
                       + [{"time_cs": 1450 + i, "version": "us"} for i in range(30)]}
    times, version = ladders.row_times(item)
    assert version == "us"
    assert min(times) >= 1450


def test_a_thin_us_population_does_not_discard_a_large_jp_one():
    # Each annotated population fits independently, even a single observation.
    item = {"entries": [{"time_cs": 1080 + i, "version": "jp"} for i in range(40)]
                       + [{"time_cs": 1450, "version": "us"}]}
    times, version = ladders.row_times(item)
    assert version == "us" and times == [1450]
    ladders.fit_payload({"targets": [{"approaches": [item], "subsections": []}]})
    assert item["ladder"] == ladders.fit_ladder([1450])
    assert item["ladder_samples"] == 1
    assert item["ladder_jp"] == ladders.fit_ladder(range(1080, 1120))
    assert item["ladder_jp_samples"] == 40


def test_one_jp_observation_gets_its_own_companion_ladder():
    item = {"entries": [{"time_cs": 1450 + i, "version": "us"} for i in range(30)]
                       + [{"time_cs": 1080, "version": "jp"}]}
    ladders.fit_payload({"targets": [{"approaches": [item], "subsections": []}]})
    assert item["ladder_version"] == "us"
    assert item["ladder_jp"] == ladders.fit_ladder([1080])
    assert item["ladder_jp_samples"] == 1


def test_unannotated_times_keep_a_base_beside_a_jp_companion():
    item = {"entries": [{"time_cs": 1500}]
                       + [{"time_cs": 1080, "version": "jp"}] * 20}
    ladders.fit_payload({"targets": [{"approaches": [item], "subsections": []}]})
    assert item["ladder_version"] is None
    assert item["ladder"] == ladders.fit_ladder([1500])
    assert item["ladder_jp"] == ladders.fit_ladder([1080])


def test_an_unversioned_row_uses_everything():
    item = {"entries": [{"time_cs": 1000 + i, "version": None} for i in range(20)]}
    times, version = ladders.row_times(item)
    assert version is None and len(times) == 20


def test_fit_payload_records_which_population_each_ladder_describes():
    payload = {"targets": [{"approaches": [
        {"name": "a", "entries": [{"time_cs": 1450 + i, "version": "us"}
                                  for i in range(30)]}], "subsections": []}]}
    item = ladders.fit_payload(payload)["targets"][0]["approaches"][0]
    assert item["ladder_version"] == "us"
    assert item["ladder_samples"] == 30

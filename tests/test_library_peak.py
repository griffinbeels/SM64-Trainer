"""Mario I means a supported fast performance, even when it equals the record."""
import pytest

from sm64_events.core.timefmt import cs_of_frame
from sm64_events.library.ladders import fit_ladder
from sm64_events.ranks.scoring import time_for_score


def elite(times):
    cutoffs = {r: round(t * 100) for r, t in fit_ladder(times).items()}
    return time_for_score(cutoffs, 99)


@pytest.mark.parametrize("times", [[246] * 57 + [250], [240] + [246] * 57])
def test_the_shared_peak_is_mario_one_even_if_it_is_the_record(times):
    assert elite(times) == 246


@pytest.mark.parametrize("slow_count", [10, 100, 1000])
def test_a_slower_global_mode_cannot_erase_the_supported_fast_peak(slow_count):
    assert elite([246] * 6 + [250, 253, 256] + [500] * slow_count) == 246


def test_nearby_frames_support_a_peak_without_exact_ties():
    assert elite([200, 246, 250, 253] + [500] * 100) == 250


def test_smooth_populations_keep_the_elite_percentile_instead_of_a_false_peak():
    times = [cs_of_frame(frame) for frame in range(300, 601)]
    # Quantile position20.1 interpolates1066..1070 to1066.4cs, rounded1066.
    assert elite(times) == 1066


def test_a_late_popular_cluster_cannot_soften_the_elite_percentile():
    times = [cs_of_frame(frame) for frame in range(300, 340)] + [2000] * 40
    assert elite(times) < 1100


def test_two_isolated_records_are_not_a_supported_cluster():
    assert elite([1000, 2000]) == 1070


def test_one_observation_remains_a_provisional_anchor_without_a_forced_cushion():
    assert elite([246]) == 246


def test_peak_populations_remain_separate_between_rom_versions():
    from sm64_events.library.ladders import fit_payload
    row = {"entries": ([{"time_cs": 246, "version": "us"}] * 57 +
                       [{"time_cs": 250, "version": "us"}] +
                       [{"time_cs": 203, "version": "jp"}] * 57 +
                       [{"time_cs": 206, "version": "jp"}])}
    fit_payload({"targets": [{"approaches": [row], "subsections": []}]})
    for name, expected in (("ladder", 246), ("ladder_jp", 203)):
        cutoffs = {r: round(t * 100) for r, t in row[name].items()}
        assert time_for_score(cutoffs, 99) == expected

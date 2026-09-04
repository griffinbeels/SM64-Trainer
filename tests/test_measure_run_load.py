"""The load meter: what it scores a test-suite configuration by, and the one
column that keeps a shared machine from lying to it.

`tools/measure_run_load.py` answers "is this configuration worth it" -- wall
time against how long the desktop waits for a core. These pin the arithmetic
and the ambient column; the probe thread and the child run are the two seams
left to the real thing.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import measure_run_load  # noqa: E402


def test_the_score_is_a_tail_percentile_not_an_average():
    """A stutter is a tail event: one wake in ten stalling 200 ms is what he
    reports as lag, and both the mean (20 ms) and the median (0.5 ms) call
    that machine fine. Nearest-rank, no interpolation -- so a percentile is
    always a wake that really happened, and a lone outlier in a hundred
    samples sits ABOVE p99 rather than being smeared into it."""
    lag = sorted([0.5] * 90 + [200.0] * 10)
    assert measure_run_load.percentile(lag, 50) == 0.5
    assert measure_run_load.percentile(lag, 95) == 200.0
    assert measure_run_load.percentile([], 95) == 0.0, "an empty run scores 0, never raises"
    assert measure_run_load.percentile([7.0], 95) == 7.0


def test_a_configuration_is_workers_and_an_optional_reserve():
    assert measure_run_load.parse_config("16:8") == (16, 8)
    assert measure_run_load.parse_config("8") == (8, 0), "no reserve means hand it every core"


def test_every_row_shows_the_ambient_load_it_was_measured_against():
    """This machine is never idle -- a sibling session's own suite running
    through one config and not the next would swamp the difference between
    them while looking like a clean number. The column is what makes such a
    row visibly incomparable instead of silently wrong."""
    busy = {"label": "16 workers, 8 core(s) reserved", "wall_s": 234.0,
            "lag_p95_ms": 1.3, "lag_p99_ms": 17.7, "cpu_mean_pct": 75.0,
            "cpu_pinned_pct": 28.0, "ram_peak_gb": 64.0,
            "ambient_cpu_pct": 83.0, "outcome": "green"}
    table = measure_run_load.render([busy])
    assert "ambient" in table.splitlines()[0]
    assert "83%" in table.splitlines()[-1]


def test_a_red_run_is_named_red_however_fast_it_was():
    """A configuration that wins on wall time by breaking the suite has not
    won anything, so the outcome column carries the failure count and the
    frontier is read from green rows only."""
    row = {"label": "32 workers, no core(s) reserved", "wall_s": 180.0,
           "lag_p95_ms": 40.0, "lag_p99_ms": 90.0, "cpu_mean_pct": 99.0,
           "cpu_pinned_pct": 95.0, "ram_peak_gb": 80.0, "ambient_cpu_pct": 10.0,
           "outcome": "RED (7 failed)", "failed_tests": ["tests/test_a.py::test_b"]}
    table = measure_run_load.render([row])
    assert "RED (7 failed)" in table
    assert "tests/test_a.py::test_b" in table, (
        "a RED row must NAME what broke -- otherwise the sweep cannot say whether "
        "a faster configuration breaks the suite or merely looks like it might")


def test_the_failing_tests_are_read_off_the_real_pytest_summary():
    """pytest's own short summary is the source; under loadgroup a nodeid
    carries its worker group as an `@` suffix, and both forms must be caught."""
    output = ("=========================== short test summary info ====================\n"
              "FAILED tests/test_ui_sync_page.py::test_chips@tests/test_ui_sync_page.py\n"
              "ERROR tests/test_api.py::test_health - RuntimeError\n"
              "1 failed, 8858 passed, 11 skipped in 233.23s\n")
    assert measure_run_load.FAILED_LINE.findall(output) == [
        "tests/test_ui_sync_page.py::test_chips@tests/test_ui_sync_page.py",
        "tests/test_api.py::test_health"]
    assert dict((kind, int(number))
                for number, kind in measure_run_load.SUMMARY.findall(output)) == {"failed": 1}, (
        "the count the outcome column shows comes off the same summary line")

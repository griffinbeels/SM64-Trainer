"""The picture ledger (replay/ledger.py, round 32 item 40): capture stamps
every DISTINCT picture with its composition time, the RAM frame, and every
registered extra -- his spec: "we should be able to extend the frame-by-frame
information storage at any point"."""
import numpy as np

from sm64_events.replay.ledger import SAMPLE_STRIDE, PictureLedger


def _picture(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, size=(120, 160, 4), dtype=np.uint8)


def test_one_row_per_distinct_picture_not_per_grab():
    ledger = PictureLedger()
    first, second = _picture(1), _picture(2)
    assert ledger.observe(first, 100.0, 7) is True
    # The same presented picture grabbed again: no new row.
    assert ledger.observe(first.copy(), 100.017, 7) is False
    assert ledger.observe(first.copy(), 100.033, 8) is False
    assert ledger.observe(second, 100.050, 8) is True
    rows = ledger.rows_between(99.0, 101.0)
    assert [(row["ts"], row["frame"]) for row in rows] == [
        (100.0, 7), (100.05, 8)]


def test_a_change_only_off_the_sample_grid_is_missed_by_design():
    """The dedup reads a strided sample, so a change confined to skipped
    pixels does not land a row -- the documented limit the rising rule
    repairs downstream. This pins that the sample really is strided (a
    full-frame compare would pass the first assert and fail this one)."""
    ledger = PictureLedger()
    base = _picture(3)
    ledger.observe(base, 100.0, 7)
    off_grid = base.copy()
    off_grid[1, 1, 0] ^= 0xFF                  # (1,1) is skipped by stride 8
    assert ledger.observe(off_grid, 100.033, 8) is False
    on_grid = base.copy()
    on_grid[SAMPLE_STRIDE, SAMPLE_STRIDE, 0] ^= 0xFF
    assert ledger.observe(on_grid, 100.067, 8) is True


def test_a_resize_reads_as_a_new_picture():
    ledger = PictureLedger()
    ledger.observe(_picture(4), 100.0, 7)
    grown = np.zeros((240, 320, 4), dtype=np.uint8)
    assert ledger.observe(grown, 100.033, 8) is True


def test_a_grab_nobody_can_place_in_time_records_nothing():
    ledger = PictureLedger()
    assert ledger.observe(_picture(5), None, 7) is False
    assert ledger.rows_between(0.0, 1e12) == []


def test_registered_stamps_ride_every_later_row():
    """THE extension point: one line registers a probe, every later row
    carries its field, and a probe that raises loses its field, never the
    row."""
    ledger = PictureLedger()
    ledger.observe(_picture(6), 100.0, 7)
    ledger.stamps["mario_action"] = lambda: 0x0880
    ledger.stamps["broken"] = lambda: 1 / 0
    ledger.observe(_picture(7), 100.033, 8)
    rows = ledger.rows_between(99.0, 101.0)
    assert "mario_action" not in rows[0]
    assert rows[1]["mario_action"] == 0x0880 and "broken" not in rows[1]
    assert rows[1]["frame"] == 8


def test_observe_never_raises_even_on_garbage():
    ledger = PictureLedger()
    assert ledger.observe(object(), 100.0, 7) is False


def test_rows_between_filters_by_composition_time():
    ledger = PictureLedger()
    for tick in range(5):
        ledger.observe(_picture(10 + tick), 100.0 + tick, 7 + tick)
    rows = ledger.rows_between(101.0, 103.0)
    assert [row["frame"] for row in rows] == [8, 9, 10]


def test_the_ledger_is_bounded():
    ledger = PictureLedger(retention_s=1.0)      # 35 rows of headroom
    for tick in range(200):
        ledger.observe(_picture(1000 + tick), 100.0 + tick * 0.033, tick)
    assert len(ledger.rows_between(0.0, 1e12)) == 35


def test_a_torn_grab_settling_lands_one_row_not_two():
    """A grab can catch the surface mid-update; the settled picture arrives
    a few ms later and must fold into the same row (measured on attempt
    4518: 118 of 647 row gaps under 20 ms against the game's ~33 ms
    cadence). The row keeps the FIRST time and stamp -- the present
    moment's."""
    ledger = PictureLedger()
    ledger.observe(_picture(30), 100.0, 7)
    ledger.observe(_picture(31), 100.008, 8)     # the same present, settling
    ledger.observe(_picture(32), 100.041, 8)     # the next real picture
    rows = ledger.rows_between(99.0, 101.0)
    assert [(row["ts"], row["frame"]) for row in rows] == [
        (100.0, 7), (100.041, 8)]


def test_caller_extras_ride_the_row():
    """Per-grab stamps the registry cannot compute (they need THIS grab's
    own time -- the frame-edge phase) arrive as an argument and merge."""
    ledger = PictureLedger()
    ledger.observe(_picture(40), 100.0, 7, {"phase": 0.011})
    row = ledger.rows_between(99.0, 101.0)[0]
    assert row["phase"] == 0.011 and row["frame"] == 7

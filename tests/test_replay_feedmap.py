"""Source identity must survive gaps, clock collisions, holds and resets."""
import pytest

from sm64_events.replay.feedmap import feed_map
from sm64_events.replay.ledger import PictureLedger
from sm64_events.replay.media import MediaRun


def records():
    # Deliberately repeat raw game counters across a save-state load.
    rows = [{"ts": 100 + i / 30, "frame": frame, "igt": 40 + i}
            for i, frame in enumerate([100, 101, 99, 100, 101])]
    feeds = [{"ts": row["ts"], "run_id": "a", "pts": i * 3000}
             for i, row in enumerate(rows)]
    return rows, feeds


def project(row):
    return row["frame"], row["igt"]


def test_the_same_counter_in_a_later_epoch_cannot_overwrite_a_picture():
    rows, feeds = records()
    values, repeats, stats = feed_map([0, 3000, 6000, 9000, 12000], "a", rows, feeds, project)
    assert values == [(100, 40), (101, 41), (99, 42), (100, 43), (101, 44)]
    assert repeats == [False] * 5
    assert stats["matched"] == 5 and stats["unmatched"] == 0
    assert stats["method"] == "source_pts"


def test_a_cut_uses_source_pts_instead_of_counting_from_the_first_feed():
    rows, feeds = records()
    values, _, _ = feed_map([6000, 9000], "a", rows, feeds, project)
    assert values == [(99, 42), (100, 43)]


@pytest.mark.parametrize("shift", [1, 90, 810, 2999])
def test_an_unpreserved_origin_is_refused_instead_of_fitted(shift):
    rows, feeds = records()
    values, _, stats = feed_map([shift + i * 3000 for i in range(5)], "a", rows, feeds, project)
    assert values is None and stats["matched"] == 0


def test_wall_time_and_feed_spacing_do_not_decide_identity():
    rows, feeds = records()
    for feed in feeds:
        feed["at"] = -999.0  # encoder write completion may be arbitrarily late
    feeds[2]["pts"] = 3001  # emulator catch-up: distinct pictures one tick apart
    values, _, _ = feed_map([0, 3000, 3001, 9000, 12000], "a", rows, feeds, project)
    assert values == [project(row) for row in rows]


def test_unknown_feed_or_state_stays_unknown_without_filling_the_gap():
    rows, feeds = records()
    del feeds[1]
    del rows[3]
    values, _, stats = feed_map([0, 3000, 6000, 9000, 12000], "a", rows, feeds, project)
    assert values == [(100, 40), None, (99, 42), None, (101, 44)]
    assert stats["unmatched"] == 2


def test_source_misses_do_not_add_pictures_to_the_map():
    rows, feeds = records()
    values, _, _ = feed_map([0, 6000, 12000], "a", rows, feeds, project)
    assert values == [(100, 40), (99, 42), (101, 44)]


def test_different_encoder_runs_cannot_answer_each_others_pts():
    rows, feeds = records()
    feeds.extend({"ts": rows[-1]["ts"], "pts": i * 3000, "run_id": "b"}
                 for i in range(5))
    assert feed_map([0], "a", rows, feeds, project)[0] == [(100, 40)]
    assert feed_map([0], "b", rows, feeds, project)[0] == [(101, 44)]
    assert feed_map([0], "missing", rows, feeds, project)[0] is None


@pytest.mark.parametrize("collision", ["feed", "row", "slot"])
def test_ambiguous_identity_rejects_every_occurrence(collision):
    rows, feeds = records()
    points = [0, 3000, 6000, 9000, 12000]
    if collision == "feed":
        feeds.append({**feeds[2], "ts": rows[0]["ts"]})
    elif collision == "row":
        rows.append({**rows[2], "igt": 999})
    else:
        points.append(6000)
    values, _, _ = feed_map(points, "a", rows, feeds, project)
    assert values[2] is None
    if collision == "slot":
        assert values[-1] is None
    assert values[0] == (100, 40) and values[4] == (101, 44)


def test_a_cut_beginning_with_a_heartbeat_retains_its_actual_picture():
    ledger = PictureLedger()
    run = MediaRun("a", 100.0)
    ledger.mark_fed(100.0, 100.0, media_run=run, pts=0)
    ledger.mark_fed(None, 103.0, media_run=run, pts=270000)
    rows = [{"ts": 100.0, "frame": 50, "igt": 12}]
    values, repeats, _ = feed_map([270000], "a", rows,
                                  ledger.feeds_between(102, 104), project)
    assert values == [(50, 12)] and repeats == [True]
    ledger.mark_fed(None, 104.0, media_run=MediaRun("b", 104.0), pts=0)
    assert feed_map([0], "b", rows, ledger.feeds_between(104, 105), project)[0] is None


def test_a_source_without_its_clock_cannot_claim_an_exact_map():
    rows, feeds = records()
    assert feed_map(None, "a", rows, feeds, project)[2]["reason"] == "missing_source_clock"
    assert feed_map([0], None, rows, feeds, project)[0] is None


def test_a_projector_may_refuse_an_inexact_capture():
    rows, feeds = records()
    assert feed_map([0], "a", rows, feeds, lambda row: None)[0] is None


def test_native_occurrences_survive_identical_timestamps_and_game_counters(tmp_path):
    import numpy as np
    ledger = PictureLedger()
    ledger.open_archive(tmp_path / 'identity.sqlite')
    run = MediaRun('native-run', 100.0)
    # Tiny distinct samples and differing states are selected at the same UTC.
    for serial in [1, 2]:
        source_id = f'producer-creation:epoch-9:{serial}'
        assert ledger.observe(np.full((8, 8, 4), serial, np.uint8), 100.0,
                              serial, {'source_id': source_id, 'igt': serial + 20})
        ledger.mark_fed(100.0, 100.0 + (serial - 1) / 90000, media_run=run,
                        pts=serial - 1, source_id=source_id)
    ledger.mark_fed(None, 102, media_run=run, pts=180000)
    ledger.flush()
    ledger.detach()
    rows, feeds = ledger.rows_between(99, 103), ledger.feeds_between(99, 103)
    values, repeats, stats = feed_map([0, 1, 180000], run.id, rows, feeds, project)
    assert values == [(1, 21), (2, 22), (2, 22)] and repeats == [False, False, True]
    assert stats['matched'] == 3
    ledger.open_archive(tmp_path / 'identity.sqlite')
    ledger.mark_fed(None, 103, media_run=MediaRun('new-run', 103), pts=0)
    assert feed_map([0], 'new-run', rows, ledger.feeds_between(103, 104), project)[0] is None


@pytest.mark.parametrize('source_id', [None, '', [], 7, 'missing', 'x' * 161])
def test_native_id_never_falls_back_to_an_unrelated_timestamp(source_id):
    rows = [{'ts': 100, 'frame': 9, 'igt': 20}]
    feeds = [{'ts': 100, 'run_id': 'a', 'pts': 0, 'source_id': source_id}]
    assert feed_map([0], 'a', rows, feeds, project)[0] is None


def test_native_id_collision_and_generation_reuse_stay_unknown():
    rows = [{'ts': 100, 'frame': 1, 'igt': 1, 'source_id': 'epoch-A:1'},
            {'ts': 100, 'frame': 2, 'igt': 2, 'source_id': 'epoch-B:1'}]
    feeds = [{'ts': 100, 'run_id': 'a', 'pts': 0, 'source_id': 'epoch-A:1'},
             {'ts': 100, 'run_id': 'a', 'pts': 1, 'source_id': 'epoch-B:1'}]
    assert feed_map([0, 1], 'a', rows, feeds, project)[0] == [(1, 1), (2, 2)]
    rows.append({**rows[0], 'igt': 99})
    assert feed_map([0, 1], 'a', rows, feeds, project)[0] == [None, (2, 2)]

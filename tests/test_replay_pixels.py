"""Deferred plugin preparation must preserve the eager recorder's output."""
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from sm64_events.replay import pixels as P
from sm64_events.replay.clock import CaptureClock
from sm64_events.replay.ledger import SAMPLE_STRIDE
from test_replay_recorder import T0, FakeAvSink, make_recorder


def eager(raw):
    """Independent reference for the old callback's full BGRA picture."""
    return np.concatenate((raw[::-1], np.full((*raw.shape[:2], 1), 255,
                                            dtype=np.uint8)), axis=2)


@pytest.mark.parametrize("height", [9, 10, 16, 17])
@pytest.mark.parametrize("width", [1, 7, 17, 1190])
def test_deferred_sample_matches_full_bgra_before_odd_edge_crop(height, width):
    padded = np.random.default_rng(81).integers(0, 256, (height, width + 3, 3),
                                              dtype=np.uint8)
    raw = padded[:, :width]
    captured = P.BgrPicture(raw)
    expected = eager(raw)
    assert captured.shape == expected.shape
    assert captured.sample_bytes(SAMPLE_STRIDE) == expected[::SAMPLE_STRIDE, ::SAMPLE_STRIDE].tobytes()
    actual = captured.as_bgra()
    assert actual.flags.c_contiguous
    np.testing.assert_array_equal(actual, expected)
    padded.fill(0)
    assert captured.as_bgra() is actual
    np.testing.assert_array_equal(actual, expected)


def recorder(tmp_path, *, picture_feed=True):
    rec = make_recorder(tmp_path, None, None)
    rec._clock = CaptureClock(anchor_qpc_100ns=0, anchor_utc=T0)
    rec._picture_feed = picture_feed
    sink = FakeAvSink()
    sink.has_room = lambda: True
    rec._video_sink = sink
    return rec, sink


def stamp(frame):
    return SimpleNamespace(frame=frame, extras=lambda: {
        "exact": True, "pad": [12, -34, 0x8000], "igt_overall": frame % 50})


def rows(rec):
    return rec.ledger.rows_between(0, 1e12)


def test_recorder_eager_and_deferred_match_across_rejects_resets_and_sources(tmp_path, monkeypatch):
    baseline, old_sink = recorder(tmp_path / "old")
    candidate, new_sink = recorder(tmp_path / "new")
    raw_a = np.random.default_rng(6).integers(0, 256, (17, 17, 3), dtype=np.uint8)
    raw_b, raw_c = raw_a ^ 7, raw_a ^ 31
    alpha_changed = eager(raw_c)
    alpha_changed[:, :, 3] = 127
    # Clock ticks, game counter, pixels, capacity, stamped. Includes same-stamp
    # folds, retry after queue refusal, counter reset, no stamp and source swap.
    schedule = [
        (0, 100, raw_a, True, True),
        (330000, 101, raw_a, True, True),
        (400000, 100, raw_b, False, True),
        (50000, 100, raw_b, True, True),
        (500000, 101, raw_b, True, True),
        (510000, 102, raw_c, True, True),
        (520000, 1, raw_a, True, True),
        (860000, 2, raw_a, True, True),
        (1200000, 3, raw_c, True, False),
        (1540000, 4, eager(raw_c), True, False),
        (1880000, 5, alpha_changed, True, False),
        (2220000, 6, raw_c, True, True),
        (2560000, 7, raw_c[:10, :7], True, True),
    ]
    convert = Mock(wraps=P.to_bgra_top_down)
    monkeypatch.setattr(P, "to_bgra_top_down", convert)
    accepted = []
    for ticks, counter, raw, room, stamped in schedule:
        old_sink.has_room = new_sink.has_room = lambda available=room: available
        legacy = raw if raw.shape[2] == 4 else eager(raw)
        deferred = raw if raw.shape[2] == 4 else P.BgrPicture(raw)
        own_stamp = stamp(counter) if stamped else None
        old_count, new_count = len(old_sink.frames), len(new_sink.frames)
        before_converts = convert.call_count
        baseline._on_frame(legacy, ticks, own_stamp)
        candidate._on_frame(deferred, ticks, own_stamp)
        added = len(old_sink.frames) - old_count
        assert len(new_sink.frames) - new_count == added
        accepted.append(bool(added))
        assert convert.call_count - before_converts == (added if raw.shape[2] == 3 else 0)
        if added:
            np.testing.assert_array_equal(new_sink.frames[-1], old_sink.frames[-1])
            assert new_sink.frames[-1].flags.c_contiguous
            # Feed association must be unchanged, independently of conversion.
            for rec, sink in [(baseline, old_sink), (candidate, new_sink)]:
                rec._on_fed(sink.tags[-1], T0.timestamp() + ticks / 1e7, pts=ticks)
        assert rows(candidate) == rows(baseline)
    assert accepted == [True, False, False, False, False, True, True,
                        False, True, False, True, True, True]
    assert new_sink.tags == old_sink.tags
    assert candidate.ledger.feeds_between(0, 1e12) == baseline.ledger.feeds_between(0, 1e12)
    assert candidate._grabs_skipped == baseline._grabs_skipped == 1


def test_failed_preparation_leaves_no_archive_row_and_allows_retry(tmp_path, monkeypatch):
    rec, sink = recorder(tmp_path)
    rec.ledger.open_archive(tmp_path / "pictures.sqlite")
    raw = np.zeros((17, 17, 3), dtype=np.uint8)
    convert = Mock(side_effect=[MemoryError("allocation refused"), eager(raw)])
    monkeypatch.setattr(P, "to_bgra_top_down", convert)
    try:
        rec._on_frame(P.BgrPicture(raw), 0, stamp(100))
        assert rows(rec) == [] and sink.frames == []
        rec._on_frame(P.BgrPicture(raw), 330000, stamp(100))
        assert len(rows(rec)) == len(sink.frames) == 1
        assert rows(rec)[0]["ts"] == T0.timestamp() + .033
        assert convert.call_count == 2
    finally:
        rec.ledger.detach()


def test_missing_clock_does_not_prepare_or_record_picture(tmp_path, monkeypatch):
    rec, sink = recorder(tmp_path)
    rec._clock = None
    convert = Mock(side_effect=AssertionError("unplaceable picture converted"))
    monkeypatch.setattr(P, "to_bgra_top_down", convert)
    rec._on_frame(P.BgrPicture(np.zeros((17, 17, 3), dtype=np.uint8)), 0, stamp(10))
    assert not convert.called and rows(rec) == [] and sink.frames == []


def test_cfr_sink_receives_bgra_even_when_ledger_rejects(tmp_path, monkeypatch):
    rec, sink = recorder(tmp_path, picture_feed=False)
    raw = np.zeros((17, 17, 3), dtype=np.uint8)
    convert = Mock(wraps=P.to_bgra_top_down)
    monkeypatch.setattr(P, "to_bgra_top_down", convert)
    for ticks in [0, 330000]:
        rec._on_frame(P.BgrPicture(raw), ticks, stamp(1))
    assert len(sink.frames) == convert.call_count == 2
    assert len(rows(rec)) == 1
    for frame in sink.frames:
        np.testing.assert_array_equal(frame, eager(raw))


def test_in_process_writer_retains_bgra_for_gap_fill_and_skips_duplicate_ticks(tmp_path, monkeypatch):
    rec, _ = recorder(tmp_path)
    rec._video_sink = None
    rec._writer = SimpleNamespace(write_video=Mock())
    raw = np.zeros((17, 17, 3), dtype=np.uint8)
    convert = Mock(wraps=P.to_bgra_top_down)
    monkeypatch.setattr(P, "to_bgra_top_down", convert)
    for ticks in [0, 0, 1000000]:
        rec._on_frame(P.BgrPicture(raw), ticks, stamp(1))
    calls = rec._writer.write_video.call_args_list
    assert [call.args[1] for call in calls] == [0, 1, 2, 3]
    assert convert.call_count == 2
    for call in calls:
        np.testing.assert_array_equal(call.args[0], eager(raw))
    assert calls[0].args[0] is calls[1].args[0] is calls[2].args[0]
    assert rec._last_frame is calls[-1].args[0]


def test_cfr_allocation_failure_cannot_commit_a_folded_sample(tmp_path, monkeypatch):
    rec, sink = recorder(tmp_path, picture_feed=False)
    first = np.zeros((17, 17, 3), dtype=np.uint8)
    second = first ^ 255
    convert = Mock(side_effect=[eager(first), MemoryError("allocation refused"), eager(second)])
    monkeypatch.setattr(P, "to_bgra_top_down", convert)
    rec._on_frame(P.BgrPicture(first), 0, stamp(100))
    with pytest.raises(MemoryError):
        rec._on_frame(P.BgrPicture(second), 50000, stamp(100))
    rec._on_frame(P.BgrPicture(second), 330000, stamp(101))
    assert [row["frame"] for row in rows(rec)] == [100, 101]
    assert len(sink.frames) == 2

"""The desktop grab's BGRA reaches the picture ledger and the sinks as handed
over: recorded by time only, one row per distinct picture, in lockstep with
the encoder's budget."""
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from sm64_events.replay.clock import CaptureClock
from test_replay_recorder import T0, FakeAvSink, make_recorder


def recorder(tmp_path, *, picture_feed=True):
    rec = make_recorder(tmp_path, None, None)
    rec._clock = CaptureClock(anchor_qpc_100ns=0, anchor_utc=T0)
    rec._picture_feed = picture_feed
    sink = FakeAvSink()
    sink.has_room = lambda: True
    rec._video_sink = sink
    return rec, sink


def grab(value):
    return np.full((17, 17, 4), value, dtype=np.uint8)


def rows(rec):
    return rec.ledger.rows_between(0, 1e12)


def test_picture_feed_files_distinct_grabs_by_time_and_waits_for_encoder_room(tmp_path):
    rec, sink = recorder(tmp_path)
    a, b = grab(1), grab(2)
    # An unchanged grab feeds nothing; a grab with no encoder budget is
    # neither recorded nor encoded, so the same picture lands on the retry.
    for ticks, picture, room in [(0, a, True), (330000, a, True),
                                 (660000, b, False), (990000, b, True)]:
        sink.has_room = lambda available=room: available
        rec._on_frame(picture, ticks)
    recorded = rows(rec)
    assert [row["frame"] for row in recorded] == [None, None]
    assert recorded[0]["ts"] == pytest.approx(T0.timestamp())
    assert recorded[1]["ts"] == pytest.approx(T0.timestamp() + .099)
    assert len(sink.frames) == 2 and sink.frames[0] is a and sink.frames[1] is b
    assert sink.tags == [(None, row["ts"]) for row in recorded]
    assert rec._grabs_skipped == 1


def test_a_grab_without_a_capture_clock_is_neither_recorded_nor_encoded(tmp_path):
    rec, sink = recorder(tmp_path)
    rec._clock = None
    rec._on_frame(grab(10), 0)
    assert rows(rec) == [] and sink.frames == []


def test_cfr_sink_receives_every_grab_while_the_ledger_keeps_one_row_per_picture(tmp_path):
    rec, sink = recorder(tmp_path, picture_feed=False)
    picture = grab(0)
    for ticks in [0, 330000]:
        rec._on_frame(picture, ticks)
    assert len(sink.frames) == 2 and all(frame is picture for frame in sink.frames)
    assert sink.tags == [None, None]
    assert len(rows(rec)) == 1


def test_in_process_writer_fills_gaps_with_the_last_grab_and_skips_duplicate_ticks(tmp_path):
    rec, _ = recorder(tmp_path)
    rec._video_sink = None
    rec._writer = SimpleNamespace(write_video=Mock())
    first, second = grab(0), grab(255)
    for picture, ticks in [(first, 0), (second, 0), (second, 1000000)]:
        rec._on_frame(picture, ticks)
    calls = rec._writer.write_video.call_args_list
    assert [call.args[1] for call in calls] == [0, 1, 2, 3]
    assert all(call.args[0] is first for call in calls[:3])
    assert calls[3].args[0] is second and rec._last_frame is second

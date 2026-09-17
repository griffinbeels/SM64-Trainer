"""Selection admission and cold AAC preparation preserve recorded identities."""

from copy import deepcopy
import io
from types import SimpleNamespace

import numpy as np
import pytest

from sm64_events.replay.ledger import PictureLedger
from sm64_events.replay.media import MediaRun
from sm64_events.replay.packetmux import H264Format, PacketFragmentMux
from sm64_events.replay.pixels import SampledPicture
from test_packetmux import (
    Output, RATE, decode_audio, fixture_packets, supply, verify_video,
)


def sample(value):
    return SampledPicture(8, 8, bytes([value]) * 4, 8)


def test_selector_queues_complete_probe_snapshot_without_touching_archive(tmp_path):
    actual = PictureLedger()
    actual.open_archive(tmp_path / "pictures.sqlite3")
    queued, probes = [], []
    native = {"source_id": "native:1", "exact": True, "pad": [5, 6, 7]}
    extension = {"positions": [[1, 2, 3]]}

    def probe():
        probes.append(True)
        return extension

    actual.stamps["extension"] = probe
    # Production admission bounds the row before making this ownership copy.
    selector = actual.selection_only(lambda row: queued.append(deepcopy(row)))
    actual.stamps["late"] = lambda: pytest.fail("registrations are a wiring snapshot")
    try:
        selected = selector.observe_result(sample(1), 1000.125, 42, native)
        assert selected.kind == "selected" and selected.source_id == "native:1"
        assert selector._archive is None and selector.stamps is not actual.stamps
        assert selector._rows.maxlen == 350 and probes == [True]
        assert actual.rows_between(0, 2000) == []
        assert queued == [{"ts": 1000.125, "frame": 42, **native, "extension": extension}]

        native["pad"][0] = 99
        extension["positions"][0][0] = 99
        assert queued[0]["pad"] == [5, 6, 7]
        assert queued[0]["extension"] == {"positions": [[1, 2, 3]]}
        assert selector.observe_result(sample(1), 1000.158, 43, native).kind == "coalesced"
        assert probes == [True] and len(queued) == 1

        actual.accept_row(queued[0])
        expected = deepcopy(queued[0])
        queued[0]["extension"]["positions"][0][0] = 55
        assert actual.rows_between(0, 2000) == [expected]
        assert actual._rows[-1][2]["extension"] == expected["extension"]
        assert actual._prev_sample is None and probes == [True]
        run = MediaRun("bound-run", 1000.125)
        actual.mark_fed(1000.125, 1000.125, media_run=run, pts=0, source_id="native:1")
        assert actual.feeds_between(0, 2000)[0]["source_id"] == "native:1"
    finally:
        actual.detach()


@pytest.mark.parametrize("failure", ["raise", "refuse"])
def test_failed_row_admission_preserves_last_selected_picture(failure):
    queued = []
    refuse = False

    def admit(row):
        if refuse:
            if failure == "raise":
                raise RuntimeError("sink full")
            return False
        queued.append(deepcopy(row))

    selector = PictureLedger().selection_only(admit)
    assert selector.observe_result(sample(1), 1000, 1, {"source_id": "one"}).kind == "selected"
    refuse = True
    failed = selector.observe_result(sample(2), 1000.033, 2, {"source_id": "two"})
    assert failed.kind == "failed" and failed.reason == "admission_failed"
    assert len(selector._rows) == 1 and selector._prev_sample == bytes([1]) * 4
    assert selector.observe_result(sample(1), 1000.05, 2).source_id == "one"
    refuse = False
    retry = selector.observe_result(sample(2), 1000.066, 2, {"source_id": "two"})
    assert retry.kind == "selected" and retry.source_id == "two"
    assert [row["source_id"] for row in queued] == ["one", "two"]


def test_selection_only_preserves_fold_restart_and_bounded_retention():
    synchronous = PictureLedger()
    admitted = []
    selector = synchronous.selection_only(lambda row: admitted.append(deepcopy(row)))
    for value, stamp, frame, source in [
        (1, 1000, 10, "one"),
        (2, 1000.010, 10, "folded"),
        (2, 1000.033, 11, "equal"),
        (3, 1000.066, 12, "three"),
    ]:
        extras = {"source_id": source, "exact": True}
        assert selector.observe_result(sample(value), stamp, frame, extras) == synchronous.observe_result(
            sample(value), stamp, frame, extras)
    assert admitted == synchronous.rows_between(0, 2000)
    selector.restart_selection()
    assert selector.observe_result(sample(3), 1000.067, 12, {"source_id": "new-run"}).kind == "selected"
    for index in range(400):
        assert selector.observe_result(sample(index % 256), 1001 + index / 30, index).kind == "selected"
    assert len(selector._rows) == 350 and not selector._feeds


def test_accept_row_archive_refusal_does_not_publish_cached_row():
    def fail(row):
        raise OSError("identity storage unavailable")

    actual = PictureLedger()
    actual._archive = SimpleNamespace(add_row=fail)
    with pytest.raises(OSError, match="identity storage"):
        actual.accept_row({"ts": 1000, "frame": 1, "source_id": "one"})
    assert not actual._rows and actual._prev_sample is None


def make_mux(output, run):
    return PacketFragmentMux(output, H264Format(320, 240, 30), run,
                             audio_rate=RATE, audio_bitrate=160000,
                             packet_limit=1 << 20, pcm_limit=384000)


def test_prepared_unbound_mux_emits_nothing_and_rejects_media():
    output = io.BytesIO()
    mux = make_mux(output, None)
    try:
        mux.prepare()
        mux.prepare()
        assert mux.audio.codec_context.is_open
        assert mux.audio.codec_context.format.name == "fltp"
        assert mux.video.codec_context is None
        with pytest.raises(RuntimeError, match="not bound"):
            mux.write_video(fixture_packets()[0])
        with pytest.raises(RuntimeError, match="not bound"):
            mux.write_pcm(bytes(3840), 1000000000)
        assert output.getvalue() == b""
        assert (mux.video_count, mux.audio_input_samples, mux.audio_output_packets) == (0, 0, 0)
        assert not mux.failed
        mux.close()  # cancelled before a first source offer
        assert output.getvalue() == b"" and mux.closed
    finally:
        mux.abort()


def test_prepared_mux_binds_actual_origin_and_preserves_video_and_audio(tmp_path):
    packets = fixture_packets()
    run = MediaRun.starting_at(1789329683.081303)
    end_sample = round((packets[-1].pts + packets[-1].duration) / 90000 * RATE)
    events = [(packet.pts / 90000, "video", packet) for packet in packets]
    events += [(first / RATE, "audio", (first, min(960, end_sample - first)))
               for first in range(-2400, end_sample, 960)]
    events.sort(key=lambda event: event[0])
    recordings = []
    for prepared in (False, True):
        directory = tmp_path / str(prepared)
        directory.mkdir()
        output = Output(directory, run)
        mux = make_mux(output, None if prepared else run)
        try:
            if prepared:
                mux.prepare()
                assert output.bytes == 0 and mux.audio_output_packets == 0
                mux.bind_run(run)
                assert mux.run is run and output.bytes == 0
            with pytest.raises(RuntimeError, match="already bound"):
                mux.bind_run(MediaRun("wrong-run", run.origin_ts + 1))
            supply(mux, events, run, 0)
            mux.close()
            output.finish()
            verify_video(output, packets, packets, None)
            assert mux.audio_input_samples == end_sample
            recordings.append(decode_audio(output.path))
        finally:
            mux.abort()
            if not output.file.closed:
                output.finish()
    first, baseline = recordings[0]
    prepared_first, prepared_audio = recordings[1]
    assert first == prepared_first == 0
    assert np.array_equal(prepared_audio, baseline)

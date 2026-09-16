"""Worker failure paths must release libav without false completion."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import io
import av
import pytest
from sm64_events.replay.media import MediaRun
from sm64_events.replay.packetmux import PacketFragmentMux
from test_packetmux import FIXTURE, fixture_packets


@pytest.fixture
def mux():
    with av.open(str(FIXTURE / "witness.h264")) as source:
        value = PacketFragmentMux(
            io.BytesIO(),
            source.streams.video[0],
            MediaRun("failure", 1000),
            audio_rate=48000,
            audio_bitrate=160000,
            packet_limit=8192,
            pcm_limit=384000,
        )
        yield value
        value.abort()


class ContainerClose:
    def __init__(self, delegate, *, fail=False):
        self.delegate = delegate
        self.fail = fail
        self.calls = 0

    def close(self):
        self.calls += 1
        self.delegate.close()
        if self.fail:
            raise OSError("container cleanup failure")


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_flush_failure_releases_container_and_preserves_cause(mux, cleanup_fails):
    proxy = ContainerClose(mux.mux, fail=cleanup_fails)
    mux.mux = proxy

    def broken_flush():
        raise ValueError("audio flush failure")

    mux._drain_filter = broken_flush
    with pytest.raises(ValueError, match="audio flush failure"):
        mux.close()
    assert proxy.calls == 1 and mux.failed and mux.closed
    assert bool(mux.cleanup_error) == cleanup_fails
    mux.abort()
    assert proxy.calls == 1


def test_container_failure_alone_cannot_mark_success(mux):
    proxy = ContainerClose(mux.mux, fail=True)
    mux.mux = proxy
    with pytest.raises(RuntimeError, match="container close failed"):
        mux.close()
    assert mux.failed and mux.closed and proxy.calls == 1


def test_foreign_worker_cannot_close_or_write(mux):
    with ThreadPoolExecutor(max_workers=1) as other:
        for call in [
            mux.close,
            mux.abort,
            lambda: mux.write_video(fixture_packets()[0]),
        ]:
            with pytest.raises(RuntimeError, match="media worker"):
                other.submit(call).result()
    assert not mux.closed and not mux.failed and mux.video_count == 0


@pytest.mark.parametrize(
    "change",
    [dict(data=bytearray(b"bad")), dict(pts=0.1), dict(duration=True), dict(key=1)],
)
def test_invalid_picture_never_enters_libav(mux, change):
    with pytest.raises(ValueError):
        mux.write_video(replace(fixture_packets()[0], **change))
    assert mux.video_count == 0 and mux.last_pts is None


def test_gap_or_overlap_needs_new_run(mux):
    first, second = fixture_packets()[:2]
    mux.write_video(first)
    with pytest.raises(ValueError, match="contiguous"):
        mux.write_video(replace(second, pts=second.pts + 1))
    assert mux.video_count == 1
    mux.write_video(second)
    assert mux.video_count == 2

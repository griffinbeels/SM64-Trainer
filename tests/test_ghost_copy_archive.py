"""The ghost copy's archive: its own ring prefix, the clean run's clock, playable.

Ghosts in recording keeps a second recording beside the clean one (the ghost
copy). Its fragments live under the "ghostcopy:" ring prefix in
recorder.ghost_copies, share the ring budget and its span pins, and can never
prune a clean picture identity. A copy starts whenever the switch does, so
its first picture sits mid-run on the clean run's clock: these tests drive
real packets and PCM through the sink GpuCapture makes for it, the real
packet mux and the archive, and play the result through VirtualMp4.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import io
import time
from types import SimpleNamespace as NS

import av
import numpy as np
import pytest

from sm64_events.ghost.capture import NativeCapture
from sm64_events.memory.layout import US
from sm64_events.replay.audio import GhostTimeline
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.gpucapture import GpuCapture
from sm64_events.replay.gpumediaworker import MediaWorker, NULL_LEDGER
from sm64_events.replay.media import MediaRun
from sm64_events.replay.packetmux import NativeFormat
from sm64_events.replay.recorder import ReplayRecorder
from sm64_events.replay.ring import SegmentRing
from test_packetmux import RATE, fixture_packets, pcm
from test_replay_startup_order import WIN, make

ORIGIN = 1_790_000_000.25  # the clean run's origin: today's scale, on the microsecond grid
CHUNK = 4800  # 100 ms PCM blocks keep the sink's queue far under its 256-block bound


class HeldLock:
    def close(self):
        pass


def recorder(tmp_path):
    """A real recorder's ring, ledger and both archives; no capture starts."""
    cfg = ReplayConfig(scratch_dir=tmp_path / "buffer", picture_feed=True)
    rec = ReplayRecorder(cfg, lambda title: None, lambda win: None, lambda pid: None,
                         codec="h264_nvenc", recorder_lock_factory=HeldLock)
    rec._prepare_scratch()
    rec.ledger.open_archive(cfg.scratch_dir / rec._ledger_name)
    return rec


def gpu_route(rec):
    """The GPU capture the recorder would pair, wired to its ghost copies."""
    capture = GpuCapture(WIN.pid, US, nominal_rate=30)
    capture.create_sink(rec._cfg, None, rec.ledger, rec.fragments.create)
    capture.set_ghost_copy(wanted=lambda: True, publish=rec.ghost_copies.create,
                           timeline=lambda: None)
    return capture


def ready(sink):
    deadline = time.monotonic() + 5
    while not sink.ready:
        assert time.monotonic() < deadline, "sink never prepared"
        time.sleep(0.01)
    return sink


def feed(sink, run, packets, *, rows=None):
    """Video in pts order, the first picture first, then PCM covering it the
    way GpuMedia's follower hands it on (video, then the audio it covers)."""
    end = round((packets[-1].pts + packets[-1].duration) / 90000 * RATE)
    first = round(packets[0].pts / 90000 * RATE) - RATE // 2  # PCM from before the copy began
    events = [(p.pts / 90000, 0, p) for p in packets]
    events += [(f / RATE, 1, (f, min(CHUNK, end - f))) for f in range(first, end, CHUNK)]
    events.sort(key=lambda e: e[:2])
    events.remove((packets[0].pts / 90000, 0, packets[0]))
    for _, kind, data in [(0, 0, packets[0]), *events]:
        if kind:
            start, count = data
            sink.write_pcm(pcm(start, count), round(run.origin_ts * 1e6 + start / RATE * 1e6))
        else:
            source_id = f"ghostcopy:{data.pts}" if rows is None else f"source:{data.occurrence}"
            at = run.origin_ts + data.pts / 90000
            if rows is not None:
                sink.add_row(dict(ts=at, frame=data.occurrence, source_id=source_id, exact=True))
            sink.publish_picture(data, at, at, media_run=run, pts=data.pts,
                                 source_id=source_id, repeat=False)
    sink.close()
    sink.finish(timeout=10)


def utc(run, ticks):
    return datetime.fromtimestamp(run.origin_ts + ticks / 90000, timezone.utc)


def decoded(media):
    data = b"".join(media.chunks())
    with av.open(io.BytesIO(data)) as source:
        video = [round(frame.pts * frame.time_base * 90000) for frame in source.decode(video=0)]
    with av.open(io.BytesIO(data)) as source:
        audio = [(frame.pts * frame.time_base, frame.to_ndarray()) for frame in source.decode(audio=0)]
    return video, audio


def test_a_ghost_copy_starting_mid_run_muxes_indexes_and_plays(tmp_path):
    """The copy begins 5 s and 7 ticks into the clean run, off every audio and
    microsecond grid: libav alone would write an edit list the index refuses."""
    rec = recorder(tmp_path)
    try:
        start = 5 * 90000 + 7
        packets = [replace(p, pts=p.pts + start) for p in fixture_packets()]
        sink = ready(gpu_route(rec).ghost_copy_sink("h264", 320, 240))
        run = MediaRun("ghost-copy", ORIGIN)  # the clean run's origin, as the lane binds it
        sink.bind(run)
        feed(sink, run, packets)

        ((_, low, high),) = rec.ghost_copies.intervals()
        assert low == start, "indexed on the clean run's ticks"
        assert packets[-1].pts < high <= packets[-1].pts + packets[-1].duration
        groups = [g for g in rec.ring.temporary_groups() if not g.startswith("fragments:")]
        assert groups and all(g.startswith("ghostcopy:ghost-copy:") for g in groups)
        files = sorted((tmp_path / "buffer" / "ghostcopy").iterdir())
        assert files and all(f.name.startswith("ghostcopy_") for f in files)
        assert rec.fragments.intervals() == [], "nothing reached the clean archive"

        with rec.ghost_copies.open(utc(run, start), utc(run, high)) as (media, result, source):
            assert result.start_utc == utc(run, start), "the same UTC the clean run gives that picture"
            assert source == {"version": 1, "run_id": "ghost-copy", "start": start, "end": source["end"]}
            relative = [p.pts - start for p in packets]
            assert media.frame_ticks == relative
            assert result.frame_times == [tick / 90000 for tick in relative]
            video, audio = decoded(media)
        assert video == relative, "every picture plays at its own tick"

        # The audio sits on the run's clock to within two samples (42 µs): the
        # tone's phase at each decoded sample matches the source PCM there.
        offset = start / 90000 * RATE  # the copy's first instant, in run samples
        times = np.concatenate([float(at) + np.arange(a.shape[1]) / RATE for at, a in audio])
        left = np.concatenate([a[0] for _, a in audio])
        window = slice(RATE // 2, RATE // 2 + RATE)  # a second, past AAC's start-up
        source_index = np.rint(offset + times[window] * RATE).astype(int)

        def match(lag):
            want = np.frombuffer(pcm(0, int(source_index[-1]) + 64), dtype="<i2").reshape(-1, 2)[:, 0]
            return float(np.corrcoef(left[window], want[source_index + lag])[0, 1])

        best = max(range(-24, 25), key=match)
        assert abs(best) <= 2 and match(best) > 0.99, (best, match(best))
    finally:
        rec.ledger.detach()


def test_evicting_a_ghost_copy_extent_leaves_clean_identities_intact(tmp_path):
    """Worst case: the copy even shares the clean run's id. Its ring prefix
    alone keeps its eviction away from the clean picture ledger."""
    rec = recorder(tmp_path)
    try:
        capture = gpu_route(rec)
        run = MediaRun("shared-run", ORIGIN)
        packets = fixture_packets()
        clean = ready(MediaWorker(NativeFormat("h264", 320, 240, 30), rec.ledger,
                                  lambda r: rec.fragments.create(r, (320, 240)),
                                  audio_rate=RATE, audio_bitrate=160000, packet_limit=1 << 20,
                                  pcm_limit=1 << 20, max_bytes=8 << 20, max_blocks=256, max_age=10))
        clean.bind(run)
        feed(clean, run, packets, rows=True)
        copy = ready(capture.ghost_copy_sink("h264", 320, 240))
        copy.bind(run)
        feed(copy, run, packets[4:])  # the switch went on at the fifth picture (an IDR)

        def identities():
            return [(f["run_id"], f["pts"]) for f in rec.ledger.feeds_between(ORIGIN - 1, ORIGIN + 60)]

        before, coverage = identities(), rec.fragments.coverage()
        assert len(before) == len(packets)
        copies = [g for g in rec.ring.temporary_groups() if g.startswith("ghostcopy:")]
        assert copies
        for group in copies:
            rec.ring.forget_temp(group, delete=True)
        rec.fragments.maintain()
        rec.ghost_copies.maintain()
        assert identities() == before, "the clean ledger kept every identity"
        assert rec.fragments.coverage() == coverage
        assert rec.ghost_copies.coverage() is None
        assert not list((tmp_path / "buffer" / "ghostcopy").glob("ghostcopy_*.bin"))
        with rec.fragments.open(utc(run, 0), utc(run, 720000)) as (_, result, _):
            assert result.frame_times[0] == 0.0, "the clean recording still plays"

        # The instrument can see a prune: evicting a CLEAN extent drops identities.
        oldest = min(g for g in rec.ring.temporary_groups() if g.startswith("fragments:"))
        rec.ring.forget_temp(oldest, delete=True)
        rec.fragments.maintain()
        assert len(identities()) < len(before)
    finally:
        rec.ledger.detach()


def test_span_pins_hold_ghost_copy_extents_and_their_archive_owns_expiry(tmp_path):
    ring = SegmentRing(None, 1, scratch_root=tmp_path)  # any unleased byte is over budget
    start = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
    end = start + timedelta(seconds=8)

    def extent(group):
        path = tmp_path / f"{group.replace(':', '_')}.bin"
        path.write_bytes(b"x" * 4096)
        with ring.pin_temp(group):  # registered under its writer lease, as the archive does
            ring.register_temp(group, [path], start, end)
        return path

    with ring.pin("video", start + timedelta(seconds=2), start + timedelta(seconds=3)):
        copy, clip = extent("ghostcopy:run:1"), extent("clip:1")
        ring.maintain()
        assert copy.exists() and "ghostcopy:run:1" in ring.temporary_groups(), "a replay's span pin holds it"
        assert not clip.exists(), "control: a span pin holds only fragment extents"
    assert not copy.exists(), "released, the over-budget extent goes"

    ring = SegmentRing(None, 1 << 30, scratch_root=tmp_path)
    paths = {group: extent(group) for group in ("ghostcopy:run:2", "fragments:run:2", "clip:2")}
    ring.expire_before(end + timedelta(seconds=1))
    assert not paths["clip:2"].exists()
    assert paths["ghostcopy:run:2"].exists() and paths["fragments:run:2"].exists(), \
        "fragment archives expire their own extents, GOP-aware"


@pytest.mark.parametrize("process_tap", [True, False])
def test_the_want_signal_and_the_copy_sink_reach_the_gpu_route(tmp_path, process_tap):
    """Ghosts in recording -> NativeCapture.records_ghosts -> the recorder ->
    the paired GpuCapture, which also hands the copy its archive and the
    process tap's ghost timeline (none on the device-wide fallback)."""
    rec, video, audio, *_ = make(tmp_path)
    if process_tap:
        audio.ghost_timeline = GhostTimeline()
    capture = NativeCapture(US, lambda *_: None, producer_pid=WIN.pid)
    rec._pose_owner = NS(source=capture, request_stop=lambda: None, stop=lambda: None)
    rec._begin_capture(WIN)
    try:
        assert rec.status()["recording"]
        assert not video.ghost_copy_wanted(), "no ghosts playing"
        capture.set_playback(enabled=True, record_ghosts=False)
        assert not video.ghost_copy_wanted(), "Ghosts in recording off"
        capture.set_playback(enabled=True, record_ghosts=True)
        assert rec.ghost_copy_wanted() and video.ghost_copy_wanted()
        assert video.ghost_timeline() is (audio.ghost_timeline if process_tap else None)

        sink = ready(video.ghost_copy_sink("av1", 640, 480))
        try:
            settings = video.settings
            assert sink.ledger is NULL_LEDGER and sink.template == NativeFormat("av1", 640, 480, 30)
            assert (sink.max_bytes, sink.max_blocks, sink.max_age) == (
                settings.pending_bytes, settings.pcm_blocks, settings.max_age), "the clean sink's bounds"
            assert sink._worker.name == "replay-ghost-copy-sink"
            sink.bind(MediaRun("copy-run", ORIGIN))
        finally:
            sink.abort()
            sink.finish(timeout=5)
        (archive,) = rec.ghost_copies._runs.values()
        assert archive.prefix == "ghostcopy" and archive.directory == (tmp_path / "buffer" / "ghostcopy").resolve()
        assert rec.ghost_copies.enabled and rec.fragments.enabled
        capture.set_playback(enabled=False)
        assert not video.ghost_copy_wanted()
    finally:
        rec.stop(cleanup=False)

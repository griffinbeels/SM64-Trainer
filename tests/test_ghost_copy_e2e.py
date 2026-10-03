"""The ghost copy end to end: every merged piece, real, in one recording.

The cadence host as a SourceV4 renderer (SM64_CADENCE_TWIN=1) draws a ghost,
a white square, into its display copy only. Its real producer feeds the real
channel; the clean recording runs as test_gpu_cadence's Pipeline builds it,
into a real FragmentMedia. CaptureSession's own lane tick starts the real
GhostCopyLane, which launches a second real encoder helper and writes through
GpuCapture's real ghost_copy_sink into a FragmentMedia(prefix="ghostcopy")
sharing the clean recording's ring. Both archives are then read back the way
the replay service reads them, and the service's own view offers the copy.

The host's markers restart at 30 on each stream command. Which marker a
clean picture shows comes from the picture ledger (feed_map), so the check
holds whatever the source did; the refusal counts are asserted separately.
"""

from datetime import timedelta
import io
import json
import os
import queue
import time
from types import SimpleNamespace as NS

import numpy as np
import pytest

from sm64_events.memory.layout import US as US_LAYOUT
from sm64_events.replay.channelencoder import ChannelEncoder
from sm64_events.replay.audio import GhostTimeline
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.feedmap import feed_map
from sm64_events.replay.fragmentmedia import FragmentMedia
from sm64_events.replay.gpucapture import GpuCapture
from sm64_events.replay.gpucapture_session import CaptureSession, helper_launch
from sm64_events.replay.gpuinput import ChannelSelection
from sm64_events.replay.gpumedia import GpuMedia
from sm64_events.replay.gpumediaworker import MediaWorker
from sm64_events.replay.gpuprocess.process_controller import Controller
from sm64_events.replay.ledger import PictureLedger
from sm64_events.replay.media import MediaRun
from sm64_events.replay.packetmux import NativeFormat
from sm64_events.replay.ring import SegmentRing
from sm64_events.replay.service import ReplayService
from test_gpu_cadence import ENCODER, FixtureDecoder, Pipeline, cadence_host  # noqa: F401 (a fixture)
from test_gpu_delivery import eventually
from test_replay_service import FakeExtractor, FakeTracker, attempt

pytestmark = pytest.mark.skipif(
    os.name != "nt" or os.environ.get("SM64_TEST_GPU_BRIDGE") != "1",
    reason="explicit Windows NVIDIA witness: set SM64_TEST_GPU_BRIDGE=1",
)

DIMS = (640, 480)
# 60 VIs a second, a fresh picture every second VI. PRE warms a fresh process
# with the lane off: its first recording refuses a few clean pictures ~1.3 s
# in with or without a lane (measured 2026-10-03), so the lane runs warm.
PRE, WARM, MAIN = (120, 60, 2), (240, 60, 2), (600, 60, 2)


def color(marker):
    """The cadence host's clear color for one marker, as 8-bit RGB."""
    return np.rint(np.array([marker % 251, (marker * 7 + marker // 251 * 31) % 251,
                             marker * 17 % 251]) / 250 * 255)


def pictures(media):
    """Each decoded picture in turn (one at a time: 400 full pictures would
    not fit in memory as arrays)."""
    import av

    data = b"".join(media.chunks())
    with av.open(io.BytesIO(data), format="mp4") as container:
        for picture in container.decode(video=0):
            yield picture.to_ndarray(format="rgb24").astype(float)


class Recording(Pipeline):
    """test_gpu_cadence's clean pipeline, archived into real fragments, with
    the ghost copy lane run by CaptureSession exactly as the media tick runs it."""

    def __init__(self, host, out):
        super().__init__(host, out, isolated=True, delay_s=0, dimensions=DIMS,
                         frames=0, vi_per_picture=2, presentation=True)
        root = out / "scratch"
        (root / "ghostcopy").mkdir(parents=True)
        self.ledger = PictureLedger()
        self.ledger.open_archive(root / "identity.sqlite3")
        self.ring = SegmentRing(None, 10**9, scratch_root=root, on_temp_evict=self._evicted)
        self.fragments = FragmentMedia(root, self.ring, self.ledger)
        self.copies = FragmentMedia(root / "ghostcopy", self.ring, None, prefix="ghostcopy")
        self.fragments.enabled = self.copies.enabled = True
        self.root, self.wanted = root, True
        # The recorder's real owner: its ghost_copy_wanted / ghost_copy_sink.
        self.owner = GpuCapture(os.getpid(), US_LAYOUT, nominal_rate=30)
        self.owner.create_sink(NS(picture_feed=True, audio_rate=48000), None, self.ledger, None)
        self.owner.set_ghost_copy(wanted=lambda: self.wanted, publish=self.copies.create,
                                  timeline=lambda: self.timeline)
        # The game plays 440 Hz; the ghost sound player plays 880 Hz, heard
        # on the same clock. Only the copy may carry the 880.
        self.timeline = GhostTimeline(48000)
        self.streams, self.session = [], None
        self._last_tick = None

    def audio_until(self, now):
        """Pipeline's game tone, plus the ghost tone fed to the real
        GhostTimeline as a tap would: each chunk arriving at its last sample."""
        audio_end = int((now - self.started) * 48000)
        while self.cursor + 960 <= audio_end:
            at = np.arange(self.cursor, self.cursor + 960) / 48000
            game = np.rint(np.sin(at * 2 * np.pi * 440) * 10000).astype(np.int16)
            ghost = (np.sin(at * 2 * np.pi * 880) * 0.25).astype(np.float32)
            self.timeline.feed(np.column_stack((ghost, ghost)),
                               self.run.origin_ts + (self.cursor + 960) / 48000)
            self.media.audio(np.column_stack((game, game)).tobytes(),
                             round((self.run.origin_ts + self.cursor / 48000) * 1e6), now=now)
            self.cursor += 960

    def _evicted(self, *args):
        self.fragments.evicted(*args)
        self.copies.evicted(*args)

    def open(self):
        """Pipeline.open, with the clean sink publishing into real fragments."""
        self.client = self.native.start(1, encoder_ready=False)
        h = self.client.header
        command, identity, env = helper_launch()
        self.controller = Controller(command=command, identity=identity, env=env,
                                     limits=self.settings.helper())
        self.command = dict(op="Open", dll_path=str(ENCODER),
            adapter_luid=[h.luid_high, h.luid_low], names=list(self.client.texture_names),
            options=self.settings.encoder(h, ReplayConfig(), nominal_rate=30))
        self.request_id = self.controller.enqueue(self.command)
        self.reply = eventually(self.controller.take_result, timeout=5)
        assert self.reply.metadata["result"] == 0 and not self.reply.metadata["error"], self.reply
        self.sink = MediaWorker(NativeFormat("h264", *DIMS, 30), self.ledger,
            lambda run: self.fragments.create(run, DIMS), **self.options,
            max_bytes=4 << 20, max_blocks=256, max_age=2)
        eventually(self.sink_ready, timeout=5)
        self.client.encoder_ready()
        assert self.native.command("active") == "active"

    def _begin(self):
        """Pipeline.start's media setup, from this stream's first offer."""
        self.started = time.monotonic()
        initial = eventually(self.client.offers)
        decoder = FixtureDecoder(self.client.header)
        self.run = MediaRun("clean", decoder.timestamp(initial[0].boundary_qpc))
        self.sink.bind(self.run)
        self.mux = self.sink
        self.media = GpuMedia(self.run, self.ledger.selection_only(self.sink.add_row), self.sink,
            source_namespace=decoder.namespace, encoder_duration=3000, packet_limit=1 << 20,
            pending_count=8, pending_bytes=4 << 20, max_age=2, pcm_bytes=1 << 20,
            pcm_blocks=256, lead_ticks=9000, publish_picture=self.sink.publish_picture)
        self.adapter = ChannelEncoder(ChannelSelection(self.client, decoder, self.media),
                                      self.controller, max_pending=8, max_age=2,
                                      defer_disposal=True)
        self.adapter.attach_open(self.command, self.request_id, self.reply,
                                 initial_offers=initial, now=time.monotonic())
        self.session = CaptureSession(self.owner, NS(request_stop=lambda reason: None))
        self.session.channel, self.session.media = self.client, self.media
        self.session.open_command, self.session.codec = self.command, "h264"

    @property
    def lane(self):
        return self.session.copy_lane if self.session else None

    def tick(self, now):
        """CaptureSession.tick's order, the lane last. Records the slowest
        lane step and the longest gap between ticks, with the lane's phase."""
        self.audio_until(now)
        self.adapter.pump(now=now)
        self.media.drain()
        self.sink.check()
        phase = self.lane.phase if self.lane else None
        began = time.monotonic()
        self.session._tick_copy_lane(now)
        spent = time.monotonic() - began
        slowest = self.evidence.setdefault("slowest_lane_tick", [0, None])
        if spent > slowest[0]:
            slowest[:] = [round(spent * 1000, 2), phase]
        gap = self.evidence.setdefault("longest_tick_gap", [0, None])
        if self._last_tick is not None and now - self._last_tick > gap[0] / 1000:
            gap[:] = [round((now - self._last_tick) * 1000, 2), phase]
        self._last_tick = time.monotonic()
        after = self.lane.phase if self.lane else None
        if after != phase:  # when each lane step landed, from the run's start
            self.evidence.setdefault("lane_phases", []).append(
                (after, round(time.monotonic() - self.started, 3)))
        pending = self.adapter.status(refresh=False)["pending"]
        peak = self.evidence.setdefault("clean_pending_peak", [0, None])
        if pending > peak[0]:
            peak[:] = [pending, round(time.monotonic() - self.started, 3)]

    def stream(self, count, hz, divisor, *, until_running=False, off_after=None):
        assert self.native.command(f"stream_vi {count} {hz} {divisor}") == "stream started"
        if self.media is None:
            self._begin()
        started, done = time.monotonic(), None
        while True:
            now = time.monotonic()
            if off_after is not None and now - started >= off_after:
                self.wanted = False
            self.tick(now)
            if done is None:
                try:
                    done = self.native.lines.get_nowait()
                except queue.Empty:
                    pass
            running = self.lane is not None and self.lane.phase == "running"
            if done is not None and (running or not until_running):
                break
            if until_running and self.lane is not None and self.lane.reason is not None:
                raise AssertionError(f"the lane stopped before it ran: {self.lane.reason}")
            assert now - started < count / hz + 15, (done, self.lane and self.lane.phase)
            time.sleep(0.002)
        fields = done.split()
        assert fields[:3] == ["stream", "done", str(count)], done
        # The host's counters are cumulative: keep this stream's own.
        before = self.streams[-1] if self.streams else dict(total_full=0, total_busy=0)
        self.streams.append(dict(f.split("=", 1) for f in fields if "=" in f) | dict(
            total_full=int(fields[3]), total_busy=int(fields[4]),
            refused_full=int(fields[3]) - before["total_full"],
            refused_busy=int(fields[4]) - before["total_busy"],
            lane=self.lane.phase if self.lane else None))

    def finish(self):
        """Drain, then close as CaptureSession.close does: the lane first."""
        until = time.monotonic() + 0.4
        while time.monotonic() < until:
            self.tick(time.monotonic())
            time.sleep(0.002)
        lane = self.lane
        self.evidence["twin_status"] = str(self.client.twin_status())
        self.session._close_copy_lane()
        assert lane.stopped, lane.phase
        helper = lane.controller.status()
        self.evidence["twin_helper"] = dict(done=helper["done"], disposal=helper["disposal"])
        assert helper["done"] and helper["disposal"]["empty"] and helper["disposal"]["handle_closed"]
        assert helper["disposal"]["active_processes"] == 0
        assert self.media.seal(self.adapter.frontier) and self.media.finish(audio_drained=True)
        self.sink.finish(timeout=3)
        self.ledger.flush()
        self.evidence["lane"] = dict(lane.counts, reason=lane.reason)

    def close(self):
        try:
            super().close()
        finally:
            self.ledger.detach()


def tones(media):
    """The decoded track's strength at 440 Hz (game) and 880 Hz (ghost)."""
    import av

    with av.open(io.BytesIO(b"".join(media.chunks())), format="mp4") as container:
        signal = np.concatenate([f.to_ndarray()[0] for f in container.decode(audio=0)])
    spectrum = np.abs(np.fft.rfft(signal))
    hz = np.fft.rfftfreq(signal.size, 1 / 48000)
    near = lambda f: spectrum[np.abs(hz - f) < 3].max()  # noqa: E731
    return near(440), near(880)


def check_sound(rec):
    """Clean hears the game alone; the copy hears the game and the ghost."""
    with open_clean(rec) as (media, _, _):
        clean = tones(media)
    with rec.copies.open(*rec.copies.coverage()) as (media, _, _):
        twin = tones(media)
    assert clean[1] < clean[0] * 0.02, clean
    assert twin[1] > twin[0] * 0.4, twin
    return dict(clean_880_vs_440=round(float(clean[1] / clean[0]), 4),
                twin_880_vs_440=round(float(twin[1] / twin[0]), 4))


def open_clean(rec):
    start, end = rec.fragments.coverage()
    return rec.fragments.open(start, end)


def check_clean(rec, ghost_box):
    """Every clean picture decodes to the color of the marker its ledger row
    names, at the center and where the ghost is drawn in the copy."""
    count = 0
    with open_clean(rec) as (media, result, _):
        pts = list(result.source_pts)
        markers, repeats, stats = feed_map(pts, rec.run.id, rec.ledger.rows_between(0, 1e10),
                                           rec.ledger.feeds_between(0, 1e10),
                                           lambda row: row["frame"])
        assert markers is not None and not any(repeats), stats
        for picture, marker in zip(pictures(media), markers, strict=True):
            count += 1
            for at in ((DIMS[1] // 2, DIMS[0] // 2), ghost_box):
                assert np.max(np.abs(picture[at] - color(marker))) <= 8, (marker, at, picture[at])
    assert count == len(pts) and count == rec.media.delivered, (count, rec.media.delivered)
    return dict(zip(pts, markers, strict=True))


def find_ghost(picture, marker):
    """The one region that differs from the clean picture (a flat color)."""
    rows, cols = np.nonzero(np.max(np.abs(picture - color(marker)), axis=2) > 60)
    assert rows.size, "the ghost copy shows no ghost"
    height, width = rows.max() - rows.min() + 1, cols.max() - cols.min() + 1
    assert 28 <= height <= 36 and 28 <= width <= 36, (height, width)
    return (rows.min() + rows.max()) // 2, (cols.min() + cols.max()) // 2


def check_twin(rec, clean_markers):
    """Every twin picture decodes, sits on a clean picture's pts, shows that
    picture (or, when the producer missed it, the one before) and the ghost.
    The one exception is where coverage starts: the host allocates its display
    surface on the first picture it presents with the consumer on, so that
    update answers SAME_AS_CLEAN and its twin picture is the clean one."""
    box, stale, count, ghostless = None, 0, 0, []
    with rec.copies.open(*rec.copies.coverage()) as (media, result, _):
        pts = list(result.source_pts)
        assert pts and len(pts) == rec.lane.counts["published"], (len(pts), rec.lane.counts)
        clean = list(clean_markers)
        index = clean.index(pts[0])
        assert pts == clean[index:index + len(pts)], "the twin is not the clean run picture for picture"
        for picture, at in zip(pictures(media), pts, strict=True):
            count += 1
            marker = clean_markers[at]
            if np.max(np.abs(picture[DIMS[1] // 2, DIMS[0] // 2] - color(marker))) > 8:
                stale += 1  # the producer missed it: a repeat of the picture before
                continue
            ghost = np.max(np.abs(picture - color(marker)), axis=2) > 60
            if not ghost.any() and min(color(marker)) < 150:
                ghostless.append(count - 1)  # a clean picture: nothing was copied
                continue
            if box is None and min(color(marker)) < 150:
                box = find_ghost(picture, marker)
            if box is not None:
                assert np.min(picture[box]) >= 230, (at, picture[box])
    assert count == len(pts) and box is not None
    assert ghostless in ([], [0]), ghostless  # only where coverage starts
    assert stale <= rec.lane.counts["missed"], (stale, rec.lane.counts)
    return box, dict(pictures=len(pts), stale=stale, first_pts=pts[0],
                     same_as_clean_at_start=len(ghostless))


def same_span(rec, twin_first_pts):
    """The service's offer check by hand: cut clean over a span inside the
    copy, then the copy over the clean cut's own span. Same first picture to
    the microsecond, same rounded picture times, different runs."""
    origin = rec.run.origin_ts + twin_first_pts / 90000
    start = rec.fragments._utc(origin + 1.0)
    with rec.fragments.open(start, start + timedelta(seconds=3)) as (_, clean, _):
        end = clean.start_utc + timedelta(seconds=clean.duration_s)
        span = (clean.start_utc, end)
        with rec.copies.open(*span, required_span=span) as (_, twin, _):
            assert twin.start_utc == clean.start_utc
            assert [round(t, 6) for t in twin.frame_times] == [
                round(t, 6) for t in clean.frame_times]
            assert twin.media_run.id != clean.media_run.id
            return len(clean.frame_times)


def service_offer(rec, out, twin_first_pts):
    """The real ReplayService view over both archives, for an attempt inside
    the twin's coverage: the cut it makes must offer the ghost copy."""
    origin = rec.run.origin_ts + twin_first_pts / 90000
    started = rec.fragments._utc(origin + 1.0)
    a = attempt(started_utc=started.isoformat(),
                ended_utc=(started + timedelta(seconds=3)).isoformat(), anchor_frame=40)
    cfg = ReplayConfig(scratch_dir=rec.root, save_root=out / "saved", pre_pad_s=0,
                       post_pad_s=0, extract_wait_s=0)
    recorder = NS(ring=rec.ring, ledger=rec.ledger, fragments=rec.fragments,
                  ghost_copies=rec.copies)
    svc = ReplayService(cfg, recorder, FakeExtractor(), FakeTracker([a]))
    view = svc.view(42)
    sidecar = svc.clips_dir / "attempt_42.json"
    return view, json.loads(sidecar.read_text()) if sidecar.exists() else None


@pytest.fixture
def twin_host(cadence_host, monkeypatch):
    monkeypatch.setenv("SM64_CADENCE_TWIN", "1")
    return cadence_host


def test_the_ghost_copy_records_beside_clean_and_the_service_offers_it(twin_host, tmp_path):
    rec = Recording(twin_host, tmp_path)
    try:
        rec.open()
        rec.wanted = False
        rec.stream(*PRE)
        rec.wanted = True                      # Ghosts in recording switched on
        rec.stream(*WARM, until_running=True)  # the lane comes up mid-stream
        rec.stream(*MAIN)                      # and is on for all of this one
        rec.finish()
        for stream in rec.streams[1:]:
            assert stream["refused_full"] == stream["refused_busy"] == 0, stream
        clean_markers = check_clean(rec, (DIMS[1] - 24, 24))
        box, twin = check_twin(rec, clean_markers)
        check_clean(rec, box)  # clean stays exact exactly where the ghost is
        twin["span_pictures"] = same_span(rec, twin["first_pts"])
        twin.update(check_sound(rec))
        view, meta = service_offer(rec, tmp_path, twin["first_pts"])
        rec.evidence.update(twin=twin, ghost_box=[int(v) for v in box], streams=rec.streams,
                            offer=dict(url=view["ghost_copy_url"], reason=view["ghost_copy_reason"]))
        assert view["ghost_copy_url"] == "/api/replay/ghostcopy/clips/ghostcopy_attempt_42.mp4", \
            view["ghost_copy_reason"]
        if meta is not None:
            assert meta["ghost_copy_source"]["run_id"] != meta["fragment_source"]["run_id"]
        print(json.dumps({k: rec.evidence[k] for k in
                          ("lane", "twin", "twin_status", "streams", "offer", "ghost_box",
                           "slowest_lane_tick", "longest_tick_gap", "lane_phases")}, default=str))
    finally:
        rec.close()


def test_switching_the_copy_off_mid_stream_leaves_clean_recording(twin_host, tmp_path):
    rec = Recording(twin_host, tmp_path)
    try:
        rec.open()
        rec.wanted = False
        rec.stream(*PRE)
        rec.wanted = True
        rec.stream(*WARM, until_running=True)
        rec.stream(*MAIN, off_after=4.0)  # Ghosts in recording switched off
        lane = rec.lane
        assert lane.stopped and lane.reason == "Ghosts in recording was switched off", lane.phase
        rec.stream(*MAIN)  # the clean run goes on with no lane at all
        assert rec.lane is lane  # never restarted without a new switch edge
        rec.finish()
        for stream in rec.streams[1:]:
            assert stream["refused_full"] == stream["refused_busy"] == 0, stream
        clean_markers = check_clean(rec, (DIMS[1] - 24, 24))
        _, twin = check_twin(rec, clean_markers)
        assert twin["pictures"] < rec.media.delivered - MAIN[0] // MAIN[2]
        rec.evidence.update(twin=twin, streams=rec.streams)
        print(json.dumps({k: rec.evidence[k] for k in ("lane", "twin", "twin_status", "streams",
                                                       "slowest_lane_tick", "longest_tick_gap")},
                         default=str))
    finally:
        rec.close()

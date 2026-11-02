"""Sink delays cannot withhold capture work; real output retains picture/PCM clocks."""

from dataclasses import replace
import threading
import time
from types import SimpleNamespace as NS

import pytest

from sm64_events.replay import config, gpumediaworker
from sm64_events.replay.gpumediaworker import MediaWorker
from sm64_events.replay.gpupublication import (
    PublicationBusyError, PublicationError, PublicationWriteError,
)
from sm64_events.replay.ledger import PictureLedger
from sm64_events.replay.media import MediaRun
from sm64_events.replay.gpusettings import GpuSettings
from sm64_events.replay.packetmux import EncodedPicture, NativeFormat
from test_gpumedia import FakeMux, complete, media, sample, stamp
from test_gpu_codec_choice import encoder_asset as encoder_asset
from test_packetmux import Output, RATE, fixture_packets, pcm, verify_audio, verify_video


def wait(predicate):
    deadline = time.monotonic() + 4
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.001)


class Mux(FakeMux):
    def __init__(self, output, template, run, **kwargs):
        super().__init__()
        self.run = run

    def prepare(self):
        pass

    def bind_run(self, run):
        self.run = run


def worker(ledger=None, publish=None, **kwargs):
    options = dict(audio_rate=RATE, audio_bitrate=160000, packet_limit=1 << 20,
                   pcm_limit=384000, max_bytes=4 << 20, max_blocks=256, max_age=5,
                   mux_factory=Mux)
    options.update(kwargs)
    q = MediaWorker(NativeFormat("h264", 320, 240, 30), ledger or PictureLedger(),
                    publish or (lambda run: NS(feed=lambda b: None, finish=lambda e: None)),
                    **options)
    wait(lambda: q.ready)
    q.check()
    return q


def test_blocked_ledger_does_not_block_selection_or_packets_and_receipts_wait(tmp_path):
    ledger = PictureLedger()
    ledger.open_archive(tmp_path / "pictures.sqlite")
    entered, release = threading.Event(), threading.Event()
    original = ledger.accept_row

    def accept(row):
        entered.set()
        assert release.wait(4)
        original(row)

    ledger.accept_row = accept
    q = worker(ledger)
    run = MediaRun("gpu-media", 1000)
    q.bind(run)
    selected = ledger.selection_only(q.add_row)
    m = media(q, selected, publish_picture=q.publish_picture)
    try:
        a = m.offer(sample(1), stamp(10), occurrence=1, capture_ts=1000, now=0)
        assert entered.wait(2)
        complete(m, a)
        b = m.offer(sample(2), stamp(11), occurrence=2, capture_ts=1000.033, now=.033)
        complete(m, b)
        # Neither SQL rows nor mux delivery finished. Source selection/encoded
        # completion nevertheless returned with an owned bounded handoff.
        assert m.delivered == q.video_count == 0
        assert q.status()["pending_blocks"] >= 3
        assert not ledger.rows_between(999, 1001)
    finally:
        release.set()
        m.abort("test suffix")
        q.finish(timeout=3)
    assert q.delivered == 1
    rows, feeds = ledger.rows_between(999, 1001), ledger.feeds_between(999, 1001)
    assert [r["frame"] for r in rows] == [10, 11]
    assert feeds[0]["source_id"] == rows[0]["source_id"]
    ledger.detach()


@pytest.mark.parametrize("bound", ["bytes", "blocks", "age"])
def test_bounds_include_inflight_and_unjoined_sink_retains_ownership(bound):
    entered, release = threading.Event(), threading.Event()
    clock = [0.0]

    def publish(run):
        entered.set()
        assert release.wait(4)
        return NS(feed=lambda b: None, finish=lambda e: None)

    opts = dict(max_bytes=256 if bound == "bytes" else 4096,
                max_blocks=1 if bound == "blocks" else 16,
                max_age=1, clock=lambda: clock[0])
    q = worker(publish=publish, **opts)
    q.bind(MediaRun("budget", 1000))
    assert entered.wait(2)
    try:
        assert q.status()["pending_bytes"] == 256
        if bound == "age":
            clock[0] = 2
        with pytest.raises(PublicationWriteError, match="capacity|age"):
            q.write_pcm(b"1234", 1000000000)
        with pytest.raises(PublicationBusyError):
            q.finish(timeout=.01)
    finally:
        release.set()
        with pytest.raises(PublicationError):
            q.finish(timeout=3)
    assert not q._worker.is_alive()


def test_stamp_metadata_is_bounded_and_does_not_alias_after_admission():
    entered, release = threading.Event(), threading.Event()
    ledger = PictureLedger()

    def publish(run):
        entered.set()
        assert release.wait(4)
        return NS(feed=lambda b: None, finish=lambda e: None)

    q = worker(ledger, publish)
    q.bind(MediaRun("owned-row", 1000))
    assert entered.wait(2)
    try:
        row = dict(ts=1000, frame=10, pad=[1, 2, 3])
        q.add_row(row)
        row["pad"][0] = 99
        circular = {}
        circular["loop"] = circular
        with pytest.raises(PublicationWriteError, match="bounds"):
            q.add_row(circular)
        with pytest.raises(PublicationWriteError, match="bounds"):
            q.add_row(dict(huge="x" * 65536))
    finally:
        release.set()
        q.abort()
        q.finish(timeout=3)
    assert ledger.rows_between(999, 1001)[0]["pad"] == [1, 2, 3]


def test_real_worker_prepares_without_publication_and_preserves_media(tmp_path):
    from sm64_events.replay.packetmux import PacketFragmentMux

    ledger = PictureLedger()
    ledger.open_archive(tmp_path / "pictures.sqlite")
    packets = fixture_packets()
    run = MediaRun("worker-real", 1000.0)
    actual = Output(tmp_path, run)
    finished = []

    def feed(data):
        # Any sample exposed by this callback already has its exact identity.
        assert ledger.feeds_between(999, 2000)
        return actual.write(data)

    def finish(error):
        finished.append(error)
        actual.finish()

    q = worker(ledger, lambda r: NS(feed=feed, finish=finish),
               mux_factory=PacketFragmentMux, max_blocks=2048, max_age=10)
    assert actual.bytes == 0 and q.video_count == 0
    q.bind(run)
    end_sample = round((packets[-1].pts + packets[-1].duration) / 90000 * RATE)
    events = [(p.pts / 90000, "video", p) for p in packets]
    events += [(first / RATE, "audio", (first, min(960, end_sample - first)))
               for first in range(-2400, end_sample, 960)]
    events.sort(key=lambda e: e[0])
    try:
        for _, kind, data in events:
            if kind == "audio":
                first, count = data
                q.write_pcm(pcm(first, count), round(run.origin_ts * 1e6 + first / RATE * 1e6))
            else:
                source_id = f"source:{data.occurrence}"
                at = run.origin_ts + data.pts / 90000
                q.add_row(dict(ts=at, frame=data.occurrence, source_id=source_id,
                               exact=True, pad=[data.occurrence, 0, 1]))
                q.publish_picture(data, at, at, media_run=run, pts=data.pts,
                                  source_id=source_id, repeat=False)
        q.close()
    finally:
        q.finish(timeout=4)
    assert finished == [None]
    assert q.delivered == q.video_count == len(packets)
    assert [f["pts"] for f in ledger.feeds_between(999, 2000)] == [p.pts for p in packets]
    verify_video(actual, packets, packets, None)
    verify_audio(tmp_path, run, end_sample, q.mux, actual, None)
    ledger.detach()


def test_session_never_admits_native_frames_until_sink_preparation_completes(monkeypatch, encoder_asset):
    from sm64_events.replay import gpucapture_session as session_module
    from sm64_events.replay.gpudemand import DemandSnapshot
    from sm64_events.replay.gpusettings import GpuSettings

    entered, release, admitted = (threading.Event() for _ in range(3))
    made, errors = [], []

    class SlowPrepare(Mux):
        def prepare(self):
            entered.set()
            assert release.wait(4)

    def factory(*args, **kwargs):
        result = MediaWorker(*args, **kwargs, mux_factory=SlowPrepare)
        made.append(result)
        return result

    monkeypatch.setattr(session_module, "MediaWorker", factory)
    settings = GpuSettings()
    settings = NS(**settings.__dict__, encoder=lambda *a, **k: {}, helper=lambda: None)
    calls = []
    owner = NS(settings=settings, cfg=NS(audio_rate=RATE), nominal_rate=30,
               ledger=PictureLedger(), want_capture=lambda: True, wait=time.sleep,
               publish=lambda *a: pytest.fail("no source origin before native admission"),
               begin_audio=lambda: calls.append("audio"))
    reply = NS(request_id=1, metadata=dict(result=0, error=None, worker_disposal_required=False))
    controller = NS(enqueue=lambda c: 1, take_result=lambda: reply)
    session = session_module.CaptureSession(owner, NS(snapshot=DemandSnapshot("preparing")),
                                            controller_factory=lambda **k: controller)
    session.channel = NS(header=NS(luid_high=0, luid_low=1, even_width=320, even_height=240),
                         texture_names=("fixture",), encoder_ready=admitted.set)

    def start():
        try:
            assert session._open_encoder() is not None
        except Exception as exc:  # noqa: BLE001 - forward test-owned worker errors into the test assertion.
            errors.append(exc)

    thread = threading.Thread(target=start)
    thread.start()
    try:
        assert entered.wait(2), errors
        assert not admitted.is_set() and not calls
        release.set()
        thread.join(3)
        assert not thread.is_alive() and not errors
        assert admitted.is_set() and calls == ["audio"]
        sink = made[0]
        assert (sink.max_bytes, sink.max_blocks, sink.max_age) == (
            settings.sink_bytes, settings.sink_blocks, settings.sink_age_s)
    finally:
        release.set()
        thread.join(3)
        for sink in made:
            sink.finish("startup test", timeout=3)


def test_recorder_keeps_ledger_open_while_retained_sink_owns_sqlite(tmp_path):
    from test_replay_recorder import make_recorder, FakeVideoSource, FakeAudioSource

    rec = make_recorder(tmp_path, FakeVideoSource(), FakeAudioSource())
    rec.ledger.open_archive(tmp_path / "owned-pictures.sqlite")
    archive = rec.ledger._archive
    entered, release = threading.Event(), threading.Event()
    original = rec.ledger.accept_row

    def blocked(row):
        with archive._lock:
            entered.set()
            assert release.wait(4)
            original(row)

    rec.ledger.accept_row = blocked
    q = worker(rec.ledger)
    q.bind(MediaRun("retained", 1000))
    q.add_row(dict(ts=1000, frame=10, source_id="retained:1"))
    assert entered.wait(2)
    rec._video_sink = NS(stop=lambda: q.finish("stop", timeout=.01))
    detached = []
    original_detach = rec.ledger.detach

    def detach():
        detached.append(True)
        original_detach()

    rec.ledger.detach = detach
    try:
        rec._teardown_capture()
        assert not detached
        assert archive._db is not None
        assert set(rec._capture_retained) == {"sink", "ledger"}
        assert not rec._capture_closed and not rec.cleanup_scratch()
    finally:
        release.set()
        q.finish("stop", timeout=3)
        rec._teardown_capture()
        rec.stop(cleanup=False)
    assert detached and archive._db is None


class Stalled:
    """A sink on the shipped budgets whose first video write blocks until
    released, on a clock the test moves: the disk stall of SM64-TRAINER-F/G."""

    def __init__(self, settings=None, **options):
        self.settings = settings or GpuSettings()
        self.now = 0.0
        self.delivered = []
        self.entered, self.release = threading.Event(), threading.Event()
        stalled = self

        class StallMux(Mux):
            def write_video(self, picture):
                if not stalled.entered.is_set():
                    stalled.entered.set()
                    assert stalled.release.wait(8)
                stalled.delivered.append(("video", picture.pts))

            def write_pcm(self, data, first_us):
                stalled.delivered.append(("pcm", first_us))

        class Ledger:
            def accept_row(self, row):
                stalled.delivered.append(("row", row["frame"]))

            def mark_fed(self, *args, **kwargs):
                pass

        self.q = worker(Ledger(), mux_factory=StallMux, clock=lambda: self.now,
                        max_bytes=self.settings.sink_bytes,
                        max_blocks=self.settings.sink_blocks,
                        max_age=self.settings.sink_age_s, **options)
        self.run = MediaRun("stall", 1000)
        self.q.bind(self.run)
        self.pts = 0

    def row(self, frame):
        self.q.add_row(dict(frame=frame, exact=True, vi_origin=frame, lists_since=1,
                            igt_overall=frame, pad=[0, 0, 0], mario=[0, 0, 0.0],
                            mario_pos=[0.0, 0.0, 0.0], source_id=f"stall:{frame}"))

    def video(self, data=b"x"):
        self.pts += 3000
        self.q.publish_picture(EncodedPicture(self.pts // 3000, self.pts, 3000, False, data),
                               1000.0, 1000.0, media_run=self.run, pts=self.pts,
                               source_id=f"stall:{self.pts}", repeat=False)

    def pcm(self, first_us, data=b"\0" * 1920):
        self.q.write_pcm(data, first_us)

    def stall(self):
        self.video()
        assert self.entered.wait(2)


def test_a_short_writer_stall_ends_nothing_and_delivers_everything_in_order():
    s = Stalled()
    s.stall()
    expected = [("video", 3000)]
    for step in range(1, 151):  # 5 s at 30 pictures/s: row, video, three PCM blocks.
        s.now = step / 30
        s.row(step)
        expected.append(("row", step))
        s.video()
        expected.append(("video", s.pts))
        for block in range(3):
            first_us = step * 1000 + block
            s.pcm(first_us)
            expected.append(("pcm", first_us))
        s.q.check()
    s.release.set()
    wait(lambda: s.q.status()["pending_blocks"] == 0)
    s.q.check()
    s.q.close()
    s.q.finish(timeout=3)
    assert s.delivered == expected
    assert s.q.delivered == s.q.video_count == 151


def test_a_stall_past_the_age_limit_still_ends_the_recording_and_says_what_stalled():
    s = Stalled()
    s.stall()
    limit = s.settings.sink_age_s
    s.now = limit - 0.01
    s.q.check()
    s.now = limit + 0.01
    with pytest.raises(PublicationWriteError, match="age budget"):
        s.q.check()
    status = s.q.status()
    assert status["inflight_command"] == "video"
    assert status["inflight_ms"] == pytest.approx((limit + 0.01) * 1000)
    s.release.set()
    with pytest.raises(PublicationError, match="age budget"):
        s.q.finish(timeout=3)


def test_the_age_limit_fires_before_capacity_at_the_worst_case_rate():
    """Every picture distinct at 60 Hz, at the encoder's capped bitrate with its
    whole VBV buffer spent at once, with audio at its measured rate."""
    s = Stalled()
    s.stall()
    limit = s.settings.sink_age_s
    bitrate = int(config.RING_MAXRATE.removesuffix("M")) * 1_000_000
    picture = bytes(bitrate // 8 // 60)
    s.video(bytes(bitrate // 8))  # the VBV buffer, spent at once
    audio_block = 0
    ticks = int(limit * 60) - 1
    for tick in range(ticks):
        s.now = tick / 60
        s.row(tick)
        s.video(picture)
        for _ in range(100 // 60 + (tick % 3 != 0)):
            audio_block += 1
            s.pcm(audio_block)
    assert s.q.status()["error"] is None
    s.now = limit + 0.01
    with pytest.raises(PublicationWriteError, match="age budget"):
        s.pcm(audio_block + 1)
    s.release.set()
    with pytest.raises(PublicationError, match="age budget"):
        s.q.finish(timeout=3)


def test_the_sink_budgets_leave_every_other_limit_alone():
    wider = GpuSettings(sink_age_s=99.0, sink_blocks=9999, sink_bytes=1 << 30)
    assert wider.request() == GpuSettings().request()
    assert wider.helper() == GpuSettings().helper()


def test_a_survived_stall_leaves_one_field_line(monkeypatch, caplog):
    monkeypatch.setattr(gpumediaworker, "NOTABLE_STALL_MS", 0.0)
    q = worker()
    with caplog.at_level("WARNING", logger="sm64.replay"):
        q.bind(MediaRun("line", 1000))
        wait(lambda: q.status()["pending_blocks"] == 0)
    q.abort()
    q.finish(timeout=3)
    lines = [r.getMessage() for r in caplog.records if "survived" in r.getMessage()]
    assert len(lines) == 1 and "bind command" in lines[0]


def _stopping_session(stalled, settings):
    """A real CaptureSession at a normal stop: its media sealed and its sink
    told the recording is complete, as GpuMedia.finish leaves them."""
    from sm64_events.replay.gpucapture_session import CaptureSession
    from sm64_events.replay.gpudemand import DemandSnapshot

    session = CaptureSession(NS(settings=settings, end_audio=lambda handoff: None),
                             NS(snapshot=DemandSnapshot("active"),
                                request_stop=lambda *a, **k: None))
    session.media = NS(closed=True, fault=None)
    session.output = stalled.q
    session._open_channel = lambda: True
    session._open_encoder = lambda: ()
    session._first_offers = lambda: (1,)
    session._create_media = lambda *_: None
    session._continue = lambda: False
    stalled.q.close()
    return session


def _stalled_with_backlog(settings):
    causes = []
    archive = NS(feed=lambda b: None, finish=causes.append)
    s = Stalled(settings, publish=lambda run: archive)
    s.stall()
    for frame in range(1, 301):
        s.row(frame)
        s.video()
    return s, causes


def test_a_stop_during_a_long_backlog_waits_and_publishes_all_of_it():
    # close_s alone is far shorter than the stall; only the age budget covers it.
    settings = replace(GpuSettings(), close_s=0.02)
    s, causes = _stalled_with_backlog(settings)
    session = _stopping_session(s, settings)
    timer = threading.Timer(0.3, s.release.set)
    timer.start()
    try:
        session.close()
    finally:
        s.release.set()
        timer.join()
    assert session._output_joined
    assert s.q.mux.closed and not s.q.mux.failed
    assert causes == [None]
    assert s.q.delivered == s.q.video_count == 301


def test_a_stall_outlasting_the_stop_stays_owned_and_still_closes_complete():
    from sm64_events.replay.gpucapture import GpuCleanupError

    settings = replace(GpuSettings(), close_s=0.01, sink_age_s=0.02)
    s, causes = _stalled_with_backlog(settings)
    session = _stopping_session(s, settings)
    try:
        with pytest.raises(GpuCleanupError):
            session.run()
        assert not session._output_joined and s.q._worker.is_alive() and not causes
        # The retained owner's retry, with the disk still stuck. The busy text
        # the first attempt left in session.error is not this recording's outcome.
        with pytest.raises(PublicationBusyError):
            session.close()
    finally:
        s.release.set()
    wait(lambda: not s.q._worker.is_alive())
    session.close()
    assert session._output_joined
    assert s.q.mux.closed and not s.q.mux.failed
    assert causes == [None]
    assert s.q.delivered == s.q.video_count == 301


def test_the_capture_owner_waits_as_long_as_its_session_may_wait_for_the_sink():
    from sm64_events.replay.gpucapture import GpuCapture

    settings = GpuSettings()
    waited = []
    owner = GpuCapture.__new__(GpuCapture)
    owner.settings = settings
    owner.request_stop = lambda: None
    owner._worker = NS(join=lambda timeout: waited.append(timeout), is_alive=lambda: False)
    owner.cleanup_error = None
    owner.finish()
    assert waited[0] > settings.sink_age_s + settings.close_s

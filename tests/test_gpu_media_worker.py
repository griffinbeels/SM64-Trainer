"""Sink delays cannot withhold capture work; real output retains picture/PCM clocks."""

import threading
import time
from types import SimpleNamespace as NS

import pytest

from sm64_events.replay.gpumediaworker import MediaWorker
from sm64_events.replay.gpupublication import (
    PublicationBusyError, PublicationError, PublicationWriteError,
)
from sm64_events.replay.ledger import PictureLedger
from sm64_events.replay.media import MediaRun
from sm64_events.replay.packetmux import NativeFormat
from test_gpumedia import FakeMux, complete, media, sample, stamp
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
                   pcm_limit=384000, max_bytes=4 << 20, max_age=5, mux_factory=Mux)
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


def test_session_never_admits_native_frames_until_sink_preparation_completes(monkeypatch):
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
        assert entered.wait(2)
        assert not admitted.is_set() and not calls
        release.set()
        thread.join(3)
        assert not thread.is_alive() and not errors
        assert admitted.is_set() and calls == ["audio"]
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

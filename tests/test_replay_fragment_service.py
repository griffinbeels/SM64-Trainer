"""The default replay API opens, leases and saves the same fragment bytes."""
from datetime import timedelta
import json
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from test_replay_fragment_archive import recording as recording
from test_replay_picture_identity import encoder as encoder, picture, read_pictures
from test_replay_recorder import make_recorder, FakeVideoSource, FakeAudioSource, WIN
from test_replay_service import attempt, FakeTracker
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.extract import ClipExtractor
from sm64_events.replay.ffmpeg_sink import FfmpegAvSink
from sm64_events.replay.fragmentmedia import FragmentMedia
from sm64_events.replay.ledger import PictureLedger
from sm64_events.replay.media import MediaRun
from sm64_events.replay.ring import SegmentRing
from sm64_events.replay.service import ReplayService
from sm64_events.server.replay_api import create_replay_router


@pytest.fixture
def fragment_service(tmp_path, recording, encoder):
    root = tmp_path / "scratch"
    root.mkdir()
    ledger = PictureLedger()
    ledger.open_archive(root / "identity.sqlite3")
    ring = SegmentRing(None, 10**8, scratch_root=root,
                       on_temp_evict=lambda *args: fragments.evicted(*args))
    fragments = FragmentMedia(root, ring, ledger)
    fragments.enabled = True
    run = MediaRun("fixture", 1000)
    archive = fragments.create(run, (640, 480))
    archive.feed(recording)
    archive.finish()
    assert archive.error is None
    # These labels and ticks came from the independent barcode/tone producer,
    # not from our parser or a fitted frame-clock association.
    for number in range(60):
        tick = [0, 1, 2][number] if number < 3 else number * 3000
        stamp = 1000 + tick / 90000
        ledger.observe(picture(number, (640, 480)), stamp, 1000 + number,
                       {"exact": True, "pad": [0, number, 32768], "igt_overall": number})
        ledger.mark_fed(stamp, stamp, media_run=run, pts=tick)
    ledger.flush()
    cfg = ReplayConfig(scratch_dir=root, save_root=tmp_path / "saved",
                       pre_pad_s=0, post_pad_s=2, extract_wait_s=0)
    a = attempt(started_utc=fragments._utc(1000.501).isoformat(),
                ended_utc=fragments._utc(1001.5).isoformat(), anchor_frame=1015)
    recorder = SimpleNamespace(ring=ring, ledger=ledger, fragments=fragments)
    service = ReplayService(cfg, recorder, ClipExtractor(cfg, encoder[1], encoder[0], fragments=fragments), FakeTracker([a]))
    try:
        yield service
    finally:
        ledger.detach()


def test_default_recorder_configures_fragment_publication_before_start(tmp_path):
    observed = []
    class InertSink(FfmpegAvSink):
        def start(self):
            observed.append(self._fragment_factory)
        def stop(self):
            pass
    recorder = make_recorder(tmp_path, FakeVideoSource(), FakeAudioSource(),
        video_sink_factory=lambda cfg, callback, codec: InertSink(cfg, callback, codec=codec))
    try:
        recorder._begin_capture(WIN)
        assert observed == [recorder.fragments.create]
        assert recorder.status()["storage_backend"] == "fragments"
    finally:
        recorder.stop()


def test_reconnected_attempt_wins_over_old_run_in_preroll(fragment_service, recording):
    service = fragment_service
    service.pre_pad_s = 2
    fragments = service.fragments
    old = fragments.create(MediaRun("old-before-reconnect", 998.9), (640, 480))
    old.feed(recording)
    old.finish()
    assert old.error is None
    a = service._attempt(42)
    with fragments.open(*service._span(a)) as (_, _, selection):
        assert selection["run_id"] == "old-before-reconnect"  # old rule's concrete wrong choice
    result = service.view(42)
    meta = json.loads(next(service.clips_dir.glob("*.json")).read_text())
    assert meta["fragment_source"]["run_id"] == "fixture"
    assert result["frame_map"][0] == 1000


def test_api_uses_native_ranges_and_preserves_on_save_without_view_copy(tmp_path, fragment_service, monkeypatch):
    service = fragment_service
    app = FastAPI()
    app.include_router(create_replay_router(service))
    with TestClient(app) as client:
        def forbidden(*args, **kwargs):
            pytest.fail("View/Save must not start a decoder, encoder, or packet probe")
        monkeypatch.setattr(subprocess, "run", forbidden)
        started = time.perf_counter()
        opened = client.post("/api/attempts/42/replay")
        elapsed = (time.perf_counter()-started)*1000
        assert opened.status_code == 200, opened.text
        replay = opened.json()
        assert replay["input_alignment"] == {"status": "source_linked"}
        assert replay["frame_map"] == list(range(1015, 1060))
        assert not replay["ends_early"] and replay["video_start_s"] == 0
        assert service.available_attempt_ids() == [42]
        assert not list(service.cfg.scratch_dir.rglob("*.mp4"))
        response = client.get(replay["clip_url"])
        assert response.status_code == 200
        media = response.content
        for header, low, high in [("bytes=0-99", 0, 100), ("bytes=-57", len(media)-57, len(media)),
                                   ("bytes=111-", 111, len(media))]:
            part = client.get(replay["clip_url"], headers={"Range": header})
            assert part.status_code == 206 and part.content == media[low:high]
        # The descriptor is the entity: the same header object serves every
        # request and the browser may resume ranges against its ETag.
        etag = response.headers["etag"]
        assert etag.startswith('"') and part.headers["etag"] == etag
        resumed = client.get(replay["clip_url"], headers={"Range": "bytes=0-99", "If-Range": etag})
        assert resumed.status_code == 206 and resumed.content == media[0:100]
        assert len(service.fragments._media) == 1
        head = client.head(replay["clip_url"])
        assert head.status_code == 200 and not head.content and int(head.headers["Content-Length"]) == len(media)
        assert client.get(replay["clip_url"], headers={"Range": f"bytes={len(media)}-"}).status_code == 416
        assert client.post("/api/attempts/42/replay").json() == replay
        saved = service.save(42)
        saved_path = Path(saved["path"])
        assert saved_path.read_bytes() == media
        # Pressure cannot affect permanent media, including the old drawer URL.
        service.recorder.ring.set_limits(None, 0)
        service.fragments.maintain()
        assert not list(service.cfg.scratch_dir.glob("fragments_*.bin"))
        assert client.get(replay["clip_url"]).content == media
        assert client.get("/api/replay/saved/42").content == media
        assert [number for _, number in read_pictures(saved_path)] == list(range(15,60))
        assert service.view(42)["frame_map"] == replay["frame_map"]
        (tmp_path / "service-report.json").write_text(json.dumps({"first_api_ms": elapsed,
            "file_bytes": len(media), "pictures": len(replay["frame_times"]), "codec": service.extractor._codec}), encoding="utf-8")


def test_pressure_keeps_reader_then_retires_identity(tmp_path, fragment_service):
    service = fragment_service
    replay = service.view(42)
    name = replay["clip_url"].split("/")[-1]
    with service.read_clip(name) as media:
        service.recorder.ring.set_limits(None, 0)
        service.fragments.maintain()
        assert len(b"".join(media.chunks())) == media.size
        assert len(service.recorder.ledger.feeds_between(0, 1e12)) == 60
    service.fragments.maintain()
    assert service.recorder.ledger.feeds_between(0, 1e12) == []
    with pytest.raises(LookupError):
        with service.read_clip(name):
            pass


def test_compilation_materializes_same_selection(tmp_path, fragment_service):
    service = fragment_service
    start, end = service._span(service._attempt(42))
    path = tmp_path / "export.mp4"
    result = service.extractor.extract(service.recorder.ring, start, end, path)
    assert [number for _, number in read_pictures(path)] == list(range(15, 60))
    assert result.source_pts == list(range(45000,180000,3000))
    assert result.start_utc == start - timedelta(milliseconds=1)


def test_http_disconnect_closes_extent_before_releasing_lease(fragment_service):
    import asyncio
    from starlette.requests import ClientDisconnect
    from sm64_events.server.replay_api import ReplayClipResponse
    service = fragment_service
    name = service.view(42)["clip_url"].split("/")[-1]
    bodies = []
    async def send(message):
        if message["type"] == "http.response.body":
            bodies.append(message)
            if len(bodies) == 2:
                service.recorder.ring.set_limits(None, 0)
                assert list(service.cfg.scratch_dir.glob("fragments_*.bin"))
                raise OSError("injected client disconnect during extent read")
    async def receive():
        return {"type": "http.disconnect"}
    scope = {"type": "http", "method": "GET", "headers": [], "asgi": {"spec_version": "2.4"}}
    with pytest.raises(ClientDisconnect):
        asyncio.run(ReplayClipResponse(service, name)(scope, receive, send))
    service.fragments.maintain()
    assert not list(service.cfg.scratch_dir.glob("fragments_*.bin"))
    assert not service.recorder.ring.protected_paths()


def test_matched_recording_cold_view_cost(tmp_path, fragment_service):
    from sm64_events.replay.ring import SegmentInfo
    from test_replay_fragment_producer import decoded_video
    service = fragment_service
    legacy_ring = SegmentRing(None, 10**8)
    archive = service.fragments._snapshot()[0]
    utc = service.fragments._utc
    reference = tmp_path / "reference.ts"  # producer tee: SAME encoded packets
    legacy_ring.add(SegmentInfo(reference, "video", utc(1000), utc(1002),
                               reference.stat().st_size, (640,480), archive.run))
    start, end = service._span(service._attempt(42))
    baseline = ClipExtractor(service.cfg, service.extractor._codec, service.extractor._ffmpeg)
    before = time.perf_counter()
    result = baseline.extract(legacy_ring, start, end, tmp_path / "legacy.mp4")
    legacy_ms = (time.perf_counter()-before)*1000
    before = time.perf_counter()
    opened = service.view(42)
    native_ms = (time.perf_counter()-before)*1000
    assert not list(service.cfg.scratch_dir.rglob("*.mp4"))
    with service.read_clip(opened["clip_url"].split("/")[-1]) as media:
        header_bytes = len(media.header)
        (tmp_path / "native-witness.mp4").write_bytes(b"".join(media.chunks()))
    assert decoded_video(result.path) == decoded_video(tmp_path / "native-witness.mp4")
    (tmp_path / "cold-open-cost.json").write_text(json.dumps({
        "codec": service.extractor._codec, "legacy_cut_ms": legacy_ms, "native_view_ms": native_ms,
        "legacy_media_write_bytes": result.path.stat().st_size, "native_view_media_write_bytes": 0,
        "native_header_bytes": header_bytes, "source": "same encoder tee packets",
        "scope": "offline remux versus full service View; no live whole-machine or browser latency claim"}), encoding="utf-8")



def test_emulator_restarts_preserve_each_prior_fragment_run(tmp_path, recording):
    """Disconnect is not session end: each old View still reads its exact bytes."""
    sinks, selections = [], []

    class PublishedSink:
        def publish_fragments(self, factory):
            self.factory = factory
            return True

        def start(self):
            self.run = MediaRun(f"restart-{len(sinks)}", 1000 + len(sinks) * 10)
            self.archive = self.factory(self.run, (640, 480))
            self.archive.feed(recording)
            sinks.append(self)

        def stop(self):
            self.archive.finish()

    recorder = make_recorder(tmp_path, FakeVideoSource(), FakeAudioSource(),
        video_sink_factory=lambda *args: PublishedSink())
    try:
        for _ in range(3):
            recorder._begin_capture(WIN)
            origin = sinks[-1].run.origin_ts
            with recorder.fragments.open(FragmentMedia._utc(origin + 0.501),
                    FragmentMedia._utc(origin + 1.5)) as (media, _, descriptor):
                selections.append((descriptor, b"".join(media.chunks())))
            recorder._teardown_capture()
            for descriptor, expected in selections:
                with recorder.fragments.read(descriptor) as media:
                    assert b"".join(media.chunks()) == expected
        assert len({descriptor["run_id"] for descriptor, _ in selections}) == 3
    finally:
        recorder.stop()

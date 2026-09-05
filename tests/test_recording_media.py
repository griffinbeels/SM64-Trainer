"""Public URLs stay stable while playback shares one disposable local copy."""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from sm64_events.core.recording_url import media_identity, start_seconds, validate_recording_url
from sm64_events.compare.importer import VideoImporter
from sm64_events.compare.media import RecordingMedia


VIDEO = "https://youtu.be/abcdefghijk?t=12"


@pytest.mark.parametrize("url", ["file:///secret", "javascript:alert(1)",
                                 "http://127.0.0.1/a", "http://[::1]/a",
                                 "https://user:pass@example.com/a",
                                 "http://localhost/a", "https://10.0.0.1/a"])
def test_recording_is_a_public_web_link(url):
    with pytest.raises(ValueError):
        validate_recording_url(url)


def test_original_link_and_start_survive_shared_identity():
    assert validate_recording_url("  " + VIDEO + "  ") == VIDEO
    assert media_identity(VIDEO) == media_identity(
        "https://www.youtube.com/watch?v=abcdefghijk&start=35&list=xyz")
    assert start_seconds(VIDEO) == 12
    assert start_seconds("https://youtu.be/abcdefghijk?t=1h2m3s") == 3723
    assert start_seconds("https://youtu.be/abcdefghijk#t=1m3s") == 63


def importer(tmp_path, download):
    def normalize(command):
        from pathlib import Path
        Path(command[-1]).write_bytes(b"normalized")
    return VideoImporter(tmp_path, "ffmpeg", downloader=download, runner=normalize)


def test_concurrent_surfaces_download_and_normalize_once(tmp_path):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def download(url, destination):
        calls.append(url)
        entered.set()
        assert release.wait(3)
        path = destination / "raw.mp4"
        path.write_bytes(b"raw")
        return path

    shared = importer(tmp_path, download)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(shared.import_video, "youtube", VIDEO)
        assert entered.wait(3)
        second = pool.submit(shared.import_video, "youtube",
                             "https://www.youtube.com/watch?v=abcdefghijk")
        release.set()
        assert first.result() == second.result()
    assert len(calls) == 1


def test_reads_and_preview_never_download_and_first_play_is_shared(tmp_path):
    calls = []
    def download(url, destination):
        calls.append(url)
        path = destination / "raw.mp4"
        path.write_bytes(b"raw")
        return path

    shared = importer(tmp_path, download)
    media = RecordingMedia(shared, preview_probe=lambda url: {"title": "My PB"},
                           frame_probe=lambda path: 1 / 60)
    assert media.status(VIDEO)["state"] == "missing"
    assert media.preview(VIDEO)["title"] == "My PB"
    assert calls == []
    media.start(VIDEO)
    media.wait_for_idle(timeout=3)
    ready = media.status(VIDEO)
    assert ready["state"] == "ready"
    assert ready["start_s"] == 12
    assert ready["frame_step_s"] == 1 / 60
    assert media.start("https://www.youtube.com/watch?v=abcdefghijk")["clip_url"] == ready["clip_url"]
    assert len(calls) == 1


def test_failed_download_retains_link_and_retries_only_on_request(tmp_path):
    calls = []
    def download(url, destination):
        calls.append(url)
        raise RuntimeError("provider refused")
    media = RecordingMedia(importer(tmp_path, download))
    media.start(VIDEO)
    media.wait_for_idle(timeout=3)
    failed = media.status(VIDEO)
    assert failed["state"] == "error" and failed["url"] == VIDEO
    media.start(VIDEO)
    assert len(calls) == 1
    media.start(VIDEO, retry=True)
    media.wait_for_idle(timeout=3)
    assert len(calls) == 2


def test_no_downloader_still_supports_link_and_embed(tmp_path):
    media = RecordingMedia(None, preview_probe=lambda url: {})
    assert media.status(VIDEO)["state"] == "missing"
    assert media.start(VIDEO)["state"] == "error"
    assert media.preview(VIDEO)["url"] == VIDEO


def test_cache_eviction_prepares_again_without_losing_link(tmp_path):
    def download(url, destination):
        path = destination / "raw.mp4"
        path.write_bytes(b"raw")
        return path
    shared = importer(tmp_path, download)
    media = RecordingMedia(shared, frame_probe=lambda path: None)
    media.start(VIDEO)
    media.wait_for_idle(3)
    shared.cache_path(shared.cached_name(VIDEO)).unlink()
    assert media.status(VIDEO)["state"] == "missing"
    media.start(VIDEO)
    media.wait_for_idle(3)
    assert media.status(VIDEO)["state"] == "ready"


@pytest.mark.parametrize("stamps,expected", [([0, 1, 2, 3, 4, 5], 1 / 60),
                                             ([0, 1, 2, 4, 5, 6], None)])
def test_stepping_reads_actual_encoded_timestamps(tmp_path, stamps, expected):
    from fractions import Fraction
    import av
    import numpy as np
    from sm64_events.compare.media import frame_step

    path = tmp_path / "timed.mp4"
    with av.open(str(path), "w") as container:
        stream = container.add_stream("libx264", rate=60)
        stream.width, stream.height, stream.pix_fmt = 64, 64, "yuv420p"
        stream.time_base = Fraction(1, 60)
        for stamp in stamps:
            frame = av.VideoFrame.from_ndarray(
                np.full((64, 64, 3), stamp * 30, dtype=np.uint8), format="rgb24")
            frame.pts, frame.time_base = stamp, Fraction(1, 60)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    assert frame_step(path) == expected

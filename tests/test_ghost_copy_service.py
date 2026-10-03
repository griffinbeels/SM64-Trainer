"""The ghost copy through the replay service (twin lane contract I5).

The ghost copy is the twin recording made while Ghosts in recording is on: the
same pictures as the clean replay, with the ghosts in them. The service offers
it only when it IS the clean clip picture for picture, serves it exactly like
the clean clip, saves it beside the clean MP4 as `<clip>.ghosts.m4v`, shrinks
it on its own, and keeps it out of everything that indexes the save tree. A
fault anywhere on the copy's side costs the copy, never the replay.

The archive is faked here (P3 owns `recorder.ghost_copies`); the last section
runs the real `FragmentMedia` and VirtualMp4 bytes, and the compression section
runs real ffmpeg.
"""
from contextlib import contextmanager
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import threading
import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from test_replay_service import T0, FakeExtractor, FakeTracker, attempt
from sm64_events.replay import compress, publication
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.savedindex import SavedClipIndex
from sm64_events.replay.service import (GHOST_COPY_GONE, GHOST_COPY_LOST_BEFORE_SAVE,
                                        GHOST_COPY_MISSING, GHOST_COPY_NONE, GHOST_COPY_PARTIAL,
                                        GHOST_COPY_TAIL_WAIT_S, GHOST_COPY_UNREADABLE,
                                        ReplayService, saved_attempt_ids)
from sm64_events.replay.virtualmp4 import VirtualMp4
from sm64_events.server.replay_api import create_replay_router

START = T0 - timedelta(seconds=3)           # the clean clip's first picture
TIMES = [0.0, 0.033367, 0.066733, 0.1001, 0.133467, 0.166833]
DURATION = 17.0
END = START + timedelta(seconds=DURATION)
CLEAN_BYTES = bytes(range(256)) * 40
TWIN_BYTES = bytes(reversed(range(256))) * 37
BUFFER_URL = "/api/replay/ghostcopy/clips/ghostcopy_attempt_42.mp4"
SAVED_URL = "/api/replay/saved/42/ghosts"


class Selection(VirtualMp4):
    """Immutable bytes in VirtualMp4's serving shape (size, chunks, identity)."""

    def __init__(self, data: bytes, identity: str):
        self.data, self.size, self.identity = data, len(data), identity

    def chunks(self, start=0, end=None):
        yield self.data[start:self.size if end is None else end]


class FakeArchive:
    """`FragmentMedia` as the service uses it: enabled, coverage, open, read."""

    def __init__(self, run_id, data, *, start=START, times=TIMES, coverage=None):
        self.enabled = True
        self.run_id, self.start, self.times = run_id, start, list(times)
        self.media = Selection(data, f"{run_id}:0:1530000")
        self._coverage = coverage or (T0 - timedelta(seconds=60), T0 + timedelta(seconds=60))
        self.error = None     # what open() raises
        self.gone = False     # read() finds the footage evicted
        self.opened = []
        self.expired = []

    def expire_before(self, cutoff):
        self.expired.append(cutoff)

    def coverage(self):
        return self._coverage() if callable(self._coverage) else self._coverage

    @contextmanager
    def open(self, start, end, *, descriptor=None, required_span=None):
        if self.error is not None:
            raise self.error
        self.opened.append((start, end))
        result = SimpleNamespace(start_utc=self.start, duration_s=DURATION, truncated=False,
                                 video_start_s=0.0, frame_times=list(self.times),
                                 media_run=None, source_pts=None)
        yield self.media, result, {"version": 1, "run_id": self.run_id, "start": 0, "end": 1530000}

    @contextmanager
    def read(self, descriptor):
        if self.gone or descriptor.get("run_id") != self.run_id:
            raise LookupError("replay footage is no longer retained")
        yield self.media


class Recorder:
    def __init__(self, fragments, ghost_copies):
        self.fragments, self.ghost_copies = fragments, ghost_copies
        self.expired = []
        self.ring = SimpleNamespace(coverage=lambda kind: None, retention_s=None, max_bytes=10**9,
                                    expire_before=self.expired.append)

    def status(self):
        return {"recording": True, "frame_source": "plugin"}


def make_service(tmp_path, twin="same", *, wait_s=0.0):
    """A service over a clean archive and, unless `twin` is None, a twin one."""
    clean = FakeArchive("clean-run", CLEAN_BYTES)
    copies = FakeArchive("twin-run", TWIN_BYTES) if twin == "same" else twin
    cfg = ReplayConfig(save_root=tmp_path / "replays", pre_pad_s=3.0,
                       scratch_dir=tmp_path / "buf", extract_wait_s=wait_s)
    return ReplayService(cfg=cfg, recorder=Recorder(clean, copies),
                         extractor=FakeExtractor(), tracker=FakeTracker([attempt()]))


def client_for(svc) -> TestClient:
    app = FastAPI()
    app.include_router(create_replay_router(svc))
    return TestClient(app)


# -- the cut: offered only when the copy IS the clean clip ----------------------

@pytest.mark.parametrize("twin, reason", [
    ({}, None),
    ({"start": START + timedelta(microseconds=1)}, GHOST_COPY_PARTIAL),
    ({"times": TIMES[:-1]}, GHOST_COPY_PARTIAL),                     # its tail is missing
    ({"times": [*TIMES[:3], TIMES[3] + 0.000001, *TIMES[4:]]}, GHOST_COPY_PARTIAL),
    ({"times": [*TIMES, 0.2002]}, GHOST_COPY_PARTIAL),
], ids=["identical", "start-1us-late", "one-picture-short", "one-picture-moved-1us",
        "one-picture-extra"])
def test_the_copy_is_offered_only_when_its_start_and_picture_times_are_identical(
        tmp_path, twin, reason):
    svc = make_service(tmp_path, FakeArchive("twin-run", TWIN_BYTES, **twin))
    view = svc.view(42)

    assert view["clip_url"] == "/api/replay/clips/clip_attempt_42.mp4"
    assert view["frame_times"] == TIMES                      # always the clean clip's
    assert view["ghost_copy_url"] == (BUFFER_URL if reason is None else None)
    assert view["ghost_copy_reason"] == reason
    assert svc.ghost_copies.opened == [(START, END)]         # the clean clip's exact span
    stored = json.loads((svc.clips_dir / "clip_attempt_42.json").read_text())
    assert stored["ghost_copy_reason"] == reason
    assert stored["ghost_copy_source"] == (
        None if reason else {"version": 1, "run_id": "twin-run", "start": 0, "end": 1530000})


def test_without_a_copy_the_replay_says_why_and_plays_clean(tmp_path):
    disabled = FakeArchive("twin-run", TWIN_BYTES)
    disabled.enabled = False                                  # the recorder records none
    nothing = FakeArchive("twin-run", TWIN_BYTES)
    nothing.error = ValueError("No published replay footage is available yet")
    for index, twin in enumerate((None, disabled, nothing)):
        view = make_service(tmp_path / str(index), twin).view(42)
        assert (view["ghost_copy_url"], view["ghost_copy_reason"]) == (None, GHOST_COPY_NONE)
        assert view["clip_url"].endswith("clip_attempt_42.mp4") and view["frame_times"] == TIMES


def test_a_fault_in_the_copy_never_costs_the_clean_replay(tmp_path):
    broken = FakeArchive("twin-run", TWIN_BYTES)
    broken.error = RuntimeError("twin index corrupt")
    view = make_service(tmp_path, broken).view(42)
    assert view["clip_url"].endswith("clip_attempt_42.mp4") and view["frame_times"] == TIMES
    assert (view["ghost_copy_url"], view["ghost_copy_reason"]) == (None, GHOST_COPY_UNREADABLE)


def test_a_cut_taken_at_once_waits_briefly_for_the_copys_tail(tmp_path):
    """The copy is encoded by its own helper, so its published tail trails the
    clean recording's. A copy covering the clip's start gets a bounded moment
    to reach the clean clip's last picture; one that does not reach back to
    the start can never match, and is not waited for."""
    polls = []

    def catching_up():
        polls.append(1)
        return START - timedelta(seconds=5), END - timedelta(seconds=0.5 if len(polls) < 4 else 0)

    svc = make_service(tmp_path / "a", FakeArchive("twin-run", TWIN_BYTES, coverage=catching_up),
                       wait_s=5.0)
    assert svc.view(42)["ghost_copy_url"] == BUFFER_URL
    assert len(polls) == 4

    late_polls = []
    late = FakeArchive("twin-run", TWIN_BYTES, start=START + timedelta(seconds=1),
                       coverage=lambda: late_polls.append(1) or (START + timedelta(seconds=1), END))
    assert make_service(tmp_path / "b", late, wait_s=5.0).view(42)["ghost_copy_reason"] \
        == GHOST_COPY_PARTIAL
    assert len(late_polls) == 1

    started = time.monotonic()
    stuck = FakeArchive("twin-run", TWIN_BYTES, times=TIMES[:-1],
                        coverage=(START, END - timedelta(seconds=1)))
    assert make_service(tmp_path / "c", stuck, wait_s=5.0).view(42)["ghost_copy_reason"] \
        == GHOST_COPY_PARTIAL
    assert GHOST_COPY_TAIL_WAIT_S <= time.monotonic() - started < GHOST_COPY_TAIL_WAIT_S + 0.5


def test_a_cached_cut_whose_copy_left_the_buffer_says_so(tmp_path):
    svc = make_service(tmp_path)
    assert svc.view(42)["ghost_copy_url"] == BUFFER_URL
    assert svc.view(42)["ghost_copy_url"] == BUFFER_URL        # from the cached sidecar
    svc.ghost_copies.gone = True
    again = svc.view(42)
    assert (again["ghost_copy_url"], again["ghost_copy_reason"]) == (None, GHOST_COPY_GONE)
    assert again["clip_url"].endswith("clip_attempt_42.mp4")


def test_a_legacy_segment_cut_has_no_copy(tmp_path):
    from test_replay_service import make_service as legacy_service
    view = legacy_service(tmp_path, [attempt()]).view(42)
    assert (view["ghost_copy_url"], view["ghost_copy_reason"]) == (None, GHOST_COPY_NONE)


def test_the_recent_attempt_window_expires_ghost_copies_with_the_clean_footage(tmp_path):
    """The ring leaves `ghostcopy:` extents to their own archive, so the
    service's history cutoff must reach it, or copies outlive their replays."""
    cutoff = T0 - timedelta(seconds=90)
    for index, enabled in enumerate((True, False)):
        twin = FakeArchive("twin-run", TWIN_BYTES)
        twin.enabled = enabled
        svc = make_service(tmp_path / str(index), twin)
        svc.history.cutoff = lambda pre_pad_s: cutoff
        svc.recorder.maintain_history()
        assert svc.recorder.expired == [cutoff] and svc.fragments.expired == [cutoff]
        assert twin.expired == [cutoff]
    bare = make_service(tmp_path / "none", None)    # a recorder without ghost copies
    bare.history.cutoff = lambda pre_pad_s: cutoff
    bare.recorder.maintain_history()
    assert bare.fragments.expired == [cutoff]


# -- the two routes serve ranges exactly like the clean ones -------------------

REQUESTS = [{}, {"Range": "bytes=0-9"}, {"Range": "bytes=-7"}, {"Range": "bytes=5-"},
            {"Range": "bytes=99999999-"}]


def _served(client, url, data):
    """Every request shape the player sends: the bodies must be `data`'s own
    slices; the statuses and range headers are returned for comparison."""
    shape = []
    for headers, body in zip(REQUESTS, [data, data[:10], data[-7:], data[5:], None], strict=True):
        r = client.get(url, headers=headers)
        shape.append((r.status_code, r.headers.get("accept-ranges"), "content-range" in r.headers))
        if body is not None:
            assert r.content == body, (url, headers)
    head = client.head(url)
    assert head.content == b"" and head.headers["content-length"] == str(len(data))
    return [*shape, head.status_code]


def test_the_buffer_copy_is_served_with_ranges_like_the_buffer_clip(tmp_path):
    svc = make_service(tmp_path)
    view = svc.view(42)
    with client_for(svc) as client:
        clean = _served(client, view["clip_url"], CLEAN_BYTES)
        assert _served(client, view["ghost_copy_url"], TWIN_BYTES) == clean == [
            (200, "bytes", False), (206, "bytes", True), (206, "bytes", True),
            (206, "bytes", True), (416, "bytes", True), 200]
        etag = client.get(BUFFER_URL).headers["etag"]
        assert etag != client.get(view["clip_url"]).headers["etag"]
        resumed = client.get(BUFFER_URL, headers={"Range": "bytes=0-3", "If-Range": etag})
        assert (resumed.status_code, resumed.content) == (206, TWIN_BYTES[:4])
        for name in ("clip_attempt_42.mp4", "ghostcopy_attempt_42.m4v", "ghostcopy_attempt_.mp4",
                     "ghostcopy_attempt_42.mp4.json", "ghostcopy_attempt_7.mp4"):
            assert client.get(f"/api/replay/ghostcopy/clips/{name}").status_code == 404, name


def test_the_saved_copy_is_served_with_ranges_like_the_saved_clip(tmp_path):
    svc = make_service(tmp_path)
    view = svc.view(42)
    svc.save(42)
    touched = []
    svc.compressor = SimpleNamespace(touch=touched.append)
    with client_for(svc) as client:
        saved = svc.view(42)
        assert (saved["ghost_copy_url"], saved["ghost_copy_reason"]) == (SAVED_URL, None)
        clean = _served(client, "/api/replay/saved/42", CLEAN_BYTES)
        assert _served(client, SAVED_URL, TWIN_BYTES) == clean
        assert [status for status, *_ in clean[1:4]] == [206, 206, 206]
        # The URL the drawer already held keeps playing after promotion.
        assert client.get(view["ghost_copy_url"]).content == TWIN_BYTES
        assert client.get("/api/replay/saved/7/ghosts").status_code == 404
    copy = svc.find_saved(42).with_suffix(".ghosts.m4v")
    assert copy in touched, "a player reading the copy holds off its compression swap"


# -- save: published beside the clip, before it, crash-safe ---------------------

def test_save_publishes_the_copy_beside_the_clip_and_records_it(tmp_path):
    svc = make_service(tmp_path)
    svc.view(42)
    saved = Path(svc.save(42)["path"])

    copy = saved.with_suffix(".ghosts.m4v")
    assert saved.read_bytes() == CLEAN_BYTES and copy.read_bytes() == TWIN_BYTES
    sidecar = json.loads(saved.with_suffix(".json").read_text())
    assert sidecar["ghost_copy"] == {"file": copy.name, "bytes": len(TWIN_BYTES),
                                     "sha256": hashlib.sha256(TWIN_BYTES).hexdigest()}
    assert sidecar["ghost_copy_reason"] is None
    assert sorted(p.name for p in saved.parent.iterdir()) == sorted(
        [saved.name, copy.name, saved.with_suffix(".json").name, saved.with_suffix(".review.json").name])
    assert not (svc.cfg.save_root / ".pending").exists() or not any((svc.cfg.save_root / ".pending").iterdir())


def test_a_copy_that_left_the_buffer_before_save_costs_only_the_copy(tmp_path):
    svc = make_service(tmp_path)
    svc.view(42)
    svc.ghost_copies.gone = True
    saved = Path(svc.save(42)["path"])
    assert saved.read_bytes() == CLEAN_BYTES
    assert not saved.with_suffix(".ghosts.m4v").exists()
    sidecar = json.loads(saved.with_suffix(".json").read_text())
    assert (sidecar["ghost_copy"], sidecar["ghost_copy_reason"]) == (None, GHOST_COPY_LOST_BEFORE_SAVE)
    view = svc.view(42)
    assert (view["ghost_copy_url"], view["ghost_copy_reason"]) == (None, GHOST_COPY_LOST_BEFORE_SAVE)


def test_a_copy_that_fails_mid_write_costs_only_the_copy(tmp_path, monkeypatch):
    svc = make_service(tmp_path)
    svc.view(42)

    def torn(start=0, end=None):
        yield TWIN_BYTES[:100]
        raise OSError("extent unreadable")

    monkeypatch.setattr(svc.ghost_copies.media, "chunks", torn)
    saved = Path(svc.save(42)["path"])
    assert saved.read_bytes() == CLEAN_BYTES
    sidecar = json.loads(saved.with_suffix(".json").read_text())
    assert sidecar["ghost_copy"] is None
    assert sidecar["ghost_copy_reason"].startswith("The ghost copy could not be saved")
    assert not list(svc.cfg.save_root.rglob("*.m4v")) and not list(svc.cfg.save_root.rglob("ghostcopy*"))


def test_a_saved_replay_without_its_copy_file_says_so(tmp_path):
    svc = make_service(tmp_path)
    svc.view(42)
    saved = Path(svc.save(42)["path"])
    saved.with_suffix(".ghosts.m4v").unlink()
    view = svc.view(42)
    assert (view["ghost_copy_url"], view["ghost_copy_reason"]) == (None, GHOST_COPY_MISSING)
    assert view["clip_url"] == "/api/replay/saved/42"


def _inputs(tmp_path):
    source = tmp_path / "scratch.mp4"
    source.write_bytes(CLEAN_BYTES)
    copy = tmp_path / "scratch-copy.mp4"
    copy.write_bytes(TWIN_BYTES)
    root = tmp_path / "saved"
    return source, copy, root, root / "date" / "attempt_0042_test.mp4"


def test_the_copy_is_renamed_beside_the_clip_before_the_mp4_commits(tmp_path, monkeypatch):
    source, copy, root, destination = _inputs(tmp_path)
    replace = publication.os.replace

    def interrupt(src, dst):
        if Path(dst) == destination.resolve():
            assert destination.with_suffix(".ghosts.m4v").read_bytes() == TWIN_BYTES
            raise OSError("injected interruption before video commit")
        return replace(src, dst)

    monkeypatch.setattr(publication.os, "replace", interrupt)
    with pytest.raises(OSError, match="injected"):
        publication.publish(root, 42, source, destination, {"clock": 7}, {}, ghost_copy=copy)
    intent = json.loads((root / ".pending" / "42" / "intent.json").read_text())
    assert intent["version"] == 3
    assert intent["ghost_copy"] == {"bytes": len(TWIN_BYTES),
                                    "sha256": hashlib.sha256(TWIN_BYTES).hexdigest()}
    assert not destination.exists()
    source.unlink()
    copy.unlink()
    monkeypatch.setattr(publication.os, "replace", replace)
    assert publication.recover(root) == []
    assert destination.read_bytes() == CLEAN_BYTES
    assert destination.with_suffix(".ghosts.m4v").read_bytes() == TWIN_BYTES
    assert json.loads(destination.with_suffix(".json").read_text())["ghost_copy"]["bytes"] == len(TWIN_BYTES)


def test_crash_recovery_installs_a_copy_still_pending(tmp_path, monkeypatch):
    """The crash lands on the copy's own rename: the copy is still staged in
    `.pending/<id>/ghostcopy.m4v` and nothing is public yet."""
    source, copy, root, destination = _inputs(tmp_path)
    replace = publication.os.replace

    def interrupt(src, dst):
        if Path(dst) == destination.with_suffix(".ghosts.m4v").resolve():
            raise OSError("power cut while installing the ghost copy")
        return replace(src, dst)

    monkeypatch.setattr(publication.os, "replace", interrupt)
    with pytest.raises(OSError, match="power cut"):
        publication.publish(root, 42, source, destination, {"ghost_copy": None}, {}, ghost_copy=copy)
    pending = root / ".pending" / "42"
    assert (pending / "ghostcopy.m4v").read_bytes() == TWIN_BYTES
    assert not destination.exists() and not destination.with_suffix(".ghosts.m4v").exists()
    source.unlink()
    copy.unlink()
    monkeypatch.setattr(publication.os, "replace", replace)

    assert publication.recover(root) == []
    assert destination.read_bytes() == CLEAN_BYTES
    assert destination.with_suffix(".ghosts.m4v").read_bytes() == TWIN_BYTES
    sidecar = json.loads(destination.with_suffix(".json").read_text())
    assert sidecar["ghost_copy"]["sha256"] == hashlib.sha256(TWIN_BYTES).hexdigest()
    assert not pending.exists()


def test_the_service_resumes_a_save_interrupted_with_its_copy_pending(tmp_path, monkeypatch):
    svc = make_service(tmp_path)
    svc.view(42)
    replace = publication.os.replace

    def interrupt(src, dst):
        if Path(src).name == "media.mp4":
            raise OSError("closed mid-save")
        return replace(src, dst)

    monkeypatch.setattr(publication.os, "replace", interrupt)
    with pytest.raises(OSError, match="closed mid-save"):
        svc.save(42)
    monkeypatch.setattr(publication.os, "replace", replace)
    svc.ghost_copies.gone = True           # the buffer is no longer needed
    saved = Path(svc.save(42)["path"])
    assert saved.with_suffix(".ghosts.m4v").read_bytes() == TWIN_BYTES
    assert svc.view(42)["ghost_copy_url"] == SAVED_URL


def test_a_copy_without_room_is_left_out_and_the_clip_still_saves(tmp_path, monkeypatch):
    source, copy, root, destination = _inputs(tmp_path)
    reserve = 1000
    short = reserve + len(CLEAN_BYTES) + len(TWIN_BYTES) - 1        # one byte short for both
    monkeypatch.setattr(publication.shutil, "disk_usage", lambda _path: SimpleNamespace(free=short))
    publication.publish(root, 42, source, destination, {}, {}, ghost_copy=copy, reserve_bytes=reserve)
    assert destination.read_bytes() == CLEAN_BYTES
    sidecar = json.loads(destination.with_suffix(".json").read_text())
    assert sidecar["ghost_copy"] is None
    assert sidecar["ghost_copy_reason"] == "Not enough free storage to keep the ghost copy"
    assert not destination.with_suffix(".ghosts.m4v").exists()


# -- nothing that indexes the save tree sees the copy ---------------------------

def test_the_copy_is_invisible_to_every_save_tree_index(tmp_path):
    svc = make_service(tmp_path)
    svc.view(42)
    saved = Path(svc.save(42)["path"])
    root = svc.cfg.save_root
    # An orphan copy for another attempt: still never a saved replay.
    shutil.copyfile(saved.with_suffix(".ghosts.m4v"),
                    saved.with_name("attempt_0043_orphan_0m01s00.ghosts.m4v"))

    assert SavedClipIndex(root).ids() == {42}
    assert SavedClipIndex(root).find(42) == saved
    assert saved_attempt_ids(root) == {42} and saved_attempt_ids(root, among=[42, 43]) == {42}
    svc._saved_index.forget()
    assert svc.saved_attempt_ids() == {42} and svc.find_saved(43) is None
    assert sorted(p.name for p in root.rglob("attempt_*.mp4")) == [saved.name]
    assert not list(root.rglob("attempt_*.ghost"))          # the native pose scanners' pattern
    # The storage total is real disk use, so it does count the copy.
    assert svc.settings()["saved_bytes"] == sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


# -- compression: the same job, the copy on its own ----------------------------

CPU_ONLY = ("libx264",)


def _ffmpeg() -> str:
    from sm64_events.core.paths import bundled_ffmpeg
    ff = bundled_ffmpeg() or shutil.which("ffmpeg")
    if not ff:
        pytest.skip("ffmpeg binary not available")
    return ff


def _clip(ff, path, look=""):
    """Two seconds, every 7th picture missing, near-lossless: the shape of
    `test_replay_compress`'s fixture. Both files share its timing exactly;
    `look` filters the pictures so the copy is not the clip's bytes. (A flat
    test card does not shrink by the archive's minimum saving.)"""
    subprocess.run([
        ff, "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=640x480:rate=30:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
        "-vf", f"{look}select='mod(n,7)'", "-fps_mode", "passthrough",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "4", "-bf", "0",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(path),
    ], check=True, capture_output=True)


@pytest.fixture(scope="module")
def two_clips(tmp_path_factory):
    ff = _ffmpeg()
    folder = tmp_path_factory.mktemp("twin-sources")
    clean, twin = folder / "clean.mp4", folder / "twin.mp4"
    _clip(ff, clean)
    _clip(ff, twin, "negate,")
    return clean, twin


@pytest.fixture
def saved_with_copy(two_clips, tmp_path):
    from sm64_events.replay.extract import frame_times_of
    clean_src, twin_src = two_clips
    saved = tmp_path / "replays" / "2026-10-03" / "session_1" / "attempt_0042_test_0m02s00.mp4"
    saved.parent.mkdir(parents=True)
    shutil.copy2(clean_src, saved)
    copy = saved.with_suffix(".ghosts.m4v")
    shutil.copy2(twin_src, copy)
    times = [round(t, 6) for t in frame_times_of(_ffmpeg(), saved)]
    assert [round(t, 6) for t in frame_times_of(_ffmpeg(), copy)] == times
    saved.with_suffix(".json").write_text(json.dumps({
        "duration_s": 2.0, "truncated": False, "encode": "picture_feed", "frame_times": times,
        "ghost_copy": {"file": copy.name, "bytes": copy.stat().st_size,
                       "sha256": compress.file_sha256(copy)},
        "ghost_copy_reason": None}))
    return saved


def _cpu_clip(**options):
    return lambda ff, clip, **kw: compress.shrink(ff, clip, codecs=CPU_ONLY, **options, **kw)


def _cpu_copy(**options):
    return lambda ff, clip, **kw: compress.shrink_ghost_copy(ff, clip, codecs=CPU_ONLY, **options, **kw)


def _compressor(saved, *, clean=None, ghost=None):
    return compress.SavedReplayCompressor(
        _ffmpeg(), saved.parents[2], poll_s=0.05, idle_s=0,
        shrink_fn=clean or _cpu_clip(), ghost_shrink_fn=ghost or _cpu_copy())


def _until(settled, worker, saved=None):
    worker.start()
    try:
        if saved is not None:
            worker.enqueue(saved, attempt_id=42)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and not settled():
            time.sleep(0.05)
    finally:
        worker.stop()


def _sidecar(saved):
    return json.loads(saved.with_suffix(".json").read_text())


def _copy_state(saved):
    return (_sidecar(saved)["ghost_copy"].get("media") or {}).get("state")


def _copy_settled(saved):
    return _copy_state(saved) in compress.GHOST_SETTLED_STATES


def _silent(args, **kwargs):
    """An encode that drops the audio: always refused by the proof, and as
    quick as a real one."""
    args = list(args)
    at = args.index("0:a:0?")
    del args[at - 1:at + 1]                      # -map 0:a:0?
    at = args.index("-c:a")
    args[at:at + 2] = ["-an"]
    return compress.run_child(args, **kwargs)


def _must_not_run(*_args, **_kwargs):
    raise AssertionError("a second generation of loss")


def test_the_same_job_shrinks_the_copy_after_the_clip_and_on_its_own(saved_with_copy):
    saved, copy = saved_with_copy, saved_with_copy.with_suffix(".ghosts.m4v")
    before, copy_before = _sidecar(saved), copy.stat().st_size
    order = []

    def clip_then(ff, clip, **kw):
        order.append("clip")
        return compress.shrink(ff, clip, codecs=CPU_ONLY, **kw)

    def copy_then(ff, clip, **kw):
        order.append("copy")
        return compress.shrink_ghost_copy(ff, clip, codecs=CPU_ONLY, **kw)

    worker = _compressor(saved, clean=clip_then, ghost=copy_then)
    _until(lambda: _copy_settled(saved), worker, saved)

    after = _sidecar(saved)
    assert order == ["clip", "copy"]
    assert after["media"]["state"] == "compressed"
    block = after["ghost_copy"]
    assert block["media"]["state"] == "compressed" and block["file"] == copy.name
    assert block["bytes"] == copy.stat().st_size < copy_before
    assert block["sha256"] == compress.file_sha256(copy) != before["ghost_copy"]["sha256"]
    # The copy's pass rewrote only its own block; the clip's verdict is the clip's.
    assert {k: v for k, v in after.items() if k not in ("media", "ghost_copy")} == \
        {k: v for k, v in before.items() if k != "ghost_copy"}
    assert list(worker.work.iterdir()) == []
    assert sorted(p.name for p in saved.parent.iterdir()) == sorted(
        [saved.name, copy.name, saved.with_suffix(".json").name])
    # A second Save re-queues the replay: the settled copy is never encoded again.
    assert compress.shrink_ghost_copy(_ffmpeg(), saved, run=_must_not_run) == block["media"]


def test_a_refused_copy_keeps_its_bytes_and_the_clip_still_shrinks(saved_with_copy):
    saved, copy = saved_with_copy, saved_with_copy.with_suffix(".ghosts.m4v")
    original, identity = copy.read_bytes(), _sidecar(saved)["ghost_copy"]
    worker = _compressor(saved, ghost=_cpu_copy(run=_silent))
    _until(lambda: _copy_settled(saved) and _sidecar(saved).get("media", {}).get("state")
           in compress.SETTLED_STATES, worker, saved)

    after = _sidecar(saved)
    assert after["media"]["state"] == "compressed"
    assert after["ghost_copy"]["media"]["state"] == "kept_original"
    assert "the audio changed" in after["ghost_copy"]["media"]["reasons"][0]
    assert copy.read_bytes() == original
    assert {k: after["ghost_copy"][k] for k in ("file", "bytes", "sha256")} == identity
    assert list(worker.work.iterdir()) == []


def test_a_refused_clip_never_stops_its_copy_from_shrinking(saved_with_copy):
    saved, copy = saved_with_copy, saved_with_copy.with_suffix(".ghosts.m4v")
    clean_bytes = saved.read_bytes()
    worker = _compressor(saved, clean=_cpu_clip(run=_silent))
    _until(lambda: _copy_settled(saved), worker, saved)

    after = _sidecar(saved)
    assert after["media"]["state"] == "kept_original" and saved.read_bytes() == clean_bytes
    assert "the audio changed" in after["media"]["reasons"][0]
    assert after["ghost_copy"]["sha256"] == compress.file_sha256(copy)
    assert list(worker.work.iterdir()) == []


@pytest.mark.parametrize("clip", ["shrinks", "refused"])
def test_closing_mid_copy_leaves_the_job_owed_for_the_copy_alone(saved_with_copy, clip):
    """The clip's half is settled either way (adopted later, or refused at
    once); the note must outlive it while the copy is still owed."""
    saved = saved_with_copy
    closing = threading.Event()

    def closed_during_the_copy(ff, clip, **kw):
        closing.set()
        return compress.shrink_ghost_copy(ff, clip, codecs=CPU_ONLY, **{**kw, "cancel": closing})

    first = _compressor(saved, ghost=closed_during_the_copy,
                        clean=_cpu_clip(run=_silent) if clip == "refused" else None)
    _until(lambda: any(o.get("ghost_copy") for o in first.outcomes), first, saved)
    assert _sidecar(saved)["media"]["state"] == ("compressed" if clip == "shrinks" else "kept_original")
    assert _copy_state(saved) is None
    assert [p.name for p in first.work.iterdir()] == [saved.name + compress.JOB_SUFFIX]

    # Next launch: the note brings the job back, and only the copy is encoded.
    second = _compressor(saved, clean=_cpu_clip(run=_must_not_run))
    _until(lambda: _copy_settled(saved), second)
    assert _copy_state(saved) == "compressed"
    assert list(second.work.iterdir()) == []


def test_a_crash_between_the_copy_swap_and_its_sidecar_write_is_settled_by_digest(tmp_path):
    saved = tmp_path / "attempt_0042_x.mp4"
    saved.write_bytes(CLEAN_BYTES)
    copy = saved.with_suffix(".ghosts.m4v")
    copy.write_bytes(TWIN_BYTES[:500])                  # the swapped-in, smaller copy
    old = {"file": copy.name, "bytes": len(TWIN_BYTES), "sha256": hashlib.sha256(TWIN_BYTES).hexdigest()}
    saved.with_suffix(".json").write_text(json.dumps({"media": {"state": "kept_original"},
                                                      "ghost_copy": old}))
    proof = compress.ghost_proof_path(saved)
    proof.write_text(json.dumps({"state": "ready", "bytes": 500,
                                 "sha256": hashlib.sha256(TWIN_BYTES[:500]).hexdigest(),
                                 "original": {"bytes": old["bytes"], "sha256": old["sha256"]}}))
    compress.settle_ghost_copy(saved)
    block = _sidecar(saved)["ghost_copy"]
    assert (block["bytes"], block["sha256"]) == (500, compress.file_sha256(copy))
    assert block["media"]["state"] == "compressed" and not proof.exists()
    assert _sidecar(saved)["media"] == {"state": "kept_original"}

    # A proof whose bytes never landed is void, and the sidecar is left alone.
    proof.write_text(json.dumps({"state": "ready", "bytes": 7, "sha256": "0" * 64}))
    compress.settle_ghost_copy(saved)
    assert not proof.exists() and _sidecar(saved)["ghost_copy"] == block


# -- the real archive: VirtualMp4 bytes end to end ------------------------------

@pytest.fixture
def real_service(tmp_path):
    """`test_replay_fragment_service`'s real recording and picture ledger, plus
    a twin archive fed the same pictures on the same media clock. Yields the
    service and the recording, for a twin on another clock."""
    from test_replay_fragment_producer import continuing_output
    from test_replay_picture_identity import picture
    from sm64_events.replay.extract import ClipExtractor
    from sm64_events.replay.fragmentmedia import FragmentMedia
    from sm64_events.replay.ledger import PictureLedger
    from sm64_events.replay.media import MediaRun
    from sm64_events.replay.ring import SegmentRing

    ff = _ffmpeg()
    continuing_output(tmp_path, ff, "libx264")
    recording = (tmp_path / "live.mp4").read_bytes()
    root = tmp_path / "scratch"
    (root / "ghostcopy").mkdir(parents=True)
    ledger = PictureLedger()
    ledger.open_archive(root / "identity.sqlite3")
    ring = SegmentRing(None, 10**8, scratch_root=root,
                       on_temp_evict=lambda *args: fragments.evicted(*args))
    fragments = FragmentMedia(root, ring, ledger)
    copies = FragmentMedia(root / "ghostcopy", ring, None)
    fragments.enabled = copies.enabled = True
    run = MediaRun("fixture", 1000)
    for media, media_run in ((fragments, run), (copies, MediaRun("twin", run.origin_ts))):
        archive = media.create(media_run, (640, 480))
        archive.feed(recording)
        archive.finish()
        assert archive.error is None
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
    recorder = SimpleNamespace(ring=ring, ledger=ledger, fragments=fragments, ghost_copies=copies)
    svc = ReplayService(cfg, recorder, ClipExtractor(cfg, "libx264", ff, fragments=fragments),
                        FakeTracker([a]))
    try:
        yield svc, recording
    finally:
        ledger.detach()


def test_a_real_twin_archive_end_to_end(real_service):
    """View offers the copy, its route serves the twin archive's own selection
    with ranges, Save publishes exactly those bytes, and the Whole picture
    ghost still reads the clean clip."""
    from sm64_events.ghost.source import from_marked_attempt
    svc, _ = real_service
    with client_for(svc) as client:
        view = client.post("/api/attempts/42/replay").json()
        assert view["ghost_copy_url"] == BUFFER_URL, view["ghost_copy_reason"]
        assert view["frame_map"] == list(range(1015, 1060))
        twin = client.get(BUFFER_URL)
        assert twin.status_code == 200 and twin.headers["etag"].startswith('"twin:')
        assert client.get(view["clip_url"]).headers["etag"].startswith('"fixture:')
        media = twin.content
        part = client.get(BUFFER_URL, headers={"Range": "bytes=100-199"})
        assert part.status_code == 206 and part.content == media[100:200]
        assert client.head(BUFFER_URL).headers["content-length"] == str(len(media))

        saved = Path(svc.save(42)["path"])
        copy = saved.with_suffix(".ghosts.m4v")
        assert copy.read_bytes() == media
        assert json.loads(saved.with_suffix(".json").read_text())["ghost_copy"]["sha256"] == \
            hashlib.sha256(media).hexdigest()
        assert client.get(SAVED_URL).content == media
        assert client.get(BUFFER_URL).content == media       # the drawer's old URL

    ghost = from_marked_attempt(SimpleNamespace(origin="attempt:42", id=5), replay=svc)
    assert ghost.available and ghost.media_url == "/api/replay/saved/42"
    assert Path(ghost.clip_path).resolve() == saved.resolve()
    assert ghost.view["clip_url"] == "/api/replay/saved/42"


def test_a_real_twin_one_tick_off_the_clean_clock_is_refused(real_service):
    from sm64_events.replay.fragmentmedia import FragmentMedia
    from sm64_events.replay.media import MediaRun
    svc, recording = real_service
    late = FragmentMedia(svc.cfg.scratch_dir / "ghostcopy-late", svc.recorder.ring, None)
    late.directory.mkdir()
    late.enabled = True
    archive = late.create(MediaRun("late", 1000 + 1 / 90000), (640, 480))
    archive.feed(recording)
    archive.finish()
    svc.recorder.ghost_copies = late
    view = svc.view(42)
    assert (view["ghost_copy_url"], view["ghost_copy_reason"]) == (None, GHOST_COPY_PARTIAL)
    assert view["frame_map"] == list(range(1015, 1060))

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.extract import ClipResult
from sm64_events.replay.service import ReplayService, slug_filename
from sm64_events.tracking.projection import Attempt

T0 = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)


def attempt(**kw):
    base = dict(id=42, session_id=3, course_id=2, star_id=2, strat_tag=None,
                anchor_type="practice_reset", anchor_frame=100,
                outcome="success", outcome_detail=None,
                igt_frames=343, rta_frames=350,
                started_utc=T0.isoformat().replace("+00:00", "Z"),
                ended_utc=(T0 + timedelta(seconds=12)).isoformat().replace("+00:00", "Z"),
                cleared=False, cleared_reason=None)
    base.update(kw)
    return Attempt(**base)


class FakeDb:
    def __init__(self, attempts):
        self._attempts = attempts
    def attempts(self):
        return self._attempts


class FakeTracker:
    def __init__(self, attempts):
        self.db = FakeDb(attempts)
        self.session_id = 3


class FakeRing:
    def __init__(self, cov):
        self._cov = cov
    def coverage(self, kind):
        return self._cov


class FakeRecorder:
    def __init__(self, cov):
        self.ring = FakeRing(cov)
    def status(self):
        return {"recording": True, "window_found": True, "audio_mode": "process",
                "encoder": "libx264", "buffer_start_utc": None,
                "buffer_end_utc": None, "disk_bytes": 0}


class FakeExtractor:
    def __init__(self):
        self.calls = []
    def extract(self, ring, start, end, out_path):
        self.calls.append((start, end, out_path))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"mp4")
        return ClipResult(path=out_path, duration_s=(end - start).total_seconds(),
                          truncated=False)


def make_service(tmp_path, attempts, cov=None):
    cfg = ReplayConfig(save_root=tmp_path / "replays",
                       scratch_dir=tmp_path / "buf", extract_wait_s=0.0)
    cov = cov or (T0 - timedelta(seconds=60), T0 + timedelta(seconds=60))
    return ReplayService(cfg=cfg, recorder=FakeRecorder(cov),
                         extractor=FakeExtractor(), tracker=FakeTracker(attempts))


def test_view_pads_span_and_returns_clip_url(tmp_path):
    svc = make_service(tmp_path, [attempt()])
    res = svc.view(42)
    assert res["clip_url"] == "/api/replay/clips/clip_attempt_42.mp4"
    assert res["truncated"] is False
    assert res["duration_s"] == 17.0          # 12 s attempt + 3 pre + 2 post
    start, end, _ = svc.extractor.calls[0]
    assert start == T0 - timedelta(seconds=3)            # pre_pad
    assert end == T0 + timedelta(seconds=12 + 2)         # post_pad


def test_view_unknown_attempt_raises_lookup(tmp_path):
    svc = make_service(tmp_path, [])
    try:
        svc.view(99)
        assert False
    except LookupError:
        pass


def test_view_is_cached_second_call_skips_extract(tmp_path):
    svc = make_service(tmp_path, [attempt()])
    svc.view(42)
    svc.view(42)
    assert len(svc.extractor.calls) == 1


def test_save_copies_into_date_session_tree(tmp_path):
    svc = make_service(tmp_path, [attempt()])
    res = svc.save(42)
    p = Path(res["path"])
    assert p.exists()
    assert p.parent.name == "session_3"
    assert p.parent.parent.parent == tmp_path / "replays"
    assert p.name.startswith("attempt_0042_")


def test_clip_path_validates_names(tmp_path):
    svc = make_service(tmp_path, [attempt()])
    svc.view(42)
    assert svc.clip_path("clip_attempt_42.mp4").exists()
    for bad in ("evil.txt", "../secrets.mp4", "clip_attempt_42.mp4.exe",
                "clip_attempt_.mp4"):
        try:
            svc.clip_path(bad)
            assert False, bad
        except LookupError:
            pass


def test_slug_filename_success_and_death():
    # format_igt(343) = 0'11"43  ->  replace ' -> m, " -> s  ->  0m11s43
    # format_igt(120) = 0'04"00  ->  replace ' -> m, " -> s  ->  0m04s00
    a = attempt()
    assert slug_filename(a, "Whomp's Fortress", "Chip Off Whomp's Block") == \
        "attempt_0042_whomps-fortress_chip-off-whomps-block_0m11s43.mp4"
    d = attempt(outcome="death", igt_frames=120)
    assert slug_filename(d, "Whomp's Fortress", "Chip Off Whomp's Block") == \
        "attempt_0042_whomps-fortress_chip-off-whomps-block_0m04s00_death.mp4"

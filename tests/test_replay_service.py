from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sm64_events.replay.config import ReplayConfig, apply_settings_file
from sm64_events.replay.extract import ClipResult
from sm64_events.replay.ring import SegmentRing
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
    assert res["fps"] == 60                   # encoded rate
    assert res["game_fps"] == 30              # step unit: SM64 logic frames
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
    assert res["truncated"] is False


class RaisingExtractor:
    """Stands in for a ring that no longer covers the span (later session)."""
    def __init__(self):
        self.calls = 0
    def extract(self, ring, start, end, out_path):
        self.calls += 1
        raise ValueError("no footage for that span")


def test_save_writes_metadata_sidecar(tmp_path):
    svc = make_service(tmp_path, [attempt()])
    p = Path(svc.save(42)["path"])
    import json
    m = json.loads(p.with_suffix(".json").read_text())
    assert m["duration_s"] == 17.0
    assert m["truncated"] is False
    assert m["fps"] == 60          # stamped at save time: outlives config changes


def test_save_is_idempotent_when_buffer_is_gone(tmp_path):
    # later session: scratch clips wiped, ring empty — saving again must
    # return the existing file, not try to re-extract
    svc = make_service(tmp_path, [attempt()])
    first = svc.save(42)
    import shutil
    shutil.rmtree(svc.clips_dir)
    svc.extractor = RaisingExtractor()
    again = svc.save(42)
    assert again["path"] == first["path"]
    assert svc.extractor.calls == 0


def test_view_falls_back_to_saved_file_when_buffer_gone(tmp_path):
    svc = make_service(tmp_path, [attempt()])
    saved_path = svc.save(42)["path"]
    import shutil
    shutil.rmtree(svc.clips_dir)        # restart wipes the extraction cache
    svc.extractor = RaisingExtractor()  # and the ring no longer has footage
    res = svc.view(42)
    assert res["clip_url"] == "/api/replay/saved/42"
    assert res["source"] == "saved"
    assert res["saved_path"] == saved_path
    assert res["duration_s"] == 17.0    # from the sidecar
    assert res["truncated"] is False
    assert res["fps"] == 60 and res["game_fps"] == 30
    assert svc.extractor.calls == 0     # saved file short-circuits extraction


def test_view_fallback_tolerates_legacy_saved_file_without_sidecar(tmp_path):
    # files saved before sidecars existed: still playable, metadata degrades
    svc = make_service(tmp_path, [attempt()])
    d = tmp_path / "replays" / "2026-06-11" / "session_3"
    d.mkdir(parents=True)
    (d / "attempt_0042_whomps-fortress_x_0m11s43.mp4").write_bytes(b"mp4")
    svc.extractor = RaisingExtractor()
    res = svc.view(42)
    assert res["clip_url"] == "/api/replay/saved/42"
    assert res["duration_s"] is None
    assert res["truncated"] is False
    assert res["fps"] == 60             # falls back to current config


def test_view_prefers_scratch_cache_and_reports_saved_path(tmp_path):
    # mid-session after a save: serve the scratch clip (same bytes) but
    # report saved_path so the UI shows the Saved state across reloads
    svc = make_service(tmp_path, [attempt()])
    saved_path = svc.save(42)["path"]
    res = svc.view(42)
    assert res["clip_url"] == "/api/replay/clips/clip_attempt_42.mp4"
    assert res["source"] == "buffer"
    assert res["saved_path"] == saved_path
    assert len(svc.extractor.calls) == 1   # save()'s view extracted once


def test_view_still_errors_when_no_saved_file_and_no_footage(tmp_path):
    svc = make_service(tmp_path, [attempt()])
    svc.extractor = RaisingExtractor()
    import pytest as _pytest
    with _pytest.raises(ValueError):
        svc.view(42)


def test_find_saved_zero_pad_disambiguates_ids(tmp_path):
    svc = make_service(tmp_path, [attempt(), attempt(id=4)])
    d = tmp_path / "replays" / "2026-06-11" / "session_3"
    d.mkdir(parents=True)
    (d / "attempt_0004_a_b_0m01s00.mp4").write_bytes(b"a")
    (d / "attempt_0042_a_b_0m11s43.mp4").write_bytes(b"b")
    assert svc.find_saved(4).name.startswith("attempt_0004_")
    assert svc.find_saved(42).name.startswith("attempt_0042_")
    assert svc.find_saved(420) is None


def test_saved_clip_path_resolves_or_404s(tmp_path):
    svc = make_service(tmp_path, [attempt()])
    svc.save(42)
    assert svc.saved_clip_path(42).exists()
    import pytest as _pytest
    with _pytest.raises(LookupError):
        svc.saved_clip_path(99)


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


def test_wait_for_tail_blocks_until_coverage_reaches_span_end(tmp_path):
    """Spec: a View Replay click right after the event waits (bounded) for
    the segment covering span end. Coverage 'catches up' on the 3rd poll."""
    import time as _time
    calls = {"n": 0}

    class GrowingRing:
        def coverage(self, kind):
            calls["n"] += 1
            end = 5 if calls["n"] < 3 else 60
            return (T0 - timedelta(seconds=60), T0 + timedelta(seconds=end))

    class Rec:
        def __init__(self):
            self.ring = GrowingRing()
        def status(self):
            return {"recording": True}

    cfg = ReplayConfig(save_root=tmp_path / "replays",
                       scratch_dir=tmp_path / "buf", extract_wait_s=5.0)
    svc = ReplayService(cfg=cfg, recorder=Rec(), extractor=FakeExtractor(),
                        tracker=FakeTracker([attempt()]))
    t0 = _time.monotonic()
    svc.view(42)
    elapsed = _time.monotonic() - t0
    assert calls["n"] >= 3          # waited until coverage caught up
    assert elapsed < 4.0            # returned well before the 5 s timeout


def test_wait_for_tail_short_circuits_when_not_recording(tmp_path):
    import time as _time

    class StoppedRec:
        class _Ring:
            def coverage(self, kind):
                return (T0 - timedelta(seconds=60), T0 + timedelta(seconds=1))
        def __init__(self):
            self.ring = self._Ring()
        def status(self):
            return {"recording": False}

    cfg = ReplayConfig(save_root=tmp_path / "replays",
                       scratch_dir=tmp_path / "buf", extract_wait_s=5.0)
    svc = ReplayService(cfg=cfg, recorder=StoppedRec(), extractor=FakeExtractor(),
                        tracker=FakeTracker([attempt()]))
    t0 = _time.monotonic()
    svc.view(42)                    # buffer will never grow; must not wait
    assert _time.monotonic() - t0 < 1.0


def test_reveal_validates_path_is_inside_save_root(tmp_path):
    opened = []
    cfg = ReplayConfig(save_root=tmp_path / "replays",
                       scratch_dir=tmp_path / "buf", extract_wait_s=0.0)
    svc = ReplayService(cfg=cfg, recorder=FakeRecorder(
                            (T0 - timedelta(seconds=60), T0 + timedelta(seconds=60))),
                        extractor=FakeExtractor(), tracker=FakeTracker([attempt()]),
                        revealer=opened.append)
    saved = Path(svc.save(42)["path"])
    svc.reveal(str(saved))
    assert opened == [saved.resolve()]
    import pytest
    with pytest.raises(LookupError):
        svc.reveal(str(tmp_path / "outside.mp4"))          # outside save_root
    with pytest.raises(LookupError):
        svc.reveal(str(cfg.save_root / "nope" / "x.mp4"))  # inside but missing
    with pytest.raises(LookupError):
        svc.reveal(str(cfg.save_root / ".." / "escape.mp4"))  # traversal


class RecorderWithRealRing:
    """Settings tests need a REAL ring (FakeRing has no set_limits)."""
    def __init__(self):
        self.ring = SegmentRing(retention_s=None, max_bytes=20 * 1024**3)
        self.idle_window = None
    def status(self):
        return {}
    def set_idle_after(self, window_s):
        self.idle_window = window_s


def test_settings_update_validates_persists_and_applies(tmp_path):
    cfg = ReplayConfig(save_root=tmp_path / "replays",
                       scratch_dir=tmp_path / "buf",
                       settings_path=tmp_path / "replay_settings.json")
    rec = RecorderWithRealRing()
    svc = ReplayService(cfg=cfg, recorder=rec, extractor=None, tracker=None)

    out = svc.update_settings(600.0, 5 * 1024**3)
    assert out["retention_s"] == 600.0
    assert out["max_buffer_bytes"] == 5 * 1024**3
    assert rec.ring.retention_s == 600.0               # applied live
    assert rec.ring.max_bytes == 5 * 1024**3
    # persisted: a fresh startup overlay picks the values up
    cfg2 = apply_settings_file(cfg)
    assert cfg2.retention_s == 600.0 and cfg2.max_buffer_bytes == 5 * 1024**3

    with pytest.raises(ValueError):
        svc.update_settings(5.0, 5 * 1024**3)          # retention below 60 s
    with pytest.raises(ValueError):
        svc.update_settings(None, 100)                 # cap below 1 GiB
    # failed updates must not clobber the persisted file
    assert apply_settings_file(cfg).retention_s == 600.0


def test_settings_pads_apply_to_span_and_idle_window(tmp_path):
    cfg = ReplayConfig(save_root=tmp_path / "replays",
                       scratch_dir=tmp_path / "buf",
                       settings_path=tmp_path / "replay_settings.json")
    rec = RecorderWithRealRing()
    svc = ReplayService(cfg=cfg, recorder=rec, extractor=None, tracker=None)

    out = svc.update_settings(None, 20 * 1024**3,
                              pre_pad_s=1.0, post_pad_s=0.5)
    assert out["pre_pad_s"] == 1.0 and out["post_pad_s"] == 0.5
    assert rec.idle_window == 1.5                      # follows the pad window
    start, end = svc._span(attempt())                  # pads drive the clip cut
    assert start == T0 - timedelta(seconds=1.0)
    assert end == T0 + timedelta(seconds=12 + 0.5)
    # persisted for the next startup
    cfg2 = apply_settings_file(cfg)
    assert cfg2.pre_pad_s == 1.0 and cfg2.post_pad_s == 0.5
    # omitted pads = unchanged
    svc.update_settings(None, 20 * 1024**3)
    assert svc.pre_pad_s == 1.0 and svc.post_pad_s == 0.5
    with pytest.raises(ValueError):
        svc.update_settings(None, 20 * 1024**3, pre_pad_s=11.0)


def test_settings_reports_saved_bytes_on_demand(tmp_path):
    cfg = ReplayConfig(save_root=tmp_path / "replays",
                       scratch_dir=tmp_path / "buf",
                       settings_path=tmp_path / "replay_settings.json")
    svc = ReplayService(cfg=cfg, recorder=RecorderWithRealRing(),
                        extractor=None, tracker=None)
    assert svc.settings()["saved_bytes"] == 0          # save_root absent -> 0
    d = tmp_path / "replays" / "2026-06-11" / "session_1"
    d.mkdir(parents=True)
    (d / "a.mp4").write_bytes(b"x" * 1000)
    s = svc.settings()
    assert s["saved_bytes"] == 1000
    assert s["save_root"].endswith("replays")

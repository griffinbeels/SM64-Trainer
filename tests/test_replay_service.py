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
        self.segment_defs = []  # mirrors TrackerService.segment_defs


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
                          truncated=False, start_utc=start)


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
    # The anchor sits pre_pad into the clip, MEASURED from the clip's own
    # first frame -- the input track starts at the anchor, so this is what
    # lets the two share one axis (live report 2026-08-22: every input
    # landed three seconds early) -- PLUS the display lag: the picture of
    # a frame appears one game frame after its wall time (measured from his
    # nine Forward-1 screenshots the same day).
    from sm64_events.replay.service import DISPLAY_LAG_FRAMES
    assert res["anchor_offset_s"] == 3.0 + DISPLAY_LAG_FRAMES / 30


def test_the_sidecar_carries_the_frame_map_and_a_save_keeps_it(tmp_path):
    """Round 32 item 17: with a frame clock wired, a cut clip's sidecar says
    which game frame each video frame shows, the view payload returns it,
    and the saved copy keeps it after the clock (and the ring) are gone. A
    service with no clock, or a clip the clock cannot cover, carries none
    -- the offset fallback stays what it was."""
    import json as _json

    from sm64_events.replay.frameclock import FrameClock

    clock = FrameClock(now=lambda: 0.0)
    base = (T0 - timedelta(seconds=3)).timestamp()
    for n in range(17 * 30):                     # the whole padded span, 30fps
        clock._now = lambda t=base + n / 30: t
        clock.mark(7000 + n)
    svc = make_service(tmp_path, [attempt()])
    svc._frame_clock = clock
    res = svc.view(42)
    fm = res["frame_map"]
    assert fm is not None and len(fm) == 17 * 60
    # The display lag rides INSIDE the map: the first slots predate the
    # first marked frame's picture (None, honestly), and the last slot shows
    # the frame the game finished DISPLAY_LAG_FRAMES earlier.
    from sm64_events.replay.service import DISPLAY_LAG_FRAMES
    # The wall bias (frameclock.MAP_WALL_BIAS_S, the measured half-slot the
    # whole encode chain answers late by) shifts every slot's answer one
    # slot earlier than the raw edge arithmetic would say.
    assert fm[0] is None and fm[2] == 7000
    assert fm[-1] == 7000 + 17 * 30 - 1 - DISPLAY_LAG_FRAMES + 1
    ordered = [frame for frame in fm if frame is not None]
    assert ordered == sorted(ordered)
    # ...and names the series that answered (edge marks only here), so the
    # pixel scorer's verdict says which map version it scored.
    assert res["frame_map_source"] == "edges"
    sidecar = _json.loads(
        (svc.clips_dir / "clip_attempt_42.mp4").with_suffix(".json").read_text())
    assert sidecar["frame_map"] == fm
    assert sidecar["frame_map_source"] == "edges"
    saved = svc.save(42)
    assert _json.loads(Path(saved["path"]).with_suffix(".json").read_text())[
        "frame_map"] == fm


def test_no_clock_or_no_coverage_means_no_map_not_a_crash(tmp_path):
    svc = make_service(tmp_path, [attempt()])
    assert svc.view(42)["frame_map"] is None     # no clock wired
    from sm64_events.replay.frameclock import FrameClock
    svc2 = make_service(tmp_path / "b", [attempt()])
    svc2._frame_clock = FrameClock()             # wired, never marked
    assert svc2.view(42)["frame_map"] is None


def test_the_anchor_offset_follows_the_clip_s_REAL_start_not_the_pad(tmp_path):
    """The ring may have evicted part of the lead-in; the offset is then
    shorter than the pad, and the sidecar's own start_utc says by how much."""
    class LateExtractor(FakeExtractor):
        def extract(self, ring, start, end, out_path):
            res = super().extract(ring, start, end, out_path)
            return ClipResult(path=res.path, duration_s=res.duration_s,
                              truncated=True,
                              start_utc=start + timedelta(seconds=2))
    svc = make_service(tmp_path, [attempt()])
    svc.extractor = LateExtractor()
    from sm64_events.replay.service import DISPLAY_LAG_FRAMES
    assert svc.view(42)["anchor_offset_s"] == 1.0 + DISPLAY_LAG_FRAMES / 30


def test_available_attempt_ids_saved_or_buffer_covered(tmp_path):
    # buffer covers [T0-60, T0+60]
    inside = attempt(id=1)                       # T0..T0+12 -> fully covered
    aged = attempt(id=2,                         # ended before the buffer -> gone
        started_utc=(T0 - timedelta(seconds=120)).isoformat().replace("+00:00", "Z"),
        ended_utc=(T0 - timedelta(seconds=110)).isoformat().replace("+00:00", "Z"))
    saved_only = attempt(id=3,                   # aged out but SAVED on disk
        started_utc=(T0 - timedelta(seconds=200)).isoformat().replace("+00:00", "Z"),
        ended_utc=(T0 - timedelta(seconds=190)).isoformat().replace("+00:00", "Z"))
    straddle = attempt(id=4,                      # started before buffer -> partial, excluded
        started_utc=(T0 - timedelta(seconds=70)).isoformat().replace("+00:00", "Z"),
        ended_utc=(T0 + timedelta(seconds=5)).isoformat().replace("+00:00", "Z"))
    svc = make_service(tmp_path, [inside, aged, saved_only, straddle],
                       cov=(T0 - timedelta(seconds=60), T0 + timedelta(seconds=60)))
    d = tmp_path / "replays" / "2026-06-11" / "session_3"
    d.mkdir(parents=True)
    (d / "attempt_0003_a_b_0m11s43.mp4").write_bytes(b"x")   # saved clip for id 3
    assert set(svc.available_attempt_ids()) == {1, 3}        # covered + saved only


def test_available_attempt_ids_empty_buffer_only_disk(tmp_path):
    a = attempt(id=1)
    svc = ReplayService(
        cfg=ReplayConfig(save_root=tmp_path / "replays",
                         scratch_dir=tmp_path / "buf", extract_wait_s=0.0),
        recorder=FakeRecorder(None),             # no coverage -> nothing buffer-covered
        extractor=FakeExtractor(), tracker=FakeTracker([a]))
    assert svc.available_attempt_ids() == []     # not saved, no buffer -> excluded
    svc.save(1)                                  # extracts to scratch + saves to disk
    assert svc.available_attempt_ids() == [1]    # now on disk -> available


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
    from sm64_events.replay.service import DISPLAY_LAG_FRAMES
    assert res["anchor_offset_s"] == 3.0 + DISPLAY_LAG_FRAMES / 30  # the pad, with no start_utc


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


# -- Task 16b: segment-aware clip naming ------------------------------------

def test_slug_filename_for_segment_attempt_uses_rta_and_segment_name():
    # format_igt(85): mins=0, secs=2, cents=83  ->  "0'02\"83"  ->  "0m02s83"
    # segment attempt: no IGT, rta_frames=85, star part empty -> no double __
    a = attempt(id=10_000_000_005, segment_id=1, course_id=None,
                star_id=None, igt_frames=None, rta_frames=85,
                outcome="success")
    assert slug_filename(a, "LBLJ", "") == \
        "attempt_10000000005_lblj_0m02s83-rta.mp4"


def test_save_segment_attempt_filename_contains_segment_name_and_rta(tmp_path):
    from sm64_events.tracking.segments import SegmentDef
    seg_def = SegmentDef(id=1, name="LBLJ", enabled=True,
                         start_triggers=[{"type": "level_enter", "to": 6,
                                          "from": 16}],
                         end_triggers=[{"type": "level_enter", "to": 17}],
                         guards=[])

    class FakeTrackerWithSegments:
        def __init__(self, attempts):
            self.db = FakeDb(attempts)
            self.session_id = 3
            self.segment_defs = [seg_def]

    a = attempt(id=10_000_000_005, segment_id=1, course_id=None,
                star_id=None, igt_frames=None, rta_frames=85,
                outcome="success")
    cfg = ReplayConfig(save_root=tmp_path / "replays",
                       scratch_dir=tmp_path / "buf", extract_wait_s=0.0)
    cov = (T0 - timedelta(seconds=60), T0 + timedelta(seconds=60))
    svc = ReplayService(cfg=cfg, recorder=FakeRecorder(cov),
                        extractor=FakeExtractor(),
                        tracker=FakeTrackerWithSegments([a]))
    res = svc.save(10_000_000_005)
    name = Path(res["path"]).name
    assert "lblj" in name
    assert "-rta" in name


def test_a_fresh_clip_is_aligned_to_its_own_footage(tmp_path):
    """Round 32, 2026-08-28. The timing constants estimate a journey nobody
    can measure from RAM, and four rounds of setting them by hand each
    landed somewhere else ("Totally desynced now, it's even worse than when
    we started"). So extraction now asks the CLIP: the alignment it
    measures is applied to the map and recorded beside it."""
    import json as _json

    from sm64_events.replay.frameclock import FrameClock
    from sm64_events.replay.mapalign import Alignment

    clock = FrameClock(now=lambda: 0.0)
    base = (T0 - timedelta(seconds=3)).timestamp()
    for n in range(17 * 30):
        clock._now = lambda t=base + n / 30: t
        clock.mark(7000 + n)
    svc = make_service(tmp_path, [attempt()])
    svc._frame_clock = clock
    seen = {}

    def aligner(clip, frame_map, a):
        seen["clip"] = clip
        seen["slots"] = len(frame_map)
        return Alignment(offset=-3, fit=0.71, margin=0.04, paired=900)

    svc.map_aligner = aligner
    res = svc.view(42)
    assert seen["slots"] == 17 * 60          # the built map, before the shift
    # The correction lands in the FRAME domain -- an odd slot shift would
    # split pictures that quantising unified -- so -3 slots rounds to a
    # constant of round(-3/2) = -2 game frames on every value.
    assert res["frame_map"][2] == 7000 - 2
    assert res["frame_map"][-1] == svc.view(42)["frame_map"][-1]
    sidecar = _json.loads(
        (svc.clips_dir / "clip_attempt_42.mp4").with_suffix(".json").read_text())
    assert sidecar["frame_map_aligned"] is True
    assert sidecar["frame_map_offset"] == -3
    assert sidecar["frame_map_fit"] == 0.71


def test_an_unreadable_display_leaves_the_map_alone_and_says_so(tmp_path):
    import json as _json

    from sm64_events.replay.frameclock import FrameClock

    clock = FrameClock(now=lambda: 0.0)
    base = (T0 - timedelta(seconds=3)).timestamp()
    for n in range(17 * 30):
        clock._now = lambda t=base + n / 30: t
        clock.mark(7000 + n)
    svc = make_service(tmp_path, [attempt()])
    svc._frame_clock = clock
    svc.map_aligner = lambda clip, frame_map, a: None
    res = svc.view(42)
    assert res["frame_map"][2] == 7000        # untouched
    sidecar = _json.loads(
        (svc.clips_dir / "clip_attempt_42.mp4").with_suffix(".json").read_text())
    assert sidecar["frame_map_aligned"] is False
    assert "frame_map_offset" not in sidecar


def test_a_broken_aligner_never_costs_the_clip(tmp_path):
    """Alignment is a correction, not a dependency: whatever it does, the
    clip and its map still reach him."""
    from sm64_events.replay.frameclock import FrameClock

    clock = FrameClock(now=lambda: 0.0)
    base = (T0 - timedelta(seconds=3)).timestamp()
    for n in range(17 * 30):
        clock._now = lambda t=base + n / 30: t
        clock.mark(7000 + n)
    svc = make_service(tmp_path, [attempt()])
    svc._frame_clock = clock

    def explode(clip, frame_map, a):
        raise RuntimeError("ffmpeg went missing")

    svc.map_aligner = explode
    res = svc.view(42)
    assert res["frame_map"] is not None and res["frame_map"][2] == 7000


def test_a_short_lead_in_is_not_a_warning_but_a_late_start_is(tmp_path):
    """2026-08-28: "there's also this warning for 'starts mid attempt' --
    but... I just reset as normal? What does this even mean?" `truncated`
    only says the ring could not serve the whole PADDED span, which is
    usually a shorter run-up with the attempt entirely present. The
    question worth asking him is whether the clip starts after the ATTEMPT
    did, and the timestamps answer that outright."""
    class ShortLeadIn(FakeExtractor):
        def extract(self, ring, start, end, out_path):
            res = super().extract(ring, start, end, out_path)
            return ClipResult(path=res.path, duration_s=res.duration_s - 1.5,
                              truncated=True,
                              start_utc=start + timedelta(seconds=1.5))

    svc = make_service(tmp_path, [attempt()])
    svc.extractor = ShortLeadIn()
    res = svc.view(42)
    assert res["truncated"] is True          # the ring really was short
    assert res["starts_mid_attempt"] is False, (
        "1.5 s off a 3 s lead-in leaves the whole attempt in the clip")
    assert res["ends_early"] is False


def test_a_clip_that_really_starts_after_the_anchor_says_so(tmp_path):
    class Late(FakeExtractor):
        def extract(self, ring, start, end, out_path):
            res = super().extract(ring, start, end, out_path)
            return ClipResult(path=res.path, duration_s=res.duration_s - 5,
                              truncated=True,
                              start_utc=start + timedelta(seconds=5))

    svc = make_service(tmp_path / "late", [attempt()])
    svc.extractor = Late()
    assert svc.view(42)["starts_mid_attempt"] is True


def test_a_clip_cut_off_before_the_finish_says_that_instead(tmp_path):
    class Stops(FakeExtractor):
        def extract(self, ring, start, end, out_path):
            res = super().extract(ring, start, end, out_path)
            return ClipResult(path=res.path, duration_s=6.0,
                              truncated=True, start_utc=start)

    svc = make_service(tmp_path / "stops", [attempt()])
    svc.extractor = Stops()
    res = svc.view(42)
    assert res["starts_mid_attempt"] is False and res["ends_early"] is True


def test_a_fresh_clip_holds_one_answer_per_picture(tmp_path):
    """His ruling, 2026-08-28: "if there's duplicated frames, input
    timeline should be identical for the sequential duplicated frames."
    The quantiser runs even when the ALIGNER refuses -- it needs nothing
    but the pictures, and his clip 4374 is exactly that case (the level's
    yellow floor swamped the digit region, so alignment had no verdict
    while 257 pictures still carried two different frames)."""
    import json as _json

    from sm64_events.replay.frameclock import FrameClock

    clock = FrameClock(now=lambda: 0.0)
    base = (T0 - timedelta(seconds=3)).timestamp()
    for n in range(17 * 30):
        clock._now = lambda t=base + n / 30: t
        clock.mark(7000 + n)
    svc = make_service(tmp_path, [attempt()])
    svc._frame_clock = clock
    svc.map_aligner = lambda clip, frame_map, a: None      # refuses
    seen = {}

    def quantiser(clip, frame_map):
        seen["slots"] = len(frame_map)
        return [7000] * len(frame_map)                     # one flat answer

    svc.map_quantiser = quantiser
    res = svc.view(42)
    assert seen["slots"] == 17 * 60
    assert set(res["frame_map"]) == {7000}
    sidecar = _json.loads(
        (svc.clips_dir / "clip_attempt_42.mp4").with_suffix(".json").read_text())
    assert sidecar["frame_map_quantised"] is True
    assert sidecar["frame_map_aligned"] is False


def test_a_broken_quantiser_never_costs_the_clip(tmp_path):
    from sm64_events.replay.frameclock import FrameClock

    clock = FrameClock(now=lambda: 0.0)
    base = (T0 - timedelta(seconds=3)).timestamp()
    for n in range(17 * 30):
        clock._now = lambda t=base + n / 30: t
        clock.mark(7000 + n)
    svc = make_service(tmp_path / "broken", [attempt()])
    svc._frame_clock = clock

    def explode(clip, frame_map):
        raise RuntimeError("ffmpeg went missing")

    svc.map_quantiser = explode
    res = svc.view(42)
    assert res["frame_map"] is not None and res["frame_map"][2] == 7000


def test_unreadable_digits_inherit_the_learned_anchor(tmp_path):
    """A clip whose digits cannot be read is corrected by the median the
    OTHER clips measured, instead of going uncorrected -- the "calibrate
    once" idea made continuous: he calibrates by playing."""
    import json as _json

    from sm64_events.replay.frameclock import FrameClock
    from sm64_events.replay.mapalign import AnchorStats

    clock = FrameClock(now=lambda: 0.0)
    base = (T0 - timedelta(seconds=3)).timestamp()
    for n in range(17 * 30):
        clock._now = lambda t=base + n / 30: t
        clock.mark(7000 + n)
    svc = make_service(tmp_path, [attempt()])
    svc._frame_clock = clock
    svc.map_aligner = lambda clip, frame_map, a: None      # digits unreadable
    stats = AnchorStats(tmp_path / "anchor.json")
    for clip_id, measured in ((1, -2), (2, -2), (3, -2)):
        stats.record(clip_id, measured, 0.5)
    svc.anchor_stats = stats
    res = svc.view(42)
    assert res["frame_map"][2] == 7000 - 1     # -2 slots -> -1 frame, learned
    sidecar = _json.loads(
        (svc.clips_dir / "clip_attempt_42.mp4").with_suffix(".json").read_text())
    assert sidecar["frame_map_aligned"] is False
    assert sidecar["frame_map_learned"] is True
    assert sidecar["frame_map_offset"] == -2


def test_a_measured_clip_teaches_the_store(tmp_path):
    from sm64_events.replay.frameclock import FrameClock
    from sm64_events.replay.mapalign import Alignment, AnchorStats

    clock = FrameClock(now=lambda: 0.0)
    base = (T0 - timedelta(seconds=3)).timestamp()
    for n in range(17 * 30):
        clock._now = lambda t=base + n / 30: t
        clock.mark(7000 + n)
    svc = make_service(tmp_path / "m", [attempt()])
    svc._frame_clock = clock
    svc.map_aligner = lambda clip, frame_map, a: Alignment(
        offset=-2, fit=0.5, margin=0.02, paired=900)
    stats = AnchorStats(tmp_path / "m" / "anchor.json")
    svc.anchor_stats = stats
    svc.view(42)
    import json as _json
    rows = _json.loads((tmp_path / "m" / "anchor.json").read_text())
    assert rows and rows[-1]["offset"] == -2


# -- the picture ledger path (round 32 item 40) -----------------------------

class FakeLedger:
    """Echoes rows inside whatever window the service asks about."""
    def __init__(self, count=3):
        self.count = count
        self.asked = []
    def rows_between(self, t0, t1):
        self.asked.append((t0, t1))
        return [{"ts": t0 + 0.5 + i / 30, "frame": 100 + i,
                 "mario_action": 0x0880}
                for i in range(self.count)]


def test_a_clip_with_a_ledger_maps_from_it_and_the_sidecar_keeps_the_rows(
        tmp_path):
    """Item 40: capture's own per-picture record answers FIRST; the map it
    builds is already one answer per picture, so the quantiser has nothing
    to do; the aligner still closes identity on top; and the rows -- extra
    stamps included -- persist in the sidecar for any future analysis."""
    import json as _json

    svc = make_service(tmp_path, [attempt()])
    svc.recorder.ledger = FakeLedger()
    quantiser_calls = []
    svc.map_quantiser = lambda clip, fm: quantiser_calls.append(clip) or fm
    aligned = {}

    def mapper(clip, rows, start_ts, duration_s, fps):
        aligned["rows"] = rows
        aligned["fps"] = fps
        return [200, 200, 201, 201]

    svc.ledger_mapper = mapper
    res = svc.view(42)
    assert res["frame_map"] == [200, 200, 201, 201]
    assert res["frame_map_source"] == "ledger"
    assert quantiser_calls == []             # already one answer per picture
    assert aligned["rows"][0]["frame"] == 100
    sidecar = _json.loads(
        (svc.clips_dir / "clip_attempt_42.mp4").with_suffix(".json").read_text())
    # The durable per-frame record: composition times as offsets from the
    # clip's own start, stamps riding along.
    assert sidecar["frame_map_quantised"] is True
    # The ask window starts 0.5 s before the clip, so the echoed rows land
    # at the clip start exactly: offsets from the clip's own first frame.
    assert [row["ts"] for row in sidecar["picture_ledger"]] == [
        0.0, round(1 / 30, 4), round(2 / 30, 4)]
    assert sidecar["picture_ledger"][0]["mario_action"] == 0x0880


def test_a_ledger_refusal_falls_back_to_the_series_map(tmp_path):
    """Too little coverage: ledger_map returns None, the frame-clock series
    answer as before -- and the rows still persist, because they are the
    record he asked for whether or not they carried the map."""
    import json as _json

    from sm64_events.replay.frameclock import FrameClock

    clock = FrameClock(now=lambda: 0.0)
    base = (T0 - timedelta(seconds=3)).timestamp()
    for n in range(17 * 30):
        clock._now = lambda t=base + n / 30: t
        clock.mark(7000 + n)
    svc = make_service(tmp_path, [attempt()])
    svc._frame_clock = clock
    svc.recorder.ledger = FakeLedger()
    svc.ledger_mapper = lambda *a: None
    res = svc.view(42)
    assert res["frame_map_source"] == "edges"
    sidecar = _json.loads(
        (svc.clips_dir / "clip_attempt_42.mp4").with_suffix(".json").read_text())
    assert len(sidecar["picture_ledger"]) == 3


def test_a_broken_ledger_mapper_never_costs_the_clip(tmp_path):
    from sm64_events.replay.frameclock import FrameClock

    clock = FrameClock(now=lambda: 0.0)
    base = (T0 - timedelta(seconds=3)).timestamp()
    for n in range(17 * 30):
        clock._now = lambda t=base + n / 30: t
        clock.mark(7000 + n)
    svc = make_service(tmp_path, [attempt()])
    svc._frame_clock = clock
    svc.recorder.ledger = FakeLedger()

    def broken(*a):
        raise RuntimeError("decode died")

    svc.ledger_mapper = broken
    res = svc.view(42)
    assert res["clip_url"].endswith("clip_attempt_42.mp4")
    assert res["frame_map_source"] == "edges"


def test_no_ledger_on_the_recorder_changes_nothing(tmp_path):
    svc = make_service(tmp_path, [attempt()])
    svc.ledger_mapper = lambda *a: [1, 2]
    res = svc.view(42)
    assert res["frame_map"] is None


def test_windowed_alignment_corrects_each_shelf_and_says_so(tmp_path):
    """Items 41-43: the aligner may answer (global, windows); each picture
    then takes its shelf's own offset and the sidecar records the shelves,
    so a 'panel is off HERE' report is answerable from the file."""
    import json as _json

    from sm64_events.replay.frameclock import FrameClock
    from sm64_events.replay.mapalign import Alignment

    clock = FrameClock(now=lambda: 0.0)
    base = (T0 - timedelta(seconds=3)).timestamp()
    for n in range(17 * 30):
        clock._now = lambda t=base + n / 30: t
        # One skipped frame at n=310 (its picture never captured): the
        # place a downward shelf step becomes expressible.
        clock.mark(7000 + n + (1 if n >= 310 else 0))
    svc = make_service(tmp_path, [attempt()])
    svc._frame_clock = clock
    windows = [(0, 500, 2, 0.9, 0.5), (500, 17 * 60, 0, 0.9, 0.5)]
    svc.map_aligner = lambda clip, fm, a: (
        Alignment(offset=0, fit=0.8, margin=0.3, paired=900), windows)
    res = svc.view(42)
    fm = res["frame_map"]
    # +2 slots = +1 frame on the early shelf, untouched on the late one --
    # proven against a twin service whose aligner applies no correction.
    twin = make_service(tmp_path / "twin", [attempt()])
    twin._frame_clock = clock
    twin.map_aligner = lambda clip, fm, a: (
        Alignment(offset=0, fit=0.8, margin=0.3, paired=900), [])
    plain = twin.view(42)["frame_map"]
    assert fm[10] == plain[10] + 1                 # early: up a frame
    # The downward step waits for the skipped frame (n=310, ~slot 800),
    # then the late shelf runs exactly as the uncorrected map does.
    assert fm[900:1000] == plain[900:1000]
    sidecar = _json.loads(
        (svc.clips_dir / "clip_attempt_42.mp4").with_suffix(".json").read_text())
    assert sidecar["frame_map_windows"] == [list(w) for w in windows]
    assert sidecar["frame_map_aligned"] is True
    assert sidecar["frame_map_offset"] == 0


def test_an_aligner_answering_no_windows_applies_the_global_offset(tmp_path):
    from sm64_events.replay.frameclock import FrameClock
    from sm64_events.replay.mapalign import Alignment

    clock = FrameClock(now=lambda: 0.0)
    base = (T0 - timedelta(seconds=3)).timestamp()
    for n in range(17 * 30):
        clock._now = lambda t=base + n / 30: t
        clock.mark(7000 + n)
    svc = make_service(tmp_path, [attempt()])
    svc._frame_clock = clock
    svc.map_aligner = lambda clip, fm, a: (
        Alignment(offset=-3, fit=0.7, margin=0.04, paired=900), [])
    res = svc.view(42)
    assert res["frame_map"][2] == 7000 - 2         # round(-3/2) frames


def test_the_digit_refit_replaces_the_map_and_says_so(tmp_path):
    """Round 32 item 55: the anchors move a map by one number, and his BBH
    clip drifts instead -- exact at frames 87-89, one to two early at
    129-141. The refit gives every picture its own frame; a refusal or a
    failure leaves the anchored map exactly as it was."""
    import json as _json

    from sm64_events.replay.frameclock import FrameClock
    from sm64_events.replay.mapalign import Alignment

    def wired(tmp):
        clock = FrameClock(now=lambda: 0.0)
        base = (T0 - timedelta(seconds=3)).timestamp()
        for n in range(17 * 30):
            clock._now = lambda t=base + n / 30: t
            clock.mark(7000 + n)
        svc = make_service(tmp, [attempt()])
        svc._frame_clock = clock
        svc.map_aligner = lambda clip, fm, a: (
            Alignment(offset=0, fit=0.8, margin=0.3, paired=900), [])
        return svc

    svc = wired(tmp_path)
    svc.map_digit_fit = lambda clip, fm, a: [4242] * len(fm)
    assert svc.view(42)["frame_map"] == [4242] * (17 * 60)
    sidecar = _json.loads(
        (svc.clips_dir / "clip_attempt_42.mp4").with_suffix(".json").read_text())
    assert sidecar["frame_map_digitfit"] is True

    refused = wired(tmp_path / "b")
    refused.map_digit_fit = lambda clip, fm, a: None
    assert refused.view(42)["frame_map"][2] == 7000

    def broken(clip, fm, a):
        raise RuntimeError("decode died")

    crashed = wired(tmp_path / "c")
    crashed.map_digit_fit = broken
    assert crashed.view(42)["frame_map"][2] == 7000

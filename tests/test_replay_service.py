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


def test_a_clip_with_no_stamped_rows_carries_no_map_at_all(tmp_path):
    """ONE map path since 2026-09-05, and it is a read: the capture layer
    stamps each picture with the frame that drew it. A clip recorded
    without the layer -- no ledger, no feed log, or rows that name no
    frame -- gets `frame_map: None`, and the panel says "Frame-exact
    capture is off" rather than showing a derived guess. Four generations
    of derived map used to answer here."""
    svc = make_service(tmp_path, [attempt()])
    assert svc.view(42)["frame_map"] is None          # no ledger at all

    class TimeOnlyLedger:
        """A desktop grab's rows: a composition time and nothing else."""
        def rows_between(self, t0, t1):
            return [{"ts": t0 + 0.5 + i / 30, "frame": None} for i in range(9)]

        def feeds_between(self, t0, t1):
            return [{"at": t0 + 0.5 + i / 30 + 0.004, "ts": t0 + 0.5 + i / 30}
                    for i in range(9)]

    svc2 = make_service(tmp_path / "b", [attempt()])
    svc2.recorder.ledger = TimeOnlyLedger()
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


# The footage aligner, the picture-run quantiser, the learned ink anchor,
# the ledger mapper, the digit refit and the pad reader were pinned here --
# six generations of DERIVING which game frame a picture shows, each with a
# refusal path and a never-costs-the-clip guard. The capture layer is told
# the frame, so all of it was deleted 2026-09-05 along with
# `replay/{mapalign,timerread,frameclock}.py` and `memory/present.py`.


class ExactLedger:
    """Rows a capture-layer clip carries: the plugin's own frame and pad."""
    def __init__(self, count=3, pads=None):
        self.count = count
        self.pads = pads or {}
    def rows_between(self, t0, t1):
        return [{"ts": t0 + 0.5 + i / 30, "frame": 100 + i, "exact": True,
                 "pad": self.pads.get(100 + i, [0, 0, 0]), "igt_overall": 40 + i,
                 "vi_origin": 0x100000 + i, "lists_since": 1}
                for i in range(self.count)]


def test_a_capture_layer_clip_takes_its_stamps_as_the_map_and_audits_the_pads(tmp_path):
    """Item 95: every row says `exact`, so the map IS the rows, and the
    stamp's own pad is checked against the input track -- the clip's only
    shipped check, and the number the timeline's chip draws. It reads the
    pad the PLUGIN copied out of RDRAM beside the picture, so it involves
    no pixels and cannot misread; the display reader it replaced scored
    82-94% on clips the oracle certified perfect (2026-09-05)."""
    import json as _json

    class PaddedFeedLedger(FeedExactLedger):
        def __init__(self, count, pads):
            super().__init__(count)
            self.pads = pads

        def rows_between(self, t0, t1):
            rows = super().rows_between(t0, t1)
            for row in rows:
                row["pad"] = self.pads.get(row["frame"], [0, 0, 0])
            return rows

    svc = make_service(tmp_path, [attempt()])
    svc.extractor = FeedExtractor(count=3)
    svc.recorder.ledger = PaddedFeedLedger(
        count=3, pads={100: [5, -9, 0x8000], 101: [5, -9, 0x8000], 102: [0, 0, 0]})
    # The track disagrees with the stamp on frame 102 and nowhere else.
    svc.track_pads = lambda attempt: {99: (5, -9, 0x8000), 100: (5, -9, 0x8000),
                                      101: (5, -9, 0x8000), 102: (3, 0, 0)}
    res = svc.view(42)
    assert res["frame_map_source"] == "plugin"
    assert res["pad_stamp_agreement"]["pictures"] == 3
    assert res["pad_stamp_agreement"]["agree"] == 2
    # `rows` is the clip's WHOLE picture count, so the chip can say how much
    # of the clip the check covers rather than printing "3 of 3".
    assert res["pad_stamp_agreement"]["rows"] == 3
    assert res["pad_stamp_agreement"]["disagreements"] == [[2, 102, [3, 0, 0], [0, 0, 0]]]
    sidecar = _json.loads(
        (svc.clips_dir / "clip_attempt_42.mp4").with_suffix(".json").read_text())
    assert sidecar["frame_map_source"] == "plugin"
    assert sidecar["picture_ledger"][0]["exact"] is True


class FeedExactLedger(ExactLedger):
    """A capture-layer ledger with the feed log the picture-feed path joins
    on: one feed entry per row, written 4 ms after its present."""
    def __init__(self, count, inexact_at=None):
        super().__init__(count)
        self.inexact_at = inexact_at
    def rows_between(self, t0, t1):
        rows = [{"ts": t0 + 1.5 + i / 30, "frame": 100 + i, "exact": i != self.inexact_at,
                 "pad": [0, 0, 0], "igt_overall": 40 + i, "vi_origin": 0x100000 + i,
                 "lists_since": 2 if i == self.inexact_at else 1}
                for i in range(self.count)]
        return rows
    def feeds_between(self, t0, t1):
        return [{"at": t0 + 1.0 + i / 30 + 0.004, "ts": t0 + 1.0 + i / 30,
                 "run_id": "fixture", "pts": i * 3000 + 360}
                for i in range(self.count)]


class FeedExtractor(FakeExtractor):
    """A picture-feed clip: every video frame carries its own time."""
    def __init__(self, count):
        super().__init__()
        self.count = count
    def extract(self, ring, start, end, out_path):
        import dataclasses
        from sm64_events.replay.media import MediaRun
        frame_times = [i / 30 + 0.004 for i in range(self.count)]
        return dataclasses.replace(super().extract(ring, start, end, out_path),
                                   frame_times=frame_times, video_start_s=frame_times[0],
                                   media_run=MediaRun("fixture", start.timestamp()),
                                   source_pts=[i * 3000 + 360 for i in range(self.count)])


def test_a_plugin_clips_map_is_its_stamps_less_the_layers_own_lag_and_carries_the_igt(tmp_path):
    """A capture-layer picture shows the pad of the stamp before its own
    (PLUGIN_PICTURE_LAG, measured on 7015: 352 of 352 A icons at -1). The
    desktop grab's lag never applies; a present that saw two lists claims
    nothing; the game's own timer rides the view for the frame the map
    names, so the clock and the pad are one picture's."""
    svc = make_service(tmp_path, [attempt()])
    svc.extractor = FeedExtractor(count=120)
    svc.recorder.ledger = FeedExactLedger(count=120, inexact_at=7)
    svc.track_pads = lambda attempt: {}
    res = svc.view(42)
    assert res["frame_map_source"] == "plugin"
    assert res["frame_map"][:7] == [99, 100, 101, 102, 103, 104, 105]
    assert res["picture_ids"][:9] == [0, 1, 2, 3, 4, 5, 6, None, 8]
    # Opening the cached clip preserves the very same capture occurrences.
    assert svc.view(42)["picture_ids"] == res["picture_ids"]
    assert res["frame_map"][7] is None                    # two lists: nothing claimed
    assert res["frame_map"][8:12] == [107, 108, 109, 110]
    # slot 0 names frame 99, which no row stamped: no clock there
    assert res["picture_igt"][:3] == [None, 40, 41] and res["picture_igt"][7] is None
    import json as _json
    sidecar = _json.loads(
        (svc.clips_dir / "clip_attempt_42.mp4").with_suffix(".json").read_text())
    assert sidecar["plugin_inexact_rows"] == 1 and sidecar["picture_igt"][8] == 47


def test_a_clip_whose_rows_are_not_all_stamped_gets_no_map(tmp_path):
    """There is no second path to fall back to: rows the plugin did not
    stamp cannot name a frame, so the map is dropped rather than derived."""
    svc = make_service(tmp_path, [attempt()])
    svc.recorder.ledger = FeedExactLedger(count=40)
    svc.recorder.ledger.__class__ = type(
        "Unstamped", (FeedExactLedger,),
        {"rows_between": lambda self, t0, t1: [
            {"ts": t0 + 1.5 + i / 30, "frame": 100 + i} for i in range(40)]})
    svc.extractor = FeedExtractor(count=40)
    res = svc.view(42)
    assert res["frame_map"] is None and res["frame_map_source"] is None


def test_picture_clock_lookup_keeps_the_matched_occurrence_across_a_reset(tmp_path):
    svc = make_service(tmp_path, [attempt()])
    rows = [{"frame": frame, "igt_overall": igt, "exact": True}
            for frame, igt in zip([100, 101, 99, 100, 101], [50, 51, 2, 3, 4], strict=True)]
    meta = {"picture_ledger": rows, "picture_rows": [1, 4, 2],
            "frame_map": [100, 100, 98]}
    svc._take_the_stamps(meta, attempt())
    assert meta["picture_igt"] == [50, 3, None]
    assert meta["state_rows"] == [0, 3, None]


def test_a_picture_feed_without_a_retained_source_clock_does_not_get_a_map(tmp_path):
    import dataclasses

    class UnknownClock(FeedExtractor):
        def extract(self, *args):
            return dataclasses.replace(super().extract(*args), media_run=None, source_pts=None)

    svc = make_service(tmp_path, [attempt()])
    svc.recorder.ledger = FeedExactLedger(count=40)
    svc.extractor = UnknownClock(count=40)
    result = svc.view(42)
    assert result["frame_map"] is None


def test_a_few_inexact_rows_keep_the_plugin_map_but_say_so(tmp_path):
    """Review finding 9: a present that saw two display lists is marked
    inexact; one such row in a hundred keeps the rows as the map and the
    sidecar says the map is inferred there. Many such rows do not."""
    class MostlyExactFeed(FeedExactLedger):
        def __init__(self, count, inexact_every):
            super().__init__(count)
            self.inexact_every = inexact_every

        def rows_between(self, t0, t1):
            rows = super().rows_between(t0, t1)
            for index, row in enumerate(rows):
                if index % self.inexact_every == 0:
                    row["exact"] = False
            return rows

    svc = make_service(tmp_path, [attempt()])
    svc.extractor = FeedExtractor(count=200)
    svc.recorder.ledger = MostlyExactFeed(count=200, inexact_every=200)
    svc.track_pads = lambda attempt: {}
    res = svc.view(42)
    assert res["frame_map_source"] == "plugin"
    assert res["plugin_inexact_rows"] == 1
    # 20 of 200 inexact is past PLUGIN_EXACT_SHARE: there is nothing else to
    # fall back to, so the clip carries no map and says so.
    svc = make_service(tmp_path / "many", [attempt()])
    svc.extractor = FeedExtractor(count=200)
    svc.recorder.ledger = MostlyExactFeed(count=200, inexact_every=10)
    svc.track_pads = lambda attempt: {}
    res = svc.view(42)
    assert res["frame_map"] is None and res["frame_map_source"] is None

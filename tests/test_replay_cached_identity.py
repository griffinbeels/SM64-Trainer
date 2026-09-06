"""Opening or saving a replay cannot promote an unproved timing association."""
import json
from pathlib import Path

import pytest

from test_replay_service import (FeedExactLedger, FeedExtractor, attempt,
                                 make_service)


def cached_clip(service, saved=False):
    if saved:
        path = service.cfg.save_root / "attempt_0042_legacy.mp4"
    else:
        path = service.clips_dir / "clip_attempt_42.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"original-video")
    return path


def legacy_metadata():
    return {"duration_s": 17, "truncated": False, "video_start_s": 0,
            "frame_times": [0, 1 / 30], "frame_map": [100, 101],
            "frame_map_source": "plugin", "picture_igt": [40, 41],
            "picture_rows": [0, 1], "feed_match": {"matched": 2},
            "pad_stamp_agreement": {"pictures": 2, "agree": 2}}


def assert_no_association(view):
    for key in ["frame_map", "frame_map_source", "picture_ids", "picture_igt",
                "pad_stamp_agreement"]:
        assert view[key] is None, (key, view[key])


@pytest.mark.parametrize("saved", [False, True], ids=["scratch", "saved"])
def test_legacy_map_is_not_served_as_a_verified_association(tmp_path, saved):
    service = make_service(tmp_path, [attempt()])
    path = cached_clip(service, saved)
    sidecar = path.with_suffix(".json")
    before = json.dumps(legacy_metadata(), indent=2).encode()
    sidecar.write_bytes(before)
    view = service.view(42)
    assert view["clip_url"]
    assert service.extractor.calls == []
    assert_no_association(view)
    assert view["input_alignment"]["status"] == "unverified"
    assert sidecar.read_bytes() == before
    assert path.read_bytes() == b"original-video"


@pytest.mark.parametrize("saved", [False, True])
def test_reading_a_missing_video_start_preserves_the_original_sidecar(tmp_path, monkeypatch, saved):
    service = make_service(tmp_path, [attempt()])
    path = cached_clip(service, saved)
    meta = legacy_metadata()
    del meta["video_start_s"]
    before = json.dumps(meta, indent=2).encode()
    path.with_suffix(".json").write_bytes(before)
    monkeypatch.setattr("sm64_events.replay.service.video_start_of", lambda *args: .011)
    assert service.view(42)["video_start_s"] == .011
    assert path.with_suffix(".json").read_bytes() == before


def test_saving_a_legacy_clip_preserves_its_bytes_and_does_not_validate_it(tmp_path):
    service = make_service(tmp_path, [attempt()])
    path = cached_clip(service)
    path.with_suffix(".json").write_text(json.dumps(legacy_metadata()))
    saved = Path(service.save(42)["path"])
    assert saved.read_bytes() == path.read_bytes()
    path.unlink()
    path.with_suffix(".json").unlink()
    view = service.view(42)
    assert view["source"] == "saved"
    assert_no_association(view)


def test_source_linked_association_survives_cached_and_saved_reads(tmp_path):
    service = make_service(tmp_path, [attempt()])
    service.extractor = FeedExtractor(count=3)
    service.recorder.ledger = FeedExactLedger(count=3)
    first = service.view(42)
    assert first["frame_map"] == [99, 100, 101]
    assert first["picture_ids"] == [0, 1, 2]
    assert first["picture_igt"] == [None, 40, 41]
    keys = ["frame_map", "picture_ids", "picture_igt"]
    expected = {key: first[key] for key in keys}
    assert {key: service.view(42)[key] for key in keys} == expected
    service.save(42)
    cached = service.clips_dir / "clip_attempt_42.mp4"
    cached.unlink()
    cached.with_suffix(".json").unlink()
    assert {key: service.view(42)[key] for key in keys} == expected


def source_linked_cache(tmp_path):
    service = make_service(tmp_path, [attempt()])
    service.extractor = FeedExtractor(count=3)
    service.recorder.ledger = FeedExactLedger(count=3)
    service.view(42)
    sidecar = (service.clips_dir / "clip_attempt_42.mp4").with_suffix(".json")
    return service, sidecar, json.loads(sidecar.read_text())


def test_stale_derived_map_and_timer_are_recovered_from_retained_source_identity(tmp_path):
    service, sidecar, meta = source_linked_cache(tmp_path)
    meta.update(frame_map=[900, 901, 902], picture_igt=[900, 901, 902],
                state_rows=[2, 2, 2], pad_stamp_agreement={"pictures": 3, "agree": 3})
    before = json.dumps(meta, indent=2).encode()
    sidecar.write_bytes(before)
    view = service.view(42)
    assert view["frame_map"] == [99, 100, 101]
    assert view["picture_igt"] == [None, 40, 41]
    assert view["picture_ids"] == [0, 1, 2]
    assert view["pad_stamp_agreement"] is None
    assert sidecar.read_bytes() == before
    assert len(service.extractor.calls) == 1


def test_invalid_cached_picture_clock_can_recover_from_the_actual_video(tmp_path, monkeypatch):
    service, sidecar, meta = source_linked_cache(tmp_path)
    actual_times = meta["frame_times"]
    meta["frame_times"] = [0, float("nan"), .1]
    before = json.dumps(meta).encode()
    sidecar.write_bytes(before)
    measured_paths = []

    def measure(_ffmpeg, path):
        measured_paths.append(path)
        return actual_times

    monkeypatch.setattr("sm64_events.replay.service.frame_times_of", measure)
    view = service.view(42)
    assert measured_paths == [sidecar.with_suffix(".mp4")]
    assert view["frame_times"] == actual_times
    assert view["frame_map"] == [99, 100, 101]
    assert view["picture_igt"] == [None, 40, 41]
    assert sidecar.read_bytes() == before


@pytest.mark.parametrize("change", [
    {"media_clock": {}},
    {"media_clock.version": 2},
    {"media_clock.version": True},
    {"media_clock.origin_ts": 10 ** 400},
    {"media_clock.source_pts": [360, 360, 6360]},
    {"media_clock.source_pts": [361, 3361, 6361]},
    {"media_clock.origin_ts": 0},
    {"frame_times": [0.004, 0.037333]},
    {"frame_times": [0.004, float("nan"), 0.070667]},
    {"frame_times": [0.004, 0.037333, 1e308]},
    {"picture_rows": [0, 1, 99]},
    {"picture_rows": [0, True, 2]},
    {"picture_rows": [0, 1]},
    {"picture_ledger": [{"frame": "100"}]},
    {"feed_match.run_id": "another-run"},
])
def test_incompatible_source_metadata_cannot_validate_a_cached_map(tmp_path, change):
    service, sidecar, meta = source_linked_cache(tmp_path)
    for path, value in change.items():
        parent, _, field = path.partition(".")
        if field:
            meta[parent][field] = value
        else:
            meta[parent] = value
    sidecar.write_text(json.dumps(meta))
    view = service.view(42)
    assert view["clip_url"]
    assert_no_association(view)
    # The HTTP JSON response rejects non-finite numbers even when view() returns.
    json.dumps(view, allow_nan=False)
    service.save(42)
    cached = service.clips_dir / "clip_attempt_42.mp4"
    cached.unlink()
    cached.with_suffix(".json").unlink()
    assert_no_association(service.view(42))


@pytest.mark.parametrize("field,value", [("frame", "100"), ("igt_overall", "40"),
                                        ("exact", "yes")])
def test_invalid_unreferenced_state_row_cannot_supply_a_timer(tmp_path, field, value):
    service, sidecar, meta = source_linked_cache(tmp_path)
    # The first capture is not shown, but its state supplies the next picture's timer.
    meta["picture_rows"][0] = None
    meta["picture_ledger"][0][field] = value
    sidecar.write_text(json.dumps(meta))
    assert_no_association(service.view(42))


def test_fresh_unverified_clip_retains_source_evidence_for_later_diagnosis(tmp_path):
    service = make_service(tmp_path, [attempt()])
    service.extractor = FeedExtractor(count=3)
    service.recorder.ledger = FeedExactLedger(count=3, inexact_at=1)
    assert_no_association(service.view(42))
    path = service.clips_dir / "clip_attempt_42.json"
    meta = json.loads(path.read_text())
    assert meta["picture_rows"] == [0, None, 2]
    assert meta["feed_match"]["method"] == "source_pts"
    assert len(meta["picture_ledger"]) == 3
    assert_no_association(service.view(42))

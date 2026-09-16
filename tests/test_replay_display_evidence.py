"""Export and discrepancy evidence follow represented capture occurrences."""
import json

from sm64_events.inputs.overlay import plan_mapped_overlay
from test_replay_service import FeedExactLedger, FeedExtractor, attempt, make_service


def test_view_projects_repeated_counters_and_excludes_unrepresented_audit_rows(tmp_path):
    service = make_service(tmp_path, [attempt()])
    service.extractor = FeedExtractor(count=5)
    service.recorder.ledger = FeedExactLedger(count=5)
    service.view(42)
    sidecar = (service.clips_dir / "clip_attempt_42.mp4").with_suffix(".json")
    meta = json.loads(sidecar.read_text())
    # Distinct visits to 100, a dropped disagreeing capture, and an unknown
    # displayed picture. The last captured row appears twice as a heartbeat.
    meta["picture_ledger"] = [
        {"ts": 0, "frame": 100, "exact": True, "pad": [1, 2, 0x8000],
         "mario": [0, 10, 0]},
        {"ts": .01, "frame": 999, "exact": True, "pad": [9, 9, 9]},
        {"ts": .02, "frame": 100, "exact": True, "pad": [3, 4, 0x4000],
         "mario": [0, 20, 0]},
        {"ts": .03, "frame": 102, "exact": True, "pad": [5, 6, 0x2000]},
    ]
    meta["picture_rows"] = [0, 2, None, 3, 3]
    sidecar.write_text(json.dumps(meta))
    original = sidecar.read_bytes()
    service.track_pads = lambda a: {100: (1, 2, 0x8000), 999: (0, 0, 0),
                                    102: (5, 6, 0)}

    view = service.view(42)

    assert view["picture_states"] == [
        {"stick_x": 1, "stick_y": 2, "buttons": 0x8000, "yaw": 10, "action": 0, "speed": 0},
        {"stick_x": 3, "stick_y": 4, "buttons": 0x4000, "yaw": 20, "action": 0, "speed": 0},
        None,
        {"stick_x": 5, "stick_y": 6, "buttons": 0x2000, "yaw": None, "action": None, "speed": None},
        {"stick_x": 5, "stick_y": 6, "buttons": 0x2000, "yaw": None, "action": None, "speed": None},
    ]
    exported = plan_mapped_overlay(view)
    assert [exported.states[i] for i in exported.per_frame] == [
        None, (0x8000, 1, 2, 10), (0x4000, 3, 4, 20), None,
        (0x2000, 5, 6, None), (0x2000, 5, 6, None),
    ]
    assert exported.frame_times == (0, .004, .037333, .070667, .104, .137333)
    assert exported.duration_s == view["duration_s"]
    # The raw-only independent track cannot audit repeated occurrences. It
    # also cannot implicate a dropped/unknown capture as a displayed picture.
    # One captured occurrence held through two video slots is ONE picture
    # checked once, at the first slot that shows it.
    assert view["pad_stamp_agreement"] == {
        "pictures": 1, "agree": 0, "rows": 5,
        "disagreements": [[3, 102, [5, 6, 0], [5, 6, 0x2000]]],
    }
    assert sidecar.read_bytes() == original


def test_unverified_clip_does_not_expose_persisted_picture_states(tmp_path):
    from test_replay_cached_identity import cached_clip, legacy_metadata

    service = make_service(tmp_path, [attempt()])
    path = cached_clip(service)
    meta = {**legacy_metadata(), "state_rows": [0],
            "picture_states": [{"buttons": 0x8000}]}
    path.with_suffix(".json").write_text(json.dumps(meta))
    assert service.view(42)["picture_states"] is None

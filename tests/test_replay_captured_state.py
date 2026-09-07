"""Pixel witnesses from Griffin's fresh PJ64 recordings on 2026-09-06.

The expected timer and pad below were read from decoded MP4 pictures, not
generated from the map. Full clips/screenshots remain in the local round37
evidence. This pins state interpretation; it does not prove capture for all
renderers or substitute for the next live playtest.
"""
from datetime import datetime, timezone

import pytest

from test_replay_service import attempt, make_service


@pytest.mark.parametrize("frame,igt,pad,previous_pad", [
    (12223, 50, [0, 84, 0xA000], [0, 84, 0x2000]),  # Wild Blue7718 slot138
    (11193, 254, [-46, 76, 0x8000], [-50, 75, 0x8000]),  # Pyramid7685 slot342
    (20881, 28, [0, 84, 0x8000], [0, 84, 0x8000]),  # Books7739 slot76
])
def test_captured_picture_does_not_borrow_its_predecessor(tmp_path, frame, igt, pad, previous_pad):
    origin = datetime(2026, 9, 6, tzinfo=timezone.utc)
    meta = {
        "start_utc": origin.isoformat(), "frame_times": [0],
        "media_clock": {"version": 1, "run_id": "retained-source", "origin_ts": origin.timestamp(),
                        "time_base": 90000, "source_pts": [0]},
        "feed_match": {"method": "source_pts", "run_id": "retained-source"},
        "picture_rows": [1],
        "picture_ledger": [
            {"frame": frame-1, "igt_overall": igt-1, "pad": previous_pad, "exact": True},
            {"frame": frame, "igt_overall": igt, "pad": pad, "exact": True},
        ],
        # The stale derived values actually shown in the reported screenshots.
        "frame_map": [frame-1], "picture_igt": [igt-1], "state_rows": [0],
    }
    service = make_service(tmp_path, [attempt()])
    result = service._validated_meta(meta, attempt())
    assert result["frame_map"] == [frame]
    assert result["picture_igt"] == [igt]
    assert result["picture_ledger"][result["state_rows"][0]]["pad"] == pad
    assert meta["frame_map"] == [frame-1]  # archival claims are not rewritten

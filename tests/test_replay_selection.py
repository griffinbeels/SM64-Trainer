"""One selector, two picture shapes.

A full RGBA array (the CPU path) and a tiny `SampledPicture` (what the GPU
hands over) must reach the SAME decision, retain the same source id and leave
the ledger in the same state. That comparison is the whole point of this file:
nothing here compares the selector against a second implementation, because
there is only one.
"""

from unittest.mock import Mock

import numpy as np
import pytest

from sm64_events.replay.media import MediaRun
from sm64_events.replay.feedmap import feed_map

from sm64_events.replay import pixels as P, ledger as L


def frame(value, height=17, width=17):
    rgba = np.full((height, width, 4), value, np.uint8)
    rgba[:, :, 3] = 255
    return rgba


def tiny(full):
    height, width = full.shape[:2]
    return P.SampledPicture(width, height, full[::8, ::8].tobytes(), 8)


def state(ledger):
    return list(ledger._rows), ledger._prev_shape, ledger._prev_sample


@pytest.mark.parametrize("dimensions", [(16, 16), (17, 17), (640, 480), (641, 481)])
def test_full_and_tiny_reach_the_same_decisions_and_keep_original_source(dimensions):
    width, height = dimensions
    full, gpu = L.PictureLedger(), L.PictureLedger()
    arrays = [frame(v, height, width) for v in [0, 17, 17, 33, 33, 44]]
    times = [1000, 1000.010, 1000.030, 1000.033, 1000.037, 1000.040]
    counters = [100, 100, 101, 101, 1, 1]
    wanted = ["selected", "coalesced", "coalesced", "selected", "coalesced", "selected"]
    retained = [
        "native:epoch:1",
        "native:epoch:1",
        "native:epoch:1",
        "native:epoch:4",
        "native:epoch:4",
        "native:epoch:6",
    ]
    selected = []
    for i, (pixels, ts, counter) in enumerate(
        zip(arrays, times, counters, strict=True)
    ):
        extras = {
            "source_id": f"native:epoch:{i + 1}",
            "pad": [i, -i, 0x8000],
            "exact": True,
        }
        a = full.observe_result(pixels, ts, counter, extras)
        b = gpu.observe_result(tiny(pixels), ts, counter, extras)
        if a.kind == "selected":
            selected.append(i)
        assert a == b and b.kind == wanted[i] and b.source_id == retained[i]
        assert state(full) == state(gpu)
        if i == 1:
            assert b.reason == "same_frame_fold"
            assert gpu._prev_sample == arrays[1][::8, ::8].tobytes()
            assert gpu._rows[-1][2]["source_id"] == "native:epoch:1"
        if i == 2:
            assert b.reason == "equal_sample" and counter != gpu._rows[-1][1]
    assert selected == [0, 3, 5]


@pytest.mark.parametrize("as_tiny", [False, True])
def test_real_sqlite_rejection_is_failed_not_duplicate_and_retry_still_works(
    tmp_path, as_tiny
):
    ledger = L.PictureLedger()
    ledger.open_archive(tmp_path / "pictures.sqlite")
    value = lambda image: tiny(image) if as_tiny else image
    assert ledger.observe(value(frame(1)), 1000, 10, {"source_id": "native:A"})
    before = state(ledger)
    try:
        # A real failing SQL INSERT, not a fake archive that may accept the row.
        ledger._archive._db.execute("""
            CREATE TRIGGER reject_fixture BEFORE INSERT ON pictures
            BEGIN SELECT RAISE(ABORT, 'fixture archive rejection'); END
        """)
        rejected = ledger.observe_result(
            value(frame(2)), 1000.04, 11, {"source_id": "native:B"}
        )
        assert rejected == L.Selection("failed", reason="archive_failed")
        assert state(ledger) == before
        assert len(ledger.rows_between(0, 2000)) == 1
        ledger._archive._db.execute("DROP TRIGGER reject_fixture")
        retry = ledger.observe_result(
            value(frame(2)), 1000.08, 11, {"source_id": "native:retry"}
        )
        assert retry == L.Selection("selected", "native:retry")
        assert [row["source_id"] for row in ledger.rows_between(0, 2000)] == [
            "native:A",
            "native:retry",
        ]
    finally:
        ledger.detach()


def test_sampling_error_and_missing_clock_are_explicit():
    class BadPixels:
        shape = (17, 17, 4)

        def __getitem__(self, item):
            raise MemoryError("sample unavailable")

    ledger = L.PictureLedger()
    assert ledger.observe_result(BadPixels(), None, 10) == L.Selection(
        "failed", reason="missing_capture_time"
    )
    assert ledger.observe_result(BadPixels(), 1000, 10) == L.Selection(
        "failed", reason="sampling_failed"
    )
    assert state(ledger) == ([], None, None)
    assert not ledger.observe(BadPixels(), None, 10)


def test_probe_and_extras_behavior_is_unchanged_across_selection_and_coalescing():
    full, gpu = L.PictureLedger(), L.PictureLedger()
    counters = []
    for ledger in [full, gpu]:
        good = Mock(return_value=71)
        bad = Mock(side_effect=LookupError("fixture missing probe"))
        ledger.stamps["probe"] = good
        ledger.stamps["optional"] = bad
        counters.append((good, bad))
    extras = {"source_id": "native:first", "pad": [31, -42, 0x8000], "exact": True}
    pixels = frame(3)
    assert full.observe_result(pixels, 1000, 10, extras).kind == "selected"
    assert gpu.observe_result(tiny(pixels), 1000, 10, extras).kind == "selected"
    assert state(full) == state(gpu)
    assert full._rows[0][2] == {**extras, "probe": 71}
    assert extras == {
        "source_id": "native:first",
        "pad": [31, -42, 0x8000],
        "exact": True,
    }
    for ledger, value in [(full, pixels), (gpu, tiny(pixels))]:
        assert not ledger.observe(
            value, 1000.033, 11, {"source_id": "native:duplicate"}
        )
    assert all(good.call_count == bad.call_count == 1 for good, bad in counters)


@pytest.mark.parametrize("source_id", [0, "", "x" * 161, []])
def test_invalid_native_id_cannot_commit_selected_picture(source_id):
    ledger = L.PictureLedger()
    assert ledger.observe_result(
        tiny(frame(4)), 1000, 10, {"source_id": source_id}
    ) == L.Selection("failed", reason="invalid_source_id")
    assert state(ledger) == ([], None, None)


def test_original_shape_and_exact_immutable_bytes_without_full_image_accessor():
    picture = tiny(frame(7, 481, 641))
    assert picture.shape == (481, 641, 4) and len(picture.sample) == 19764
    assert P.sample_bytes(picture, 8) is picture.sample
    assert not hasattr(picture, "as_bgra")
    with pytest.raises(ValueError, match="stride"):
        picture.sample_bytes(4)
    with pytest.raises(ValueError, match="immutable"):
        P.SampledPicture(641, 481, bytearray(picture.sample), 8)
    with pytest.raises(ValueError, match="immutable"):
        P.SampledPicture(641, 481, picture.sample[:-1], 8)
    with pytest.raises(ValueError, match="dimensions"):
        P.SampledPicture(0, 481, b"", 8)
    with pytest.raises(ValueError, match="stride"):
        P.SampledPicture(641, 481, picture.sample, 4)


def test_source_id_feed_heartbeat_and_run_guard_follow_the_fed_row():
    ledger = L.PictureLedger()
    run = MediaRun("encoder-A", 1000)
    a = ledger.observe_result(tiny(frame(1)), 1000, 10, {"source_id": "capture:1"})
    b = ledger.observe_result(tiny(frame(2)), 1000, 11, {"source_id": "capture:2"})
    assert (a.kind, b.kind) == ("selected", "selected")
    ledger.mark_fed(1000, 1000, media_run=run, pts=0, source_id=a.source_id)
    ledger.mark_fed(1000, 1000 + 1 / 90000, media_run=run, pts=1, source_id=b.source_id)
    ledger.mark_fed(None, 1001, media_run=run, pts=90000)
    rows = ledger.rows_between(0, 2000)
    feeds = ledger.feeds_between(0, 2000)
    values, repeated, stats = feed_map(
        [0, 1, 90000], run.id, rows, feeds, lambda row: row["frame"]
    )
    assert (
        values == [10, 11, 11]
        and repeated == [False, False, True]
        and stats["matched"] == 3
    )
    assert ledger._last_fed_row == ("encoder-A", 1000, "capture:2")
    ledger.mark_fed(None, 1002, media_run=MediaRun("encoder-B", 1002), pts=0)
    assert ledger._last_fed_row == ("encoder-B", None, None)
    assert "source_id" not in ledger.feeds_between(1002, 1002)[0]
    ledger.reset()
    assert ledger._last_fed_row == (None, None, None)
    assert (
        ledger.observe_result(
            tiny(frame(2)), 1003, 11, {"source_id": "new:capture"}
        ).kind
        == "selected"
    )

"""Identity retention with scratch SQLite and fake files; no media processes."""
from datetime import datetime, timezone

import numpy as np
import pytest

from sm64_events.replay.ledger import PictureLedger
from sm64_events.replay.media import MediaRun
from sm64_events.replay.ring import SegmentInfo, SegmentRing


def segment(tmp_path, run, start, end, name="segment"):
    path = tmp_path / f"{name}.ts"
    path.write_bytes(b"fake segment")
    return SegmentInfo(path, "video", datetime.fromtimestamp(start, timezone.utc),
                       datetime.fromtimestamp(end, timezone.utc), path.stat().st_size,
                       media_run=run)


def picture(ledger, run, ts, frame):
    pixels = np.full((1, 1, 4), frame % 256, dtype=np.uint8)
    assert ledger.observe(pixels, ts, frame, {"pad": [frame % 80, 0, 0], "exact": True})
    ledger.mark_fed(ts, ts, media_run=run, pts=run.ticks_at(ts))


def test_more_than_35_minutes_of_identity_is_persisted_with_bounded_ram(tmp_path):
    ledger = PictureLedger()
    path = tmp_path / "identity.sqlite3"
    ledger.open_archive(path)
    run = MediaRun("whole-session", 1000)
    # Exceed the former 63,000-row ceiling and span the largest explicit
    # retention setting. Sparse presents are legitimate VFR footage.
    for frame in range(66_000):
        picture(ledger, run, 1000 + frame * 1.4, frame)
    assert len(ledger._rows) <= 350 and len(ledger._feeds) <= 350
    first = ledger.rows_between(1000, 1000)[0]
    assert first["frame"] == 0 and first["pad"] == [0, 0, 0]
    assert ledger.feeds_between(1000, 1000)[0]["pts"] == 0
    ledger.reset()  # closes/commits; a fresh object cannot answer from RAM
    reopened = PictureLedger()
    reopened.open_archive(path)
    assert reopened.rows_between(1000, 1000) == [first]
    assert reopened.feeds_between(1000, 1000)[0]["run_id"] == run.id
    reopened.reset()


@pytest.mark.parametrize("retention", [None, 60])
def test_ring_eviction_preserves_the_source_of_a_later_heartbeat(tmp_path, retention):
    ledger = PictureLedger()
    ledger.open_archive(tmp_path / "identity.sqlite3")
    run = MediaRun("held-picture", 1000)
    picture(ledger, run, 1005, 5)
    ledger.mark_fed(None, 1020, media_run=run, pts=run.ticks_at(1020))
    a = segment(tmp_path, run, 1000, 1010, "a")
    b = segment(tmp_path, run, 1010, 1030, "b")
    ring = SegmentRing(retention, a.size_bytes, on_evict=ledger.discard_segment)
    ring.add(a)
    ring.add(b)  # byte cap evicts a, whose original picture is still in b
    assert not a.path.exists() and b.path.exists()
    assert ledger.feeds_between(1000, 1010) == []
    assert ledger.rows_between(1005, 1005)[0]["frame"] == 5
    assert ledger.feeds_between(1020, 1020)[0]["ts"] == 1005
    picture(ledger, run, 1040, 40)
    c = segment(tmp_path, run, 1030, 1050, "c")
    ring.add(c)
    assert ledger.rows_between(1000, 1030) == []
    assert ledger.rows_between(1040, 1040)[0]["frame"] == 40
    ledger.reset()


def test_pruning_one_run_does_not_erase_an_old_childs_late_segment(tmp_path):
    ledger = PictureLedger()
    ledger.open_archive(tmp_path / "identity.sqlite3")
    old, new = MediaRun("old", 1000), MediaRun("new", 1000)
    picture(ledger, old, 1005, 5)
    picture(ledger, new, 1006, 6)
    # Identical coverage in a different encoder run is a distinct identity.
    ledger.discard_segment(segment(tmp_path, new, 1000, 1010))
    assert ledger.feeds_between(1005, 1005)[0]["run_id"] == "old"
    assert ledger.rows_between(1005, 1005)[0]["frame"] == 5
    ledger.reset()


def test_evicted_metadata_does_not_accumulate_on_disk(tmp_path):
    ledger = PictureLedger()
    path = tmp_path / "identity.sqlite3"
    ledger.open_archive(path)
    run = MediaRun("rotating", 1000)
    ring = SegmentRing(None, 12, on_evict=ledger.discard_segment)
    for batch in range(150):
        start = 1000 + batch * 10
        for tick in range(30):
            picture(ledger, run, start + tick / 3, batch * 30 + tick)
        ring.add(segment(tmp_path, run, start, start + 10, str(batch)))
    assert len(ledger.feeds_between(0, 1e12)) == 30
    assert len(ledger.rows_between(0, 1e12)) == 30
    ledger.flush()
    assert path.stat().st_size < 256 * 1024
    ledger.reset()


def test_detach_releases_file_handle_but_keeps_existing_footage_queryable(tmp_path):
    ledger = PictureLedger()
    path = tmp_path / "identity.sqlite3"
    ledger.open_archive(path)
    run = MediaRun("retained", 1000)
    picture(ledger, run, 1001, 1)
    ledger.detach()
    assert ledger.rows_between(1001, 1001)[0]["frame"] == 1
    # Windows refuses this unlink if either capture or the read retained a
    # SQLite handle. Queries must not recreate a removed owner's database.
    path.unlink()
    assert ledger.rows_between(1000, 1010) == []
    assert ledger.feeds_between(1000, 1010) == []
    assert not path.exists()
    ledger.reset()


def test_native_identity_pruning_distinguishes_equal_timestamps(tmp_path):
    ledger = PictureLedger()
    path = tmp_path / 'native.sqlite3'
    ledger.open_archive(path)
    a, b = MediaRun('a', 1000), MediaRun('b', 1000)
    for serial, run in [(1, a), (2, b)]:
        source_id = f'producer:epoch:{serial}'
        assert ledger.observe(np.full((1, 1, 4), serial, np.uint8), 1005, serial,
                              {'source_id': source_id})
        ledger.mark_fed(1005, 1005, media_run=run, pts=run.ticks_at(1005), source_id=source_id)
    ledger.discard_segment(segment(tmp_path, a, 1000, 1010))
    assert [row['source_id'] for row in ledger.rows_between(1005, 1005)] == ['producer:epoch:2']
    ledger.mark_fed(None, 1020, media_run=b, pts=b.ticks_at(1020))
    ledger.discard_segment(segment(tmp_path, b, 1000, 1010))
    assert ledger.rows_between(1005, 1005)[0]['source_id'] == 'producer:epoch:2'
    picture(ledger, b, 1030, 3)
    ledger.discard_segment(segment(tmp_path, b, 1010, 1025))
    assert ledger.rows_between(1005, 1005) == []
    ledger.reset()


def test_old_scratch_schema_is_migrated_without_erasing_identity(tmp_path):
    import json
    import sqlite3
    from sm64_events.replay.picturearchive import PictureArchive
    path = tmp_path / 'old.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE pictures(ts REAL, data TEXT NOT NULL);'
                         'CREATE TABLE feeds(at REAL, ts REAL, run TEXT, pts INTEGER, data TEXT NOT NULL);')
        row = {'ts': 1000, 'frame': 4, 'source_id': 'producer:old:4'}
        feed = {'at': 1000, 'ts': 1000, 'run_id': 'old', 'pts': 0, 'source_id': 'producer:old:4'}
        db.execute('INSERT INTO pictures VALUES (?, ?)', (1000, json.dumps(row)))
        db.execute('INSERT INTO feeds VALUES (?, ?, ?, ?, ?)', (1000, 1000, 'old', 0, json.dumps(feed)))
    archive = PictureArchive(path)
    assert archive.rows_between(999, 1001) == [row]
    assert archive.feeds_between(999, 1001) == [feed]
    assert archive._db.execute('SELECT native_id FROM pictures').fetchone() == ('producer:old:4',)
    archive.discard_segment(segment(tmp_path, MediaRun('old', 1000), 1000, 1001))
    assert archive.rows_between(999, 1001) == []
    archive.close()

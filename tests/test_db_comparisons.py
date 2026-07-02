import pytest
from sm64_events.storage.db import Database


def _db(tmp_path):
    return Database(tmp_path / "t.db")


def test_insert_and_query_by_entity_strat(tmp_path):
    db = _db(tmp_path)
    cid = db.insert_comparison("star:7:0", "Ledgegrab", "XYZ run", "youtube",
                               "https://youtu.be/abc", "deadbeef.mp4",
                               "2026-07-02T00:00:00Z", "2026-07-02T00:00:00Z")
    assert isinstance(cid, int)
    rows = db.comparisons("star:7:0", "Ledgegrab")
    assert len(rows) == 1
    r = rows[0]
    assert r["name"] == "XYZ run" and r["source_kind"] == "youtube"
    assert r["cache_name"] == "deadbeef.mp4" and r["in_frame"] is None
    # not returned for a different pair
    assert db.comparisons("star:7:0", "Other") == []


def test_update_sync_points_and_touch(tmp_path):
    db = _db(tmp_path)
    cid = db.insert_comparison("segment:3", "Fast", "n", "file", "/v.mp4",
                               "c.mp4", "2026-07-02T00:00:00Z",
                               "2026-07-02T00:00:00Z")
    db.update_comparison(cid, in_frame=90, out_frame=300,
                         last_used_utc="2026-07-03T00:00:00Z")
    r = db.comparisons("segment:3", "Fast")[0]
    assert r["in_frame"] == 90 and r["out_frame"] == 300
    assert r["last_used_utc"] == "2026-07-03T00:00:00Z"


def test_update_unknown_raises(tmp_path):
    with pytest.raises(LookupError):
        _db(tmp_path).update_comparison(999, name="x")


def test_delete_and_cache_refcount(tmp_path):
    db = _db(tmp_path)
    a = db.insert_comparison("star:7:0", "L", "a", "youtube", "u1", "same.mp4",
                             "t", "t")
    db.insert_comparison("star:7:0", "L", "b", "youtube", "u2", "same.mp4",
                         "t", "t")
    assert db.comparison_cache_refs("same.mp4") == 2
    db.delete_comparison(a)
    assert db.comparison_cache_refs("same.mp4") == 1
    with pytest.raises(LookupError):
        db.delete_comparison(a)  # already gone

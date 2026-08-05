"""Which copy of the library wins, and what a refresh is allowed to do."""
import json

import pytest

from sm64_events.library.build import SCHEMA_VERSION
from sm64_events.library.store import (LibraryStore, newer, read_snapshot,
                                       write_snapshot)


def _snapshot(revision, label="Big Bob-omb on the Summit", schema=SCHEMA_VERSION):
    return {"schema_version": schema, "sheet_revision": revision,
            "fetched_at": "x", "runners": ["Kally"], "ladder_model": {},
            "targets": [{"entity_key": "star:1:0", "group": "1. Bob-omb Battlefield",
                         "section": "1. Bob-omb Battlefield", "label": label,
                         "version": None, "miss_reason": None,
                         "approaches": [{"ids": ["1"], "name": label,
                                         "best_cs": 4363, "best_runner": "Avatar",
                                         "times": {}, "ideal_cs": None,
                                         "fill_rate": 0.28, "ladder": {"Mario": 43.6},
                                         "entries": [{"runner": "Kally",
                                                      "time_cs": 4380,
                                                      "video": "https://youtu.be/z",
                                                      "version": None}]}],
                         "subsections": []}]}


def test_the_newer_sheet_revision_wins_whichever_copy_it_is():
    old, new = _snapshot("2026-01-01T00:00:00"), _snapshot("2026-08-05T09:15:18")
    # refreshed on Tuesday, updated Wednesday to a release built Monday
    assert newer(new, old) is new
    # refreshed in January, updated in March
    assert newer(old, new) is new


def test_a_copy_from_an_older_schema_is_discarded_whatever_its_revision():
    stale = _snapshot("2099-01-01T00:00:00", schema=SCHEMA_VERSION - 1)
    current = _snapshot("2020-01-01T00:00:00")
    assert newer(stale, current) is current
    assert newer(stale, None) is None


def test_a_local_copy_survives_an_app_update_carrying_an_older_snapshot(tmp_path):
    local, bundled = tmp_path / "local.json.gz", tmp_path / "bundled.json.gz"
    write_snapshot(local, _snapshot("2026-08-05T09:15:18", "fresh"))
    write_snapshot(bundled, _snapshot("2026-07-01T00:00:00", "stale"))
    store = LibraryStore(local, bundled)
    store.load()
    assert store.revision == "2026-08-05T09:15:18"
    assert store.payload["targets"][0]["label"] == "fresh"


def test_a_newer_release_replaces_a_stale_local_copy(tmp_path):
    local, bundled = tmp_path / "local.json.gz", tmp_path / "bundled.json.gz"
    write_snapshot(local, _snapshot("2026-01-01T00:00:00", "stale"))
    write_snapshot(bundled, _snapshot("2026-08-05T09:15:18", "fresh"))
    store = LibraryStore(local, bundled)
    store.load()
    assert store.payload["targets"][0]["label"] == "fresh"


def test_a_missing_or_corrupt_snapshot_is_simply_absent(tmp_path):
    broken = tmp_path / "broken.json.gz"
    broken.write_bytes(b"not a gzip file")
    assert read_snapshot(broken) is None
    assert read_snapshot(tmp_path / "nope.json.gz") is None
    store = LibraryStore(tmp_path / "nope.json.gz", broken)
    store.load()
    assert store.payload["targets"] == [] and store.revision is None


def test_a_snapshot_round_trips_through_disk(tmp_path):
    path = tmp_path / "lib.json.gz"
    write_snapshot(path, _snapshot("2026-08-05T09:15:18"))
    assert read_snapshot(path)["sheet_revision"] == "2026-08-05T09:15:18"


def test_writing_an_unchanged_snapshot_produces_identical_bytes(tmp_path):
    path = tmp_path / "lib.json.gz"
    write_snapshot(path, _snapshot("2026-08-05T09:15:18"))
    first = path.read_bytes()
    write_snapshot(path, _snapshot("2026-08-05T09:15:18"))
    assert path.read_bytes() == first


def _fetch_returning(payload):
    """A fetch whose parse we stub, since a real one needs the workbook."""
    def fake_fetch():
        return b"workbook bytes"
    return fake_fetch


def test_a_refresh_that_lands_on_an_older_sheet_is_not_applied(tmp_path, monkeypatch):
    store = LibraryStore(tmp_path / "local.json.gz", None)
    store._payload = _snapshot("2026-08-05T09:15:18", "current")
    monkeypatch.setattr("sm64_events.library.build.build",
                        lambda data, fetched_at, overrides=None:
                        _snapshot("2026-01-01T00:00:00", "older"))
    monkeypatch.setattr("sm64_events.library.ladders.fit_payload", lambda p: p)
    result = store.refresh(_fetch_returning(None))
    assert result["applied"] is False
    assert store.payload["targets"][0]["label"] == "current"
    assert not (tmp_path / "local.json.gz").exists()


def test_a_refresh_that_lands_on_a_newer_sheet_is_kept(tmp_path, monkeypatch):
    path = tmp_path / "local.json.gz"
    store = LibraryStore(path, None)
    store._payload = _snapshot("2026-01-01T00:00:00", "current")
    monkeypatch.setattr("sm64_events.library.build.build",
                        lambda data, fetched_at, overrides=None:
                        _snapshot("2026-08-05T09:15:18", "fresher"))
    monkeypatch.setattr("sm64_events.library.ladders.fit_payload", lambda p: p)
    result = store.refresh(_fetch_returning(None))
    assert result["applied"] is True
    assert store.payload["targets"][0]["label"] == "fresher"
    assert read_snapshot(path)["targets"][0]["label"] == "fresher"


def test_reads_answer_the_questions_the_ui_asks():
    store = LibraryStore()
    store._payload = _snapshot("2026-08-05T09:15:18")
    index = store.index()
    assert index["groups"][0]["targets"][0]["entries"] == 1
    assert store.target(0)["label"] == "Big Bob-omb on the Summit"
    assert store.target(99) is None
    assert len(store.for_entity("star:1:0")) == 1
    assert store.for_entity("star:9:9") == []
    runner = store.runner("Kally")
    assert runner["entries"][0]["time_cs"] == 4380
    assert runner["entries"][0]["video"] == "https://youtu.be/z"
    assert store.runner("nobody")["entries"] == []

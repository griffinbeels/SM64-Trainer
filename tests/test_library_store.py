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


def test_a_pathless_refresh_never_claims_a_persisted_local_copy(monkeypatch):
    """A store with no local path (no real embedder passes one; create_app
    always does) can still apply a refresh in memory, but nothing was WRITTEN
    anywhere -- status() must not call that "local", which implies a saved
    copy that survives a restart."""
    store = LibraryStore(None, None)
    monkeypatch.setattr("sm64_events.library.build.build",
                        lambda data, fetched_at, overrides=None:
                        _snapshot("2026-08-05T09:15:18", "fresher"))
    monkeypatch.setattr("sm64_events.library.ladders.fit_payload", lambda p: p)
    result = store.refresh(_fetch_returning(None))
    assert result["applied"] is True
    assert store.status()["source"] is None


def test_a_refresh_stamps_each_approachs_vetted_twin(tmp_path, monkeypatch):
    """The refresh producer must stamp exactly as scrape time does -- a
    refreshed snapshot the Library page reads differently is a fork."""
    twin = {"Mario": 30.9, "Grandmaster": 31.8, "Master": 32.8, "Diamond": 33.8}
    fresh = _snapshot("2026-08-05T09:15:18")
    fresh["targets"][0]["approaches"][0]["ladder"] = dict(twin)
    seed = tmp_path / "vetted.json"
    seed.write_text(json.dumps(
        {"entities": {"star:1:0": {"strategies": {"Skyjump": twin}}}}),
        encoding="utf-8")
    monkeypatch.setattr("sm64_events.core.paths.bundled_rank_standards",
                        lambda: seed)
    monkeypatch.setattr("sm64_events.library.build.build",
                        lambda data, fetched_at, overrides=None: fresh)
    monkeypatch.setattr("sm64_events.library.ladders.fit_payload", lambda p: p)
    store = LibraryStore(tmp_path / "local.json.gz", None)
    result = store.refresh(_fetch_returning(None))
    assert result["applied"] is True
    stamped = store.payload["targets"][0]["approaches"][0]
    assert stamped["matched_strategy"] == "Skyjump"


# -- status()'s "source" field: which copy is actually being served --------

def test_status_reports_bundled_when_only_the_bundled_copy_exists(tmp_path):
    bundled = tmp_path / "bundled.json.gz"
    write_snapshot(bundled, _snapshot("2026-08-05T09:15:18", "bundled-only"))
    store = LibraryStore(tmp_path / "local.json.gz", bundled)
    store.load()
    assert store.status()["source"] == "bundled"


def test_status_reports_local_when_the_local_copy_is_newer(tmp_path):
    local, bundled = tmp_path / "local.json.gz", tmp_path / "bundled.json.gz"
    write_snapshot(local, _snapshot("2026-08-05T09:15:18", "fresh"))
    write_snapshot(bundled, _snapshot("2026-07-01T00:00:00", "stale"))
    store = LibraryStore(local, bundled)
    store.load()
    assert store.status()["source"] == "local"


def test_status_reports_nothing_when_no_snapshot_exists_anywhere(tmp_path):
    store = LibraryStore(tmp_path / "local.json.gz", tmp_path / "bundled.json.gz")
    store.load()
    assert store.status()["source"] is None


def test_an_applied_refresh_is_the_only_persistent_confirmation_offered(tmp_path, monkeypatch):
    """Right after a successful refresh, status() must say "local" -- that is
    the whole of what tells the user their refresh actually took."""
    path = tmp_path / "local.json.gz"
    store = LibraryStore(path, None)
    store._payload = _snapshot("2026-01-01T00:00:00", "current")
    monkeypatch.setattr("sm64_events.library.build.build",
                        lambda data, fetched_at, overrides=None:
                        _snapshot("2026-08-05T09:15:18", "fresher"))
    monkeypatch.setattr("sm64_events.library.ladders.fit_payload", lambda p: p)
    result = store.refresh(_fetch_returning(None))
    assert result["applied"] is True
    assert store.status()["source"] == "local"


def test_a_falsy_group_stamps_the_same_value_it_was_keyed_by():
    """The stored `group` field must equal the key it was grouped under, or a
    falsy group collapses every such target into one permanently-unopenable
    cell -- falsy is the UI's own "nothing is open" sentinel."""
    store = LibraryStore()
    payload = _snapshot("2026-08-05T09:15:18")
    payload["targets"][0]["group"] = None
    store._payload = payload
    index = store.index()
    assert len(index["groups"]) == 1
    assert index["groups"][0]["group"] == payload["targets"][0]["section"]


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

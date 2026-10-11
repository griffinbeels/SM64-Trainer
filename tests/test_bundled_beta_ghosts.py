"""Packaged legacy provenance stays separate from live practice certificates."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from sm64_events.ghost.bundled_beta import AUTHOR, COLLECTION, DESCRIPTOR, BundledPopulation, with_bundled_beta
from sm64_events.ghost.portable import make_entry, write_package
from test_ghost_sharing import recording, segment
from sm64_events.ghost.sharing_targets import describe


class Local:
    generation = 1

    def metadata_snapshot(self, *args, **kwargs):
        return ()

    def get(self, _identity):
        raise LookupError("not local")

    def search(self, *args, **kwargs):
        return {"records": [], "next_cursor": None}

    def close(self):
        pass


def provider(tmp_path, *, author=AUTHOR, game_version=None, definitions=()):
    target = describe("segment", "3", "Standard", definitions) if definitions else None
    source, row = recording(tmp_path, kind="segment" if definitions else "star", target=target, pad=None)
    row = make_entry(source, author=author, target=row["target"], frames=row["frames"], clock=row["clock"],
                     recorded_utc=row["recorded_utc"], practice=False, outcome="success", game_version=game_version)
    collection = tmp_path / COLLECTION
    write_package(collection, [(row, source)])
    descriptor = tmp_path / DESCRIPTOR
    descriptor.write_text(json.dumps({"schema": 1, "file": COLLECTION, "author": AUTHOR,
        "sha256": hashlib.sha256(collection.read_bytes()).hexdigest(), "count": 1}))
    root = tmp_path / "cache"
    value = BundledPopulation(Local(), descriptor=descriptor, collection=collection, cache=root, definitions=definitions)
    return value, row, source, root


def test_metadata_is_immediate_but_selected_asset_is_extracted_lazily(tmp_path):
    value, raw, source, root = provider(tmp_path)
    rows = value.metadata_snapshot("star", "2-1", clock="igt", game_version="us", strategy="10 coin")
    assert len(rows) == 1 and not root.exists()
    row = rows[0]
    assert (row["source_status"], row["structurally_valid"], row["certified"], row["full_start"]) == (
        "bundled-author-recording", True, False, None)
    assert row["recorded_game_version"] is None and row["game_version"] == "us"
    assert row["player_name"] == AUTHOR and row["ghost"].pb is False and row["ghost"].attempt_id is None
    path, report = value.resolve(row["ghost"], {"kind": "star", "entity_key": "2-1", "strat": "10 coin"})
    assert path.read_bytes() == source.read_bytes()
    assert report["pad_records"] == 1  # The first frame remains unknown; the second was recorded.
    assert value._rows[raw["id"]]["game_version"] is None
    assert len(list(root.glob("*.ghost"))) == 1
    assert not (tmp_path / "tracker.db").exists()


def test_seed_clock_rom_and_strategy_scope_filters_remain_real(tmp_path):
    value, row, _, _ = provider(tmp_path, game_version="us")
    assert not value.metadata_snapshot("star", "2-1", clock="rta", game_version="us")
    assert not value.metadata_snapshot("star", "2-1", clock="igt", game_version="jp")
    assert not value.metadata_snapshot("star", "2-1", clock="igt", game_version="us", strategy="other")
    assert not value.metadata_snapshot("star", "2-2", clock="igt", game_version="us")
    with pytest.raises(ValueError, match="another practice"):
        value.resolve(value.get(row["id"])["ghost"], {"kind": "star", "entity_key": "2-1", "strat": "other"})


def test_bad_author_or_collection_hash_refuses_packaged_trust(tmp_path):
    with pytest.raises(ValueError, match="author"):
        provider(tmp_path, author="gtest1")
    good = tmp_path / "good"
    good.mkdir()
    value, _, _, _ = provider(good)
    value.collection.write_bytes(value.collection.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="verification"):
        BundledPopulation(Local(), descriptor=good / DESCRIPTOR, collection=value.collection,
                          cache=good / "cache", definitions=[])


def test_pinned_collection_change_after_metadata_cannot_supply_recordings(tmp_path):
    value, row, _, _ = provider(tmp_path)
    value.collection.write_bytes(value.collection.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="changed"):
        value.resolve(value.get(row["id"])["ghost"], {"kind": "star", "entity_key": "2-1", "strat": "10 coin"})


def test_seed_segment_definition_signature_rebinds_without_creating_definitions(tmp_path):
    value, _, _, _ = provider(tmp_path, definitions=[segment(3)])
    value._definitions = [segment(97)]
    rows = value.metadata_snapshot("segment", "97", clock="igt", game_version="us", strategy="Standard")
    assert rows[0]["signature"] and rows[0]["ghost"].entity_key == "97"
    value._definitions = [segment(97, end_level=9)]
    assert not value.metadata_snapshot("segment", "97", clock="igt", game_version="us", strategy="Standard")


def test_named_search_includes_curated_author_and_preserves_original_identity(tmp_path):
    value, row, _, _ = provider(tmp_path)
    identity = {"kind": "star", "entity_key": "2-1", "clock": "igt", "game_version": "us",
                "strategy": "10 coin", "scope": "strategy"}
    result = value.search(identity, "Griffman")
    assert result["records"][0]["id"] == row["id"]
    assert result["next_cursor"] is None
    assert value.search(identity, "gtest")["records"] == []


def test_source_and_stable_builds_cannot_acquire_bundled_beta_trust(tmp_path, monkeypatch):
    import sys
    from sm64_events.core import build_channel
    local = Local()
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(build_channel, "current_build", lambda: SimpleNamespace(channel="beta"))
    assert with_bundled_beta(local, cache=tmp_path, definitions=[]) is local
    monkeypatch.setattr(sys, "frozen", True)
    monkeypatch.setattr(build_channel, "current_build", lambda: SimpleNamespace(channel="stable"))
    assert with_bundled_beta(local, cache=tmp_path, definitions=[]) is local

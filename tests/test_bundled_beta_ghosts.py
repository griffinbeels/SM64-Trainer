"""Packaged legacy provenance stays separate from live practice certificates."""
import hashlib
import asyncio
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


def provider(tmp_path, *, author=AUTHOR, game_version=None, definitions=(), pad=(12, -34, 0x8000)):
    target = describe("segment", "3", "Standard", definitions) if definitions else None
    source, row = recording(tmp_path, kind="segment" if definitions else "star", target=target, pad=pad)
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
    assert report["pad_records"] == report["records"] == 2  # every frame's pad is known
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


def test_bundled_and_cloud_composition_can_admit_and_offer_the_testers_new_recording(tmp_path, monkeypatch):
    from sm64_events.community.cache import CachedPopulation
    from sm64_events.ghost.adaptive import PracticeIdentity
    from sm64_events.ghost.population import LocalPopulation
    from sm64_events.ghost.rival_recording import PopulationRecorder
    from sm64_events.storage.db import Database
    from test_ghost_population import recorded
    (tmp_path / "seed").mkdir()
    bundled, _, _, _ = provider(tmp_path / "seed")
    bundled.local = LocalPopulation(tmp_path / "population")
    population = CachedPopulation(bundled, root=tmp_path / "cloud-cache")
    database = Database(tmp_path / "practice.db")
    row, replay, original = recorded(tmp_path / "new-run")
    offered, events = [], []
    authenticated_uid = "c9a0a702-cab1-4c82-9c07-1077e6a8763e"
    qualified_source = bundled.local.qualified_source

    def contribution_context():
        events.append("context")
        return {"owner": authenticated_uid, "consent_epoch": 3}

    def qualify(*args, **kwargs):
        events.append("qualified")
        return qualified_source(*args, **kwargs)

    def remember_source(source, *, owner, consent_epoch):
        events.append("remember")
        offered.append((source, owner, consent_epoch))

    monkeypatch.setattr(bundled.local, "qualified_source", qualify)
    community = SimpleNamespace(contribution_context=contribution_context, remember_source=remember_source)
    identity = PracticeIdentity("star", "2-1", "igt", "us", "Standard")
    recorder = PopulationRecorder(population, replay, SimpleNamespace(db=database), lambda _attempt: identity,
                                  community=community)

    async def scenario():
        recorder.start()
        recorder.enqueue(row)
        await asyncio.wait_for(recorder.queue.join(), timeout=5)
        await recorder.stop()

    try:
        asyncio.run(scenario())
        assert recorder.accepted == 1 and recorder.refused == 0
        assert events == ["context", "qualified", "remember"]
        assert len(offered) == 1 and offered[0][1:] == (authenticated_uid, 3)
        assert offered[0][0]["raw_sha256"] == hashlib.sha256(original.read_bytes()).hexdigest()
        records = population.metadata_snapshot("star", "2-1", clock="igt", game_version="us", strategy="Standard")
        assert len(records) == 1 and records[0]["certified"] is True and records[0]["full_start"] is True
        assert records[0]["player_id"] != "bundled:griffman1212"
        assert database.attempts() == [] and database.pbs() == []
    finally:
        population.close()
        database.close()


def test_a_bundled_recording_with_an_unknown_input_is_refused_with_the_reason(tmp_path):
    """His rule (round 40): the review refuses such a collection; playback refuses it again."""
    value, _raw, _source, root = provider(tmp_path, pad=None)
    row, = value.metadata_snapshot("star", "2-1", clock="igt", game_version="us", strategy="10 coin")
    with pytest.raises(ValueError, match="not known on every frame"):
        value.resolve(row["ghost"], {"kind": "star", "entity_key": "2-1", "strat": "10 coin"})
    assert not list(root.glob("*.ghost"))


def test_cloud_wrapper_forwards_the_players_names_to_the_bundled_collection(tmp_path):
    """Composed as the beta runs it: cloud cache over the bundled collection over local recordings."""
    from sm64_events.community.cache import CachedPopulation
    from sm64_events.ghost.bundled_beta import PLAYER_ID
    uid = "c9a0a702-cab1-4c82-9c07-1077e6a8763e"
    bundled, _, _, _ = provider(tmp_path)
    account = SimpleNamespace(snapshot=lambda: {"authenticated": True, "status": "active", "uid": uid, "name": "GriffMan1212"})
    population = CachedPopulation(bundled, root=tmp_path / "cloud-cache", account=account)
    try:
        assert population.owned_player_ids("local") == {"local", uid}
        assert population.owned_player_ids("local", names=("someone", " Griffman1212 ")) == {"local", uid, PLAYER_ID}
        assert bundled.owned_player_ids("local", names=("gtest", None)) == {"local"}
        assert bundled.owned_player_ids(None, names=(AUTHOR,)) == {PLAYER_ID}
    finally:
        population.close()

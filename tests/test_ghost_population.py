"""Local population admission and fresh profiles exercise real immutable clips."""
from dataclasses import replace
from datetime import datetime
import hashlib
import json
from pathlib import Path

import pytest

from ghost_native_fixture import attempt, frame, producer
from sm64_events.core.events import Event
from sm64_events.ghost.clip import STAR_CLOCK, attempt_origin, check_manifest, write_clip
from sm64_events.ghost.population import LocalPopulation
from sm64_events.storage.db import Database
from sm64_events.tracking.projection import Attempt
from tools.ghost_climb import launch_profile, prepare_profile, seed_population


def recorded(tmp_path, *, number=77, strategy="Standard", frames=2, game_version="us"):
    origin = attempt()
    row = Attempt(id=number, session_id=9, course_id=2, star_id=1, strat_tag=strategy,
        anchor_type="practice_reset", anchor_frame=101, outcome="success", outcome_detail=None,
        igt_frames=frames, rta_frames=frames, started_utc=origin.started_utc, ended_utc=origin.ended_utc,
        cleared=False, cleared_reason=None, valid=True, timed_at="xcam", game_version=game_version,
        **attempt_origin(origin))
    path = tmp_path / "replays" / "native" / f"attempt_{number}.ghost"
    target = {"kind": "star", "entity_key": "2-1", "strat": strategy}
    manifest = {**write_clip(path, [frame(n, elapsed=n-1) for n in (1, 2, 3)], producer(), clock_kind=STAR_CLOCK),
                "attempt_id": number, "session_id": 9, **attempt_origin(row), "target": target}
    path.with_suffix(".json").write_text(json.dumps(manifest), encoding="utf-8")
    class Replay:
        current = row
        def attempt(self, number):
            return self.current
        def preserve_native(self, number):
            return {"path": str(path)}
        def find_native(self, number):
            check_manifest(path, manifest, attempt=self.current, target=target)
            return path, manifest
    return row, Replay(), path


def admit(provider, row, replay, *, player="p1", name="FormFrame", **changes):
    return provider.accept_verified(row, replay, **{**dict(player_id=player, player_name=name,
        clock="igt", game_version="us", full_start=True), **changes})


def test_preserved_identity_survives_reused_attempt_ids_and_population_restart(tmp_path):
    root = tmp_path / "population"
    row, replay, original = recorded(tmp_path / "first")
    provider = LocalPopulation(root)
    first = admit(provider, row, replay)
    assert admit(provider, row, replay)["id"] == first["id"]
    second_row, second_replay, _ = recorded(tmp_path / "second", strategy="10 coin")
    second = admit(provider, second_row, second_replay)
    assert first["id"] != second["id"]
    assert first["ghost"].attempt_id is None
    assert first["certified"] and first["valid"] and first["full_start"]
    assert first["source"] == {"session_id": row.session_id, "attempt_id": row.id, "started_utc": row.started_utc}
    assert provider.metadata_snapshot("star", "2-1", clock="igt", game_version="jp") == ()
    path, manifest = provider.resolve(first["ghost"], {"kind": "star", "entity_key": "2-1"})
    assert path.read_bytes() == original.read_bytes()
    assert manifest["sha256"] == hashlib.sha256(original.read_bytes()).hexdigest()
    provider.close()
    reopened = LocalPopulation(root)
    assert reopened.get(first["id"])["frames"] == 2
    assert len(reopened.metadata_snapshot("star", "2-1", clock="igt", game_version="us")) == 2
    reopened.close()


@pytest.mark.parametrize("changes,options", [
    ({"valid": None}, {}), ({"valid": False}, {}), ({"cleared": True}, {}),
    ({"outcome": "reset"}, {}), ({"state_loads": 1}, {}), ({"anchor_type": "state_loaded"}, {}),
    ({"timed_at": "grab"}, {}), ({"timed_at": None}, {}), ({"native_clock_kind": "segment"}, {}),
    ({"timed_by": "imported"}, {}), ({"strat_tag": None}, {}),
    ({"igt_frames": 0}, {}), ({}, {"game_version": None}), ({}, {"game_version": "jp"}),
    ({}, {"full_start": False}), ({}, {"full_start": 1}),
])
def test_uncertified_or_incompatible_runs_do_not_enter_population(tmp_path, changes, options):
    row, replay, _ = recorded(tmp_path)
    row = replace(row, **changes)
    replay.current = row
    provider = LocalPopulation(tmp_path / "population")
    with pytest.raises(ValueError):
        admit(provider, row, replay, **options)
    assert provider.metadata_snapshot("star", "2-1", clock="igt", game_version="us") == ()
    assert not list((provider.root / "clips").glob("*.ghost"))
    provider.close()


def test_projection_change_during_preservation_refuses_publication(tmp_path):
    row, replay, _ = recorded(tmp_path)
    previous = replay.preserve_native
    def changing(number):
        result = previous(number)
        replay.current = replace(row, cleared=True)
        return result
    replay.preserve_native = changing
    provider = LocalPopulation(tmp_path / "population")
    with pytest.raises(ValueError):
        admit(provider, row, replay)
    assert provider.generation[0] == 0
    provider.close()


def test_late_admission_failure_removes_uncommitted_asset(tmp_path):
    row, replay, _ = recorded(tmp_path)
    provider = LocalPopulation(tmp_path / "population")
    publish = provider._publish_asset
    def invalidated_after_install(*args):
        created = publish(*args)
        replay.current = replace(row, cleared=True)
        return created
    provider._publish_asset = invalidated_after_install
    with pytest.raises(ValueError):
        admit(provider, row, replay)
    assert provider.generation[0] == 0
    assert list((provider.root / "clips").glob("*.ghost")) == []
    provider.close()


def test_population_limit_reuses_bytes_and_refuses_extra_rows(tmp_path, monkeypatch):
    from sm64_events.ghost import population
    row, replay, path = recorded(tmp_path)
    monkeypatch.setattr(population, "MAX_POPULATION_BYTES", path.stat().st_size)
    monkeypatch.setattr(population, "MAX_RECORDINGS", 2)
    provider = LocalPopulation(tmp_path / "population")
    admit(provider, row, replay)
    second = LocalPopulation(provider.root)
    admit(second, row, replay, player="p2")  # Same immutable bytes cost no additional storage.
    with pytest.raises(ValueError, match="storage limit"):
        admit(provider, row, replay, player="p3")
    assert provider.generation[0] == 2 and len(list((provider.root / "clips").glob("*.ghost"))) == 1
    second.close()
    provider.close()


def test_prefix_search_is_literal_bounded_and_paged(tmp_path):
    provider = LocalPopulation(tmp_path / "population")
    for n, name in enumerate(("FormFrame", "formframe", "FORMFRAME", "Form%", "Elsewhere")):
        row, replay, _ = recorded(tmp_path / str(n), frames=n+1)
        admit(provider, row, replay, player=f"p{n}", name=name)
    identity = {"kind": "star", "entity_key": "2-1", "clock": "igt", "game_version": "us"}
    first = provider.search(identity, "FORMFRAME", limit=2)
    assert len(first["records"]) == 2
    next_page = provider.search(identity, "formframe", cursor=first["next_cursor"], limit=2)
    assert len(next_page["records"]) == 1 and next_page["next_cursor"] is None
    assert {r["id"] for r in first["records"]}.isdisjoint({r["id"] for r in next_page["records"]})
    assert len(provider.search(identity, "Form%")['records']) == 1
    assert provider.search(identity, "") == {"records": [], "next_cursor": None}
    with pytest.raises(ValueError):
        provider.search(identity, "Form", limit=1000)
    provider.close()


def test_deleted_or_modified_bytes_never_resolve_as_an_available_rival(tmp_path):
    row, replay, _ = recorded(tmp_path)
    provider = LocalPopulation(tmp_path / "population")
    record = admit(provider, row, replay)
    target = {"kind": "star", "entity_key": "2-1"}
    path, _ = provider.resolve(record["ghost"], target)
    path.write_bytes(path.read_bytes()[:-1] + b"!")
    with pytest.raises(ValueError):
        provider.resolve(record["ghost"], target)
    with pytest.raises(ValueError):
        provider.resolve(record["ghost"], {"kind": "star", "entity_key": "1-1"})
    provider.close()


def source_profile(tmp_path, *, unversioned=False):
    source = tmp_path / "source"
    row, _replay, _ = recorded(source, game_version=None if unversioned else "us")
    database = Database(source / "data" / "tracker.db")
    sid = database.insert_session(row.started_utc)
    database.upsert_attempt(replace(row, session_id=sid))
    database.insert_pb(2, 1, "Standard", "igt", 2, row.id, row.ended_utc, game_version=row.game_version)
    database.append_event(sid, 1, Event(type="session_started", frame=0,
                                     timestamp_utc=datetime.fromisoformat(row.started_utc), payload={}))
    segment = database.insert_segment_def("My route", [{"type": "practice_reset"}],
        [{"type": "star_collected"}], [], row.started_utc, default_strat="Standard")
    database.set_state("ghost_saved", [{"attempt_id": row.id}])
    database.close()
    # Exact manifest follows this source's real saved session number.
    path = source / "replays" / "native" / f"attempt_{row.id}.json"
    manifest = json.loads(path.read_text())
    manifest["session_id"] = sid
    path.write_text(json.dumps(manifest), encoding="utf-8")
    (source / "data" / "rank_standards.json").write_text('{"version":1}', encoding="utf-8")
    return source, segment


def test_fresh_profile_preserves_definitions_population_and_real_history(tmp_path):
    source, segment = source_profile(tmp_path)
    before = {str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in source.rglob("*") if path.is_file()}
    population = tmp_path / "population"
    seeded = seed_population(source, population, player_name="FormFrame")
    assert len(seeded["accepted"]) == 1
    record = seeded["accepted"][0]
    first = prepare_profile(source, population, tmp_path / "profiles")
    second = prepare_profile(source, population, tmp_path / "profiles")
    assert first["profile"] != second["profile"] and first["player_id"] != second["player_id"]
    fresh = Database(Path(first["profile"]) / "data" / "tracker.db")
    assert fresh.attempts() == [] and fresh.pbs() == [] and fresh.events() == [] and fresh.sessions() == []
    assert any(row["id"] == segment and row["name"] == "My route" for row in fresh.segment_defs())
    assert fresh.get_state("ghost_saved", []) == []
    fresh.close()
    reopened = LocalPopulation(population)
    assert reopened.get(record)["player_name"] == "FormFrame"
    reopened.close()
    after = {str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in source.rglob("*") if path.is_file()}
    assert all(after.get(name) == digest for name, digest in before.items())
    # SQLite's read-only WAL reader may create coordination files, never data writes.
    assert set(after) - set(before) <= {"data\\tracker.db-wal", "data\\tracker.db-shm"}
    if "data\\tracker.db-wal" not in before and "data\\tracker.db-wal" in after:
        assert (source / "data" / "tracker.db-wal").stat().st_size == 0


def test_unversioned_historical_seed_requires_explicit_developer_certification(tmp_path):
    source, _ = source_profile(tmp_path, unversioned=True)
    refused = seed_population(source, tmp_path / "population")
    assert refused["accepted"] == [] and len(refused["refused"]) == 1
    seeded = seed_population(source, tmp_path / "population", source_version="us")
    provider = LocalPopulation(tmp_path / "population")
    assert provider.get(seeded["accepted"][0])["certificate_source"] == "developer_source_version"
    provider.close()


def test_seed_uses_source_canonical_clock_without_rewriting_standards(tmp_path):
    source, _ = source_profile(tmp_path)
    path = source / "data" / "rank_standards.json"
    document = {"version": 999, "entities": {"star:2:1": {"clock": "rta", "strategies": {}}}}
    before = json.dumps(document).encode()
    path.write_bytes(before)
    result = seed_population(source, tmp_path / "population")
    provider = LocalPopulation(tmp_path / "population")
    assert provider.get(result["accepted"][0])["clock"] == "rta"
    assert provider.metadata_snapshot("star", "2-1", clock="igt", game_version="us") == ()
    assert path.read_bytes() == before
    provider.close()


def test_launch_refuses_competition_and_continuously_holds_reserved_capture(tmp_path, monkeypatch):
    source, _ = source_profile(tmp_path)
    profile = prepare_profile(source, tmp_path / "population", tmp_path / "profiles")
    calls = []
    with pytest.raises(RuntimeError, match="Close the other trainer"):
        launch_profile(profile["profile"], runner=lambda **kw: calls.append(kw),
                       live_probe=lambda port: True, lock_factory=lambda: pytest.fail("must refuse before reservation"))
    with pytest.raises(RuntimeError, match="owns recording"):
        launch_profile(profile["profile"], runner=lambda **kw: calls.append(kw),
                       live_probe=lambda port: False, lock_factory=lambda: None)
    handle = (tmp_path / "fake-lock").open("w")
    monkeypatch.setenv("SM64_MAINTAINER", "1")
    def running(*, recorder_lock):
        assert recorder_lock is handle and not handle.closed
        from sm64_events.core.paths import db_path, ghost_population_root, shared_defaults_root
        assert db_path() == Path(profile["profile"]) / "data" / "tracker.db"
        assert ghost_population_root() == tmp_path / "population"
        assert shared_defaults_root() is None
        calls.append("ran")
    launch_profile(profile["profile"], runner=running, live_probe=lambda port: False, lock_factory=lambda: handle)
    assert calls == ["ran"] and handle.closed
    import os
    assert os.environ["SM64_MAINTAINER"] == "1"

"""The developer climb copies exact clips into independent tester profiles."""
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ghost_native_fixture import attempt, frame, producer
from sm64_events.ghost.clip import STAR_CLOCK, attempt_origin, write_clip
from sm64_events.ghost.clip import read_clip
from sm64_events.ghost.settings import defaults
from sm64_events.ghost.population import LocalPopulation
from sm64_events.ghost.rivals import Rivals
from sm64_events.storage.db import Database
from sm64_events.tracking.projection import Attempt, replay
from tests.test_ghost_adaptive import Standards, attempt as played_attempt
from tests.test_ghost_rivals import Native
from tools import ghost_climb
from tools.ghost_climb import SIMULATION_KEY, WILD_BLUE, launch_profile, simulate_fresh, simulate_wild_blue


def donors(tmp_path, timings=(337, 332, 331, 329, 326, 323, 321, 320)):
    source = tmp_path / "source"
    database = Database(source / "data" / "tracker.db")
    origin = attempt()
    sid = database.insert_session(origin.started_utc)
    for index, frames in enumerate(timings):
        row = Attempt(id=100 + index, session_id=sid, course_id=2, star_id=2, strat_tag="Standard",
            anchor_type="practice_reset", anchor_frame=101, outcome="success", outcome_detail=None,
            igt_frames=frames, rta_frames=frames, started_utc=origin.started_utc, ended_utc=origin.ended_utc,
            cleared=False, cleared_reason=None, valid=None, timed_at="xcam", **attempt_origin(origin))
        database.upsert_attempt(row)
        path = source / "replays" / "native" / f"attempt_{row.id}.ghost"
        manifest = {**write_clip(path, [frame(n, elapsed=n-1, action=0x0c400201+index, pad=None) for n in (1, 2, 3)],
            producer(), clock_kind=STAR_CLOCK), "attempt_id": row.id, "session_id": sid,
            **attempt_origin(row), "target": WILD_BLUE}
        path.with_suffix(".json").write_text(json.dumps(manifest), encoding="utf-8")
    database.insert_pb(2, 2, "Standard", "igt", min(timings), 100 + timings.index(min(timings)),
        origin.ended_utc, game_version=None)
    database.close()
    return source


def file_hashes(folder):
    return {path.relative_to(folder): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in folder.rglob("*") if path.is_file()}


def test_named_simulation_preserves_bytes_times_legacy_evidence_and_real_history(tmp_path):
    source = donors(tmp_path)
    before = file_hashes(source)
    document = simulate_wild_blue(source, tmp_path / "profiles")
    profile = Path(document["profile"])
    provider = LocalPopulation(document["population"])
    database = Database(profile / "data" / "tracker.db")
    try:
        rows = provider.metadata_snapshot("star", "2-2", clock="igt", game_version="us")
        assert sorted(row["frames"] for row in rows) == [320, 321, 323, 326, 329, 331, 332, 337]
        assert len({row["player_id"] for row in rows}) == len(rows)
        assert all(row["player_name"].startswith("Simulated ") for row in rows)
        assert all(row["simulated"] and row["certificate_source"] == "developer_simulation" for row in rows)
        assert all(row["source_valid"] is None and row["source_game_version"] is None for row in rows)
        for row in rows:
            copied, _ = provider.resolve(row["ghost"], WILD_BLUE)
            original = source / "replays" / "native" / f"attempt_{row['source_attempt_id']}.ghost"
            assert copied.read_bytes() == original.read_bytes()
        marker = database.get_state(SIMULATION_KEY, None)
        assert marker == document["simulation"]
        assert marker["scenario_root"] == str(profile) and marker["target"] == WILD_BLUE
        baseline = marker["baseline"]
        assert baseline["frames"] == 337 and database.pbs()[0]["frames"] == 337
        assert database.pbs()[0]["attempt_id"] == baseline["attempt_id"]
        assert database.pbs()[0]["id"] == baseline["pb_id"]
        assert len(database.attempts()) == 1 and database.attempts()[0].timed_by == "imported"
        # The new tester owns a journaled baseline, never a fabricated native arm.
        assert database.attempts()[0].native_clock_frame is None
        projected, state = replay(database.events())
        assert state.target == ("star", 2, 2) and state.strat_by_star[(2, 2)] == "Standard"
        database.replace_attempts(projected)
        assert database.delete_orphaned_pbs() == 0 and database.pbs()[0]["frames"] == 337
        tester = provider.get(baseline["recording_id"])
        assert tester["manual_only"] and tester["player_id"] == document["player_id"]
        assert database.get_state("ghost_player_id", None) == document["player_id"]
    finally:
        database.close()
        provider.close()
    after = file_hashes(source)
    assert all(after.get(name) == digest for name, digest in before.items())
    assert set(after) - set(before) <= {Path("data/tracker.db-wal"), Path("data/tracker.db-shm")}


def test_every_rerun_has_independent_tester_baseline_and_small_population(tmp_path):
    source = donors(tmp_path)
    first = simulate_wild_blue(source, tmp_path / "profiles")
    second = simulate_wild_blue(source, tmp_path / "profiles")
    assert first["profile"] != second["profile"] and first["player_id"] != second["player_id"]
    assert first["population"] != second["population"]
    assert Path(first["population"]).is_relative_to(first["profile"])
    assert len(list(Path(second["population"]).glob("clips/*.ghost"))) == 8
    assert not (Path(first["profile"]) / "replays").exists()


def test_simulation_refuses_insufficient_donors_and_never_weakens_real_admission(tmp_path):
    source = donors(tmp_path, timings=(337, 332, 331, 329))
    profiles = tmp_path / "profiles"
    with pytest.raises(ValueError, match="five differently timed"):
        simulate_wild_blue(source, profiles)
    assert not profiles.exists()
    with pytest.raises(ValueError, match="separate profile"):
        simulate_wild_blue(source, source.parent)
    with pytest.raises(ValueError, match="source ROM"):
        simulate_wild_blue(source, profiles, source_version=None)


def test_simulated_profile_launch_holds_reservation_without_touching_real_profile(tmp_path):
    source = donors(tmp_path)
    document = simulate_wild_blue(source, tmp_path / "profiles")
    calls = []
    reservation = (tmp_path / "inert-lock").open("w")
    def runner(*, recorder_lock):
        from sm64_events.core.paths import db_path, ghost_population_root
        assert recorder_lock is reservation and not reservation.closed
        assert db_path() == Path(document["profile"]) / "data" / "tracker.db"
        assert ghost_population_root() == Path(document["population"])
        calls.append(True)
    launch_profile(document["profile"], runner=runner, lock_factory=lambda: reservation,
        live_probe=lambda _port: False)
    assert calls == [True] and reservation.closed


def test_launcher_defaults_to_zero_practice_and_can_resume_existing_profiles():
    launcher = Path(__file__).resolve().parents[1] / "run-ghost-climb.bat"
    text = launcher.read_text(encoding="utf-8")
    assert "--simulate-fresh --launch" in text
    assert 'if /i "%~1"=="--profile"' in text and 'ghost_climb.py --launch %*' in text
    assert "--seed --prepare --launch" not in text


def test_fresh_simulation_has_no_practice_or_pb_and_preserves_source(tmp_path):
    source = donors(tmp_path)
    before = file_hashes(source)
    document = simulate_fresh(source, tmp_path / "profiles")
    profile = Path(document["profile"])
    database = Database(profile / "data" / "tracker.db")
    provider = LocalPopulation(document["population"])
    try:
        assert database.sessions() == [] and database.events() == []
        assert defaults()["path_enabled"] is False
        assert database.attempts() == [] and database.pbs() == []
        assert database.get_state("ghost_player_id", None) == document["player_id"]
        marker = database.get_state(SIMULATION_KEY, None)
        assert marker == document["simulation"] and marker["zero_practice"]
        assert "baseline" not in marker
        assert database.get_state("ghost_rivals_v1", {})["options"]["mode"] == "adaptive"
        rows = provider.metadata_snapshot("star", "2-2", clock="igt", game_version="us")
        assert {row["player_name"] for row in rows} == {f"gtest{n}" for n in range(1, 9)}
        assert len({row["player_id"] for row in rows}) == 8
        assert all(row["player_id"] != document["player_id"] and not row["manual_only"] for row in rows)
        assert sorted(row["frames"] for row in rows) == [320, 321, 323, 326, 329, 331, 332, 337]
        for row in rows:
            copied, _ = provider.resolve(row["ghost"], WILD_BLUE)
            original = source / "replays" / "native" / f"attempt_{row['source_attempt_id']}.ghost"
            assert copied.read_bytes() == original.read_bytes()
            assert row["simulated"] and row["source_valid"] is None and row["source_game_version"] is None
    finally:
        database.close()
        provider.close()
    after = file_hashes(source)
    assert all(after.get(name) == digest for name, digest in before.items())
    assert set(after) - set(before) <= {Path("data/tracker.db-wal"), Path("data/tracker.db-shm")}
    second = simulate_fresh(source, tmp_path / "profiles")
    assert second["profile"] != document["profile"] and second["player_id"] != document["player_id"]


def test_explicit_full_range_stress_test_gets_four_rivals_and_pb_adds_fifth(tmp_path):
    source = donors(tmp_path)
    document = simulate_fresh(source, tmp_path / "profiles")
    database = Database(Path(document["profile"]) / "data" / "tracker.db")
    provider, native = LocalPopulation(document["population"]), Native()
    tracker = SimpleNamespace(db=database, ranks=Standards())
    rivals = Rivals(tracker, provider, native, lambda: "us")
    controls = {"hue_degrees": 250}
    try:
        rivals.refresh(WILD_BLUE, controls, enabled=True)
        # No PB: the floor range is empty, so it widens upward to a full house (round 49).
        assert rivals.pb is None and len(rivals.rows) == 4
        rivals.apply(rivals.checked({"options": {"mode": "fixed", "min_step": 0, "max_step": 44}}, WILD_BLUE))
        rivals.refresh(WILD_BLUE, controls, enabled=True)
        assert rivals.pb is None and len(rivals.rows) == 4
        ghosts = native.preparations[-1][1]
        assert len(ghosts) == len({ghost.origin for ghost in ghosts}) == 4
        assert len({ghost.hue_degrees for ghost in ghosts}) == 4
        assert len({provider.resolve(ghost, WILD_BLUE)[0] for ghost in ghosts}) == 4
        assert rivals.payload()["scope"] == "strategy"
        # Simulate the first played/saved result only AFTER verifying empty state.
        origin = attempt()
        sid = database.insert_session(origin.started_utc)
        played = played_attempt(1, frames=400, session_id=sid, course_id=2, star_id=2, game_version="us")
        database.upsert_attempt(played)
        database.insert_pb(2, 2, "Standard", "igt", 400, played.id, played.ended_utc, game_version="us")
        rivals.refresh(WILD_BLUE, controls, enabled=True, force=True)
        ghosts = native.preparations[-1][1]
        assert len(rivals.rows) == 4 and rivals.pb["attempt_id"] == played.id
        assert len(ghosts) == 5 and ghosts[0].pb and ghosts[0].origin == "attempt:1"
    finally:
        database.close()
        provider.close()


def test_fresh_population_keeps_other_strategies_targets_and_clock(tmp_path, monkeypatch):
    source = donors(tmp_path)
    database = Database(source / "data" / "tracker.db")
    origin = database.attempt(100)
    for number, star, strategy, rta in ((300, 2, "Other", 900), (301, 3, "Standard", 700)):
        row = replace(origin, id=number, star_id=star, strat_tag=strategy, rta_frames=rta)
        database.upsert_attempt(row)
        path = source / "replays" / "native" / f"attempt_{number}.ghost"
        manifest = {**write_clip(path, [frame(n, elapsed=n-1, action=0x0c400201+number, pad=None) for n in (1, 2, 3)],
            producer(), clock_kind=STAR_CLOCK), "attempt_id": row.id, "session_id": row.session_id,
            **attempt_origin(row), "target": {"kind": "star", "entity_key": f"2-{star}", "strat": strategy}}
        path.with_suffix(".json").write_text(json.dumps(manifest), encoding="utf-8")
    database.close()
    ranks = SimpleNamespace(clock_for=lambda key: "rta" if key == "star:2:3" else "igt")
    monkeypatch.setattr(ghost_climb, "_seed_standards", lambda _: ranks)
    document = simulate_fresh(source, tmp_path / "profiles")
    provider = LocalPopulation(document["population"])
    try:
        other = provider.metadata_snapshot("star", "2-2", clock="igt", game_version="us", strategy="Other")
        assert len(other) == 1 and other[0]["frames"] == 337
        rta = provider.metadata_snapshot("star", "2-3", clock="rta", game_version="us")
        assert len(rta) == 1 and rta[0]["frames"] == 700
        for row in (*other, *rta):
            provider.resolve(row["ghost"], row["target"])
    finally:
        provider.close()


def test_fresh_simulation_refuses_a_partial_roster_before_creating_profile(tmp_path):
    source = donors(tmp_path, timings=(337, 332, 331))
    with pytest.raises(ValueError, match="four|4 distinct"):
        simulate_fresh(source, tmp_path / "profiles")
    assert not (tmp_path / "profiles").exists()


def input_donors(tmp_path):
    from tests.test_ghost_padfill import action_at
    source=donors(tmp_path)
    database=Database(source/"data/tracker.db")
    row=replace(database.attempt(100),closed_frame=140)
    database.upsert_attempt(row)
    database.close()
    path=source/"replays/native/attempt_100.ghost"
    manifest=json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    recorded=write_clip(path,[frame(n,timer=100+n,elapsed=n-1,action=action_at(100+n),pad=None)
                              for n in range(1,41)],producer(),clock_kind=STAR_CLOCK)
    path.with_suffix(".json").write_text(json.dumps({**manifest,**recorded,**attempt_origin(row)}),encoding="utf-8")
    return source


def test_simulation_carries_proven_inputs_without_modifying_original_or_faking_gaps(tmp_path,monkeypatch):
    from tests.test_ghost_padfill import track, pad_at
    source=input_donors(tmp_path)
    before=file_hashes(source)
    held=[row for row in track(range(101,141)) if row[0]!=110]
    monkeypatch.setattr(ghost_climb,"track_for_attempt",lambda _store,attempt,_span:held if attempt.id==100 else [])
    document=simulate_fresh(source,tmp_path/"profiles")
    provider=LocalPopulation(document["population"])
    try:
        row=next(row for row in provider.metadata_snapshot("star","2-2",clock="igt",game_version="us")
                 if row["source_attempt_id"]==100)
        path,manifest=provider.resolve(row["ghost"],WILD_BLUE)
        played=read_clip(path,manifest["sha256"])
        original=read_clip(source/"replays/native/attempt_100.ghost")
        assert row["sha256"]!=row["source_sha256"] and row["source_sha256"]==before[Path("replays/native/attempt_100.ghost")]
        assert played.clip_id==original.clip_id
        assert [frame.global_timer for frame in played.frames if frame.pad is None]==[110]
        assert all(frame.pad==pad_at(frame.global_timer) for frame in played.frames if frame.pad is not None)
        assert all(file_hashes(source).get(name)==digest for name,digest in before.items())
    finally:
        provider.close()


def test_existing_fresh_tester_upgrade_keeps_practice_identity_pins_and_original_pool(tmp_path,monkeypatch):
    from tests.test_ghost_padfill import track
    source=input_donors(tmp_path)
    document=simulate_fresh(source,tmp_path/"profiles")
    document.pop("input_enrichment_version")  # previous tester setup, without embedded pads
    profile=Path(document["profile"])
    old_pool=file_hashes(Path(document["population"])/"clips")
    database=Database(profile/"data/tracker.db")
    played=played_attempt(1,frames=400,course_id=2,star_id=2)
    database.upsert_attempt(played)
    database.insert_pb(2,2,"Standard","igt",400,played.id,played.ended_utc,game_version="us")
    before=database.attempts(),database.pbs()
    old_id=document["recordings"][0]["id"]
    settings=database.get_state("ghost_rivals_v1",{})
    settings["pins"]={json.dumps(["star","2-2","Standard"],separators=(",",":")):{"0":old_id}}
    database.set_state("ghost_rivals_v1",settings)
    database.close()
    monkeypatch.setattr(ghost_climb,"track_for_attempt",lambda _store,attempt,_span:track(range(101,141)) if attempt.id==100 else [])
    upgraded=ghost_climb.upgrade_simulation_inputs(profile,document)
    assert upgraded["profile"]==document["profile"] and upgraded["player_id"]==document["player_id"]
    assert upgraded["population"]!=document["population"] and upgraded["input_enrichment_version"]==1
    assert file_hashes(Path(document["population"])/"clips")==old_pool
    database=Database(profile/"data/tracker.db")
    provider=LocalPopulation(upgraded["population"])
    try:
        assert (database.attempts(),database.pbs())==before
        new_id=upgraded["recordings"][0]["id"]
        assert new_id!=old_id
        assert database.get_state("ghost_rivals_v1",{})["pins"]=={next(iter(settings["pins"])):{"0":new_id}}
        row=provider.get(new_id)
        assert row["player_name"]=="gtest1" and row["player_id"]!=document["player_id"]
        path,manifest=provider.resolve(row["ghost"],WILD_BLUE)
        assert all(frame.pad is not None for frame in read_clip(path,manifest["sha256"]).frames)
        assert ghost_climb.upgrade_simulation_inputs(profile,upgraded)==upgraded
    finally:
        database.close()
        provider.close()

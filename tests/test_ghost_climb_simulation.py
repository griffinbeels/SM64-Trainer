"""The developer climb copies exact clips into independent tester profiles."""
import hashlib
import json
from pathlib import Path

import pytest

from ghost_native_fixture import attempt, frame, producer
from sm64_events.ghost.clip import STAR_CLOCK, attempt_origin, write_clip
from sm64_events.ghost.population import LocalPopulation
from sm64_events.storage.db import Database
from sm64_events.tracking.projection import Attempt, replay
from tools.ghost_climb import SIMULATION_KEY, WILD_BLUE, launch_profile, simulate_wild_blue


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
        manifest = {**write_clip(path, [frame(n, elapsed=n-1, action=0x0c400201+index) for n in (1, 2, 3)],
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


def test_launcher_defaults_to_the_named_simulation():
    launcher = Path(__file__).resolve().parents[1] / "run-ghost-climb.bat"
    text = launcher.read_text(encoding="utf-8")
    assert "--simulate-wild-blue --launch" in text
    assert 'if /i "%~1"=="--profile"' in text and 'ghost_climb.py --launch %*' in text
    assert "--seed --prepare --launch" not in text

"""Prepare or user-launch an isolated climb against a retained local population.

Examples (from the checkout containing this tool):
  uv run python tools/ghost_climb.py --seed --certify-source-version us
  uv run python tools/ghost_climb.py --prepare --launch
  uv run python tools/ghost_climb.py --launch --profile C:/absolute/profile
  uv run python tools/ghost_climb.py --simulate-wild-blue --launch

Seeding uses already committed clips and a read-only consistent database copy.
--certify-source-version is an explicit developer declaration for old recordings
without a journal ROM stamp; it never turns private imports into certified runs.
Every --prepare creates a new profile; no reset command deletes surviving data.
Launching stays in this process and holds one recorder reservation continuously.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import urllib.request
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from sm64_events.core.paths import bundled_rank_standards, bundled_sheet_ladders, candidate_server_ports  # noqa: E402
from sm64_events.core.events import Event  # noqa: E402
from sm64_events.ghost.clip import check_manifest, read_clip  # noqa: E402
from sm64_events.ghost.pb import local_attempt_source  # noqa: E402
from sm64_events.ghost.population import LocalPopulation  # noqa: E402
from sm64_events.ghost.sharing_targets import canonical, describe  # noqa: E402
from sm64_events.inputs.service import entity_key_of  # noqa: E402
from sm64_events.ranks.standards import RankStandards, entity_key  # noqa: E402
from sm64_events.replay.publication import atomic_json  # noqa: E402
from sm64_events.storage.db import Database  # noqa: E402
from sm64_events.tracking.caveats import pb_blocked_by  # noqa: E402
from sm64_events.tracking.projection import replay as project  # noqa: E402

CONFIG_FILES = ("rank_standards.json", "sheet_library.json.gz", "rank_calibration.bin",
                "library_adoptions.json", "tracker_mode.json")
CONFIG_TABLES = ("segment_defs", "routes", "landmark_names")
SIMULATION_KEY = "ghost_simulation_v1"
WILD_BLUE = {"kind": "star", "entity_key": "2-2", "strat": "Standard"}
SIMULATED_NAMES = ("Alex", "Sam", "Kai", "Morgan", "Taylor", "Riley", "Jamie", "Robin", "Casey", "Drew", "Avery", "Jules")


def _absolute(path):
    value = Path(path)
    if not value.is_absolute():
        raise ValueError("Choose an absolute profile or population directory.")
    return value.resolve()


@contextmanager
def source_snapshot(source_root):
    """Migrations may run on the copy, never on the player's source database."""
    source = _absolute(source_root) / "data" / "tracker.db"
    if not source.is_file():
        raise ValueError("This source profile has no practice database yet.")
    with tempfile.TemporaryDirectory(prefix="sm64-climb-source-") as folder:
        snapshot = Path(folder) / "tracker.db"
        original = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
        copied = sqlite3.connect(snapshot)
        try:
            original.backup(copied)
        finally:
            copied.close()
            original.close()
        database = Database(snapshot)
        try:
            yield database
        finally:
            database.close()


def _copy_definitions(source, destination):
    # Constant metadata table names; keep IDs so references and adoptions bind.
    with source._lock, destination._lock, destination._conn:
        for table in CONFIG_TABLES:
            columns = [row[1] for row in source._conn.execute(f"PRAGMA table_info({table})")]
            rows = source._conn.execute(f"SELECT * FROM {table}").fetchall()
            names = ",".join(columns)
            slots = ",".join("?" for _ in columns)
            destination._conn.execute(f"DELETE FROM {table}")  # Only this newly created empty profile.
            destination._conn.executemany(f"INSERT INTO {table} ({names}) VALUES ({slots})", rows)


def prepare_profile(source_root, population_root, profiles_root):
    source, population, profiles = map(_absolute, (source_root, population_root, profiles_root))
    if profiles == source or source.is_relative_to(profiles):
        raise ValueError("Fresh profiles must have their own directory, outside the source profile's parents.")
    with source_snapshot(source) as original:
        return _fresh_profile(source, population, profiles, original)


def _fresh_profile(source, population, profiles, original):
    profile = profiles / uuid4().hex
    fresh = Database(profile / "data" / "tracker.db")
    try:
        _copy_definitions(original, fresh)
    finally:
        fresh.close()
    for name in CONFIG_FILES:
        candidate = source / "data" / name
        if candidate.is_file():
            shutil.copyfile(candidate, profile / "data" / name)
    document = {"version": 1, "profile": str(profile), "source": str(source),
                "population": str(population), "player_id": f"local:{uuid4().hex}"}
    atomic_json(profile / "climb-profile.json", document)
    return document


def _simulation_donors(database, source, version):
    """Only exact playable local recordings; never synthesize or retime poses."""
    replay = _SeedReplay(database, source, None)
    donors = {}
    for attempt in database.attempts():
        if (entity_key_of(attempt) != (WILD_BLUE["kind"], WILD_BLUE["entity_key"])
                or attempt.strat_tag != WILD_BLUE["strat"] or attempt.outcome != "success"
                or attempt.cleared or attempt.valid is False or pb_blocked_by(attempt)
                or attempt.game_version not in (None, version)
                or type(attempt.igt_frames) is not int or attempt.igt_frames <= 0):
            continue
        found = replay.find_native(attempt.id)
        if found is None:
            continue
        path = found[0]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        read_clip(path, digest)
        # One representative per actual recorded time keeps the fixture compact.
        donors.setdefault(attempt.igt_frames, (attempt, path, digest))
    result = sorted(donors.values(), key=lambda item: item[0].igt_frames, reverse=True)
    if len(result) < 5:
        raise ValueError("Wild Blue needs five differently timed saved Standard ghosts for this simulation.")
    return result


def _simulated_record(provider, donor, *, player_id, name, version, baseline=False):
    attempt, path, digest = donor
    document = {"player_id": player_id, "player_name": name,
        "target": describe(WILD_BLUE["kind"], WILD_BLUE["entity_key"], WILD_BLUE["strat"], ()),
        "frames": attempt.igt_frames, "clock": "igt", "game_version": version,
        "sha256": digest, "recorded_utc": attempt.started_utc,
        "source": local_attempt_source(attempt), "source_attempt_id": attempt.id,
        "source_valid": attempt.valid, "source_game_version": attempt.game_version,
        "simulated": True, "certificate_source": "developer_simulation",
        # These flags certify a developer fixture, never a remote performance.
        "certified": True, "valid": True, "full_start": True, "manual_only": baseline}
    document["id"] = hashlib.sha256(canonical(document)).hexdigest()
    validate = lambda: hashlib.sha256(path.read_bytes()).hexdigest() == digest
    # Only the newly created scenario directory receives these marked fixtures.
    return provider._publish_record(document, path, validate)


def _simulation_baseline(database, frames, version):
    now = datetime.now(timezone.utc)
    sid = database.insert_session(now.isoformat(), label="Developer simulation")
    database.append_event(sid, 0, Event(type="session_started", frame=0, timestamp_utc=now,
        payload={"session_id": sid, "attempt_rules": 2}))
    aid = database.append_event(sid, 1, Event(type="time_imported", frame=0, timestamp_utc=now,
        payload={"source": "Developer simulation: Wild Blue", "course_id": 2, "star_id": 2,
                 "strat_tag": "Standard", "timer_mode": "igt", "frames": frames, "game_version": version}))
    database.append_event(sid, 2, Event(type="strat_set", frame=0, timestamp_utc=now,
        payload={"course_id": 2, "star_id": 2, "strat_tag": "Standard"}))
    database.append_event(sid, 3, Event(type="target_set", frame=0, timestamp_utc=now,
        payload={"course_id": 2, "star_id": 2, "strat_tag": "Standard"}))
    database.replace_attempts(project(database.events())[0])
    pid = database.insert_pb(2, 2, "Standard", "igt", frames, aid, now.isoformat(),
        imported_from="Developer simulation: Wild Blue", game_version=version)
    return {"attempt_id": aid, "pb_id": pid, "frames": frames}


def simulate_wild_blue(source_root, profiles_root, *, source_version="us"):
    """A new tester and named local opponents, using unchanged real clip bytes.

    Legacy evidence stays in source_* fields. The new names and grading flags
    belong only to this explicitly marked developer fixture population.
    """
    source, profiles = map(_absolute, (source_root, profiles_root))
    if source_version not in ("us", "jp"):
        raise ValueError("Choose the simulation's source ROM explicitly.")
    if profiles == source or source.is_relative_to(profiles):
        raise ValueError("Simulations need a separate profile directory.")
    with source_snapshot(source) as original:
        donors = _simulation_donors(original, source, source_version)
        document = _fresh_profile(source, profiles / "unused", profiles, original)
    profile = Path(document["profile"])
    population = profile / "data" / "ghost_population"
    document["population"] = str(population)
    provider = LocalPopulation(population)
    database = Database(profile / "data" / "tracker.db")
    try:
        tester = _simulated_record(provider, donors[0], player_id=document["player_id"],
            name="Simulated Tester", version=source_version, baseline=True)
        opponents = [_simulated_record(provider, donor,
            player_id=f"simulated:{uuid4().hex}", name=f"Simulated {SIMULATED_NAMES[index % len(SIMULATED_NAMES)]}"
                + (f" {index // len(SIMULATED_NAMES) + 1}" if index >= len(SIMULATED_NAMES) else ""),
            version=source_version) for index, donor in enumerate(donors[1:])]
        baseline = _simulation_baseline(database, tester["frames"], source_version)
        simulation = {"version": 1, "label": "Developer simulation · Wild Blue", "scenario_root": str(profile),
            "target": dict(WILD_BLUE), "game_version": source_version,
            "baseline": {**baseline, "recording_id": tester["id"],
                         "source": tester["source"], "sha256": tester["sha256"]}}
        database.set_state("ghost_player_id", document["player_id"])
        database.set_state(SIMULATION_KEY, simulation)
        document.update(simulation=simulation, recordings=[{
            "id": row["id"], "player_name": row["player_name"], "frames": row["frames"],
            "sha256": row["sha256"], "source_attempt_id": row["source_attempt_id"]} for row in (tester, *opponents)])
        atomic_json(profile / "climb-profile.json", document)
        atomic_json(population / "developer-simulation.json", simulation)
    finally:
        database.close()
        provider.close()
    return document


class _SeedReplay:
    """Read-only saved-clip adapter; cannot preserve new assets or capture play."""
    def __init__(self, database, source_root, certification):
        self.database = database
        self.root = Path(source_root) / "replays"
        self._certification = certification
        self.certificate_source = "developer_source_version" if certification else "journal"
        self._paths = {}
        for path in sorted(self.root.rglob("attempt_*.ghost")):
            number = path.stem[8:]
            if number.isdecimal():
                self._paths.setdefault(int(number), []).append(path)

    def attempt(self, number):
        row = self.database.attempt(number)
        if row is None:
            raise LookupError("This seed attempt no longer exists.")
        return replace(row, game_version=self._certification) if row.game_version is None and self._certification else row

    def find_native(self, number):
        row = self.attempt(number)
        kind, key = entity_key_of(row)
        target = {"kind": kind, "entity_key": key, "strat": row.strat_tag}
        for path in self._paths.get(number, ()):
            if not path.resolve().is_relative_to(self.root.resolve()):
                continue
            try:
                manifest = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
                manifest = manifest.get("native_ghost", manifest)
                check_manifest(path, manifest, attempt=row, target=target)
                return path, manifest
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                continue
        return None

    def preserve_native(self, number):
        found = self.find_native(number)
        return {"path": str(found[0]) if found else None,
                "native_ghost_reason": None if found else "No certified saved Mario clip exists for this attempt."}


def _seed_standards(source):
    # Rank loading can reconcile/write its file; only this private copy may change.
    with tempfile.TemporaryDirectory(prefix="sm64-climb-ranks-") as folder:
        copied = Path(folder) / "rank_standards.json"
        stored = source / "data" / "rank_standards.json"
        if stored.is_file():
            shutil.copyfile(stored, copied)
        ranks = RankStandards(copied, bundled_rank_standards(), bundled_sheet_ladders())
        ranks.load()
        return ranks


def seed_population(source_root, population_root, *, source_version=None, player_name="You"):
    source, population = map(_absolute, (source_root, population_root))
    if source_version not in (None, "us", "jp"):
        raise ValueError("Certify US or JP source recordings explicitly.")
    player_id = "local:" + hashlib.sha256(str(source).casefold().encode()).hexdigest()
    ranks = _seed_standards(source)
    accepted, refused = [], []
    with source_snapshot(source) as database:
        replay = _SeedReplay(database, source, source_version)
        provider = LocalPopulation(population, database.segment_defs)
        try:
            for attempt in database.attempts():
                if attempt.id not in replay._paths:
                    continue
                version = attempt.game_version or source_version
                clock = ranks.clock_for(entity_key(attempt.course_id, attempt.star_id, attempt.segment_id))
                try:
                    row = provider.accept_verified(replay.attempt(attempt.id), replay, player_id=player_id, player_name=player_name,
                        clock=clock, game_version=version, full_start=attempt.valid is True)
                    accepted.append(row["id"])
                except (ValueError, LookupError, OSError) as error:
                    refused.append({"attempt_id": attempt.id, "reason": str(error)})
        finally:
            provider.close()
    return {"accepted": accepted, "refused": refused, "developer_source_version": source_version,
            "population": str(population)}


def launch_profile(profile, *, port=8070, runner=None, lock_factory=None, live_probe=None):
    """Only the user's launch gesture invokes this; tests supply an inert runner."""
    from sm64_events.core.recorder_lock import acquire_recorder_lock
    profile = _absolute(profile)
    document = json.loads((profile / "climb-profile.json").read_text(encoding="utf-8"))
    if document.get("version") != 1 or _absolute(document["profile"]) != profile:
        raise ValueError("Choose a profile prepared by the climb tool.")
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("Choose a local port from 1024 to 65535.")
    probe = live_probe or _live_trainer
    if any(probe(number) for number in dict.fromkeys((port, *candidate_server_ports()))):
        raise RuntimeError("Close the other trainer before starting the isolated climb. It will not be taken over.")
    reservation = (lock_factory or acquire_recorder_lock)()
    if reservation is None:
        raise RuntimeError("Another trainer owns recording. Close it before starting the isolated climb.")
    previous = {name: os.environ.get(name) for name in
                ("SM64_PRACTICE_ROOT", "SM64_GHOST_POPULATION", "SM64_PORT", "SM64_MAINTAINER")}
    try:
        os.environ["SM64_PRACTICE_ROOT"] = str(profile)
        os.environ["SM64_GHOST_POPULATION"] = str(_absolute(document["population"]))
        os.environ["SM64_PORT"] = str(port)
        os.environ.pop("SM64_MAINTAINER", None)
        if runner is None:
            from sm64_events.main import run
            runner = run
        runner(recorder_lock=reservation)
    finally:
        if not reservation.closed:
            reservation.close()
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _live_trainer(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=.3) as response:
            json.loads(response.read(65536))
            return True
    except (OSError, ValueError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--population", type=Path)
    parser.add_argument("--profiles", type=Path)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--certify-source-version", choices=("us", "jp"))
    parser.add_argument("--player-name", default="You")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--simulate-wild-blue", action="store_true", help="Fresh simulated tester and exact Wild Blue recordings; original history stays untouched.")
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--port", type=int, default=8070)
    args = parser.parse_args()
    source = _absolute(args.source_root)
    population = args.population or source / "data" / "ghost_population"
    profiles = args.profiles or source / ".iteration" / "ghost-climb" / "profiles"
    if not (args.seed or args.prepare or args.launch or args.simulate_wild_blue):
        parser.error("Choose --seed, --prepare, --simulate-wild-blue or --launch.")
    if args.simulate_wild_blue and (args.seed or args.prepare or args.population or args.profile):
        parser.error("The Wild Blue simulation creates its own fresh profile and population.")
    try:
        if args.seed:
            print(json.dumps(seed_population(source, population, source_version=args.certify_source_version,
                                            player_name=args.player_name), indent=2))
        profile = args.profile
        if args.simulate_wild_blue:
            document = simulate_wild_blue(source, profiles, source_version=args.certify_source_version or "us")
            profile = Path(document["profile"])
            print(json.dumps(document, indent=2))
        if args.prepare:
            document = prepare_profile(source, population, profiles)
            profile = Path(document["profile"])
            print(json.dumps(document, indent=2))
        if args.launch:
            if profile is None:
                parser.error("Use --prepare or choose an existing --profile before --launch.")
            print(f"Isolated climb: http://127.0.0.1:{args.port}; close with Ctrl+C.")
            launch_profile(profile, port=args.port)
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, f"Climb could not start: {error}\n")


if __name__ == "__main__":
    main()

"""Prepare or user-launch an isolated climb against a retained local population.

Examples (from the checkout containing this tool):
  uv run python tools/ghost_climb.py --seed --certify-source-version us
  uv run python tools/ghost_climb.py --prepare --launch
  uv run python tools/ghost_climb.py --launch --profile C:/absolute/profile

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
from sm64_events.ghost.clip import check_manifest  # noqa: E402
from sm64_events.ghost.population import LocalPopulation  # noqa: E402
from sm64_events.inputs.service import entity_key_of  # noqa: E402
from sm64_events.ranks.standards import RankStandards, entity_key  # noqa: E402
from sm64_events.replay.publication import atomic_json  # noqa: E402
from sm64_events.storage.db import Database  # noqa: E402

CONFIG_FILES = ("rank_standards.json", "sheet_library.json.gz", "rank_calibration.bin",
                "library_adoptions.json", "tracker_mode.json")
CONFIG_TABLES = ("segment_defs", "routes", "landmark_names")


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
    profile = profiles / uuid4().hex
    with source_snapshot(source) as original:
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
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--port", type=int, default=8070)
    args = parser.parse_args()
    source = _absolute(args.source_root)
    population = args.population or source / "data" / "ghost_population"
    profiles = args.profiles or source / ".iteration" / "ghost-climb" / "profiles"
    if not (args.seed or args.prepare or args.launch):
        parser.error("Choose --seed, --prepare or --launch.")
    try:
        if args.seed:
            print(json.dumps(seed_population(source, population, source_version=args.certify_source_version,
                                            player_name=args.player_name), indent=2))
        profile = args.profile
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

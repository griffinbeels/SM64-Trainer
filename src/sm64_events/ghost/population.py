"""Certified local population for the adaptive pilot, independent of practice PBs.

Admission runs on the session worker after a genuine projected success commits.
Clip bytes and record identities are immutable; the index has bounded indexed
target/prefix reads. Private portable imports do not acquire certification here.
This is local trust, not remote account authorization or anti-cheat evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import unicodedata

from sm64_events.ghost.clip import attempt_origin, blink_frames, read_clip
from sm64_events.ghost.portable import checked_author, checked_hash
from sm64_events.ghost.sharing import PortableGhost
from sm64_events.ghost.sharing_targets import bind, canonical, describe
from sm64_events.inputs.service import entity_key_of
from sm64_events.tracking.caveats import pb_blocked_by, save_state_run

MAX_RECORDINGS = 16384
MAX_POPULATION_BYTES = 512 << 20


def _normalize_player(value: str) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > 128 or any(ord(c) < 32 for c in value):
        raise ValueError("Enter a player name up to 128 UTF-8 bytes.")
    return unicodedata.normalize("NFKC", value).casefold().strip()


def _player(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128 or any(ord(c) < 33 for c in value):
        raise ValueError("A population recording needs a stable player identity.")
    return value


def _admissible(attempt, clock, game_version, full_start):
    if (full_start is not True or getattr(attempt, "valid", None) is not True
            or getattr(attempt, "outcome", None) != "success" or getattr(attempt, "cleared", True)
            or save_state_run(attempt) or pb_blocked_by(attempt)
            or getattr(attempt, "timed_by", None) == "imported"):
        raise ValueError("Automatic rivals require a certified full, valid successful run.")
    if clock not in ("igt", "rta") or game_version not in ("us", "jp"):
        raise ValueError("Automatic rivals require a known clock and ROM version.")
    observed = getattr(attempt, "game_version", None)
    if observed != game_version:
        raise ValueError("The source certificate disagrees with this run's ROM version.")
    kind = getattr(attempt, "native_clock_kind", None)
    if getattr(attempt, "segment_id", None) is not None:
        native_start = kind == "segment"
    else:
        native_start = (kind == "star" and getattr(attempt, "timed_at", None) == "xcam") or (
            kind == "segment" and getattr(attempt, "course_id", 0) in range(1, 16)
            and getattr(attempt, "star_id", None) == 6)
    if not native_start:
        raise ValueError("This run has no complete native clock certificate.")
    from sm64_events.ghost import pb as pb_rules
    predicate = getattr(pb_rules, "eligible_attempt", None)
    if predicate is not None:
        from sm64_events.ghost.adaptive import PracticeIdentity
        kind, key = entity_key_of(attempt)
        identity = PracticeIdentity(kind=kind, entity_key=key, clock=clock,
                                    game_version=game_version, strategy=attempt.strat_tag)
        if not predicate(attempt, identity):
            raise ValueError("This run does not qualify for automatic rivals.")
    if clock == "igt" and getattr(attempt, "timed_by", None) != "igt":
        raise ValueError("This run was not measured on its declared clock.")
    frames = getattr(attempt, f"{clock}_frames", None)
    if type(frames) is not int or not 0 < frames <= 30 * 60 * 60 * 24 or not getattr(attempt, "strat_tag", None):
        raise ValueError("Automatic rivals require a measured time and strategy.")
    attempt_origin(attempt)
    return frames


@dataclass(frozen=True)
class Admission:
    """A worker's immutable projected owner, checked again after preservation."""
    owner: bytes
    frames: int


def _admission(attempt, clock, game_version, full_start):
    frames = _admissible(attempt, clock, game_version, full_start)
    return Admission(canonical({"id": attempt.id, "session": attempt.session_id,
        "target": entity_key_of(attempt), "strategy": attempt.strat_tag, "frames": frames,
        "ended": attempt.ended_utc, **attempt_origin(attempt)}), frames)


def _preserved_asset(replay, attempt):
    kept = replay.preserve_native(attempt.id)
    if not kept.get("path"):
        raise ValueError(kept.get("native_ghost_reason") or "This run has no preserved Mario recording.")
    found = replay.find_native(attempt.id)  # the existing strict owner/hash validator
    if found is None:
        raise ValueError("This run's Mario recording could not be validated.")
    fill = getattr(replay, "native_with_inputs", None)
    path, _manifest = fill(attempt.id, found) if fill is not None else found
    source = Path(path)
    with source.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    read_clip(source, digest)
    return source, digest


class LocalPopulation:
    def __init__(self, root, definitions=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._definitions = definitions
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.root / "population.sqlite3", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            self._db.close()
            raise ValueError("This local ghost population uses an unsupported format.")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS recordings (
                id TEXT PRIMARY KEY, player TEXT NOT NULL, normalized TEXT NOT NULL,
                target TEXT NOT NULL, kind TEXT NOT NULL, clock TEXT NOT NULL,
                version TEXT NOT NULL, frames INTEGER NOT NULL,
                sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, document TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS recordings_target ON recordings(target,clock,version,frames,id);
            CREATE INDEX IF NOT EXISTS recordings_player ON recordings(target,clock,version,normalized,frames,id);
            PRAGMA user_version=1;
        """)
        self._verified = {}

    def close(self):
        with self._lock:
            self._db.close()

    def _defs(self):
        value = self._definitions() if callable(self._definitions) else self._definitions
        return value or ()

    def _target(self, kind, key):
        target = describe(kind, key, None, self._defs())
        return target.get("signature") or f"star:{key}"

    @property
    def generation(self):
        with self._lock:
            return tuple(self._db.execute("SELECT COUNT(*),MAX(rowid) FROM recordings").fetchone())

    def _row(self, stored):
        row = json.loads(stored["document"])
        key, reason = bind(row["target"], self._defs())
        if reason:
            raise LookupError(reason)
        ghost = PortableGhost(row["id"], row["target"]["kind"], key,
                              row["target"]["strat"], row["frames"], row["player_name"])
        return {**row, "kind": ghost.kind, "entity_key": key, "strategy": ghost.strat_tag,
                "signature": row["target"].get("signature"), "ghost": ghost}

    def metadata_snapshot(self, kind, entity_key, *, clock, game_version):
        target = self._target(kind, entity_key)
        with self._lock:
            rows = self._db.execute("SELECT document FROM recordings WHERE target=? AND clock=? AND version=? "
                                    "ORDER BY frames,id LIMIT ?",
                                    (target, clock, game_version, MAX_RECORDINGS)).fetchall()
        return tuple(self._row(row) for row in rows)

    def get(self, identity):
        with self._lock:
            row = self._db.execute("SELECT document FROM recordings WHERE id=?", (checked_hash(identity),)).fetchone()
        if row is None:
            raise LookupError("That rival recording is no longer in the local population.")
        return self._row(row)

    def search(self, identity, player_prefix, *, cursor=None, limit=20):
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("Choose a search page of 1 to 20 records.")
        # Prefix range avoids LIKE wildcards and a scan of every player.
        prefix = _normalize_player(player_prefix)
        if not prefix:
            return {"records": [], "next_cursor": None}
        kind = identity["kind"] if isinstance(identity, dict) else identity.kind
        key = identity["entity_key"] if isinstance(identity, dict) else identity.entity_key
        clock = identity["clock"] if isinstance(identity, dict) else identity.clock
        version = identity["game_version"] if isinstance(identity, dict) else identity.game_version
        target = self._target(kind, key)
        after = ()
        if cursor is not None:
            if (not isinstance(cursor, list) or len(cursor) != 3 or not isinstance(cursor[0], str)
                    or not cursor[0].startswith(prefix) or type(cursor[1]) is not int):
                raise ValueError("This rival search cursor is invalid.")
            checked_hash(cursor[2])
            after = tuple(cursor)
        where = " AND (normalized,frames,id) > (?,?,?)" if after else ""
        with self._lock:
            rows = self._db.execute("SELECT document,normalized,frames,id FROM recordings "
                "WHERE target=? AND clock=? AND version=? AND normalized>=? AND normalized<?" + where +
                " ORDER BY normalized,frames,id LIMIT ?",
                (target, clock, version, prefix, prefix + chr(0x10ffff), *after, limit + 1)).fetchall()
        next_cursor = ([rows[limit - 1][key] for key in ("normalized", "frames", "id")]
                       if len(rows) > limit else None)
        return {"records": [self._row(row) for row in rows[:limit]], "next_cursor": next_cursor}

    def resolve(self, ghost, target):
        row = self.get(ghost.id)
        if target is None or (row["kind"], row["entity_key"]) != (target["kind"], target["entity_key"]):
            raise ValueError("This rival belongs to another practice target.")
        path = self.root / "clips" / f"{checked_hash(row['sha256'])}.ghost"
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("This rival recording is outside the population folder.")
        stamp = path.stat()
        key = stamp.st_size, stamp.st_mtime_ns, stamp.st_ctime_ns
        with self._lock:
            previous = self._verified.get(row["sha256"])
            if previous is None or previous[0] != key:
                clip = read_clip(path, row["sha256"])
                value = dict(clip_id=clip.clip_id, sha256=row["sha256"],
                             blink_frames=blink_frames(clip), records=len(clip.frames))
                self._verified[row["sha256"]] = key, value
                if len(self._verified) > 128:
                    self._verified.pop(next(iter(self._verified)))
            else:
                value = previous[1]
        return path, {**value, "author": row["player_name"], "target": {
            "kind": row["kind"], "entity_key": row["entity_key"], "strat": row["strategy"]}}

    def accept_verified(self, attempt, replay, *, player_id, player_name, clock, game_version, full_start=False):
        """Preserve and publish on a worker. Refusals leave the index untouched.

        `full_start` and ROM are the composition root's observed certificate,
        not values supplied by a remote request or private import manifest.
        """
        player_id, player_name = _player(player_id), checked_author(player_name)
        current = replay.attempt(attempt.id)
        before = _admission(current, clock, game_version, full_start)
        if _admission(attempt, clock, game_version, full_start) != before:
            raise ValueError("This attempt changed before population admission.")
        source, digest = _preserved_asset(replay, current)
        kind, key = entity_key_of(current)
        target = describe(kind, key, current.strat_tag, self._defs())
        document = {"player_id": player_id, "player_name": player_name, "target": target,
            "frames": before.frames, "clock": clock, "game_version": game_version,
            "sha256": digest, "recorded_utc": current.started_utc,
            "certified": True, "valid": True, "full_start": True,
            "certificate_source": getattr(replay, "certificate_source", "observed_runtime")}
        document["id"] = hashlib.sha256(canonical(document)).hexdigest()
        validate = lambda: _admission(replay.attempt(current.id), clock, game_version, full_start) == before
        return self._publish_record(document, source, validate)

    def _publish_record(self, document, source, validate):
        """Publish only after immutable bytes and the latest owner agree."""
        digest, target, kind = document["sha256"], document["target"], document["target"]["kind"]
        key = target["entity_key"]
        size = source.stat().st_size
        with self._lock, self._db:
            # Serialize capacity checks and publication across provider instances.
            self._db.execute("BEGIN IMMEDIATE")
            if self._db.execute("SELECT 1 FROM recordings WHERE id=?", (document["id"],)).fetchone():
                return self.get(document["id"])
            count = self._db.execute("SELECT COUNT(*) FROM recordings").fetchone()[0]
            used = self._db.execute("SELECT COALESCE(SUM(bytes),0) FROM "
                                   "(SELECT sha256,MAX(bytes) bytes FROM recordings GROUP BY sha256)").fetchone()[0]
            present = self._db.execute("SELECT 1 FROM recordings WHERE sha256=?", (digest,)).fetchone()
            if count >= MAX_RECORDINGS or used + (0 if present else size) > MAX_POPULATION_BYTES:
                raise ValueError("The local ghost population has reached its storage limit.")
            destination = self.root / "clips" / f"{digest}.ghost"
            destination.parent.mkdir(exist_ok=True)
            if not destination.resolve().is_relative_to(self.root):
                raise ValueError("This population destination is outside its folder.")
            created = self._publish_asset(source, destination, digest, validate)
            try:
                if not validate():
                    raise ValueError("This attempt changed during population preservation.")
                self._db.execute("INSERT INTO recordings VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (document["id"], document["player_id"], _normalize_player(document["player_name"]),
                     target.get("signature") or f"star:{key}", kind, document["clock"], document["game_version"],
                     document["frames"], digest, size, canonical(document).decode()))
            except BaseException:
                if created and not present:
                    destination.unlink(missing_ok=True)
                raise
        return self.get(document["id"])

    def _publish_asset(self, source, destination, digest, validate):
        if destination.exists():
            read_clip(destination, digest)
            return False
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
                temporary = Path(stream.name)
                with source.open("rb") as original:
                    shutil.copyfileobj(original, stream)
                stream.flush()
                os.fsync(stream.fileno())
            read_clip(temporary, digest)
            if not validate():
                raise ValueError("This attempt changed during population preservation.")
            os.replace(temporary, destination)
            return True
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

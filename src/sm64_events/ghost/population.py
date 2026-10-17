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

from sm64_events.ghost.clip import attempt_origin, blink_frames, read_clip, require_complete_inputs
from sm64_events.ghost.pb import local_attempt_source
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


def searched_versions(identity):
    """The recorded ROMs a search for this practice identity may return."""
    from sm64_events.ranks.region_scope import REGIONAL, servable_versions
    read = (lambda key, default=None: identity.get(key, default)) if isinstance(identity, dict) \
        else (lambda key, default=None: getattr(identity, key, default))
    return servable_versions(read("region_scope", REGIONAL), read("game_version"))


def _known(versions, game_version):
    """The ROMs among `versions` (default: `game_version` alone) a local row can have."""
    return tuple(dict.fromkeys(rom for rom in (versions if versions is not None else (game_version,))
                               if rom in ("us", "jp")))


@dataclass(frozen=True)
class Admission:
    """A worker's immutable projected owner, checked again after preservation."""
    owner: bytes
    frames: int


def _admission(attempt, clock, game_version, full_start):
    frames = _admissible(attempt, clock, game_version, full_start)
    return Admission(canonical({"id": attempt.id, "session": attempt.session_id,
        "target": entity_key_of(attempt), "strategy": attempt.strat_tag, "frames": frames,
        "ended": attempt.ended_utc, "source": local_attempt_source(attempt), **attempt_origin(attempt)}), frames)


def _playback_copy(replay, attempt, found):
    """The bytes a rival and a community offer carry, and their digest."""
    fill = getattr(replay, "native_with_inputs", None)
    path, _manifest = fill(attempt.id, found) if fill is not None else found
    source = Path(path)
    with source.open("rb") as stream:
        return source, hashlib.file_digest(stream, "sha256").hexdigest()


def committed_digest(replay, attempt):
    """The offered bytes' digest when this run's clip is already committed.

    Never preserves: a run with no committed clip was never offered, so it has
    nothing to retract. None when the clip is absent or no longer validates.
    """
    try:
        found = replay.find_native(attempt.id)
        return None if found is None else _playback_copy(replay, attempt, found)[1]
    except (OSError, ValueError, LookupError):
        return None


def _preserved_asset(replay, attempt):
    kept = replay.preserve_native(attempt.id)
    if not kept.get("path"):
        raise ValueError(kept.get("native_ghost_reason") or "This run has no preserved Mario recording.")
    found = replay.find_native(attempt.id)  # the existing strict owner/hash validator
    if found is None:
        raise ValueError("This run's Mario recording could not be validated.")
    source, digest = _playback_copy(replay, attempt, found)
    # His rule: a recording the input track could not complete never becomes
    # a rival here nor a community upload (ghost/clip.py::INCOMPLETE_INPUTS).
    require_complete_inputs(read_clip(source, digest))
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
            CREATE INDEX IF NOT EXISTS recordings_strategy ON recordings(target,clock,version,json_extract(document,'$.target.strat'),frames,id);
            CREATE INDEX IF NOT EXISTS recordings_strategy_player ON recordings(target,clock,version,json_extract(document,'$.target.strat'),normalized,frames,id);
            PRAGMA user_version=1;
        """)
        self._verified = {}
        self._generation_stamp = None
        self._generation_value = None

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
            stamp = self._db.execute("PRAGMA data_version").fetchone()[0]
            if stamp != self._generation_stamp:
                self._generation_value = tuple(self._db.execute("SELECT COUNT(*),MAX(rowid) FROM recordings").fetchone())
                self._generation_stamp = stamp
            return self._generation_value

    def _row(self, stored):
        row = json.loads(stored["document"])
        key, reason = bind(row["target"], self._defs())
        if reason:
            raise LookupError(reason)
        ghost = PortableGhost(row["id"], row["target"]["kind"], key,
                              row["target"]["strat"], row["frames"], row["player_name"])
        return {**row, "kind": ghost.kind, "entity_key": key, "strategy": ghost.strat_tag,
                "signature": row["target"].get("signature"), "ghost": ghost}

    def metadata_snapshot(self, kind, entity_key, *, clock, game_version, strategy=None, versions=None):
        """`versions` names the recorded ROMs to return (`region_scope.servable_versions`);
        omitted, only `game_version`'s. Every local row knows its ROM."""
        target = self._target(kind, entity_key)
        roms = _known(versions, game_version)
        if not roms:
            return ()
        scope = " AND json_extract(document,'$.target.strat')=?" if strategy is not None else ""
        parameters = (strategy,) if strategy is not None else ()
        with self._lock:
            rows = self._db.execute("SELECT document FROM recordings WHERE target=? AND clock=?"
                                    f" AND version IN ({','.join('?' * len(roms))})" + scope +
                                    " ORDER BY frames,id LIMIT ?",
                                    (target, clock, *roms, *parameters, MAX_RECORDINGS)).fetchall()
        return tuple(self._row(row) for row in rows)

    def snapshot_all(self):
        """Every bindable row across targets, bounded; unbindable rows are skipped."""
        with self._lock:
            rows = self._db.execute("SELECT document FROM recordings ORDER BY target,clock,version,frames,id LIMIT ?",
                                    (MAX_RECORDINGS,)).fetchall()
        result = []
        for row in rows:
            try:
                result.append(self._row(row))
            except (LookupError, ValueError):
                continue  # A disabled or ambiguous segment is left exactly as stored.
        return tuple(result)

    def rows_for_source(self, source):
        """This run's rows by its local lineage: session, attempt number and start.

        The full lineage, not the attempt number alone, because a fresh profile
        reuses attempt numbers (`ghost/pb.py::local_attempt_source`).
        """
        if not isinstance(source, dict):
            return ()
        with self._lock:
            rows = self._db.execute(
                "SELECT id,sha256,json_extract(document,'$.target.strat') AS strategy FROM recordings "
                "WHERE json_extract(document,'$.source.attempt_id')=? AND json_extract(document,'$.source.session_id')=? "
                "AND json_extract(document,'$.source.started_utc')=? ORDER BY id LIMIT ?",
                (source["attempt_id"], source["session_id"], source["started_utc"], MAX_RECORDINGS)).fetchall()
        return tuple(dict(row) for row in rows)

    def source_lineages(self):
        """Every local lineage some row was admitted from, as
        (session_id, attempt_id, started_utc); see `rows_for_source`."""
        with self._lock:
            rows = self._db.execute("SELECT DISTINCT json_extract(document,'$.source.session_id'),"
                                    "json_extract(document,'$.source.attempt_id'),"
                                    "json_extract(document,'$.source.started_utc') FROM recordings LIMIT ?",
                                    (MAX_RECORDINGS,)).fetchall()
        return {tuple(row) for row in rows if type(row[1]) is int}

    def remove(self, ids):
        """Delete rows; a clip goes only when no remaining row references its bytes.

        Same discipline as `_publish_record`: one IMMEDIATE transaction under
        the lock. A doomed clip is renamed inside it and restored on rollback,
        so a row never outlives its bytes and committed removal leaves no clip.
        """
        ids = tuple(dict.fromkeys(checked_hash(identity) for identity in ids))
        if not ids:
            return 0
        marks = ",".join("?" * len(ids))
        staged = []
        with self._lock:
            try:
                with self._db:
                    self._db.execute("BEGIN IMMEDIATE")
                    digests = sorted({row[0] for row in self._db.execute(
                        f"SELECT sha256 FROM recordings WHERE id IN ({marks})", ids)})
                    removed = self._db.execute(f"DELETE FROM recordings WHERE id IN ({marks})", ids).rowcount
                    for digest in digests:
                        if self._db.execute("SELECT 1 FROM recordings WHERE sha256=?", (digest,)).fetchone():
                            continue
                        clip = self.root / "clips" / f"{checked_hash(digest)}.ghost"
                        if clip.exists():
                            doomed = clip.with_name(clip.name + ".removing")
                            os.replace(clip, doomed)
                            staged.append((doomed, clip))
                    self._generation_stamp = None
            except BaseException:
                for doomed, clip in staged:
                    os.replace(doomed, clip)
                raise
            for doomed, _clip in staged:
                doomed.unlink(missing_ok=True)
            for digest in digests:
                self._verified.pop(digest, None)
        return removed

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
        strategy = identity.get("strategy") if isinstance(identity, dict) else getattr(identity,"strategy",None)
        matching_scope = identity.get("scope","strategy") if isinstance(identity,dict) else getattr(identity,"scope","strategy")
        roms = _known(searched_versions(identity), version)
        if not roms:
            return {"records": [], "next_cursor": None}
        if matching_scope=="overall":
            strategy = None
        scope = " AND json_extract(document,'$.target.strat')=?" if strategy is not None else ""
        parameters = (strategy,) if strategy is not None else ()
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
                f"WHERE target=? AND clock=? AND version IN ({','.join('?' * len(roms))})" + scope +
                " AND normalized>=? AND normalized<?" + where +
                " ORDER BY normalized,frames,id LIMIT ?",
                (target, clock, *roms, *parameters, prefix, prefix + chr(0x10ffff), *after, limit + 1)).fetchall()
        next_cursor = ([rows[limit - 1][key] for key in ("normalized", "frames", "id")]
                       if len(rows) > limit else None)
        return {"records": [self._row(row) for row in rows[:limit]], "next_cursor": next_cursor}

    def resolve(self, ghost, target):
        row = self.get(ghost.id)
        if (target is None or (row["kind"], row["entity_key"]) != (target["kind"], target["entity_key"])
                or target.get("scope","strategy")=="strategy" and (row["strategy"] or "Standard") != (target.get("strat") or "Standard")):
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
            "source": local_attempt_source(current),
            "certified": True, "valid": True, "full_start": True,
            "certificate_source": getattr(replay, "certificate_source", "observed_runtime")}
        document["id"] = hashlib.sha256(canonical(document)).hexdigest()
        validate = lambda: _admission(replay.attempt(current.id), clock, game_version, full_start) == before
        return self._publish_record(document, source, validate)

    def qualified_source(self, attempt, replay, *, clock, game_version, full_start=False):
        """Offer committed native bytes independently of local rank-bin retention."""
        current = replay.attempt(attempt.id)
        before = _admission(current, clock, game_version, full_start)
        if _admission(attempt, clock, game_version, full_start) != before:
            raise ValueError('This attempt changed before community admission.')
        source, digest = _preserved_asset(replay, current)
        if _admission(replay.attempt(current.id), clock, game_version, full_start) != before:
            raise ValueError('This attempt changed during community admission.')
        kind, key = entity_key_of(current)
        return {'target': describe(kind, key, current.strat_tag, self._defs()),
                'clock': clock, 'game_version': game_version, 'strategy': current.strat_tag,
                'frames': before.frames, 'path': str(source), 'raw_sha256': digest,
                'full_start': True, 'valid': True}

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
                self._generation_stamp = None
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

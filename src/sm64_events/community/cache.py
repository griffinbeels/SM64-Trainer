"""A bounded, synchronous community view layered over the local population."""
from collections import OrderedDict
import hashlib
import os
from pathlib import Path
import threading
import time
import unicodedata
from uuid import UUID, uuid4

from sm64_events.ghost.clip import INCOMPLETE_INPUTS, blink_frames, decode_clip, inputs_complete
from sm64_events.ghost.portable import checked_author, checked_hash
from sm64_events.ghost.sharing import PortableGhost
from sm64_events.ghost.sharing_targets import bind, checked_target, describe

from . import codec
from .config import CommunityError

MAX_METADATA = 1024
MAX_POOLS = 32
CACHE_ENCODED = 16 << 20
CACHE_RAW = 32 << 20
TTL = 600  # Publication leases expire after ten minutes even when the network is down.


def value_of(identity, name, default=None):
    return identity.get(name, default) if isinstance(identity, dict) else getattr(identity, name, default)


def target_key(target):
    return (target["kind"], target.get("signature") if target["kind"] == "segment" else target["entity_key"])


def checked_metadata(value):
    fields = {"id", "player_id", "player_name", "target", "strategy", "clock", "game_version", "frames",
              "raw_sha256", "raw_bytes", "encoded_sha256", "encoded_bytes", "codec", "generation", "step",
              "provenance", "structurally_valid", "full_start"}
    if not isinstance(value, dict) or set(value) != fields:
        raise CommunityError("invalid_metadata")
    result = dict(value)
    try:
        for field in ("id", "raw_sha256", "encoded_sha256"):
            checked_hash(value[field])
        if str(UUID(value["player_id"])) != value["player_id"]:
            raise ValueError()
        checked_author(value["player_name"])
        result["target"] = checked_target(value["target"])
    except (ValueError, TypeError, AttributeError):
        raise CommunityError("invalid_metadata") from None
    if (value["clock"] not in ("igt", "rta") or value["game_version"] not in ("us", "jp")
            or not isinstance(value["strategy"], str) or not 1 <= len(value["strategy"].encode("utf8")) <= 256
            or any(ord(c) < 32 for c in value["strategy"])
            or value["target"].get("strat") not in (None, value["strategy"])
            or value["codec"] != "ghc1" or value["provenance"] != "community-unverified"
            or value["full_start"] is not True or value["structurally_valid"] is not True
            or any(type(value[field]) is not int or not 1 <= value[field] <= bound for field, bound in
                   (("frames", 30 * 60 * 60 * 24), ("raw_bytes", codec.MAX_RAW), ("encoded_bytes", codec.MAX_ENCODED)))
            or type(value["step"]) is not int or not 0 <= value["step"] <= 1000
            or not isinstance(value["generation"], str) or not 1 <= len(value["generation"]) <= 128):
        raise CommunityError("invalid_metadata")
    return result


class CachedPopulation:
    def __init__(self, local, *, root, account=None, definitions=None):
        self.local, self.account = local, account
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._definitions = definitions
        self._lock = threading.RLock()
        self._rows, self._assets, self._pools, self._searches = (OrderedDict() for _ in range(4))
        self._generation = 0
        self._owner = None
        self._closed = False
        # The index is deliberately memory-only. Reopening requires publication
        # gates again; stale private files cannot become an offline public roster.
        for suffix in (".ghost", ".ghc1", ".part"):
            for path in self.root.glob("*" + suffix):
                try:
                    pieces = path.stem.split('.')
                    checked_hash(pieces[0])
                    if len(pieces) > 1 and (suffix != '.part' or len(pieces) != 2 or UUID(pieces[1]).hex != pieces[1]):
                        raise ValueError()
                except (ValueError, AttributeError):
                    continue
                if path.is_file() and not path.is_symlink():
                    path.unlink(missing_ok=True)

    def _defs(self):
        return (self._definitions() if callable(self._definitions) else self._definitions) or ()

    def portable_target(self, identity):
        provided = value_of(identity, "target")
        if provided is not None:
            return checked_target(provided)
        return describe(value_of(identity, "kind"), value_of(identity, "entity_key"),
                        value_of(identity, "strategy"), self._defs())

    def identity_key(self, identity):
        target = self.portable_target(identity)
        clock, rom, scope = value_of(identity, "clock"), value_of(identity, "game_version"), value_of(identity, "scope", "strategy")
        strategy = value_of(identity, "strategy") if scope == "strategy" else None
        if clock not in ("igt", "rta") or rom not in ("us", "jp") or scope not in ("strategy", "overall") or scope == "strategy" and not strategy:
            raise CommunityError("unsupported_identity")
        return (*target_key(target), clock, rom, scope, strategy)

    def _active(self):
        snapshot = self.account.snapshot() if self.account else {}
        owner = snapshot.get("uid") if snapshot.get("authenticated") and snapshot.get("status") == "active" else None
        if owner != self._owner:
            self._owner = owner
            self._pools.clear()
            self._searches.clear()
            self._rows.clear()
            self._generation += 1
        return owner is not None and not self._closed

    @property
    def generation(self):
        with self._lock:
            self._active()
            return self.local.generation, self._generation, int(time.monotonic() // TTL)

    def owned_player_ids(self, local_id, *, names=()):
        """The signed-in account's uid, plus whatever the wrapped layers own."""
        snapshot = self.account.snapshot() if self.account else {}
        uid = snapshot.get("uid") if snapshot.get("authenticated") else None
        method = getattr(self.local, "owned_player_ids", None)
        inner = method(local_id, names=names) if method else ()
        return frozenset(value for value in (local_id, uid, *inner) if value)

    def additional_owned_player_ids(self, local_id):
        return self.owned_player_ids(local_id)

    def _row(self, value):
        key, reason = bind(value["target"], self._defs())
        if reason:
            raise LookupError(reason)
        ghost = PortableGhost(value["id"], value["target"]["kind"], key, value["strategy"], value["frames"], value["player_name"])
        return {**value, "kind": ghost.kind, "entity_key": key, "strategy": ghost.strat_tag,
                "signature": value["target"].get("signature"), "sha256": value["raw_sha256"],
                "valid": True, "ghost": ghost}

    def _ready(self, identity):
        stored = self._rows.get(identity)
        asset = self._assets.get(identity)
        return bool(stored and stored[1] > time.monotonic() and asset
                    and (stored[0]["raw_sha256"], stored[0]["encoded_sha256"])
                    == (asset["raw_sha256"], asset["encoded_sha256"]))

    def metadata_snapshot(self, kind, entity_key, *, clock, game_version, strategy=None, versions=None):
        """`versions` names the recorded ROMs to return (`region_scope.servable_versions`);
        omitted, only `game_version`'s. Every community row knows its ROM."""
        local = self.local.metadata_snapshot(kind, entity_key, clock=clock, game_version=game_version,
                                             strategy=strategy, versions=versions)
        roms = (game_version,) if versions is None else versions
        with self._lock:
            if not self._active():
                return local
            rows = []
            for metadata, expiry in self._rows.values():
                if expiry <= time.monotonic():
                    continue
                try:
                    row = self._row(metadata)
                except LookupError:
                    continue
                if ((row["kind"], row["entity_key"], row["clock"]) == (kind, entity_key, clock)
                        and row["game_version"] in roms and (strategy is None or row["strategy"] == strategy)):
                    rows.append(row)
            return tuple(local) + tuple(rows)

    def get(self, identity):
        checked_hash(identity)
        with self._lock:
            if self._active() and identity in self._rows and self._rows[identity][1] > time.monotonic():
                return self._row(self._rows[identity][0])
        return self.local.get(identity)

    def is_ready(self, identity):
        with self._lock:
            if self._active() and identity in self._rows:
                if not self._ready(identity):
                    return False
                path = self.root / (identity + ".ghost")
                try:
                    stamp = path.stat()
                    return not path.is_symlink() and (stamp.st_size, stamp.st_mtime_ns, stamp.st_ctime_ns) == self._assets[identity]["stamp"]
                except OSError:
                    return False
        try:
            self.local.get(identity)
            return True
        except (LookupError, ValueError):
            return False

    def search(self, identity, prefix, *, cursor=None, limit=20):
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("Choose a search page of 1 to 20 records.")
        if isinstance(cursor, list):
            local_cursor = None if cursor == ["community-local-start"] else cursor
            return self.local.search(identity, prefix, cursor=local_cursor, limit=limit)
        key = (self.identity_key(identity), self._prefix(prefix), cursor)
        with self._lock:
            found = self._searches.get(key) if self._active() else None
            if found and found[1] > time.monotonic():
                remote = [self._row(row) for row in found[0]["recordings"][:limit]]
                coverage = dict(found[0]["coverage"])
                remote_cursor = found[0]["next_cursor"]
                if remote_cursor is not None:
                    # Finish remote pages once, then continue the local index.
                    return {"records": remote, "next_cursor": remote_cursor, "coverage": coverage}
                if len(remote) == limit:
                    local = self.local.search(identity, prefix, limit=1)
                    return {"records": remote, "coverage": coverage,
                            "next_cursor": ["community-local-start"] if local["records"] else None}
                local = self.local.search(identity, prefix, limit=limit - len(remote))
                seen = {row["id"] for row in remote}
                records = remote + [row for row in local["records"] if row["id"] not in seen]
                return {"records": records, "next_cursor": local.get("next_cursor"), "coverage": coverage}
        # Remote cursors never enter the local SQLite cursor contract.
        return self.local.search(identity, prefix, cursor=cursor if isinstance(cursor, list) else None, limit=limit)

    @staticmethod
    def _prefix(prefix):
        if not isinstance(prefix, str) or len(prefix.encode("utf8")) > 128 or any(ord(c) < 32 for c in prefix):
            raise CommunityError("invalid_prefix")
        return unicodedata.normalize("NFKC", prefix).casefold().strip()

    def coverage(self, identity):
        with self._lock:
            item = self._pools.get(self.identity_key(identity)) if self._active() else None
            if not item or item[1] <= time.monotonic():
                return {"complete": False, "min_step": None, "max_step": None, "base_step": None, "generation": None}
            result = dict(item[0]["coverage"])
            result["complete"] = result["complete"] and all(
                row['id'] in self._rows and self._rows[row['id']][1] > time.monotonic()
                for row in item[0]['recordings'])
            return result

    def remember_page(self, identity, page, *, prefix=None, cursor=None):
        with self._lock:
            if not self._active():
                return
            expires = time.monotonic() + TTL
            for row in page["recordings"]:
                self._rows[row["id"]] = row, expires
                self._rows.move_to_end(row["id"])
            while len(self._rows) > MAX_METADATA:
                self._rows.popitem(last=False)
            mapping = self._pools if prefix is None else self._searches
            key = self.identity_key(identity) if prefix is None else (self.identity_key(identity), self._prefix(prefix), cursor)
            mapping[key] = page, expires
            mapping.move_to_end(key)
            while len(mapping) > MAX_POOLS:
                mapping.popitem(last=False)
            self._generation += 1

    def renew_metadata(self, row):
        row = checked_metadata(row)
        with self._lock:
            if self._active():
                self._rows[row["id"]] = row, time.monotonic() + TTL
                self._rows.move_to_end(row["id"])
                while len(self._rows) > MAX_METADATA:
                    self._rows.popitem(last=False)
                self._generation += 1

    def store_asset(self, row, encoded, raw, *, owner=None):
        """Prepare on the owned IO worker; lock only the final cache publication."""
        row = checked_metadata(row)
        if (len(raw) != row["raw_bytes"] or len(encoded) != row["encoded_bytes"]
                or hashlib.sha256(raw).hexdigest() != row["raw_sha256"]
                or hashlib.sha256(encoded).hexdigest() != row["encoded_sha256"]):
            raise CommunityError("asset_mismatch")
        clip = decode_clip(raw)
        if not inputs_complete(clip):
            # His rule: a shared recording with any unknown pad never enters
            # this cache, whatever the service accepted (ghost/clip.py).
            raise CommunityError("incomplete_inputs", INCOMPLETE_INPUTS)
        blinks = blink_frames(clip)
        expected_owner = owner or (self.account.snapshot().get('uid') if self.account else None)
        staged = []
        try:
            for suffix, content in ((".ghc1", encoded), (".ghost", raw)):
                temporary = self.root / (row['id'] + '.' + uuid4().hex + '.part')
                staged.append((temporary, self.root / (row['id'] + suffix)))
                with temporary.open('xb') as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
            self._publish_asset(row, clip, blinks, staged, expected_owner)
        finally:
            for temporary, _ in staged:
                temporary.unlink(missing_ok=True)

    def _publish_asset(self, row, clip, blinks, staged, owner):
        with self._lock:
            if not self._active() or self._owner != owner:
                return
            identifier = row["id"]
            for temporary, destination in staged:
                if destination.is_symlink():
                    raise CommunityError("unsafe_cache")
                os.replace(temporary, destination)
            path = self.root / (identifier + ".ghost")
            stamp = path.stat()
            self._assets[identifier] = {"raw": row['raw_bytes'], "encoded": row['encoded_bytes'], "stamp": (stamp.st_size, stamp.st_mtime_ns, stamp.st_ctime_ns),
                "raw_sha256": row["raw_sha256"], "encoded_sha256": row["encoded_sha256"],
                "clip_id": clip.clip_id, "records": len(clip.frames), "blink_frames": blinks}
            self._assets.move_to_end(identifier)
            self._rows[identifier] = row, time.monotonic() + TTL
            while (sum(item["raw"] for item in self._assets.values()) > CACHE_RAW or
                   sum(item["encoded"] for item in self._assets.values()) > CACHE_ENCODED):
                oldest, _ = self._assets.popitem(last=False)
                for suffix in (".ghost", ".ghc1"):
                    (self.root / (oldest + suffix)).unlink(missing_ok=True)
            self._generation += 1

    def resolve(self, ghost, target):
        with self._lock:
            if not self._active() or ghost.id not in self._rows:
                return self.local.resolve(ghost, target)
            if not self._ready(ghost.id):
                raise LookupError("This community ghost is not downloaded or its visibility needs refreshing.")
            row, asset = self._row(self._rows[ghost.id][0]), self._assets[ghost.id]
            if (target is None or (row["kind"], row["entity_key"]) != (target["kind"], target["entity_key"])
                    or target.get("scope", "strategy") == "strategy" and (target.get("strat") or "Standard") != row["strategy"]):
                raise ValueError("This community ghost belongs to another practice target.")
            path = self.root / (ghost.id + ".ghost")
            if path.is_symlink() or not path.resolve().is_relative_to(self.root):
                raise CommunityError("unsafe_cache")
            stamp = path.stat()
            if (stamp.st_size, stamp.st_mtime_ns, stamp.st_ctime_ns) != asset["stamp"]:
                self._assets.pop(ghost.id, None)
                self._generation += 1
                raise LookupError("This community ghost changed after validation.")
            self._assets.move_to_end(ghost.id)
            return path, {"clip_id": asset["clip_id"], "sha256": row["raw_sha256"], "records": asset["records"],
                          "blink_frames": asset["blink_frames"], "author": row["player_name"],
                          "target": {"kind": row["kind"], "entity_key": row["entity_key"], "strat": row["strategy"]}}

    def accept_verified(self, *args, **kwargs):
        return self.local.accept_verified(*args, **kwargs)

    def qualified_source(self, *args, **kwargs):
        return self.local.qualified_source(*args, **kwargs)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._rows.clear()
            self._pools.clear()
            self._searches.clear()
        self.local.close()

"""A bounded, synchronous community view layered over the local population."""
from collections import OrderedDict
import hashlib
import os
from pathlib import Path
import threading
import time
import unicodedata
from uuid import UUID, uuid4

from sm64_events.ghost.clip import blink_frames
from sm64_events.ghost.portable import checked_author, checked_hash
from sm64_events.ghost.sharing import PortableGhost
from sm64_events.ghost.sharing_targets import bind, binding_index, checked_target, describe

from . import claims, codec
from .config import CommunityError
from .names import incoming_name

MAX_METADATA = 1024
"""Rows outside any rival list: search pages, chosen-rival renewals, downloads."""
MAX_POOLS = 16
"""Rival lists held at once, each owning its rows (at most 4 pages of 256), so a
list for one strategy never evicts another's. Least recently stored goes first."""
CACHE_ENCODED = 16 << 20
CACHE_RAW = 32 << 20
TTL = 600  # Publication leases expire after ten minutes even when the network is down.
LEASE_RENEW_S = 60
"""A held list or chosen-rival row renews this long before its lease lapses."""


def value_of(identity, name, default=None):
    return identity.get(name, default) if isinstance(identity, dict) else getattr(identity, name, default)


def target_key(target):
    return (target["kind"], target.get("signature") if target["kind"] == "segment" else target["entity_key"])


def account_owner(snapshot):
    """The signed-in account whose cloud view this is, or None.

    A refresh token that failed to rotate (a network blip at expiry) leaves
    the profile and the stored token: still this account, so nothing it
    holds is wiped. Signing out or another account changes the owner.
    """
    if snapshot.get("status") != "active" or not (snapshot.get("authenticated") or snapshot.get("stored")):
        return None
    return snapshot.get("uid")


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
        result["target"] = checked_target(value["target"])
    except (ValueError, TypeError, AttributeError):
        raise CommunityError("invalid_metadata") from None
    try:
        result["player_name"] = incoming_name(checked_author(value["player_name"]))
    except ValueError:
        raise CommunityError("invalid_name") from None
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
        self._kept = frozenset()
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
        kind = value_of(identity, "kind")
        # Only a segment needs the definitions; a star never reads the database.
        return describe(kind, value_of(identity, "entity_key"), value_of(identity, "strategy"),
                        self._defs() if kind == "segment" else ())

    def _target_key(self, identity):
        signature = value_of(identity, "signature")
        if value_of(identity, "target") is None and value_of(identity, "kind") == "segment" and signature is not None:
            # A practice identity already carries its definition's signature.
            return target_key(checked_target({"kind": "segment", "entity_key": value_of(identity, "entity_key"),
                                              "signature": signature}))
        return target_key(self.portable_target(identity))

    def identity_key(self, identity):
        clock, rom, scope = value_of(identity, "clock"), value_of(identity, "game_version"), value_of(identity, "scope", "strategy")
        strategy = value_of(identity, "strategy") if scope == "strategy" else None
        if clock not in ("igt", "rta") or rom not in ("us", "jp") or scope not in ("strategy", "overall") or scope == "strategy" and not strategy:
            raise CommunityError("unsupported_identity")
        return (*self._target_key(identity), clock, rom, scope, strategy)

    def _active(self):
        owner = account_owner(self.account.snapshot()) if self.account else None
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
        uid = snapshot.get("uid") if snapshot.get("authenticated") or snapshot.get("stored") else None
        method = getattr(self.local, "owned_player_ids", None)
        inner = method(local_id, names=names) if method else ()
        return frozenset(value for value in (local_id, uid, *inner) if value)

    def additional_owned_player_ids(self, local_id):
        return self.owned_player_ids(local_id)

    def _row(self, value, index=None):
        if value["target"]["kind"] == "segment" and index is None:
            index = binding_index(self._defs())
        key, reason = bind(value["target"], index=index or {})
        if reason:
            raise LookupError(reason)
        ghost = PortableGhost(value["id"], value["target"]["kind"], key, value["strategy"], value["frames"], value["player_name"])
        return {**value, "kind": ghost.kind, "entity_key": key, "strategy": ghost.strat_tag,
                "signature": value["target"].get("signature"), "sha256": value["raw_sha256"],
                "valid": True, "ghost": ghost}

    def _entry(self, identifier):
        """The freshest lease on one recording: a loose row or any held list's."""
        found = self._rows.get(identifier)
        for page, expiry in self._pools.values():
            row = page["by_id"].get(identifier)
            if row is not None and (found is None or expiry > found[1]):
                found = row, expiry
        return found

    def _ready(self, identity):
        stored = self._entry(identity)
        asset = self._assets.get(identity)
        return bool(stored and stored[1] > time.monotonic() and asset
                    and (stored[0]["raw_sha256"], stored[0]["encoded_sha256"])
                    == (asset["raw_sha256"], asset["encoded_sha256"]))

    def metadata_snapshot(self, kind, entity_key, *, clock, game_version, strategy=None, versions=None):
        """Local rows, then this lineup's own rival list.

        A strategy reads only that strategy's list; None reads only the Overall
        list. Another identity's list, search results and other loose rows
        never become candidates; a chosen rival is looked up by `get`.
        """
        local = self.local.metadata_snapshot(kind, entity_key, clock=clock, game_version=game_version, strategy=strategy, versions=versions)
        roms = (game_version,) if versions is None else versions
        with self._lock:
            if not self._active():
                return local
            now = time.monotonic()
            scope = ("strategy", strategy) if strategy is not None else ("overall", None)
            lists = [(row, expiry) for key, (page, expiry) in self._pools.items()
                     if key[0] == kind and key[2] == clock and key[3] in roms and key[4:] == scope
                     for row in page["recordings"]]
            rows, seen, index = [], set(), None
            for metadata, expiry in lists:
                if expiry <= now or metadata["id"] in seen:
                    continue
                if metadata["target"]["kind"] == "segment" and index is None:
                    index = binding_index(self._defs())
                try:
                    row = self._row(metadata, index)
                except LookupError:
                    continue
                if ((row["kind"], row["entity_key"], row["clock"]) == (kind, entity_key, clock)
                        and row["game_version"] in roms and (strategy is None or row["strategy"] == strategy)):
                    seen.add(row["id"])
                    rows.append(row)
            return tuple(local) + tuple(rows)

    def get(self, identity):
        checked_hash(identity)
        with self._lock:
            entry = self._entry(identity) if self._active() else None
            if entry is not None and entry[1] > time.monotonic():
                return self._row(entry[0])
        return self.local.get(identity)

    def is_ready(self, identity):
        with self._lock:
            if self._active() and self._entry(identity) is not None:
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
        """The held list's coverage. Its rows share its lease, so a held
        complete list stays complete until the lease lapses."""
        with self._lock:
            item = self._pools.get(self.identity_key(identity)) if self._active() else None
            if not item or item[1] <= time.monotonic():
                return {"complete": False, "min_step": None, "max_step": None, "base_step": None, "generation": None}
            return dict(item[0]["coverage"])

    def keep(self, identifiers):
        """The recordings the lineup on screen uses: a download never evicts them."""
        with self._lock:
            self._kept = frozenset(identifiers)

    def lease_until(self, identity=None, *, recording=None):
        """When a held list (or one recording's freshest row) lapses; None when absent."""
        with self._lock:
            if not self._active():
                return None
            if recording is not None:
                entry = self._entry(recording)
                return entry[1] if entry else None
            item = self._pools.get(self.identity_key(identity))
            return item[1] if item else None

    def _trim_rows(self):
        while len(self._rows) > MAX_METADATA:
            self._rows.popitem(last=False)

    def remember_page(self, identity, page, *, prefix=None, cursor=None):
        with self._lock:
            if not self._active():
                return
            expires = time.monotonic() + TTL
            key = self.identity_key(identity)
            if prefix is None:
                # The list owns its rows: no other list or search can evict them.
                self._pools[key] = {**page, "by_id": {row["id"]: row for row in page["recordings"]}}, expires
                self._pools.move_to_end(key)
                while len(self._pools) > MAX_POOLS:
                    self._pools.popitem(last=False)
            else:
                for row in page["recordings"]:
                    self._rows[row["id"]] = row, expires
                    self._rows.move_to_end(row["id"])
                self._trim_rows()
                key = key, self._prefix(prefix), cursor
                self._searches[key] = page, expires
                self._searches.move_to_end(key)
                while len(self._searches) > MAX_POOLS:
                    self._searches.popitem(last=False)
            self._generation += 1

    def renew_metadata(self, row):
        row = checked_metadata(row)
        with self._lock:
            if self._active():
                self._rows[row["id"]] = row, time.monotonic() + TTL
                self._rows.move_to_end(row["id"])
                self._trim_rows()
                self._generation += 1

    def store_asset(self, row, encoded, raw, *, owner=None):
        """Prepare on the owned IO worker; lock only the final cache publication."""
        row = checked_metadata(row)
        if (len(raw) != row["raw_bytes"] or len(encoded) != row["encoded_bytes"]
                or hashlib.sha256(raw).hexdigest() != row["raw_sha256"]
                or hashlib.sha256(encoded).hexdigest() != row["encoded_sha256"]):
            raise CommunityError("asset_mismatch")
        # His rule: a shared recording whose ROM, inputs or time its own bytes
        # do not bear out never enters this cache, whatever the service
        # accepted (community/claims.py).
        clip = claims.check_claims(raw, game_version=row["game_version"], clock=row["clock"], frames=row["frames"])
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
            self._rows.move_to_end(identifier)
            self._trim_rows()
            while (sum(item["raw"] for item in self._assets.values()) > CACHE_RAW or
                   sum(item["encoded"] for item in self._assets.values()) > CACHE_ENCODED):
                # Oldest first, never a recording the lineup on screen uses (at
                # most four seats, so the cap can be exceeded by that bound only).
                oldest = next((key for key in self._assets if key not in self._kept and key != identifier), None)
                if oldest is None:
                    break
                self._assets.pop(oldest)
                for suffix in (".ghost", ".ghc1"):
                    (self.root / (oldest + suffix)).unlink(missing_ok=True)
            self._generation += 1

    def resolve(self, ghost, target):
        with self._lock:
            entry = self._entry(ghost.id) if self._active() else None
            if entry is None:
                return self.local.resolve(ghost, target)
            if not self._ready(ghost.id):
                raise LookupError("This community ghost is not downloaded or its visibility needs refreshing.")
            row, asset = self._row(entry[0]), self._assets[ghost.id]
            if (target is None or (row["kind"], row["entity_key"]) != (target["kind"], target["entity_key"])
                    or target.get("scope", "strategy") == "strategy" and target.get("strat") != row["strategy"]):
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

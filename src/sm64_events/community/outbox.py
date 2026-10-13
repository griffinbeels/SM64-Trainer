"""Bounded metadata for previously consented native contributions, never clips.

Only the admission worker remembers a qualified source and its current account
UID. Recovery never assigns historical, imported or packaged runs to a login.
Deleted/changed source bytes, capacity eviction and explicit cancellation can
retire an intent; this file is not a second recording archive.

A reclassified run also leaves a durable retraction: its owner, the clip hash
and the one strategy to keep. It survives a restart until the server answers,
so a ghost never stays shared under the strategy it no longer belongs to.
"""
from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import threading
from uuid import UUID, uuid4

from sm64_events.ghost.portable import checked_hash
from sm64_events.ghost.sharing_targets import canonical, checked_target
from .config import CommunityError

MAX_OFFERS = 256
MAX_RETRACTIONS = 256
MAX_BYTES = 1 << 20
SOURCE_FIELDS = {"target", "clock", "game_version", "strategy", "frames", "path", "raw_sha256", "full_start", "valid"}
STATE_FIELDS = {"mutation_keys", "mutation_generation"}


def checked_source(value):
    if (not isinstance(value, dict) or set(value) - (SOURCE_FIELDS | STATE_FIELDS | {"owner"})
            or not SOURCE_FIELDS.issubset(value) or value.get("full_start") is not True
            or value.get("valid") is not True or value.get("clock") not in ("igt", "rta")
            or value.get("game_version") not in ("us", "jp")
            or type(value.get("frames")) is not int or not 1 <= value["frames"] <= 2592000
            or not isinstance(value.get("strategy"), str) or not 1 <= len(value["strategy"].encode()) <= 256
            or any(ord(char) < 32 for char in value["strategy"])
            or not isinstance(value.get("path"), str) or not 1 <= len(value["path"]) <= 4096
            or not Path(value["path"]).is_absolute()):
        raise CommunityError("invalid_source")
    try:
        checked_hash(value["raw_sha256"])
        if str(UUID(value["owner"])) != value["owner"]:
            raise ValueError()
        target = checked_target(value["target"])
        if target.get("strat") not in (None, value["strategy"]):
            raise ValueError()
        keys = value.get("mutation_keys", {})
        if not isinstance(keys, dict) or set(keys) - {"grant", "publish"}:
            raise ValueError()
        if any(str(UUID(key)) != key for key in keys.values()):
            raise ValueError()
    except (ValueError, TypeError, AttributeError, KeyError):
        raise CommunityError("invalid_source") from None
    generation = value.get("mutation_generation")
    if generation is not None and (not isinstance(generation, str) or not 1 <= len(generation) <= 128):
        raise CommunityError("invalid_source")
    return {**deepcopy(value), "target": target}


def checked_strategy(value):
    if (not isinstance(value, str) or not 1 <= len(value.encode()) <= 256
            or any(ord(char) < 32 for char in value)):
        raise CommunityError("invalid_source")
    return value


def checked_retraction(value):
    try:
        if not isinstance(value, dict) or set(value) != {"owner", "raw_sha256", "keep_strategy", "key"}:
            raise ValueError()
        checked_hash(value["raw_sha256"])
        if value["keep_strategy"] is not None:
            checked_strategy(value["keep_strategy"])
        if str(UUID(value["owner"])) != value["owner"] or str(UUID(value["key"])) != value["key"]:
            raise ValueError()
    except (ValueError, TypeError, AttributeError, CommunityError):
        raise CommunityError("invalid_retraction") from None
    return dict(value)


def retraction_key(retraction):
    """One pending retraction per owner and clip; the latest keep wins."""
    return ("retract", retraction["owner"], retraction["raw_sha256"])


def _read_document(path, field, limit):
    """Bounded read of `{"v": 1, field: [...]}`; ValueError when unusable."""
    if path.is_symlink() or path.stat().st_size > MAX_BYTES:
        raise ValueError()
    with path.open("rb") as stream:
        encoded = stream.read(MAX_BYTES + 1)
    if len(encoded) > MAX_BYTES:
        raise ValueError()
    document = json.loads(encoded)
    if (not isinstance(document, dict) or set(document) != {"v", field} or type(document["v"]) is not int
            or document["v"] != 1 or not isinstance(document[field], list) or len(document[field]) > limit):
        raise ValueError()
    return document[field]


def _replace(path, encoded):
    if len(encoded) > MAX_BYTES or path.is_symlink():
        raise CommunityError("outbox_unavailable")
    temporary = path.with_name(path.stem + "-" + uuid4().hex + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def source_key(source):
    return ("offer", source["owner"], canonical(source["target"]), source["clock"], source["game_version"],
            source["strategy"], source["frames"], source["raw_sha256"])


def source_id(source):
    return hashlib.sha256(canonical({key: source[key] for key in ("owner", "target", "clock", "game_version",
                                                              "strategy", "frames", "raw_sha256")})).hexdigest()


class SourceOutbox:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._rows = OrderedDict()
        self.load_error = False
        self._loaded = False

    def load(self):
        """Read bounded metadata once on the IO worker, after normal startup."""
        with self._lock:
            if self._loaded:
                return
            self._loaded = True
            if not self.path.exists():
                return
            try:
                rows = [checked_source(row) for row in _read_document(self.path, "offers", MAX_OFFERS)]
            except (OSError, ValueError, UnicodeError, RecursionError):
                self.load_error = True
                return
            for row in rows:
                self._rows.setdefault(source_id(row), row)

    def _write(self):
        encoded = canonical({"v": 1, "offers": list(self._rows.values())})
        while len(encoded) > MAX_BYTES and len(self._rows) > 1:
            self._rows.popitem(last=False)
            encoded = canonical({"v": 1, "offers": list(self._rows.values())})
        _replace(self.path, encoded)

    def remember(self, source):
        source = checked_source(source)
        key = source_id(source)
        with self._lock:
            self.load()
            previous = self._rows.get(key, {})
            self._rows[key] = {**previous, **source}
            self._rows.move_to_end(key)
            while len(self._rows) > MAX_OFFERS:
                self._rows.popitem(last=False)
            self._write()

    def contains(self, source):
        with self._lock:
            return source_id(source) in self._rows

    def update(self, source):
        """Persist retry keys only for an already qualified contribution."""
        value = {key: item for key, item in source.items() if key in SOURCE_FIELDS | STATE_FIELDS | {"owner"}}
        with self._lock:
            if source_id(value) in self._rows:
                self.remember(value)

    def pending(self, owner):
        with self._lock:
            return tuple(deepcopy(row) for row in self._rows.values() if row["owner"] == owner)

    def discard(self, sources=(), *, owner=None):
        identities = {source_id(source) for source in sources}
        with self._lock:
            self.load()
            keys = [key for key, row in self._rows.items() if key in identities or owner is not None and row["owner"] == owner]
            for key in keys:
                self._rows.pop(key)
            if keys:
                self._write()

    def clear(self):
        """Explicit logout without a known profile cancels every local intent."""
        with self._lock:
            self.load()
            self._rows.clear()
            self._write()

    def retire(self, raw_sha256, keep_strategy):
        """Drop intents for these bytes under any strategy but `keep_strategy`."""
        with self._lock:
            self.load()
            keys = [key for key, row in self._rows.items()
                    if row["raw_sha256"] == raw_sha256 and row["strategy"] != keep_strategy]
            for key in keys:
                self._rows.pop(key)
            if keys:
                self._write()
            return len(keys)

    def available(self, owner):
        """Metadata-only cleanup; payload validation happens before upload."""
        pending = self.pending(owner)
        missing = [row for row in pending if not Path(row["path"]).is_file() or Path(row["path"]).is_symlink()]
        self.discard(missing)
        dead = {source_id(row) for row in missing}
        return tuple(row for row in pending if source_id(row) not in dead)


class RetractionOutbox:
    """Durable owner-bound retractions until the server answers each one."""
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._rows = OrderedDict()
        self._loaded = False

    def load(self):
        with self._lock:
            if self._loaded:
                return
            self._loaded = True
            if not self.path.exists():
                return
            try:
                rows = [checked_retraction(row) for row in _read_document(self.path, "retractions", MAX_RETRACTIONS)]
            except (OSError, ValueError, UnicodeError, RecursionError, CommunityError):
                return
            for row in rows:
                self._rows[retraction_key(row)] = row

    def _write(self):
        _replace(self.path, canonical({"v": 1, "retractions": list(self._rows.values())}))

    def remember(self, retraction):
        retraction = checked_retraction(retraction)
        with self._lock:
            self.load()
            key = retraction_key(retraction)
            self._rows.pop(key, None)
            self._rows[key] = retraction
            while len(self._rows) > MAX_RETRACTIONS:
                self._rows.popitem(last=False)
            self._write()

    def pending(self, owner):
        with self._lock:
            self.load()
            return tuple(dict(row) for row in self._rows.values() if row["owner"] == owner)

    def discard(self, retraction):
        """Forget an answered retraction unless a newer one replaced it."""
        with self._lock:
            self.load()
            key = retraction_key(retraction)
            if self._rows.get(key, {}).get("key") == retraction["key"]:
                self._rows.pop(key)
                self._write()

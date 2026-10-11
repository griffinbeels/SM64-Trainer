"""Bounded metadata for previously consented native contributions, never clips.

Only the admission worker remembers a qualified source and its current account
UID. Recovery never assigns historical, imported or packaged runs to a login.
Deleted/changed source bytes, capacity eviction and explicit cancellation can
retire an intent; this file is not a second recording archive.
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
                if self.path.is_symlink() or self.path.stat().st_size > MAX_BYTES:
                    raise ValueError()
                with self.path.open("rb") as stream:
                    encoded = stream.read(MAX_BYTES + 1)
                if len(encoded) > MAX_BYTES:
                    raise ValueError()
                document = json.loads(encoded)
                if (not isinstance(document, dict) or set(document) != {"v", "offers"} or type(document["v"]) is not int
                        or document["v"] != 1 or not isinstance(document["offers"], list) or len(document["offers"]) > MAX_OFFERS):
                    raise ValueError()
                rows = [checked_source(row) for row in document["offers"]]
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
        if len(encoded) > MAX_BYTES or self.path.is_symlink():
            raise CommunityError("outbox_unavailable")
        temporary = self.path.with_name("pending-contributions-" + uuid4().hex + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("xb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

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

    def available(self, owner):
        """Metadata-only cleanup; payload validation happens before upload."""
        pending = self.pending(owner)
        missing = [row for row in pending if not Path(row["path"]).is_file() or Path(row["path"]).is_symlink()]
        self.discard(missing)
        dead = {source_id(row) for row in missing}
        return tuple(row for row in pending if source_id(row) not in dead)

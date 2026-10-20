"""Trusted private-beta author recordings, read separately from practice and PBs.

The maintainer validates the complete collection during packaging. This provider
pins those immutable bytes and preserves legacy unknown ROM/input provenance.
It is local playback trust, never a full-start certificate or cloud admission.
Only selected assets are decoded and cached; startup reads bounded metadata.
"""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unicodedata
from urllib.parse import urlsplit
from uuid import UUID
import zipfile

from sm64_events.ghost.clip import blink_frames, read_clip, require_complete_inputs
from sm64_events.ghost.portable import FORMAT, VERSION, MAX_ENTRIES, MAX_MANIFEST_BYTES, checked_entry, checked_hash
from sm64_events.ghost.sharing import PortableGhost
from sm64_events.ghost.sharing_targets import bind

DESCRIPTOR = "beta-ghost-seed.json"
COLLECTION = "griffman1212.sm64ghosts"
AUTHOR = "griffman1212"
PLAYER_ID = "bundled:griffman1212"
MAX_COLLECTION_BYTES = 128 << 20
MAX_CACHE_BYTES = 64 << 20


def checked_author_identity(value):
    """A reviewed Auth identity, never inferred from a recording's display name."""
    try:
        if not isinstance(value, dict) or set(value) != {"issuer", "subject"}:
            raise ValueError()
        issuer, subject = value["issuer"], value["subject"]
        if not isinstance(issuer, str) or not 1 <= len(issuer) <= 2048 or not isinstance(subject, str):
            raise ValueError()
        parts = urlsplit(issuer)
        if (parts.scheme != "https" or not parts.hostname or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment or parts.path != "/auth/v1" or parts.port not in (None, 443)
                or str(UUID(subject)) != subject):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ValueError("The curated author identity is invalid.") from None
    return dict(value)


def _same_author(name):
    """A display name spells the collection author, ignoring case, width and edges."""
    return (isinstance(name, str)
            and unicodedata.normalize("NFKC", name).casefold().strip() == unicodedata.normalize("NFKC", AUTHOR).casefold())


def with_bundled_beta(local, *, cache, definitions):
    """Composition door; source/stable builds never acquire packaged seed trust."""
    from sm64_events.core.build_channel import current_build
    build = current_build()
    if not getattr(sys, "frozen", False) or build.channel != "beta":
        return local
    descriptor = Path(sys._MEIPASS) / DESCRIPTOR
    if not descriptor.exists():
        return local
    return BundledPopulation(local, descriptor=descriptor,
                             collection=Path(sys.executable).parent / COLLECTION,
                             cache=cache, definitions=definitions,
                             auth_issuer=build.supabase_url.rstrip("/") + "/auth/v1" if build.community_enabled else None)


class BundledPopulation:
    def __init__(self, local, *, descriptor, collection, cache, definitions, auth_issuer=None):
        self.local, self.collection, self.root = local, Path(collection), Path(cache).resolve()
        self._definitions, self._lock = definitions, threading.RLock()
        self._ready = OrderedDict()
        descriptor = Path(descriptor)
        if descriptor.is_symlink() or not 0 < descriptor.stat().st_size <= 4096:
            raise ValueError("The packaged beta ghost descriptor is invalid.")
        value = json.loads(descriptor.read_text(encoding="utf-8"))
        fields = {"schema", "file", "author", "sha256", "count"}
        if (not isinstance(value, dict) or type(value.get("schema")) is not int or value["schema"] not in (1, 2)
                or set(value) != fields | ({"author_identity"} if value["schema"] == 2 else set())
                or value["file"] != COLLECTION or value["author"] != AUTHOR
                or type(value["count"]) is not int or not 1 <= value["count"] <= MAX_ENTRIES):
            raise ValueError("The packaged beta ghost descriptor is invalid.")
        binding = checked_author_identity(value["author_identity"]) if value["schema"] == 2 else None
        self.author_identity = binding if binding and binding["issuer"] == auth_issuer else None
        self.player_id = self.author_identity["subject"] if self.author_identity else PLAYER_ID
        self.digest = checked_hash(value["sha256"])
        if self.collection.is_symlink() or not 0 < self.collection.stat().st_size <= MAX_COLLECTION_BYTES:
            raise ValueError("The packaged beta ghost collection is invalid.")
        with self.collection.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != self.digest:
                raise ValueError("The packaged beta ghost collection failed verification.")
        self._stamp = self._collection_stamp()
        with zipfile.ZipFile(self.collection) as archive:
            info = archive.getinfo("collection.json")
            if info.file_size > MAX_MANIFEST_BYTES:
                raise ValueError("The packaged beta ghost manifest is too large.")
            manifest = json.loads(archive.read(info))
            if (not isinstance(manifest, dict) or set(manifest) != {"format", "version", "ghosts"}
                    or manifest["format"] != FORMAT or manifest["version"] not in (1, VERSION)
                    or not isinstance(manifest["ghosts"], list) or len(manifest["ghosts"]) != value["count"]):
                raise ValueError("The packaged beta ghost manifest is invalid.")
            rows = [checked_entry(row) for row in manifest["ghosts"]]
            if any(row["author"] != AUTHOR or row["practice"] or row["outcome"] != "success" for row in rows):
                raise ValueError("The packaged beta contains unexpected author recordings.")
            names = archive.namelist()
            if (len(names) != len(set(names)) or len({row["id"] for row in rows}) != len(rows)
                    or set(names) != {"collection.json", *(row["file"] for row in rows)}
                    or any(archive.getinfo(row["file"]).file_size > MAX_CACHE_BYTES for row in rows)):
                raise ValueError("The packaged beta ghost assets are invalid.")
        self._rows = {row["id"]: row for row in rows}

    def _collection_stamp(self):
        value = self.collection.stat()
        return value.st_size, value.st_mtime_ns, value.st_ctime_ns

    def _defs(self):
        return (self._definitions() if callable(self._definitions) else self._definitions) or ()

    def _row(self, row, game_version=None):
        key, reason = bind(row["target"], self._defs())
        if reason:
            raise LookupError(reason)
        ghost = PortableGhost(row["id"], row["target"]["kind"], key, row["target"].get("strat"),
                              row["frames"], AUTHOR)
        return {**row, "kind": ghost.kind, "entity_key": key, "strategy": ghost.strat_tag,
                "signature": row["target"].get("signature"), "ghost": ghost,
                "player_id": self.player_id, "player_name": AUTHOR, "valid": True,
                "source_status": "bundled-author-recording", "structurally_valid": True,
                "certified": False, "full_start": None,
                # A row naming no ROM predates the stamp, and every recording
                # before task 0064 was on US: JP's layout was empty, so the
                # poller held and nothing recorded on a JP cartridge.
                "recorded_game_version": row.get("game_version") or "us",
                # A query's current ROM is a local compatibility context only.
                "game_version": game_version if game_version is not None else row.get("game_version")}

    @property
    def generation(self):
        return self.local.generation, self.digest

    def metadata_snapshot(self, kind, entity_key, *, clock, game_version, strategy=None, versions=None):
        """`versions` names the recorded ROMs to return (`region_scope.servable_versions`);
        omitted, only `game_version`'s. A recording whose ROM nobody knows is
        always a candidate: whether it may serve is `ghost/adaptive.py`'s call."""
        local = self.local.metadata_snapshot(kind, entity_key, clock=clock, game_version=game_version,
                                             strategy=strategy, versions=versions)
        roms = (game_version,) if versions is None else versions
        rows = []
        for raw in self._rows.values():
            if (raw["clock"] != clock or raw.get("game_version") not in (None, *roms)
                    or strategy is not None and raw["target"].get("strat") != strategy):
                continue
            try:
                row = self._row(raw, game_version)
            except LookupError:
                continue
            if (row["kind"], row["entity_key"]) == (kind, entity_key):
                rows.append(row)
        return tuple(local) + tuple(rows)

    def get(self, identity):
        raw = self._rows.get(checked_hash(identity))
        return self._row(raw) if raw else self.local.get(identity)

    def is_ready(self, identity):
        try:
            self.get(identity)
            return True
        except (LookupError, ValueError):
            return False

    def owned_player_ids(self, local_id, *, names=()):
        """The wrapped layers' own recordings, plus every bundled row when the
        player is this collection's author by any name they go by (export
        credit or signed-in account). His rule (2026-10-13): the player never
        fights their own ghosts as a rival; only the PB ghost is theirs."""
        method = getattr(self.local, "owned_player_ids", None)
        owned = set(method(local_id, names=names) if method else (local_id,))
        # Historical schema1 has only export credit. A reviewed schema2
        # identity uses the account subject; a matching name proves nothing.
        if getattr(self, "author_identity", None) is None and any(_same_author(name) for name in names):
            owned.add(PLAYER_ID)
        return frozenset(value for value in owned if value)

    def additional_owned_player_ids(self, local_id):
        return self.owned_player_ids(local_id)

    def search(self, identity, prefix, *, cursor=None, limit=20):
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("Choose a search page of 1 to 20 records.")
        if not isinstance(prefix, str) or len(prefix.encode("utf-8")) > 128 or any(ord(c) < 32 for c in prefix):
            raise ValueError("Enter a bounded player name.")
        normalized = unicodedata.normalize("NFKC", prefix).casefold().strip()
        local = self.local.search(identity, prefix, cursor=cursor, limit=limit)
        if not normalized or not AUTHOR.startswith(normalized):
            return local
        value = lambda key, default=None: identity.get(key, default) if isinstance(identity, dict) else getattr(identity, key, default)
        from sm64_events.ghost.population import searched_versions
        rows = self.metadata_snapshot(value("kind"), value("entity_key"), clock=value("clock"),
            game_version=value("game_version"), strategy=value("strategy") if value("scope", "strategy") == "strategy" else None,
            versions=searched_versions(identity))
        seeds = [row for row in rows if row.get("source_status") == "bundled-author-recording"]
        key = lambda row: (unicodedata.normalize("NFKC", row["player_name"]).casefold(), row["frames"], row["id"])
        if cursor is not None:
            if not isinstance(cursor, list) or len(cursor) != 3:
                raise ValueError("This ghost search cursor is invalid.")
            seeds = [row for row in seeds if key(row) > tuple(cursor)]
        merged = {row["id"]: row for row in (*local["records"], *seeds)}
        records = sorted(merged.values(), key=key)
        after = list(key(records[limit - 1])) if len(records) > limit or local.get("next_cursor") else None
        return {"records": records[:limit], "next_cursor": after}

    def resolve(self, ghost, target):
        raw = self._rows.get(checked_hash(ghost.id))
        if raw is None:
            return self.local.resolve(ghost, target)
        row = self._row(raw)
        if (not target or (row["kind"], row["entity_key"]) != (target["kind"], target["entity_key"])
                or target.get("scope", "strategy") == "strategy"
                and row["strategy"] != target.get("strat")):
            raise ValueError("This ghost belongs to another practice target.")
        with self._lock:
            if self.collection.is_symlink() or self._collection_stamp() != self._stamp:
                raise ValueError("The packaged beta ghost collection changed after verification.")
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.root / (raw["sha256"] + ".ghost")
            if path.is_symlink():
                raise ValueError("The bundled ghost cache contains an unsafe path.")
            if not path.exists():
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(dir=self.root, delete=False) as out:
                        temporary = Path(out.name)
                        with zipfile.ZipFile(self.collection) as archive, archive.open(raw["file"]) as source:
                            shutil.copyfileobj(source, out, 1 << 20)
                    require_complete_inputs(read_clip(temporary, raw["sha256"]))
                    os.replace(temporary, path)
                finally:
                    if temporary:
                        temporary.unlink(missing_ok=True)
            stamp = path.stat()
            stamp = stamp.st_size, stamp.st_mtime_ns, stamp.st_ctime_ns
            known = self._ready.get(raw["sha256"])
            if known is None or known[0] != stamp:
                clip = require_complete_inputs(read_clip(path, raw["sha256"]))
                result = {"clip_id": clip.clip_id, "sha256": raw["sha256"], "blink_frames": blink_frames(clip),
                          "pad_records": sum(frame.pad is not None for frame in clip.frames), "records": len(clip.frames)}
                self._ready[raw["sha256"]] = stamp, result
            else:
                result = known[1]
            self._ready.move_to_end(raw["sha256"])
            self._trim_cache(path)
        return path, {**result, "author": AUTHOR, "target": {"kind": row["kind"],
                      "entity_key": row["entity_key"], "strat": row["strategy"]}}

    def _trim_cache(self, current):
        files = sorted(self.root.glob("*.ghost"), key=lambda path: path.stat().st_mtime_ns)
        total = sum(path.stat().st_size for path in files)
        for path in files:
            if total <= MAX_CACHE_BYTES:
                break
            if path == current or path.is_symlink():
                continue
            try:
                checked_hash(path.stem)
            except ValueError:
                continue
            total -= path.stat().st_size
            path.unlink()
            self._ready.pop(path.stem, None)

    def accept_verified(self, *args, **kwargs):
        return self.local.accept_verified(*args, **kwargs)

    def qualified_source(self, *args, **kwargs):
        return self.local.qualified_source(*args, **kwargs)

    def close(self):
        self.local.close()

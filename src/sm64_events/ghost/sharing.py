"""Native ghost collection provider, independent of input templates and PBs.

Explicit export preserves selected recent attempts through ReplayService. Import
validates complete collections before publishing a single immutable index.
Creator names are the credit supplied by the exporter, not account identities.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import tempfile
import threading
from uuid import uuid4

from sm64_events.ghost.clip import INCOMPLETE_INPUTS, blink_frames, inputs_complete, read_clip, require_complete_inputs
from sm64_events.ghost.portable import (MAX_ENTRIES, checked_author, checked_entry, checked_hash,
                                       _checked_recording_target, validate_package, write_package)
from sm64_events.ghost.library_preferences import AUTHOR_KEY, shortcuts, save_author, save_shortcut, remove_shortcut
from sm64_events.ghost.sharing_targets import bind, binding_index
from sm64_events.replay.publication import atomic_json

MAX_IMPORTED = 16384


@dataclass(frozen=True)
class PortableGhost:
    id: str
    kind: str
    entity_key: str
    strat_tag: str | None
    frames: int
    author: str
    pb: bool = False
    attempt_id: None = None
    practice: bool = False

    @property
    def import_id(self):
        return self.id

    @property
    def origin(self):
        return f"ghost:{self.id}"

    @property
    def name(self):
        return self.author


def sharing_for(replay, database):
    """One provider per replay service; a late database binding replaces it."""
    db = database() if callable(database) else database
    if replay is None or getattr(replay, "cfg", None) is None or db is None:
        raise RuntimeError("Ghost sharing is available after the trainer starts recording.")
    current = getattr(replay, "_ghost_sharing", None)
    if current is None or current.database is not db:
        current = GhostSharing(replay, db)
        replay._ghost_sharing = current
    return current


class GhostSharing:
    def __init__(self, replay, database):
        self.replay, self.database = replay, database
        self.root = (Path(replay.cfg.save_root) / "shared-ghosts").resolve()
        if not self.root.is_relative_to(Path(replay.cfg.save_root).resolve()):
            raise ValueError("The ghost collection folder is outside the recording folder.")
        self._index = self.root / "collection.json"
        self._rows = {}
        self._stamp = None
        self._lock = threading.RLock()
        self._verified = {}
        from sm64_events.ghost.library_imports import ImportPreviews
        self.previews = ImportPreviews(self)

    def _load(self):
        try:
            stamp = self._index.stat().st_mtime_ns, self._index.stat().st_size
        except FileNotFoundError:
            stamp = None
        if stamp == self._stamp:
            return
        if stamp is None:
            self._rows, self._stamp = {}, None
            return
        if stamp[1] > 32 << 20:
            raise ValueError("The saved ghost collection index is too large.")
        document = json.loads(self._index.read_text(encoding="utf-8"))
        rows = document.get("ghosts") if isinstance(document, dict) and document.get("version") == 1 else None
        if not isinstance(rows, list) or len(rows) > MAX_IMPORTED:
            raise ValueError("The saved ghost collection index is invalid.")
        self._rows = {checked_entry(row)["id"]: row for row in rows}
        self._stamp = stamp

    @property
    def generation(self):
        with self._lock:
            self._load()
            return self._stamp

    def _definitions(self):
        return self.database.segment_defs()

    def _bindings(self, rows):
        return binding_index(self._definitions()) if any(row["target"]["kind"] == "segment" for row in rows) else {}

    def _ghost(self, row, bindings):
        key, reason = bind(row["target"], index=bindings)
        if reason:
            return None
        return PortableGhost(row["id"], row["target"]["kind"], key, row["target"].get("strat"),
                             row["frames"], row["author"], practice=row["practice"])

    def get(self, number):
        with self._lock:
            self._load()
            row = self._rows.get(checked_hash(number))
            ghost = self._ghost(row, self._bindings([row])) if row else None
            if ghost is None:
                raise LookupError("That imported ghost is missing or its segment does not match.")
            return ghost

    def imports_for(self, kind, key):
        with self._lock:
            self._load()
            rows = [row for row in self._rows.values() if row["target"]["kind"] == kind
                    and (kind != "star" or row["target"]["entity_key"] == key)]
            bindings = self._bindings(rows)
            return [ghost for row in rows if (ghost := self._ghost(row, bindings)) is not None
                    and (ghost.kind, ghost.entity_key) == (kind, key)]

    def resolve(self, ghost, target):
        """Provider door: validate bytes and local target, without changing PBs."""
        current = self.get(ghost.id)
        if target is None or (current.kind, current.entity_key) != (target["kind"], target["entity_key"]):
            raise ValueError("This ghost belongs to another practice target.")
        path, value = self.export_asset(current.id)
        return path, {**value, "author": current.author, "target": {
            "kind": current.kind, "entity_key": current.entity_key, "strat": current.strat_tag}}

    def export_asset(self, number):
        """Read even an unmatched segment's bytes, preserving its portable identity."""
        with self._lock:
            self._load()
            row = self._rows.get(checked_hash(number))
            if row is None:
                raise LookupError("That imported ghost is no longer in the library.")
        path = self.root / f"{row['sha256']}.ghost"
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("This imported recording is outside the ghost collection.")
        stat = path.stat()
        stamp = stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
        cached = self._verified.get(row["sha256"])
        if cached is None or cached[0] != stamp:
            clip = require_complete_inputs(read_clip(path, row["sha256"]))
            value = dict(clip_id=clip.clip_id, sha256=row["sha256"], blink_frames=blink_frames(clip),
                         pad_records=sum(frame.pad is not None for frame in clip.frames), records=len(clip.frames))
            self._verified[row["sha256"]] = stamp, value
            if len(self._verified) > 128:
                self._verified.pop(next(iter(self._verified)))
        else:
            value = cached[1]
        return path, {**value, "author": row["author"], "target": dict(row["target"])}

    def portable_entry(self, number):
        with self._lock:
            self._load()
            row = self._rows.get(checked_hash(number))
            if row is None:
                raise LookupError("That imported ghost is no longer in the library.")
            return dict(row)

    def imported_rows(self):
        with self._lock:
            self._load()
            result = []
            bindings = self._bindings(self._rows.values())
            for row in self._rows.values():
                result.append(self._import_row(row, bindings, self.root))
            return result

    def _complete(self, path, digest):
        """His rule, asked once per file state: an import with any unknown pad is not a ghost."""
        try:
            stat = path.stat()
        except OSError:
            return False
        stamp = stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
        cache = self.__dict__.setdefault("_complete_cache", {})
        known = cache.get(digest)
        if known is None or known[0] != stamp:
            try:
                known = stamp, inputs_complete(read_clip(path, digest))
            except (OSError, ValueError):
                known = stamp, False
            cache[digest] = known
            if len(cache) > 512:
                cache.pop(next(iter(cache)))
        return known[1]

    def _import_row(self, row, bindings, folder):
        key, reason = bind(row["target"], index=bindings)
        if not reason and not (folder / f"{row['sha256']}.ghost").is_file():
            reason = "This imported recording was removed from disk."
        if not reason and not self._complete(folder / f"{row['sha256']}.ghost", row["sha256"]):
            reason = INCOMPLETE_INPUTS
        return {**row, "source": "import", "import_id": row["id"], "attempt_id": None,
                "entity_key": key, "kind": row["target"]["kind"], "strat": row["target"].get("strat"),
                "target_name": row["target"].get("name"), "game_version": row.get("game_version"),
                "available": reason is None, "reason": reason}

    def catalog(self):
        """Recent local pose candidates plus durable imports, asked on open."""
        from sm64_events.ghost.sharing_catalog import local_rows
        from sm64_events.ghost.library_bests import historical_bests
        from sm64_events.ghost.native_layout import GHOSTS
        local = local_rows(self.replay, self.database)
        return {"ghosts": [*local, *self.imported_rows()], "bests": historical_bests(self.database, local),
                "shortcuts": shortcuts(self.database),
                "author": self.database.get_state(AUTHOR_KEY, ""), "max_export": MAX_ENTRIES,
                "max_race": GHOSTS}

    def save_author(self, author):
        with self._lock:
            return {"author": save_author(self.database, author)}

    def save_shortcut(self, name, criteria):
        with self._lock:
            return {"shortcuts": save_shortcut(self.database, name, criteria)}

    def remove_shortcut(self, number):
        with self._lock:
            return {"shortcuts": remove_shortcut(self.database, number)}

    def preview_import(self, source, step=lambda *_: None):
        return self.previews.preview(source, step)

    def commit_import(self, number, selected, step=lambda *_: None):
        return self.previews.commit(number, selected, step)

    def discard_preview(self, number):
        return self.previews.discard(number)

    def prepare_race(self, choice, target):
        """Preserve explicitly chosen recent runs before the session selects them."""
        from sm64_events.ghost.selection import checked_choice
        from sm64_events.ghost import saved
        from sm64_events.inputs.service import entity_key_of
        choice = checked_choice(choice)
        imported = {ghost.id for ghost in self.imports_for(target["kind"], target["entity_key"])}
        if any(identity not in imported for identity in choice.get("imports", [])):
            raise ValueError("Choose imported ghosts for the current practice target.")
        for identity in choice.get("imports", []):
            self.export_asset(identity)
        attempts = [self.replay.attempt(number) for number in choice["attempts"]]
        for attempt in attempts:
            if entity_key_of(attempt) != (target["kind"], target["entity_key"]):
                raise ValueError("Choose recent ghosts for the current practice target.")
        # Validate every source before changing the saved list. Preservation
        # may write immutable pose files, but never changes PBs or the tick set.
        for attempt in attempts:
            kept = self.replay.preserve_native(attempt.id)
            if not kept.get("path") or self.replay.find_native(attempt.id) is None:
                raise ValueError(kept.get("native_ghost_reason") or "This run's Mario poses could not be saved.")
        for attempt in attempts:
            saved.save(self.database, attempt.id, target["kind"], target["entity_key"])
        return choice

    def export(self, selected, author, step=lambda *_: None):
        from sm64_events.ghost.sharing_catalog import export_rows
        author = checked_author(author)
        entries, skipped = export_rows(self, selected, author, step)
        if not entries:
            detail = skipped[0]["reason"] if skipped else "Choose ghosts to export."
            raise ValueError(f"No selected ghost could be exported: {detail}")
        self.root.mkdir(parents=True, exist_ok=True)
        destination = self.root / "exports" / f"{uuid4().hex}.sm64ghosts"
        destination.parent.mkdir(exist_ok=True)
        try:
            write_package(destination, entries, lambda progress, text: step(.5 + .5 * progress, text))
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        self.database.set_state(AUTHOR_KEY, author)
        for stale in sorted(destination.parent.glob("*.sm64ghosts"), key=lambda path: path.stat().st_mtime_ns)[:-8]:
            stale.unlink(missing_ok=True)
        return {"path": str(destination), "exported": len(entries), "skipped": skipped}

    def import_file(self, source, step=lambda *_: None):
        self.root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="import-", dir=self.root) as staging:
            rows, _verified = validate_package(source, staging, lambda progress, text: step(.9 * progress, text))
            return self._publish(rows, Path(staging), lambda progress, text: step(.9 + .1 * progress, text))

    def _publish(self, rows, staging, step):
        rows = [checked_entry(row) for row in rows]
        # Decode and copy off the provider lock: the native session can read
        # generation/selection while a large explicit import is being checked.
        checked = {}
        for index, row in enumerate(rows):
            recording_target = checked.get(row["sha256"])
            if recording_target is None:
                clip = read_clip(staging / f"{row['sha256']}.ghost", row["sha256"])
                recording_target = clip.clock_kind, clip.level
                checked[row["sha256"]] = recording_target
                del clip
            _checked_recording_target(row["target"], *recording_target)
            step(.7 * (index + 1) / len(rows), f"Checked {index + 1} of {len(rows)} selected ghosts.")
        temporary_assets = {}
        try:
            for digest in checked:
                temporary = self.root / f"{uuid4().hex}.pending"
                temporary_assets[digest] = temporary
                shutil.copyfile(staging / f"{digest}.ghost", temporary)
            with self._lock:
                bindings = self._bindings(rows)
                unmatched = [row["id"] for row in rows if bind(row["target"], index=bindings)[1]]
                self._load()
                new = {row["id"]: row for row in rows if row["id"] not in self._rows}
                if len(self._rows) + len(new) > MAX_IMPORTED:
                    raise ValueError("The ghost library is full; remove old imports before adding this collection.")
                for digest in {row["sha256"] for row in new.values()}:
                    temporary_assets[digest].replace(self.root / f"{digest}.ghost")
                if new:
                    atomic_json(self._index, {"version": 1, "ghosts": list({**self._rows, **new}.values())})
                    self._stamp = None
                    self._load()
                step(1, f"Imported {len(new)} selected ghosts.")
                return {"imported": len(new), "duplicates": len(rows) - len(new), "unmatched": len(unmatched),
                        "import_ids": [row["id"] for row in rows], "recorded_utc": datetime.now(timezone.utc).isoformat()}
        finally:
            for temporary in temporary_assets.values():
                temporary.unlink(missing_ok=True)

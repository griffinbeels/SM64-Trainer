"""Owned, expiring import previews; the collection index is the commit boundary."""
from datetime import datetime, timedelta, timezone
import shutil
import threading
from uuid import uuid4

from sm64_events.ghost.library_bests import comparison, historical_bests
from sm64_events.ghost.portable import MAX_ENTRIES, canonical, checked_entry, checked_hash, validate_package

MAX_PREVIEWS = 4
PREVIEW_SECONDS = 30 * 60
MAX_PREVIEW_BYTES = 4 << 30
MARKER = "owned-preview.json"


class ImportPreviews:
    def __init__(self, sharing):
        self.sharing = sharing
        self.root = sharing.root / "previews"
        self.rows = {}
        self.lock = threading.RLock()
        # A provider restart cannot recover the in-memory review lease. Only
        # remove directories carrying our exact ownership marker.
        if self.root.is_dir():
            for folder in self.root.glob("preview-*"):
                if folder.is_dir() and (folder / MARKER).is_file():
                    self._remove(folder)

    def _remove(self, folder):
        if folder.resolve().is_relative_to(self.root.resolve()):
            shutil.rmtree(folder)

    def _expire(self):
        now = datetime.now(timezone.utc)
        for number, row in list(self.rows.items()):
            if now >= row["expires"]:
                self._remove(row["folder"])
                self.rows.pop(number)

    def _get(self, number):
        self._expire()
        if not isinstance(number, str) or number not in self.rows:
            raise ValueError("This import preview expired or was closed; choose the collection again.")
        return self.rows[number]

    def preview(self, source, step):
        with self.lock:
            self._expire()
            if len(self.rows) >= MAX_PREVIEWS:
                raise ValueError("Close an existing import preview before opening another collection.")
            self.root.mkdir(parents=True, exist_ok=True)
            number = uuid4().hex
            folder = self.root / f"preview-{number}"
            folder.mkdir()
            (folder / MARKER).write_bytes(canonical({"preview_id": number}))
            try:
                used = sum(path.stat().st_size for path in self.root.glob("preview-*/*.ghost"))
                rows, _ = validate_package(source, folder, step, unpacked_limit=MAX_PREVIEW_BYTES - used)
                expires = datetime.now(timezone.utc) + timedelta(seconds=PREVIEW_SECONDS)
                self.rows[number] = {"folder": folder, "rows": rows, "expires": expires}
                bests = historical_bests(self.sharing.database)
                with self.sharing._lock:
                    self.sharing._load()
                    bindings = self.sharing._bindings(rows)
                    display = []
                    for row in rows:
                        value = self.sharing._import_row(row, bindings, folder)
                        duplicate = row["id"] in self.sharing._rows
                        compared, frames = comparison(value, bests)
                        display.append({**value, "duplicate": duplicate, "importable": not duplicate,
                                        "comparison": compared, "your_best_frames": frames})
                return {"preview_id": number, "ghosts": display, "expires_utc": expires.isoformat()}
            except Exception:
                self.rows.pop(number, None)
                self._remove(folder)
                raise

    def checked_selection(self, number, selected):
        with self.lock:
            preview = self._get(number)
            if not isinstance(selected, list) or not 1 <= len(selected) <= MAX_ENTRIES:
                raise ValueError("Select at least one ghost to import.")
            for identity in selected:
                checked_hash(identity)
            if len(set(selected)) != len(selected):
                raise ValueError("Select each ghost only once.")
            lookup = {row["id"]: row for row in preview["rows"]}
            if any(identity not in lookup for identity in selected):
                raise ValueError("A selected ghost does not belong to this preview; choose the collection again.")
            return [checked_entry(lookup[identity]) for identity in selected]

    def commit(self, number, selected, step):
        with self.lock:
            rows = self.checked_selection(number, selected)
            preview = self._get(number)
            result = self.sharing._publish(rows, preview["folder"], step)
            self._remove(preview["folder"])
            self.rows.pop(number)
            return result

    def discard(self, number):
        with self.lock:
            self._expire()
            preview = self.rows.pop(number, None)
            if preview:
                self._remove(preview["folder"])
            return {"discarded": True}

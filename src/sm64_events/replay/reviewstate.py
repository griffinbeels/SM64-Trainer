"""Review preferences follow the replay, independently of capture evidence.

Unsaved preferences exist only in the owning ReplayService's memory. Saving
promotes them to a versioned ``.review.json`` next to the saved MP4. Later
edits replace that small file atomically; media and its archival sidecar never
need rewriting. Callers serialize saves with extraction, while this store's
lock serializes promotion against edits arriving during a save.
"""
import copy
import json
import logging
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import uuid

log = logging.getLogger("sm64.replay")
MAX_FRAMES = 1_000_000
MAX_SECONDS = 86_400
MAX_TEMPLATES = 128
MAX_FILE_BYTES = 32_768
_TEMPLATE_KEY = re.compile(r"[1-9][0-9]{0,18}:[0-9a-f]{64}")


def empty_state() -> dict:
    return {"template_offsets": {}, "zoom": None, "loop": None}


def _range(value, limit: int, *, loop: bool) -> dict | None:
    if value is None:
        return None
    keys = {"start", "end", "enabled"} if loop else {"start", "end"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("review range has invalid fields")
    start, end = value["start"], value["end"]
    if (any(type(number) not in (int, float) for number in (start, end))
            or not 0 <= start < end <= limit
            or not all(math.isfinite(number) for number in (start, end))):
        raise ValueError(f"review range must satisfy 0 <= start < end <= {limit}")
    if loop and type(value["enabled"]) is not bool:
        raise ValueError("loop enabled must be a boolean")
    return dict(value)


def validate_state(value) -> dict:
    """Validate a complete replacement; omitted fields reset to their defaults."""
    if not isinstance(value, dict) or set(value) - set(empty_state()):
        raise ValueError("review state has invalid fields")
    offsets = value.get("template_offsets", {})
    if not isinstance(offsets, dict) or len(offsets) > MAX_TEMPLATES:
        raise ValueError(f"review state permits up to {MAX_TEMPLATES} template offsets")
    for key, offset in offsets.items():
        if not isinstance(key, str) or not _TEMPLATE_KEY.fullmatch(key):
            raise ValueError("template offset key must contain a template ID and revision")
        if type(offset) is not int or not -MAX_FRAMES <= offset <= MAX_FRAMES:
            raise ValueError(f"template offset must be an integer within +/-{MAX_FRAMES} frames")
    return {"template_offsets": dict(offsets),
            "zoom": _range(value.get("zoom"), MAX_FRAMES, loop=False),
            "loop": _range(value.get("loop"), MAX_SECONDS, loop=True)}


def state_path(saved: Path) -> Path:
    return saved.with_suffix(".review.json")


def _read(saved: Path) -> dict:
    path = state_path(saved)
    try:
        # Read bounded bytes even if a manually replaced file is unexpectedly
        # large. Damaged preferences must not prevent playing the saved clip.
        with path.open("rb") as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise ValueError("review state file is too large")
        document = json.loads(raw)
        if not isinstance(document, dict) or document.get("version") != 1:
            raise ValueError("unknown review state version")
        return validate_state(document["state"])
    except FileNotFoundError:
        return empty_state()
    except (OSError, ValueError, KeyError) as error:
        log.warning("could not load replay review preferences: %s", error)
        return empty_state()


def _write(saved: Path, state: dict) -> None:
    path = state_path(saved)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=path.parent, prefix=".review-",
                                         suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({"version": 1, "state": state}, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class ReviewStateStore:
    """Service-lifetime temporary state, with explicit save promotion."""

    def __init__(self):
        self._temporary: dict[int, dict] = {}
        self._lock = threading.Lock()
        self.session_token = uuid.uuid4().hex
        self._edits: dict[tuple[int, str], int] = {}

    def get(self, attempt_id: int, saved: Path | None) -> dict:
        with self._lock:
            if attempt_id in self._temporary:
                return copy.deepcopy(self._temporary[attempt_id])
            return _read(saved) if saved is not None else empty_state()

    def put(self, attempt_id: int, saved: Path | None, state: dict,
            edit: str | None = None) -> dict:
        state = validate_state(state)
        with self._lock:
            writer = None
            if edit is not None:
                match = re.fullmatch(r"([0-9a-f]{32})/([0-9a-f]{32})/([1-9][0-9]{0,15})", edit)
                if match is None or match[1] != self.session_token:
                    raise ValueError("review session changed; reopen the replay")
                writer, sequence = (attempt_id, match[2]), int(match[3])
                if sequence <= self._edits.get(writer, 0):
                    current = self._temporary.get(attempt_id)
                    return copy.deepcopy(current) if current is not None else (
                        _read(saved) if saved is not None else empty_state())
            if saved is None:
                self._temporary[attempt_id] = state
            else:
                _write(saved, state)
                self._temporary.pop(attempt_id, None)
            if writer is not None:
                self._edits[writer] = sequence
            return copy.deepcopy(state)

    def promote(self, attempt_id: int, saved: Path) -> None:
        with self._lock:
            state = self._temporary.get(attempt_id)
            if state is not None:
                _write(saved, state)
                self._temporary.pop(attempt_id, None)

    def clear(self) -> None:
        with self._lock:
            self._temporary.clear()
            self._edits.clear()
            self.session_token = uuid.uuid4().hex

"""The Scene viewer's camera paths (2026-11-08): one path of keys per level and area, kept in one file
in the lab folder, so they survive restarts.

Griffin, 2026-11-08: "evaluate https://github.com/MikeXander/SM64Scripts to see what camera techniques
/ methods we could add + support from this tool". A path is a list of keys (ui/components/
scenepath.js owns what they mean): each key a camera on a frame of the scene, {"frame", "camera",
"ease", "hold", "speed"}, its camera one the free camera draws ({focus, position, roll, fov},
scene_session.world_camera checks it) or null for the recorded camera. A camera's coordinates belong
to one area of one level, so the file keeps one path per "level:area", as camera_presets.py keeps one
set of slots; a key's frame counts from the first frame of whichever scene plays it.

The file is {"version": 1, "areas": {"9:1": [key, ...]}}. Writes are atomic and serialized by a lock;
a file it cannot read never stops the viewer: every area answers empty with one plain sentence, and
the next write keeps the unread file beside the new one (BAD_SUFFIX).

Used by server/scene_lab_api.py's /camera-paths routes; the page is ui/components/scenepathbar.js.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
from pathlib import Path

import camera_presets
from sm64_events.replay.publication import atomic_json

log = logging.getLogger("sm64.scene_lab")

FILE_NAME = "camera-paths.json"
BAD_SUFFIX = ".bad.json"
VERSION = 1
MAX_KEYS = 64                     # scenepath.js MAX_KEYS
MAX_FRAME = 1_000_000
SPEEDS = (1, 0.5, 0.25)           # scenepath.js PLAY_SPEEDS
MAX_HOLD_MS = 60000               # scenepath.js HOLD.maxMs
MAX_FILE_BYTES = 4 << 20
_AREA_KEY = re.compile(r"(\d{1,3}):(\d{1,3})")
KEY_FIELDS = {"frame", "camera", "ease", "hold", "speed"}
UNREADABLE = ("The saved camera paths could not be read, so none are shown. Saving one keeps the "
              f"old file beside the new one as {FILE_NAME.removesuffix('.json')}{BAD_SUFFIX}.")


class PathError(ValueError):
    """A path the store refuses, in words the page shows as they are."""


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def checked_key(key) -> dict:
    if not isinstance(key, dict) or set(key) != KEY_FIELDS:
        raise PathError("Each key is {frame, camera, ease, hold, speed}.")
    frame, ease, hold, speed = key["frame"], key["ease"], key["hold"], key["speed"]
    if type(frame) is not int or not 0 <= frame <= MAX_FRAME:
        raise PathError("A key's frame is a whole number from 0.")
    if not isinstance(ease, bool):
        raise PathError("A key's ease is true or false.")
    if not (_number(hold) and 0 <= hold <= MAX_HOLD_MS):
        raise PathError(f"A key holds 0 to {MAX_HOLD_MS // 1000} seconds.")
    if speed not in SPEEDS or isinstance(speed, bool):
        raise PathError("A key's speed is 1, 0.5 or 0.25.")
    try:
        camera = None if key["camera"] is None else camera_presets.checked_camera(key["camera"])
    except camera_presets.PresetError as problem:
        raise PathError(str(problem)) from None
    return {"frame": frame, "camera": camera, "ease": ease, "hold": float(hold), "speed": speed}


def checked_keys(keys) -> list[dict]:
    """A whole path, in the page's order (keys on one frame keep theirs), or PathError."""
    if not isinstance(keys, list) or len(keys) > MAX_KEYS:
        raise PathError(f"A path is a list of at most {MAX_KEYS} keys.")
    return [checked_key(key) for key in keys]


def _checked_file(raw: bytes) -> dict[str, list[dict]]:
    document = json.loads(raw)
    if not isinstance(document, dict) or document.get("version") != VERSION or set(document) != {"version", "areas"}:
        raise PathError(f"not a version {VERSION} camera paths file")
    areas = document["areas"]
    if not isinstance(areas, dict):
        raise PathError("areas is not an object")
    for key in areas:
        if not _AREA_KEY.fullmatch(key):
            raise PathError(f"{key!r} is not a level:area key")
    return {key: checked_keys(keys) for key, keys in areas.items() if keys}


class CameraPaths:
    """The file at `path` (the lab folder's FILE_NAME). Thread-safe: the routes run on the threadpool."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _read(self) -> tuple[dict[str, list[dict]], str | None]:
        try:
            with self.path.open("rb") as stream:
                raw = stream.read(MAX_FILE_BYTES + 1)
        except FileNotFoundError:
            return {}, None
        except OSError as problem:
            log.warning("Camera paths %s could not be opened: %s", self.path, problem)
            return {}, UNREADABLE
        try:
            if len(raw) > MAX_FILE_BYTES:
                raise PathError(f"larger than {MAX_FILE_BYTES} bytes")
            return _checked_file(raw), None
        except (ValueError, UnicodeDecodeError) as problem:
            log.warning("Camera paths %s refused: %s", self.path, problem)
            return {}, UNREADABLE

    def keys(self, level: int, area: int) -> dict:
        """{"keys": [...], "problem": None or the sentence why none could be read}."""
        with self._lock:
            areas, problem = self._read()
        return {"keys": areas.get(camera_presets.area_key(level, area), []), "problem": problem}

    def replace(self, level: int, area: int, keys) -> dict:
        """Makes `keys` that area's whole path (an empty list clears it) and answers as keys() does."""
        wanted = checked_keys(keys)
        with self._lock:
            areas, problem = self._read()
            if problem and self.path.exists():
                os.replace(self.path, self.path.with_name(self.path.stem + BAD_SUFFIX))
            key = camera_presets.area_key(level, area)
            if wanted:
                areas[key] = wanted
            else:
                areas.pop(key, None)
            atomic_json(self.path, {"version": VERSION, "areas": dict(sorted(areas.items()))})
        return {"keys": wanted, "problem": None}

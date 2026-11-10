"""The Scene viewer's camera presets (M4 round 6, item 2): named cameras in slots 1-9, kept per
level and area in one file in the lab folder, so they survive restarts.

His words: "move the camera to a specific position and then SAVE that camera data as a preset for
the course for future use ... assign it to a camera slot position (numbers 1-9 on the keyboard)".

A camera's coordinates belong to one area of one level (a level's areas are separate worlds), so
the file keeps one set of slots per "level:area". The file is {"version": 1, "areas": {"9:1":
[{"slot", "name", "camera": {"focus", "position", "roll", "fov"}}, ...]}}; every camera is one the
free camera draws (scene_session.world_camera checks it). Writes are atomic (a temporary file
replaced into place) and serialized by a lock; a read checks the whole file, and a file it
cannot read never stops the viewer: every area answers empty with one plain sentence, and the
next write keeps the unread file beside the new one (BAD_SUFFIX) instead of discarding it.

Used by server/scene_lab_api.py's /camera-presets routes; the page is
ui/components/scenepresets.js.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path

import scene_session
from sm64_events.replay.publication import atomic_json

log = logging.getLogger("sm64.scene_lab")

FILE_NAME = "camera-presets.json"
BAD_SUFFIX = ".bad.json"          # where a file the store could not read goes when it next writes
VERSION = 1
SLOTS = range(1, 10)              # the number keys
MAX_NAME = 48
MAX_FILE_BYTES = 2 << 20          # every area of every level, nine slots each, fits many times over
_AREA_KEY = re.compile(r"(\d{1,3}):(\d{1,3})")
UNREADABLE = ("The saved cameras could not be read, so none are shown. Saving one keeps the "
              f"old file beside the new one as {FILE_NAME.removesuffix('.json')}{BAD_SUFFIX}.")


class PresetError(ValueError):
    """A set of slots the store refuses, in words the page shows as they are."""


def area_key(level: int, area: int) -> str:
    return f"{level}:{area}"


def checked_camera(camera) -> dict:
    """A preset's camera as the free camera draws it: {focus, position, roll, fov} (roll the game's
    s16 angle, fov degrees; either None for the frame's own), or PresetError."""
    try:
        drawn = scene_session.world_camera(camera)
    except ValueError as problem:
        raise PresetError(str(problem)) from None
    focus, position, roll, fov = (*drawn, None, None) if len(drawn) == 2 else drawn
    return {"focus": focus, "position": position, "roll": roll, "fov": fov}


def checked_name(name) -> str:
    if not isinstance(name, str) or not name.strip():
        raise PresetError("A saved camera needs a name.")
    name = " ".join(name.split())
    if len(name) > MAX_NAME:
        raise PresetError(f"A camera's name is at most {MAX_NAME} characters.")
    return name


def checked_slots(slots) -> list[dict]:
    """A whole area's slots, sorted by slot, or PresetError: each {"slot": 1-9, "name", "camera"},
    no slot twice, nothing else."""
    if not isinstance(slots, list) or len(slots) > len(SLOTS):
        raise PresetError("Slots are a list of at most nine cameras.")
    kept = {}
    for entry in slots:
        if not isinstance(entry, dict) or set(entry) != {"slot", "name", "camera"}:
            raise PresetError("Each slot is {slot, name, camera}.")
        slot = entry["slot"]
        if type(slot) is not int or slot not in SLOTS:
            raise PresetError("A slot is a number from 1 to 9.")
        if slot in kept:
            raise PresetError(f"Slot {slot} is named twice.")
        kept[slot] = {"slot": slot, "name": checked_name(entry["name"]), "camera": checked_camera(entry["camera"])}
    return [kept[slot] for slot in sorted(kept)]


def _checked_file(raw: bytes) -> dict[str, list[dict]]:
    document = json.loads(raw)
    if not isinstance(document, dict) or document.get("version") != VERSION or set(document) != {"version", "areas"}:
        raise PresetError(f"not a version {VERSION} camera presets file")
    areas = document["areas"]
    if not isinstance(areas, dict):
        raise PresetError("areas is not an object")
    for key in areas:
        if not _AREA_KEY.fullmatch(key):
            raise PresetError(f"{key!r} is not a level:area key")
    return {key: checked_slots(slots) for key, slots in areas.items() if slots}


class CameraPresets:
    """The file at `path` (the lab folder's FILE_NAME). Thread-safe: the routes run on the threadpool."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _read(self) -> tuple[dict[str, list[dict]], str | None]:
        """Every area's slots and None, or nothing and why when the file cannot be read (logged)."""
        try:
            with self.path.open("rb") as stream:
                raw = stream.read(MAX_FILE_BYTES + 1)
        except FileNotFoundError:
            return {}, None
        except OSError as problem:
            log.warning("Camera presets %s could not be opened: %s", self.path, problem)
            return {}, UNREADABLE
        try:
            if len(raw) > MAX_FILE_BYTES:
                raise PresetError(f"larger than {MAX_FILE_BYTES} bytes")
            return _checked_file(raw), None
        except (ValueError, UnicodeDecodeError) as problem:      # JSONDecodeError and PresetError are ValueErrors
            log.warning("Camera presets %s refused: %s", self.path, problem)
            return {}, UNREADABLE

    def slots(self, level: int, area: int) -> dict:
        """{"slots": [...], "problem": None or the sentence why none could be read}."""
        with self._lock:
            areas, problem = self._read()
        return {"slots": areas.get(area_key(level, area), []), "problem": problem}

    def replace(self, level: int, area: int, slots) -> dict:
        """Makes `slots` that area's whole set (an empty list clears it) and answers as slots() does.
        PresetError for a set it refuses; a file it could not read is set aside first."""
        wanted = checked_slots(slots)
        with self._lock:
            areas, problem = self._read()
            if problem and self.path.exists():
                os.replace(self.path, self.path.with_name(self.path.stem + BAD_SUFFIX))
            key = area_key(level, area)
            if wanted:
                areas[key] = wanted
            else:
                areas.pop(key, None)
            atomic_json(self.path, {"version": VERSION, "areas": dict(sorted(areas.items()))})
        return {"slots": wanted, "problem": None}

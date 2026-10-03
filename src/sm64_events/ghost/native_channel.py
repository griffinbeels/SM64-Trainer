"""One thread's ownership of a native Ghost mapping and command lease."""
from __future__ import annotations

import ctypes as C
import math
import os
import secrets
from contextlib import contextmanager

from sm64_events.ghost import native_layout as L, native_protocol as P
from sm64_events.ghost.proximity import DEFAULT_PROXIMITY, checked_proximity
from sm64_events.ghost.settings import (
    DEFAULT_INPUT_MODE, DEFAULT_NAME_BACKGROUND, DEFAULT_NAME_BACKGROUND_OPACITY, DEFAULT_OPACITY,
    DEFAULT_VOLUME, INPUT_MODES, NAME_BACKGROUNDS, NUDGE_LIMIT_FRAMES, PATH_SOURCES, native_path_defaults,
    native_switches,
)
from sm64_events.memory.addresses import ACT_JUMBO_STAR_CUTSCENE, STAR_DANCE_ACTIONS
from sm64_events.replay.capturecontrol import _kernel, _process_creation


# What ends a race for the native ghost: each Mario's x-cam.
RACE_END_ACTIONS = STAR_DANCE_ACTIONS | {ACT_JUMBO_STAR_CUTSCENE}


class RejectedControl(ValueError):
    """A playback command that cannot be encoded. The live command and the
    channel are untouched; the caller drops the command and keeps capturing."""
from sm64_events.replay.ownedclose import close_after_error, release_many


class NativeChannel:
    def __init__(self, pid: int, layout: bytes):
        if len(layout) != P.LAYOUT_BYTES:
            raise ValueError("ghost layout has an unsupported byte size")
        self.pid, self.name = pid, P.PREFIX + str(pid)
        self._k = _kernel()
        self._map = self._view = self._mutex = self._wake = None
        self._request = None
        self._birth = None
        try:
            self._map = self._k.OpenFileMappingW(0xF001F, False, self.name)
            if not self._map:
                raise FileNotFoundError(C.get_last_error(), "native ghost renderer is unavailable")
            self._view = self._k.MapViewOfFile(self._map, 0xF001F, 0, 0, P.PAGE_BYTES)
            self._mutex = self._k.CreateMutexW(None, False, self.name + "_mutex")
            self._wake = self._k.CreateEventW(None, False, False, self.name + "_wake")
            if not self._view or not self._mutex or not self._wake:
                raise C.WinError(C.get_last_error())
            with self._locked():
                status = self._status()
                self._birth = (status.producer_created_lo, status.producer_created_hi)
                if _process_creation(self._k, pid) != self._birth:
                    raise ProcessLookupError("native ghost producer was replaced")
                previous = P.Request.from_buffer_copy(C.string_at(self._view + 256, C.sizeof(P.Request)))
                if (previous.token and previous.capture_enabled
                        and previous.generation == status.generation
                        and _process_creation(self._k, previous.owner_pid) == (
                            previous.owner_created_lo, previous.owner_created_hi)):
                    raise RuntimeError("native ghost capture already has an owner")
                created = _process_creation(self._k, os.getpid())
                if created is None:
                    raise ProcessLookupError("native ghost owner disappeared")
                request = P.Request()
                request.owner_pid = os.getpid()
                request.owner_created_lo, request.owner_created_hi = created
                request.generation = status.generation
                request.token = secrets.randbelow((1 << 64) - 1) + 1
                request.revision = request.heartbeat = request.capture_enabled = 1
                request.rom_generation = status.rom_generation
                _set_defaults(request)
                request.layout[:] = layout
                # The race ends at the x-cam, the dance, never the midair fall
                # before one (his ruling, 2026-09-28: "the ghost death / real
                # mario comparison should be based on the XCAM DANCE start"):
                # his latch and each ghost's end are its dance. A Bowser key
                # dances too; the Bowser 3 grand star's own cutscene is its
                # dance ("The key should work the same way as a normal star").
                actions = sorted(RACE_END_ACTIONS)
                if len(actions) > 8:
                    raise ValueError("native ghost star action registry exceeds channel capacity")
                request.star_action_count = len(actions)
                request.star_actions[:] = actions + [0] * (8 - len(actions))
                # Old buffered values belong to the preceding ownership lease.
                P.U64.from_address(self._view + P.Status.consumed.offset).value = status.produced
                self._request = request
                self._publish()
        except BaseException:
            close_after_error(self)
            raise

    @contextmanager
    def _locked(self):
        if not self._view or not self._mutex:
            raise RuntimeError("native ghost channel is closed")
        result = self._k.WaitForSingleObject(self._mutex, 50)
        if result not in (0, 0x80):
            raise BlockingIOError("native ghost channel is busy")
        try:
            yield
        finally:
            if not self._k.ReleaseMutex(self._mutex):
                raise C.WinError(C.get_last_error())

    def _status(self):
        result = P.checked_status(C.string_at(self._view, 256), self.pid)
        if self._birth is not None and self._birth != (
                result.producer_created_lo, result.producer_created_hi):
            raise ProcessLookupError("native ghost mapping changed producer")
        return result

    def _publish(self):
        C.memmove(self._view + 256, bytes(self._request), C.sizeof(P.Request))
        if not self._k.SetEvent(self._wake):
            raise C.WinError(C.get_last_error())

    def poll(self, playback: dict | None = None):
        """Copy a bounded batch and renew demand; never return mapped pointers."""
        # A mapping can outlive its producer because this client retains a
        # handle. Its last READY status is not proof that Project64 still lives.
        if _process_creation(self._k, self.pid) != self._birth:
            raise ProcessLookupError("native ghost producer ended or was replaced")
        with self._locked():
            status = self._status()
            request = self._request
            if request is None or request.generation != status.generation or not status.state:
                raise ProcessLookupError("native ghost channel has ended")
            current = P.Request.from_buffer_copy(C.string_at(self._view + 256, C.sizeof(P.Request)))
            if current.token != request.token:
                raise RuntimeError("native ghost lease was replaced")
            if playback is not None:
                try:
                    self._set_playback(playback)
                except (KeyError, TypeError, ValueError, OverflowError) as error:
                    raise RejectedControl(f"native ghost command rejected: {error!r}") from error
                request = self._request
            if request.rom_generation != status.rom_generation:
                request.rom_generation = status.rom_generation
                self._next_revision()
            request.heartbeat = (request.heartbeat + 1) & 0xFFFFFFFF
            self._publish()
            frames = tuple(C.string_at(self._view + P.RING_OFFSET + (slot % P.SLOTS) * P.RECORD_BYTES,
                                       P.RECORD_BYTES)
                           for slot in range(status.consumed, status.produced))
            P.U64.from_address(self._view + P.Status.consumed.offset).value = status.produced
            if status.ack_token != request.token or status.ack_revision != request.revision:
                # The copied records still carry their original producer, but
                # old playback telemetry cannot acknowledge a newer selection.
                status.displayed = 0
                status.playback_reason = 1  # GP_PREPARING
                status.state = 3 if request.play_enabled else 2
            return status, frames

    def _next_revision(self):
        if self._request.revision == (1 << 64) - 1:
            raise OverflowError("native ghost command identity exhausted")
        self._request.revision += 1

    def _set_playback(self, value: dict):
        # Construct first: rejected controls cannot partly mutate a live command.
        request = P.Request.from_buffer_copy(bytes(self._request))
        retired = sorted(set(value) & {"clip_id", "clip_sha256", "clip_path", "hue_degrees"})
        if retired:
            raise ValueError(f"native ghost clips are a list; retired keys: {retired}")
        enabled = bool(value.get("enabled", False))
        opacity, nudge = float(value.get("opacity", DEFAULT_OPACITY)), int(value.get("nudge", 0))
        if not 0 <= opacity <= 1 or not -NUDGE_LIMIT_FRAMES <= nudge <= NUDGE_LIMIT_FRAMES:
            raise ValueError("invalid native ghost opacity or timing nudge")
        request.play_enabled, request.opacity, request.nudge = enabled, opacity, nudge
        request.stop_at_star = bool(value.get("stop_at_star", True))
        request.owner_suspended = bool(value.get("owner_suspended", False))
        proximity = checked_proximity({
            "enabled": value.get("proximity_enabled", DEFAULT_PROXIMITY["enabled"]),
            "extra_distance": value.get("proximity_distance", DEFAULT_PROXIMITY["extra_distance"]),
        })
        request.proximity_enabled = proximity["enabled"]
        request.proximity_distance = proximity["extra_distance"]
        request.tint_enabled = bool(value.get("tint_enabled", False))
        request.volume = _volume(value)
        for field, default in native_switches().items():
            setattr(request, field, _switch(value, field, default))
        _set_annotations(request, value)
        mode, seek = value.get("mode", 0), value.get("seek_igt", 0)
        if type(mode) is not int or mode not in (0, 1) or type(seek) is not int or not 0 <= seek < 2**32:
            raise ValueError("invalid native ghost seek")
        request.mode, request.seek_igt, request.origin = mode, seek, P.Origin()
        if enabled and mode == 0:
            origin = value.get("origin", {})
            if not isinstance(origin, dict):
                raise ValueError("native ghost origin is missing")
            for field, bits in (("kind", 32), ("configuration", 32), ("epoch", 32),
                                ("global_timer", 32), ("session_id", 64), ("arm_id", 64)):
                scalar = origin.get(field)
                if type(scalar) is not int or not (0 if field == "global_timer" else 1) <= scalar < 2**bits:
                    raise ValueError("native ghost origin identity is invalid")
                setattr(request.origin, field, scalar)
            instant = origin.get("utc")
            if (request.origin.kind not in (1, 2) or type(instant) not in (int, float)
                    or not math.isfinite(instant) or instant <= 0):
                raise ValueError("native ghost origin timestamp is invalid")
            request.origin.utc = instant
        if enabled:
            model_id = value["model_id"]
            if type(model_id) is not int or not 0 < model_id < 2**64:
                raise ValueError("native ghost model identity is invalid")
            request.model_id = model_id
            units = P.path_units(value["rom_path"]) if value.get("rom_path") else ()
            request.rom_path[:] = units + (0,) * (P.PATH_CHARS - len(units))
        if enabled or "clips" in value:
            _set_clips(request, value.get("clips"), enabled)
        _set_path(request, value)
        if request.revision == (1 << 64) - 1:
            raise OverflowError("native ghost command identity exhausted")
        self._request = request
        self._next_revision()

    def close(self):
        if self._request is not None and self._view:
            with self._locked():
                current = P.Request.from_buffer_copy(C.string_at(self._view + 256, C.sizeof(P.Request)))
                if current.token == self._request.token:
                    self._request.capture_enabled = self._request.play_enabled = 0
                    self._next_revision()
                    self._publish()
            self._request = None
        release_many(self, (("_view", "UnmapViewOfFile"), ("_map", "CloseHandle"),
                            ("_wake", "CloseHandle"), ("_mutex", "CloseHandle")))


CLIP_KEYS = {"clip_id", "clip_sha256", "clip_path", "hue_degrees"}


def _set_defaults(request) -> None:
    """A new lease's controls: each setting's registry default."""
    request.opacity, request.volume = DEFAULT_OPACITY, DEFAULT_VOLUME
    request.name_background = NAME_BACKGROUNDS[DEFAULT_NAME_BACKGROUND]
    request.name_background_opacity = DEFAULT_NAME_BACKGROUND_OPACITY
    for field, default in native_switches().items():
        setattr(request, field, int(default))
    for field, default in native_path_defaults().items():
        setattr(request, field, default)


def _switch(value: dict, key: str, default: bool) -> bool:
    """A native A/B switch (`ghost/settings.py::native_switches`); absent means its default."""
    setting = value.get(key, default)
    if type(setting) is not bool:
        raise ValueError(f"native ghost {key} must be a bool")
    return setting


def _set_annotations(request, value: dict) -> None:
    """The input display's mode and the name's box, each absent key its default."""
    input_mode = value.get("input_mode", INPUT_MODES[DEFAULT_INPUT_MODE])
    if type(input_mode) is not int or input_mode not in INPUT_MODES.values():
        raise ValueError("native ghost input display mode is invalid")
    background = value.get("name_background", NAME_BACKGROUNDS[DEFAULT_NAME_BACKGROUND])
    if type(background) is not int or background not in NAME_BACKGROUNDS.values():
        raise ValueError("native ghost name background is invalid")
    alpha = value.get("name_background_opacity", DEFAULT_NAME_BACKGROUND_OPACITY)
    if type(alpha) not in (int, float) or not math.isfinite(alpha) or not 0 <= alpha <= 1:
        raise ValueError("native ghost name background opacity must be a number in 0..1")
    request.input_mode, request.name_background = input_mode, background
    request.name_background_opacity = float(alpha)


#: Each path number the request carries: (accepted types, low, high); `path_clip` is checked against the clips.
PATH_BOUNDS = {
    "path_source": ((int,), min(PATH_SOURCES.values()), max(PATH_SOURCES.values())),
    "path_ahead_ms": ((int,), L.GP_PATH_AHEAD_MS_MIN, L.GP_PATH_AHEAD_MS_MAX),
    "path_width": ((int, float), L.GP_PATH_WIDTH_MIN, L.GP_PATH_WIDTH_MAX),
    "path_opacity": ((int, float), 0, 1),
    "path_spacing_ms": ((int,), L.GP_PATH_SPACING_MS_MIN, L.GP_PATH_SPACING_MS_MAX),
}


def _set_path(request, value: dict) -> None:
    """The path's numbers, each absent key its registry default, plus the clip it
    follows: an index into the clips just encoded (0 while there are none), so
    the renderer is never handed a ghost that is not playing."""
    numbers = {}
    for field, default in native_path_defaults().items():
        number = value.get(field, default)
        kinds, low, high = PATH_BOUNDS[field]
        if type(number) not in kinds or not math.isfinite(number) or not low <= number <= high:
            raise ValueError(f"native ghost {field} must be a number in {low}..{high}")
        numbers[field] = number
    clip = value.get("path_clip", 0)
    if type(clip) is not int or not 0 <= clip < max(request.clip_count, 1):
        raise ValueError("native ghost path_clip must name one of the clips")
    for field, number in numbers.items():
        setattr(request, field, number)
    request.path_clip = clip


def _volume(value: dict) -> float:
    """Ghost sound loudness (absent: half; 0 is exact silence natively)."""
    volume = value.get("volume", DEFAULT_VOLUME)
    if type(volume) not in (int, float) or not math.isfinite(volume) or not 0 <= volume <= 1:
        raise ValueError("native ghost volume must be a number in 0..1")
    return float(volume)


def _bytes(value) -> bytes:
    return bytes(value) if isinstance(value, (bytes, bytearray, memoryview)) else b""


def _set_clips(request, clips, enabled: bool):
    """Encode the displayed set in request order; every entry is checked first."""
    if not isinstance(clips, (list, tuple)) or not (1 if enabled else 0) <= len(clips) <= P.GHOSTS:
        raise ValueError(f"native ghost set needs 1..{P.GHOSTS} clips")
    encoded, seen = [], set()
    for entry in clips:
        if not isinstance(entry, dict) or not CLIP_KEYS <= set(entry) or set(entry) - CLIP_KEYS - {"label"}:
            raise ValueError(f"native ghost clip needs {sorted(CLIP_KEYS)} and an optional label")
        clip_id, digest, hue = _bytes(entry["clip_id"]), _bytes(entry["clip_sha256"]), entry["hue_degrees"]
        if len(clip_id) != 16 or not any(clip_id) or len(digest) != 32 or clip_id in seen:
            raise ValueError("native ghost clip identity is missing or repeated")
        if type(hue) is not int or not 0 <= hue <= 359:
            raise ValueError("invalid native ghost hue")
        seen.add(clip_id)
        clip = P.Clip()
        clip.clip_id[:], clip.clip_sha256[:], clip.hue_degrees = clip_id, digest, hue
        clip.label = P.label_bytes(entry.get("label", ""))
        units = P.path_units(entry["clip_path"])
        clip.clip_path[:] = units + (0,) * (P.PATH_CHARS - len(units))
        encoded.append(clip)
    request.clips[:] = encoded + [P.Clip() for _ in range(P.GHOSTS - len(encoded))]
    request.clip_count = len(encoded)

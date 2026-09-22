"""One thread's ownership of a native Ghost mapping and command lease."""
from __future__ import annotations

import ctypes as C
import math
import os
import secrets
from contextlib import contextmanager

from sm64_events.ghost import native_protocol as P
from sm64_events.ghost.proximity import DEFAULT_PROXIMITY, checked_proximity
from sm64_events.memory.addresses import STAR_GRAB_ACTIONS
from sm64_events.replay.capturecontrol import _kernel, _process_creation
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
                request.opacity = 0.5
                request.layout[:] = layout
                actions = sorted(STAR_GRAB_ACTIONS)
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
                self._set_playback(playback)
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
        enabled = bool(value.get("enabled", False))
        opacity, nudge = float(value.get("opacity", 0.5)), int(value.get("nudge", 0))
        if not 0 <= opacity <= 1 or not -600 <= nudge <= 600:
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
        hue = value.get("hue_degrees", 0)
        if type(hue) is not int or not 0 <= hue <= 359:
            raise ValueError("invalid native ghost hue")
        request.hue_degrees = hue
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
            clip_id, digest = bytes(value["clip_id"]), bytes(value["clip_sha256"])
            if not request.model_id or len(clip_id) != 16 or not any(clip_id) or len(digest) != 32:
                raise ValueError("native ghost asset identity is missing")
            request.clip_id[:], request.clip_sha256[:] = clip_id, digest
            for field in ("rom_path", "clip_path"):
                units = (() if field == "rom_path" and not value.get(field)
                         else P.path_units(value[field]))
                getattr(request, field)[:] = units + (0,) * (520 - len(units))
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

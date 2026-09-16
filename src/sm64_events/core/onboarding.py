"""Observed setup readiness and its persisted outcome, separate from grading.

A plugin heartbeat never proves a game or pictures. JP can complete the
available installation, but the result explicitly excludes JP tracking.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from sm64_events.memory.version_probe import NAME_FIELD, normalise_header, version_from_header

log = logging.getLogger("sm64.setup")
JP_WARNING = ("JP tracking isn't supported yet; support is planned for a later patch. "
              "You can finish setup now.")


def identify_rom(raw: bytes | None) -> dict:
    """Identity from the loaded cartridge; a preference is never detection."""
    header = normalise_header(raw) if raw else None
    if header is None:
        return {"state": "missing", "region": None, "name": None, "warning": None}
    name = header[NAME_FIELD].decode("ascii", errors="replace").rstrip(" \0")
    region = version_from_header(header)
    state = "unsupported"
    if name == "SM64 USAMUNE v1.93u" and region == "us":
        state = "supported"
    elif name.startswith("SM64 USAMUNE") and region == "jp":
        state = "jp"
    return {"state": state, "region": region, "name": name,
            "warning": JP_WARNING if state == "jp" else None}


#: The cartridges the trainer practises on. Any other ROM -- vanilla SM64,
#: another hack, another game -- runs with the practice tooling off: the
#: poller does not serve it, the recorder does not capture, and the graphics
#: plugin behaves as plain GLideN64 (his ruling, 2026-09-16). The native
#: mirror is plugin/gfxwrap/practice_rom.h; tests/test_practice_rom.py
#: compares the two over the same headers.
PRACTICE_ROM_STATES = frozenset({"supported", "jp"})


def is_practice_rom(identity: dict) -> bool:
    """True for an `identify_rom` result the practice tooling runs on."""
    return identity.get("state") in PRACTICE_ROM_STATES


class CounterWindow:
    """Recent movement survives repeated readers, but not a new source.

    First observations establish a baseline. Counter resets establish another;
    neither can earn success. A stalled source expires even if polled often.
    """

    def __init__(self, clock=time.monotonic, freshness_s=3.0):
        self.clock = clock
        self.freshness_s = freshness_s
        self.key = None
        self.values = {}
        self.moved = {}

    def observe(self, key, **values) -> dict:
        now = self.clock()
        if key != self.key:
            self.key, self.values, self.moved = key, {}, {}
        for name, value in values.items():
            previous = self.values.get(name)
            if value is None or (previous is not None and value < previous):
                self.moved.pop(name, None)
            elif previous is not None and value > previous:
                self.moved[name] = now
            self.values[name] = value
        return {name: value is not None and name in self.moved
                and now - self.moved[name] < self.freshness_s
                for name, value in values.items()}


def readiness(layer, observation: dict) -> dict:
    """The current next action. JP bypasses only unimplemented tracking."""
    target = observation.get("target", {})
    rom = observation.get("rom", identify_rom(None))
    checks = observation.get("checks", {})
    installed = layer.installation_verified
    if target.get("state") != "ready":
        step, message = "emulator", target.get("message", "Open Project64 v1.6.")
    elif not installed:
        step = "close" if layer.pj64_running else "install"
        message = "Close Project64 before installing." if layer.pj64_running else "Install Practice Replay."
    elif not layer.pj64_running:
        step, message = "reopen", "Open Project64 v1.6."
    elif rom["state"] == "missing":
        step, message = "rom", "Open Usamune in Project64."
    elif rom["state"] == "unsupported":
        step, message = "rom", "Open Usamune v1.93u (US). This game isn't supported by the trainer."
    elif not checks.get("plugin") or not checks.get("pictures"):
        step, message = "verify", "Waiting for game pictures. Keep Usamune running."
    elif rom["state"] == "jp":
        step, message = "ready", "Practice Replay installed. JP tracking will arrive in a later patch."
    elif not checks.get("inputs") or not checks.get("game"):
        step, message = "verify", "Waiting for game and controller data. Keep Usamune running; no controller input is needed."
    else:
        step, message = "ready", "Game pictures and inputs verified."
    if layer.state == "unavailable":
        step, message = "unavailable", "This build doesn't include Practice Replay. Install the latest trainer build."
    return {"step": step, "message": message, "ready": step == "ready",
            "limited": rom["state"] == "jp", "installed": installed,
            "checks": checks}


class SetupRecord:
    """Versioned completion, never confused with current emulator health."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def read(self) -> dict:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and value.get("schema") == 1:
                return value
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            log.warning("could not read setup progress at %s", self.path, exc_info=True)
        return {}

    def write(self, **fields) -> dict:
        with self._lock:
            value = {**self.read(), "schema": 1, **fields}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            scratch = self.path.with_suffix(".tmp")
            scratch.write_text(json.dumps(value, indent=2), encoding="utf-8")
            scratch.replace(self.path)
            return value

    def complete(self, platform: str, limited: bool) -> dict:
        return self.write(platform=platform, limited=limited, started=True,
                          completed_at=datetime.now(timezone.utc).isoformat())

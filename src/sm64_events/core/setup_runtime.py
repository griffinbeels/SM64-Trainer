"""Read-only setup observations; never attach through the recorder's handle.

Only main wires this Windows adapter. Fixtures inject observations and never
enumerate real processes or read the user's game. Reads are throttled and
serialized so extra browser clients cannot consume each other's health edge.
"""
from __future__ import annotations

import logging
import threading
import time

from sm64_events.core.onboarding import CounterWindow, identify_rom

log = logging.getLogger("sm64.setup")


class SetupRuntime:
    def __init__(self, processes, memory_factory, poller, replay, clock=time.monotonic):
        self.processes = processes
        self.memory_factory = memory_factory
        self.poller = poller
        self.replay = replay
        self.clock = clock
        self._lock = threading.Lock()
        self._next_read = 0.0
        self._cached = {}
        self._cached_layer = None
        self._window = CounterWindow(clock)

    def _rom(self, target) -> dict:
        if target.get("pid") is None:
            return identify_rom(None)
        memory = self.memory_factory()
        try:
            return identify_rom(memory.rom_header() if memory.attach() else None)
        except (OSError, RuntimeError):
            log.warning("game identity is temporarily unreadable", exc_info=True)
            return identify_rom(None)
        finally:
            memory.detach()

    def __call__(self, layer) -> dict:
        with self._lock:
            gpu = getattr(layer, "gpu_observation", None)
            gpu_key = (gpu.identity, gpu.idle, gpu.alive, gpu.pictures) if gpu else None
            identity = (layer.pj64_running, layer.pj64_dir, layer.plugin_pid,
                        layer.layer_alive, layer.consented_at,
                        gpu_key)
            if self.clock() < self._next_read and identity == self._cached_layer:
                return self._cached
            result = self._observe(layer)
            self._cached = result
            self._cached_layer = identity
            self._next_read = self.clock() + 0.75
            return result

    def _observe(self, layer) -> dict:
        target = self.processes.setup_target()
        if target["state"] == "missing" and layer.pj64_dir:
            target = self.processes.check_folder(layer.pj64_dir)
        rom = self._rom(target) if target["state"] == "ready" else identify_rom(None)
        recorder = self.replay.recorder.status() if self.replay else {}
        health = recorder.get("frame_source_health") or {}
        sampler = getattr(self.poller, "input_sampler", None)
        inputs = sampler.health() if sampler else {}
        latest = getattr(self.poller, "latest", None)
        gpu = getattr(layer, "gpu_observation", None)
        counters = self._window.observe(
            (target.get("pid"), rom["name"], rom["region"], layer.plugin_pid,
             gpu.identity if gpu else None),
            delivered=health.get("delivered"), inputs=inputs.get("frames"),
            game=latest.global_timer if latest else None)
        same_process = target.get("pid") is not None and target["pid"] == layer.plugin_pid
        plugin = same_process and layer.layer_alive
        captured = (recorder.get("recording") and recorder.get("frame_source") == "plugin"
                    and counters["delivered"])
        pictures = captured
        if gpu is not None:
            # The GPU observer validates native control identity and counts only
            # accepted captured pictures. Encoder repeats cannot refresh it.
            captured = gpu.matches(recorder)
            plugin = plugin and captured
            if gpu.idle:
                # Passive ControlV1 deliberately has no heartbeat timer. This
                # exception needs actual game/input movement even for limited
                # JP setup; a live process alone cannot prove a running game.
                plugin = bool(plugin and counters["inputs"] and counters["game"]
                              and not self.poller.paused
                              and not getattr(self.poller, "hold_reason", None))
            pictures = gpu.pictures and captured
        checks = {"plugin": bool(plugin), "pictures": bool(plugin and pictures),
                  "inputs": bool(counters["inputs"] and rom["state"] == "supported"),
                  "game": bool(counters["game"] and not self.poller.paused
                               and not getattr(self.poller, "hold_reason", None)
                               and rom["state"] == "supported")}
        return {"target": target, "rom": rom, "checks": checks,
                "capture_note": recorder.get("frame_source_note"),
                "paused": self.poller.paused,
                "tracking_note": getattr(self.poller, "hold_reason", None)}

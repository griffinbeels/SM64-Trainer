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
    #: How long a Project64 with no game booted (no RDRAM to attach to) is
    #: left before this observer walks its memory again.
    ATTACH_RETRY_S = 2.0

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
        # ONE reader per Project64 process, kept while that process runs. The
        # setup screen observes once a second, and a fresh reader each time
        # attached anew: a process-list snapshot, a debug-privileged open and a
        # walk of Project64's memory to find RDRAM, 38 times in one minute of
        # live play (2026-09-28). Its own reader, never the poller's.
        self._reader = None
        self._reader_pid: int | None = None
        self._refused: tuple[int, float] | None = None   # (pid, when) attach found no RDRAM

    def _rom(self, target) -> dict:
        reader = self._reader_for(target.get("pid"))
        if reader is None:
            return identify_rom(None)
        try:
            return identify_rom(reader.rom_header())
        except (OSError, RuntimeError):
            log.warning("game identity is temporarily unreadable", exc_info=True)
            self._release_reader()
            return identify_rom(None)

    def _reader_for(self, pid):
        """This process's reader, attached once; None while there is no
        Project64 or it has no game booted yet."""
        if pid is None:
            self._release_reader()
            return None
        if self._reader_pid == pid and self._reader.alive():
            return self._reader
        self._release_reader()
        if (self._refused is not None and self._refused[0] == pid
                and self.clock() - self._refused[1] < self.ATTACH_RETRY_S):
            return None
        reader = self.memory_factory()
        try:
            attached = reader.attach(pid=pid)
        except (OSError, RuntimeError):
            log.warning("game identity is temporarily unreadable", exc_info=True)
            attached = False
        if not attached:
            reader.detach()
            self._refused = (pid, self.clock())
            return None
        self._reader, self._reader_pid, self._refused = reader, pid, None
        return reader

    def _release_reader(self) -> None:
        reader, self._reader, self._reader_pid = self._reader, None, None
        if reader is not None:
            reader.detach()

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
        rom = self._rom(target if target["state"] == "ready" else {})
        recorder = self.replay.recorder.status() if self.replay else {}
        health = recorder.get("frame_source_health") or {}
        # This Project64's adapter can never record through the layer, so the
        # recorder keeps the desktop grab: ITS written pictures are the proof.
        unsupported = recorder.get("gpu_unsupported")
        sampler = getattr(self.poller, "input_sampler", None)
        inputs = sampler.health() if sampler else {}
        latest = getattr(self.poller, "latest", None)
        gpu = getattr(layer, "gpu_observation", None)
        counters = self._window.observe(
            (target.get("pid"), rom["name"], rom["region"], layer.plugin_pid,
             gpu.identity if gpu else None),
            delivered=recorder.get("pictures_fed") if unsupported else health.get("delivered"),
            inputs=inputs.get("frames"),
            game=latest.global_timer if latest else None)
        same_process = target.get("pid") is not None and target["pid"] == layer.plugin_pid
        plugin = same_process and layer.layer_alive
        captured = (recorder.get("recording") and recorder.get("frame_source") == "plugin"
                    and counters["delivered"])
        pictures = captured
        if unsupported:
            # No GPU lease means the control page's heartbeat never moves, so
            # the layer is proved the way the passive exception below proves
            # it: its page names this Project64 and the game and inputs move.
            plugin = bool(same_process and counters["inputs"] and counters["game"]
                          and not self.poller.paused
                          and not getattr(self.poller, "hold_reason", None))
            pictures = bool(recorder.get("recording")
                            and recorder.get("frame_source") == "desktop"
                            and counters["delivered"])
        elif gpu is not None:
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

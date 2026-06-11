# src/sm64_events/server/poller.py
"""60 Hz poll loop: snapshot -> detectors -> broadcast.

Polling at ~60 Hz against 30 Hz game logic means every game frame is
observed; the star dance lasts ~60-90 frames so edges cannot be missed.
"""
import asyncio
import logging
from datetime import datetime, timezone

from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot, SnapshotReader
from sm64_events.memory.base import MemoryReadError

log = logging.getLogger("sm64.poller")


def _lifecycle_event(type_: str) -> Event:
    return Event(type=type_, frame=0,
                 timestamp_utc=datetime.now(timezone.utc), payload={})


def _plausible(snap: GameSnapshot) -> bool:
    """Layout sanity: values the real game can never produce mean the
    address registry doesn't match this ROM — refuse rather than emit
    wrong star IDs (spec: hard refusal on layout mismatch)."""
    return (0 <= snap.num_stars <= 182
            and 0 <= snap.last_completed_course <= 25
            and 0 <= snap.last_completed_star <= 7)


class Poller:
    def __init__(self, memory, detectors, broadcaster, hz: int = 60, reader=None):
        self.memory = memory
        self.detectors = list(detectors)
        self.broadcaster = broadcaster
        self.interval = 1.0 / hz
        self.reader = reader or SnapshotReader(memory)
        self.latest: GameSnapshot | None = None
        self._prev: GameSnapshot | None = None

    async def tick(self) -> None:
        try:
            curr = self.reader.read()
        except MemoryReadError:
            log.warning("lost emulator; detaching")
            self.memory.detach()
            self._prev = None
            self.latest = None
            await self.broadcaster.publish(_lifecycle_event("emulator_disconnected"))
            return
        if not _plausible(curr):
            log.error("memory layout mismatch (impossible values read) — "
                      "refusing to emit events; check the address registry")
            self.memory.detach()
            self._prev = None
            self.latest = None
            return
        if self._prev is not None:
            for detector in self.detectors:
                try:
                    events = detector.process(self._prev, curr)
                except Exception:
                    log.exception("detector %s failed; skipped this tick",
                                  type(detector).__name__)
                    continue
                for event in events:
                    await self.broadcaster.publish(event)
        self._prev = curr
        self.latest = curr

    def _probe(self) -> bool:
        """Post-attach layout check: refuse to serve a ROM whose reads are
        impossible for SM64 (e.g. wrong ROM loaded in the emulator)."""
        try:
            curr = self.reader.read()
        except MemoryReadError:
            self.memory.detach()
            return False
        if not _plausible(curr):
            log.error("memory layout mismatch (impossible values read) — "
                      "refusing to serve; check ROM / address registry")
            self.memory.detach()
            return False
        return True

    async def run(self) -> None:
        while True:
            if not self.memory.attached:
                if not self.memory.attach():
                    await asyncio.sleep(2.0)
                    continue
                if not self._probe():
                    await asyncio.sleep(5.0)
                    continue
                await self.broadcaster.publish(_lifecycle_event("emulator_connected"))
            await self.tick()
            await asyncio.sleep(self.interval)

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
from sm64_events.detectors.anchors import BOOT_TIMER_MAX
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
        # last good global_timer, kept ACROSS a detach (unlike _prev) so a
        # console reset (F1) that detaches us mid-reset is still recognised on
        # reattach — see tick().
        self._last_timer: int | None = None
        self.paused = False  # session pause: run() idles — no reads, no events

    def set_paused(self, paused: bool) -> None:
        """Session pause (POST /api/pause): while paused, run() neither
        reads memory nor dispatches detectors — gameplay is intentionally
        unobserved (no events, no journal rows). On RESUME, _prev resets so
        detectors receive a fresh establishing pair: the same self-heal
        contract as emulator reattach (LevelChangeDetector re-establishes
        level state via corrective events; a stale open attempt closes by
        the normal next-anchor rules)."""
        if self.paused == paused:
            return
        self.paused = paused
        if not paused:
            self._prev = None  # resume = fresh attach for detector streams
        log.info("session %s", "paused" if paused else "resumed")

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
        # Reset-across-reattach synthesis (live gate 2026-06-15): an F1 console
        # reset makes RDRAM briefly implausible/unreadable, so the poller
        # detaches and reattaches — which nulls _prev and breaks the consecutive
        # pair GameResetDetector needs to see the backward-into-boot jump (live
        # journal: gGlobalTimer 206 -> [detach/reattach] -> 96, game_reset never
        # fired). _last_timer survives the gap: when the stream is freshly
        # (re)established (_prev is None) and the timer dropped from above the
        # boot range into it, emit the game_reset that was lost. In the no-detach
        # case _prev is not None, so GameResetDetector fires instead — exactly
        # one of the two fires (mirrors lifecycle.py / anchors.py).
        # (Residual edge: if reattach lands AFTER boot, timer >= BOOT_TIMER_MAX,
        # so a slow reattach can still miss it — acceptable; F1 reattach observed
        # in the boot range.)
        if (self._prev is None and self._last_timer is not None
                and self._last_timer >= BOOT_TIMER_MAX
                and curr.global_timer < BOOT_TIMER_MAX):
            await self.broadcaster.publish(Event(
                type="game_reset", frame=curr.global_timer,
                timestamp_utc=curr.wall_time_utc, payload={}))
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
        self._last_timer = curr.global_timer

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
            if self.paused:
                await asyncio.sleep(0.2)  # bounds resume latency; zero reads
                continue
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

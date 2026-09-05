# src/sm64_events/server/poller.py
"""250 Hz poll loop: sample the pad every tick, snapshot once per GAME frame.

The rate is set by the CONTROLLER, not by the game. The game rewrites its
controller struct ~62% of the way THROUGH each frame
(`addresses.CONTROLLER_SETTLE_PHASE`), so a loop must look at the pad after
that point or it reads the previous frame's input. Measured with
`tools/probe_inputs.py` over four live sessions, 2026-08-20: at 60 Hz the last
look of a frame lands at 50% and reads FRESH on 0% of frames — one frame late,
every frame, invisibly. At 250 Hz it lands at 85% and reads fresh on 99%.

Nothing else needs that rate. The game produces thirty frames a second, so the
full snapshot and every detector run ONCE per game frame, late in it — which
is both fresher than the old loop (whose phase was wherever the sleep landed)
and cheaper: ~1.5% of a core against the ~20% the 60 Hz loop cost before the
snapshot's byte swap came out.

Two numbers that used to live in this docstring as folklore, both measured the
same day: the old `hz=60` was the REQUESTED rate and 45.9 Hz was the achieved
one (each tick worked for 3.4 ms and then slept a full interval), and no game
frame went unobserved at any rate tried — 60, 120, 250 or 500.
"""
import asyncio
import logging
from datetime import datetime, timezone
from time import perf_counter

from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot, SnapshotReader
from sm64_events.core.timefmt import GAME_FPS
from sm64_events.memory import addresses as A
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
    SAMPLING_HZ = 250        # with a pad to catch after the game's rewrite
    SNAPSHOT_HZ = 60         # without one: the old loop, every tick a read

    def __init__(self, memory, detectors, broadcaster, hz: int | None = None,
                 reader=None, on_frame=None, input_sampler=None,
):
        self.memory = memory
        self.detectors = list(detectors)
        self.broadcaster = broadcaster
        # The rate follows the sampler. Without one there is nothing to do
        # between game frames and every tick reads the whole snapshot -- so
        # 250 Hz would be eight snapshots per game frame for no reason, on
        # exactly the layouts (a version with no controller row yet) that
        # have the least to spend.
        if hz is None:
            hz = (self.SAMPLING_HZ if input_sampler is not None
                  else self.SNAPSHOT_HZ)
        # The input half runs EVERY tick; the snapshot and the detectors run
        # once per GAME frame. See this module's docstring for why the rate
        # moved and why that costs less than the 60 Hz loop it replaces.
        self.input_sampler = input_sampler
        self._frame_now: int | None = None      # the frame ticks are landing in
        self._snapshot_frame: int | None = None  # the last frame we snapshotted
        self._ticks_in_frame = 0
        # Consecutive ticks the sampler could not read. On the sampler-paced
        # path a snapshot is due only when the FRAME advances, and a dead
        # emulator advances nothing -- so the read that would notice it
        # (MemoryReadError -> detach -> re-attach) was never reached: the
        # tracker sat "attached" to a closed Project64 with a frozen
        # snapshot (2026-09-05, the first install of the capture layer:
        # PJ64 closed and reopened under a live tracker, and nothing came
        # back). Past UNREADABLE_TICKS_BEFORE_READ the tick reads anyway.
        self._unreadable_ticks = 0
        # Wait until the game has FINISHED writing a frame before reading it.
        # The pad's own rewrite lands ~62% in (addresses.CONTROLLER_SETTLE_PHASE,
        # measured); the phases of the other fields are UNMEASURED, so this is
        # "at least as late as the one field we checked", not a claim about all
        # of them. Snapshotting on the frame-change tick instead would take
        # every reading at the EARLIEST moment in a frame, which is worse than
        # the arbitrary phase it replaced.
        self._settle_ticks = max(
            1, round(A.CONTROLLER_SETTLE_PHASE * hz / GAME_FPS))
        # Awaited with the live game frame after each tick's events are
        # published — the tracker's deferred-judgement heartbeat (main.py wires
        # TrackerService.settle_frame). Injected rather than duck-typed off
        # `broadcaster`, which is sometimes a plain Broadcaster: this poller's
        # sink contract is publish(), and a clock consumer is a second concern.
        self.on_frame = on_frame
        self.interval = 1.0 / hz
        self.reader = reader or SnapshotReader(memory)
        # Set when the reader can never read (core/snapshot.py::UnreadyReader
        # -- a version whose layout is not verified yet). The attach probe
        # then keeps the poller detached; /health carries the reason.
        self.hold_reason: str | None = getattr(self.reader, "reason", None)
        self.latest: GameSnapshot | None = None
        self._prev: GameSnapshot | None = None
        # last good global_timer, kept ACROSS a detach (unlike _prev) so a
        # console reset (F1) that detaches us mid-reset is still recognised on
        # reattach — see tick().
        self._last_timer: int | None = None
        self.paused = False  # session pause: run() idles — no reads, no events
        # per-tick detector-COMPUTE timing — the "performance over a session"
        # CPU signal the memory probes can't see. A climbing EMA = detector
        # dispatch slowing (the user's "inefficient function calls" hypothesis).
        # Fed to the perf monitor via perf_stats().
        self._tick_ms_ema = 0.0
        self._tick_ms_max = 0.0
        self._tick_count = 0

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

    def _due_for_a_snapshot(self) -> bool:
        """Sample the pad, and say whether this tick should read the game.

        Returns True at most once per game frame, on the first tick at or
        after the settle point — or on the tick that ENDS a frame which never
        reached it, because a frame nobody snapshotted is a frame whose events
        nobody saw.

        It does NOT decide which frame the read will describe. `tick()` takes
        that from the snapshot's own counter, because on the short-frame path
        the read lands in the frame that just STARTED and claiming otherwise
        would file a reading under a frame it cannot possibly hold.
        """
        frame_now = self.input_sampler.sample()
        if frame_now is None:
            self._unreadable_ticks += 1
            return False                       # straddled or unreadable
        self._unreadable_ticks = 0
        if frame_now != self._frame_now:
            ended = self._frame_now
            self._frame_now = frame_now
            self._ticks_in_frame = 0
            return ended is not None and self._snapshot_frame != ended
        self._ticks_in_frame += 1
        return (self._snapshot_frame != frame_now
                and self._ticks_in_frame >= self._settle_ticks)

    #: consecutive unreadable sampler ticks (half a second at 250 Hz) before
    #: the tick reads the snapshot regardless, so a dead emulator detaches
    UNREADABLE_TICKS_BEFORE_READ = 125

    async def tick(self) -> None:
        if self.input_sampler is not None:
            due_for_a_snapshot = self._due_for_a_snapshot()
            if (not due_for_a_snapshot
                    and self._unreadable_ticks < self.UNREADABLE_TICKS_BEFORE_READ):
                return
            if not due_for_a_snapshot:
                # Half a second of unreadable samples: read the snapshot
                # anyway so a dead emulator raises and detaches below.
                self._unreadable_ticks = 0
        try:
            curr = self.reader.read()
            # Which frame this reading DESCRIBES comes from the snapshot's own
            # counter, never from the sampler's guess before the read.
            self._snapshot_frame = curr.global_timer
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
            # Time the synchronous detector COMPUTE only (not the awaited
            # broadcast I/O): collect, measure, then publish.
            t0 = perf_counter()
            out: list[Event] = []
            for detector in self.detectors:
                try:
                    out.extend(detector.process(self._prev, curr))
                except Exception:
                    log.exception("detector %s failed; skipped this tick",
                                  type(detector).__name__)
            self._record_tick_ms((perf_counter() - t0) * 1000)
            for event in out:
                await self.broadcaster.publish(event)
            # AFTER this tick's events: an event on this frame may record the
            # very position change being judged, and closures-before-arming is
            # the order the whole engine keeps.
            if self.on_frame is not None:
                await self.on_frame(curr.global_timer)
        self._prev = curr
        self.latest = curr
        self._last_timer = curr.global_timer

    def _record_tick_ms(self, dt_ms: float) -> None:
        self._tick_count += 1
        if dt_ms > self._tick_ms_max:
            self._tick_ms_max = dt_ms
        self._tick_ms_ema = (dt_ms if self._tick_count == 1
                             else 0.99 * self._tick_ms_ema + 0.01 * dt_ms)

    def perf_stats(self) -> dict:
        """Per-tick detector-compute timing for the perf monitor's gauges. The
        windowed max RESETS on read (one read per monitor interval); the EMA is
        a cumulative trend — a climbing EMA over a session is the 'inefficient
        calls building up' signature the memory probes can't catch."""
        m = self._tick_ms_max
        self._tick_ms_max = 0.0
        return {"tick_ms_ema": round(self._tick_ms_ema, 3),
                "tick_ms_max": round(m, 3), "ticks": self._tick_count}

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

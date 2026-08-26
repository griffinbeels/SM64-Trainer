# src/sm64_events/memory/present.py
"""Hunt PJ64's HOST-side present counter each session -- map v4's one address.

tools/probe_host_present.py found it live (2026-08-23): sweeping the
emulator's process memory OUTSIDE the emulated RDRAM/ROM, exactly ONE u32
carries the present-event signature -- it ticks 30.00 times a second (one
per game frame) while its PHASE against gGlobalTimer wanders in runs
(149 flips across 449 frames that session), which is the footage's +-1
logic->present desync seen from the RAM side at last. Emulation-side
mirrors hold phase perfectly and pure noise flips it every frame, so the
signature is selective in both directions.

The address is HEAP -- no loaded module contains it -- so it moves every
session and can never be a `memory/layout.py` row (those are per-ROM N64
addresses with a sync gate each; this is per-process host memory with no
fixed home). Production therefore HUNTS it by the probe's own signature at
attach, the way the RDRAM base is found by the osBootConfig signature:

1. SWEEP committed regions outside RDRAM (chunked, paced) for u32s that
   advanced at a display-ish rate across a short gap;
2. CONFIRM the survivors over two longer gaps;
3. CLASSIFY the finalists against gGlobalTimer at 250 Hz and keep the one
   whose rate sits at the game's and whose phase-flip share lands in the
   probe's measured wobble band.

The hunt needs the game RUNNING (a paused game ticks nothing) and takes
roughly half a minute of play, so it runs on its own daemon thread --
`server/poller.py` starts it whenever frames are advancing and no counter
is in hand, and reads the found counter on its 250 Hz tick. All reads go
through Pj64Memory's host accessors: read-only, like everything else.

A failed hunt cools down and retries; a found counter that stops ticking
while the game runs is invalidated by the poller's watchdog (heap reuse,
plugin restart) and hunted again. Every read failure degrades to "no
counter", which the frame map treats as "fall back to the v2 series" --
the hunt can only ever ADD precision.
"""
import logging
import threading
import time

import numpy as np

from sm64_events.memory.base import MemoryReadError

log = logging.getLogger("sm64.present")

# -- sweep -------------------------------------------------------------------
SWEEP_CHUNK = 32 * 1024 * 1024   # largest single read
# Chunks are paced in GROUPS up to this many bytes: read the group, sleep
# once, re-read, diff. The unit MUST be the group, not the chunk -- a 32-bit
# process holds hundreds of small regions (970 measured live, 2026-08-25),
# and one sleep per chunk made the sweep 341 s of pure sleep while its first
# log line waited at the end. Grouped: ~8 sleeps over 715 MB.
SWEEP_GROUP = 96 * 1024 * 1024
SWEEP_GAP_S = 0.35               # gap between a group's two read passes
CONFIRM_GAP_S = 1.0
CONFIRM_ROUNDS = 2
MAX_REGION = 512 * 1024 * 1024   # the probe's cap; bigger is never the heap
RATE_LO, RATE_HI = 20.0, 45.0    # ticks/s worth keeping during sweep/confirm
MAX_FINALISTS = 48

# -- classify ----------------------------------------------------------------
# The verdict bands were REBUILT 2026-08-25 after the first live find turned
# out to be a fake: 0xED1F9D4 -- the address the probe blessed and the first
# classifier selected -- ticks +3 every 100.0 ms (a 10 Hz FPS-style
# accumulator), which passes every RATE and PHASE test while quantising the
# frame map to 100 ms (the scored clip's -1..-8 slot press error). The
# discriminators that actually separate a per-present counter from every
# impostor are its STEP SIZE and its TICK GAP: one +1 per picture, ~33 ms
# apart. Measured live the same day: ~40 heap counters carry that exact
# signature (steps {1: all}, median gap 33.4 ms) while the fake reads
# {3: all} at 100 ms and a 60 Hz mirror reads +2 steps.
#
# Among the true per-frame counters, rank by TICK-TIME WANDER against the
# logic clock's own edges: a counter incremented beside gGlobalTimer has
# offset-std ~1 ms (it IS the edge series by another name -- harmless but
# informationless), while display-side families measured std 9-22 ms.
# Scored against footage (attempt 1630's A-icon), every +1/33 ms family
# beat the fake outright; picking the strongest wanderer keeps whatever
# display information exists without ever doing worse than the edge series.
CLASSIFY_S = 12.0
CLASSIFY_HZ = 250
MIN_TIMER_EDGES = 120            # fewer = the game barely ran; inconclusive
VERDICT_RATE_LO, VERDICT_RATE_HI = 24.0, 36.0
STEP_ONE_SHARE = 0.9             # nearly every advance must be exactly +1
GAP_LO_S, GAP_HI_S = 0.025, 0.042   # median tick gap: one per game frame

COOLDOWN_S = 45.0                # after a failed hunt
RETRY_AFTER_INVALIDATE_S = 5.0   # after the watchdog kills a stale find


class PresentHunter:
    """Owns the hunt's state machine and the found address.

    `memory` is a Pj64Memory (host_regions / read_host_bytes /
    read_host_u32 / rdram_host_base / read_u32 / attached); `timer_address`
    is the layout's gGlobalTimer. `sleep` and `clock` exist so tests drive
    the whole hunt on simulated time.
    """

    def __init__(self, memory, timer_address: int,
                 sleep=time.sleep, clock=time.perf_counter):
        self._memory = memory
        self._timer_address = timer_address
        self._sleep = sleep
        self._clock = clock
        self._address: int | None = None
        self._thread: threading.Thread | None = None
        self._cooldown_until = 0.0

    @property
    def address(self) -> int | None:
        return self._address

    def state(self) -> str:
        """One word for /health: the found address as hex, "hunting" while
        the background sweep runs, "idle" between attempts."""
        if self._address is not None:
            return hex(self._address)
        if self._thread is not None and self._thread.is_alive():
            return "hunting"
        return "idle"

    def read(self) -> int | None:
        """The counter's current value -- the poller's 250 Hz read. None
        while no address is in hand or the process went away."""
        address = self._address
        if address is None:
            return None
        try:
            return self._memory.read_host_u32(address)
        except MemoryReadError:
            return None

    def ensure_hunting(self) -> None:
        """Start a hunt if none is running, none is needed, and the last
        failure's cooldown has passed. Called from the poll loop whenever
        frames are advancing -- cheap when there is nothing to do."""
        if self._address is not None:
            return
        if self._thread is not None and self._thread.is_alive():
            return
        if self._clock() < self._cooldown_until:
            return
        if not getattr(self._memory, "attached", False):
            return
        self._thread = threading.Thread(target=self._hunt,
                                        name="present-hunt", daemon=True)
        self._thread.start()

    def invalidate(self, reason: str = "") -> None:
        """Drop the found address (watchdog: it stopped ticking; detach:
        the session it belonged to is gone). The next advancing frame
        starts a fresh hunt after a short pause."""
        if self._address is not None:
            log.info("present counter at 0x%X invalidated: %s",
                     self._address, reason or "no reason given")
        self._address = None
        self._cooldown_until = self._clock() + RETRY_AFTER_INVALIDATE_S

    # -- hunt thread ---------------------------------------------------------

    def _hunt(self) -> None:
        started = self._clock()
        try:
            found = self._run_hunt()
        except MemoryReadError:
            found = None                 # emulator went away mid-hunt
        except Exception:
            log.exception("present hunt failed")
            found = None
        if found is not None:
            self._address = found
            log.info("present counter found at 0x%X (%.0f s hunt)",
                     found, self._clock() - started)
        else:
            self._cooldown_until = self._clock() + COOLDOWN_S
            log.info("present hunt found nothing (%.0f s); retrying in %.0f s",
                     self._clock() - started, COOLDOWN_S)

    def _run_hunt(self) -> int | None:
        survivors = self._sweep()
        if not survivors:
            return None
        for _round in range(CONFIRM_ROUNDS):
            survivors = self._confirm(survivors)
            if not survivors:
                return None
        if len(survivors) > MAX_FINALISTS:
            log.info("present hunt: %d finalists, classifying the first %d",
                     len(survivors), MAX_FINALISTS)
            survivors = survivors[:MAX_FINALISTS]
        return self._classify(sorted(survivors))

    def _sweep(self) -> list[int]:
        """Addresses of u32s that advanced at a display-ish rate across one
        short gap. Chunks are read in byte-budgeted GROUPS -- one sleep per
        group -- and each chunk's accept band derives from its own MEASURED
        elapsed time, so re-read overhead can never shift a real counter out
        of band."""
        rdram = self._memory.rdram_host_base
        excluded = []
        if rdram is not None:
            excluded.append((rdram, rdram + 0x800000))
        chunks: list[tuple[int, int]] = []
        for base, size in self._memory.host_regions():
            if size > MAX_REGION:
                continue
            if any(base < ex_end and base + size > ex_base
                   for ex_base, ex_end in excluded):
                continue
            for offset in range(0, size, SWEEP_CHUNK):
                chunks.append((base + offset, min(SWEEP_CHUNK, size - offset)))
        log.info("present sweep: %d chunks, %.0f MB, %.0f MB per pace group",
                 len(chunks), sum(size for _base, size in chunks) / 1e6,
                 SWEEP_GROUP / 1e6)
        found: list[int] = []
        at = 0
        while at < len(chunks):
            group: list[tuple[int, int]] = []
            budget = 0
            while at < len(chunks) and budget < SWEEP_GROUP:
                group.append(chunks[at])
                budget += chunks[at][1]
                at += 1
            first_pass: list[tuple[float, np.ndarray | None]] = []
            for chunk_base, chunk_size in group:
                try:
                    raw = self._memory.read_host_bytes(chunk_base, chunk_size)
                    array = np.frombuffer(raw[:len(raw) & ~3],
                                          dtype=np.uint32).copy()
                except MemoryReadError:
                    array = None         # region shrank or turned unreadable
                first_pass.append((self._clock(), array))
            self._sleep(SWEEP_GAP_S)
            for (chunk_base, chunk_size), (read_at, before) \
                    in zip(group, first_pass):
                if before is None:
                    continue
                try:
                    raw = self._memory.read_host_bytes(chunk_base, chunk_size)
                except MemoryReadError:
                    continue
                elapsed = max(self._clock() - read_at, SWEEP_GAP_S)
                low = max(2, int(RATE_LO * elapsed * 0.7))
                high = int(RATE_HI * elapsed * 1.3) + 1
                after = np.frombuffer(raw[:len(raw) & ~3], dtype=np.uint32)
                shared = min(len(before), len(after))
                delta = (after[:shared].astype(np.int64)
                         - before[:shared].astype(np.int64))
                for hit in np.flatnonzero((delta >= low) & (delta <= high)):
                    found.append(chunk_base + int(hit) * 4)
        log.info("present sweep: %d candidates", len(found))
        return found

    def _confirm(self, addresses: list[int]) -> list[int]:
        before = {}
        for address in addresses:
            try:
                before[address] = self._memory.read_host_u32(address)
            except MemoryReadError:
                continue
        self._sleep(CONFIRM_GAP_S)
        kept = []
        for address, old in before.items():
            try:
                new = self._memory.read_host_u32(address)
            except MemoryReadError:
                continue
            ticks = new - old
            if RATE_LO * CONFIRM_GAP_S * 0.7 <= ticks \
                    <= RATE_HI * CONFIRM_GAP_S * 1.3:
                kept.append(address)
        log.info("present confirm: %d of %d kept", len(kept), len(addresses))
        return kept

    def _classify(self, addresses: list[int]) -> int | None:
        """Keep the finalist with the per-present signature -- +1 per tick,
        one tick per game frame -- ranked by how much its tick TIMES wander
        against the logic clock's own edges (see the constants block for
        the live evidence, including the 10 Hz fake this replaced)."""
        samples: list[tuple[float, int, list[int]]] = []
        deadline = self._clock() + CLASSIFY_S
        while self._clock() < deadline:
            try:
                timer = self._memory.read_u32(self._timer_address)
                values = [self._memory.read_host_u32(address)
                          for address in addresses]
            except MemoryReadError:
                self._sleep(1 / CLASSIFY_HZ)
                continue
            samples.append((self._clock(), timer, values))
            self._sleep(1 / CLASSIFY_HZ)
        if len(samples) < 2:
            return None
        span_s = samples[-1][0] - samples[0][0]
        edge_times = [samples[index][0] for index in range(1, len(samples))
                      if samples[index][1] == samples[index - 1][1] + 1]
        if len(edge_times) < MIN_TIMER_EDGES:
            log.info("present classify: only %d timer edges -- the game "
                     "barely ran; inconclusive", len(edge_times))
            return None
        edge_array = np.array(edge_times)
        best: tuple[float, int] | None = None
        for slot, address in enumerate(addresses):
            series = np.array([values[slot] for _t, _f, values in samples],
                              dtype=np.int64)
            times = np.array([tick for tick, _f, _v in samples])
            changed = np.flatnonzero(np.diff(series) != 0) + 1
            if len(changed) < 8:
                continue
            steps = np.diff(series)[changed - 1]
            tick_times = times[changed]
            gaps = np.diff(tick_times)
            rate = float(series[-1] - series[0]) / span_s
            one_share = float(np.mean(steps == 1))
            median_gap = float(np.median(gaps))
            if not (VERDICT_RATE_LO <= rate <= VERDICT_RATE_HI
                    and one_share >= STEP_ONE_SHARE
                    and GAP_LO_S <= median_gap <= GAP_HI_S):
                continue
            # tick-time wander vs the logic edges: 0 = the edge series by
            # another name (fine); bigger = display-side timing (better).
            slots_before = np.searchsorted(edge_array, tick_times) - 1
            valid = slots_before >= 0
            offsets = tick_times[valid] - edge_array[slots_before[valid]]
            wander_ms = float(np.std(offsets)) * 1000 if valid.any() else 0.0
            log.info("present classify: 0x%X rate %.2f/s steps+1 %.0f%% "
                     "gap %.1f ms wander %.1f ms", address, rate,
                     one_share * 100, median_gap * 1000, wander_ms)
            if best is None or wander_ms > best[0]:
                best = (wander_ms, address)
        return best[1] if best is not None else None

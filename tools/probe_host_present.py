"""Live hunt (round 32 items 29/30): PJ64's HOST-side present counter.

probe_present.py proved the emulated N64's display clock is rigidly locked
to game logic (0 phase flips / 399 frames), so the footage's +-1 wobble
arises PAST the emulated VI -- in the video plugin's GPU present, DWM, or
our capture. His ruling: "I'm certain that we can access this information
precisely... I know for sure you can probe it effectively. We just have to
put in enough effort to figure it out."

This is that effort, aimed at the host: sweep PJ64's process memory OUTSIDE
the emulated RDRAM/ROM for 32-bit counters ticking at display-ish rates
(the plugin's frames-presented counter, a D3D swap count), then classify
every survivor by its PHASE against gGlobalTimer:

  - a survivor whose phase NEVER flips is an emulation-side mirror -- it
    carries nothing gGlobalTimer does not;
  - a survivor whose phase WANDERS in runs is the true present event, the
    wobble seen from the RAM side -- the frame clock should tag captures
    with IT, and the pixel path becomes console-only.

Read-only, safe beside a live session. Just play (unpaused) for ~40 s.

ANSWERED 2026-08-23, live during his session: the counter EXISTS.
  - The sweep (698 MB outside RDRAM/ROM, three narrowing rounds) plus a
    15 s phase classification found ONE candidate with the present-event
    signature: 0xED1F9D4 that run -- rate 30.00/s exactly (one tick per
    game frame), phase against gGlobalTimer wandering IN RUNS (149 flips
    across 449 frames), while every emulation-side mirror held 0 flips
    and pure noise flipped every frame. Runs of a wandering phase are
    precisely what the footage's +-1 desync looks like from RAM.
  - The address is HEAP (no loaded module contains it), so it moves per
    session: production must HUNT it at attach by this signature --
    rate ~30/s and phase-run wobble against gGlobalTimer -- exactly how
    the controller struct is hunted by shape (tools/probe_inputs.py).
  - THE BUILD THIS UNLOCKS (map v4, emulator-only, no vision): the
    poller already samples at 250 Hz; watching this counter beside
    gGlobalTimer enumerates every PRESENT with the game frame on screen
    at that instant; the frame clock records (present time, frame), and
    the sidecar map reads presents instead of logic edges -- the wobble
    is then IN the data instead of around it. Item 26's pixel reader
    stays parked for console capture, where no process memory exists.

CORRECTED 2026-08-25, by the first SCORED clip: 0xED1F9D4 is an IMPOSTOR.
Read at 1 kHz it ticks +3 every 100.0 ms -- a 10 Hz FPS-style accumulator,
not a per-picture event -- which this probe's rate (30.00/s, an average)
and phase-flip tests (its odd steps alternate parity in runs) could not
see, and which quantised the shipped map to -1..-8 slots of press error
against Usamune's own pixels. The discriminators are STEP SIZE and TICK
GAP: a re-hunt on them found ~40 counters at exactly +1 per frame /
33.4 ms median gap, in two tick-time families (offset-std 9-22 ms vs the
logic edges) plus one edge-locked mirror (std 1 ms). Scored against
footage (attempt 1630's A-icon), every +1/33 ms family beat the fake;
memory/present.py's classifier now requires that signature and ranks by
tick-time wander. Lesson recorded: a rate is an average, a phase share is
a parity trick -- the SHAPE of a counter's individual ticks is the only
signature that survives contact with an accumulator.
"""
import ctypes
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pymem  # noqa: E402

from sm64_events.memory.layout import layout_for  # noqa: E402
from sm64_events.memory.pj64 import Pj64Memory, iter_committed_regions  # noqa: E402

# The one door for RAM addresses (tests/test_single_source.py): the sweep
# phase-classifies against gGlobalTimer wherever THIS ROM's layout says it is.
GLOBAL_TIMER = layout_for("us").global_timer
RATE_LO, RATE_HI = 20.0, 130.0        # counters worth classifying, per second
SWEEP_GAP_S = 1.0
ROUNDS = 3
CLASSIFY_S = 15.0
SAMPLE_HZ = 250
MAX_REGION = 512 * 1024 * 1024


def sweep(pm, exclude: list[tuple[int, int]]) -> list[int]:
    """Addresses of u32s that ticked RATE_LO..RATE_HI per second, narrowed
    over ROUNDS passes."""
    regions = [(base, size) for base, size in
               iter_committed_regions(pm.process_handle)
               if size <= MAX_REGION
               and not any(base < ex_end and base + size > ex_base
                           for ex_base, ex_end in exclude)]
    print(f"{len(regions)} regions to sweep "
          f"({sum(size for _b, size in regions) / 1e6:.0f} MB)")
    candidates: dict[int, int] | None = None
    for round_index in range(ROUNDS):
        before: dict[int, np.ndarray] = {}
        for base, size in regions:
            try:
                raw = pm.read_bytes(base, size)
            except Exception:
                continue
            before[base] = np.frombuffer(raw[:len(raw) & ~3],
                                         dtype=np.uint32).copy()
        time.sleep(SWEEP_GAP_S)
        found: dict[int, int] = {}
        for base, old in before.items():
            try:
                raw = pm.read_bytes(base, len(old) * 4)
            except Exception:
                continue
            new = np.frombuffer(raw, dtype=np.uint32)
            delta = (new - old).astype(np.int64)
            hits = np.flatnonzero((delta >= RATE_LO * SWEEP_GAP_S * 0.7) &
                                  (delta <= RATE_HI * SWEEP_GAP_S * 1.3))
            for offset in hits:
                address = base + int(offset) * 4
                if candidates is None or address in candidates:
                    found[address] = int(delta[offset])
        candidates = found
        print(f"round {round_index + 1}: {len(candidates)} candidates")
        if len(candidates) <= 32:
            break
    return sorted(candidates or {})


def classify(pm, memory: Pj64Memory, addresses: list[int]) -> None:
    print(f"classifying {len(addresses)} candidates against gGlobalTimer "
          f"for {CLASSIFY_S:.0f}s...")
    samples = []
    deadline = time.perf_counter() + CLASSIFY_S
    while time.perf_counter() < deadline:
        try:
            timer = memory.read_u32(GLOBAL_TIMER)
            values = [pm.read_uint(address) for address in addresses]
        except Exception:
            continue
        samples.append((time.perf_counter(), timer, values))
        time.sleep(1 / SAMPLE_HZ)
    span = samples[-1][0] - samples[0][0]
    timer_edges = [index for index in range(1, len(samples))
                   if samples[index][1] == samples[index - 1][1] + 1]
    print(f"{len(samples)} samples, {len(timer_edges)} timer edges\n")
    print(f"{'address':>12}  {'rate/s':>7}  {'phase flips':>11}  verdict")
    for slot, address in enumerate(addresses):
        first, last = samples[0][2][slot], samples[-1][2][slot]
        rate = (last - first) / span
        if not (RATE_LO <= rate <= RATE_HI):
            continue
        phases = [samples[index][2][slot] % 2 for index in timer_edges]
        flips = sum(1 for a, b in zip(phases, phases[1:]) if a != b)
        share = flips / max(1, len(phases) - 1)
        verdict = ("LOCKED (emulation mirror)" if share < 0.02 else
                   "WOBBLES -- present-side candidate" if share < 0.6 else
                   "uncorrelated noise")
        print(f"{address:#12x}  {rate:7.2f}  {flips:5d}/{len(phases) - 1:<5d} "
              f" {verdict}")


def main() -> int:
    memory = Pj64Memory()
    if not memory.attach():
        raise SystemExit("PJ64 not running or no ROM loaded")
    rdram = memory._rdram_base
    pm = pymem.Pymem("Project64.exe")
    exclude = [(rdram, rdram + 0x800000)]
    # exclude the ROM image region too (found by its magic earlier probes)
    for base, size in iter_committed_regions(pm.process_handle):
        if size == 0x800000 and base != rdram:
            try:
                head = pm.read_bytes(base, 4)
            except Exception:
                continue
            if head in (b"\x80\x37\x12\x40", b"\x40\x12\x37\x80",
                        b"\x37\x80\x40\x12"):
                exclude.append((base, base + size))
    print(f"excluding RDRAM at {rdram:#x} and {len(exclude) - 1} ROM region(s)")
    addresses = sweep(pm, exclude)
    if not addresses:
        print("no counters in range survived the sweep -- a finding: the "
              "plugin keeps no host-side 32-bit present counter, or it "
              "lives in another process (dwm.exe)")
        return 1
    if len(addresses) > 48:
        print(f"{len(addresses)} survivors -- classifying the first 48")
        addresses = addresses[:48]
    classify(pm, memory, addresses)
    memory.detach()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

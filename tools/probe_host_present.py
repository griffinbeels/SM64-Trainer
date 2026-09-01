"""Live gate (round 32 item 70): find PJ64's OWN present counter, in host memory.

This is the stone `tools/probe_present.py` named and left unturned on
2026-08-23, verbatim: *"PJ64's own video plugin keeps HOST-side present state
(process memory OUTSIDE the RDRAM region). A probe_inputs-style scan for a
counter ticking ~60/s that pauses with the game could find the true host
present counter; sampling THAT at capture time would see the wobble from the
RAM side. Nobody has run that scan."*

WHY IT IS THE REMAINING ROUTE, with the other two now closed by measurement:

  * The clock inside the emulated N64 is lockstep -- 0 phase flips in 399 game
    frames (probe_present.py, 2026-08-23) -- so a capture-time read of the swap
    chain carries nothing gGlobalTimer does not already say.
  * The PICTURE is not in RDRAM either: gFrameBuffer0/1/2 changed 0 times
    across 181 game frames and decode to heap noise, because PJ64 1.6's HLE
    plugin renders on the PC's GPU and never writes back (probe_framebuffer.py,
    2026-08-31).

So every frame of the wobble lives between PJ64's present call and the picture
our capture receives, and nothing we have sampled so far can see it. A host
present counter closes it exactly: sample it beside a wall clock and we learn
WHEN each present happened; a captured picture carries its own composition time
(WGC's SystemRelativeTime), and the picture composed at T shows the present
that happened most recently before T. That is a lookup, not an inference -- no
display-lag constant, no digit anchor, no drift.

METHOD -- three passes, because two can be fooled by anything that merely grows:
  1. snapshot every committed, readable, private region outside RDRAM;
  2. after ~0.5 s, keep u32s whose delta is plausible for a 30/60 Hz tick;
  3. after another ~0.5 s, keep only those whose SECOND delta matches the
     first, which is what separates a clock from a byte counter or an
     allocation high-water mark.
Then it reports the survivors with their measured rate so the caller can pick
the ~60/s and ~30/s families apart -- and it prints how many candidates each
pass killed, because a scan that ends with thousands of survivors has not
found anything and should say so rather than naming the first row.

CONFIRMING A HIT is a separate step and needs him: pause the emulator and
re-run with --watch <address>. A real present counter STOPS. A host timer or a
DWM counter keeps going, and that is the whole discriminator.

    uv run python tools/probe_host_present.py               # the scan
    uv run python tools/probe_host_present.py --watch 0x1234abcd

Read-only: it opens PJ64 with PROCESS_VM_READ only and writes nothing, ever.
"""
import argparse
import ctypes
import time
from ctypes import wintypes

import numpy as np

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
PAGE_READABLE = 0x02 | 0x04 | 0x20 | 0x40      # RO, RW, EXEC_READ, EXEC_RW
PAGE_GUARD = 0x100

# Regions bigger than this are texture/frame pools, not counters, and scanning
# them costs seconds per pass for nothing.
MAX_REGION = 32 << 20
# A 60 Hz counter moves ~30 per half second and a 30 Hz one ~15. Keep a band
# wide enough for sampling jitter and for a counter running at 2x either.
MIN_TICK, MAX_TICK = 5, 140
# Pass 3's bucketing: enough buckets to correlate, short enough that the
# emulator's own rate varies between them.
BUCKETS, BUCKET_S = 10, 0.4


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p),
                ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wintypes.DWORD),
                ("__alignment1", wintypes.DWORD),
                ("RegionSize", ctypes.c_size_t),
                ("State", wintypes.DWORD),
                ("Protect", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("__alignment2", wintypes.DWORD)]


def pj64_pid() -> int:
    import subprocess
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Project64.exe",
                          "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, encoding="utf-8",
                         check=False).stdout
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) > 1 and parts[0].lower().startswith("project64"):
            return int(parts[1])
    raise SystemExit("Project64.exe is not running")


class Reader:
    def __init__(self, pid: int):
        self.kernel = ctypes.windll.kernel32
        self.handle = self.kernel.OpenProcess(
            PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
        if not self.handle:
            raise SystemExit(f"cannot open pid {pid} for reading "
                             f"(error {ctypes.get_last_error()})")

    def regions(self):
        info = MEMORY_BASIC_INFORMATION()
        address = 0
        while self.kernel.VirtualQueryEx(self.handle, ctypes.c_void_p(address),
                                         ctypes.byref(info),
                                         ctypes.sizeof(info)):
            size = info.RegionSize
            if (info.State == MEM_COMMIT and info.Type == MEM_PRIVATE
                    and (info.Protect & PAGE_READABLE)
                    and not (info.Protect & PAGE_GUARD)
                    and 0 < size <= MAX_REGION):
                yield int(info.BaseAddress or 0), size
            address += size or 0x1000
            if address > 0x7FFFFFFF0000:
                break

    def read(self, address: int, size: int):
        buffer = (ctypes.c_char * size)()
        got = ctypes.c_size_t(0)
        ok = self.kernel.ReadProcessMemory(self.handle, ctypes.c_void_p(address),
                                           buffer, size, ctypes.byref(got))
        return bytes(buffer[:got.value]) if ok and got.value else None

    def read_u32(self, address: int):
        raw = self.read(address, 4)
        return None if raw is None or len(raw) < 4 else int(
            np.frombuffer(raw, dtype="<u4")[0])


def snapshot(reader, regions):
    out = {}
    for base, size in regions:
        block = reader.read(base, size)
        if block and len(block) >= 4:
            out[base] = np.frombuffer(block[:len(block) // 4 * 4], dtype="<u4")
    return out


def scan(reader) -> None:
    regions = list(reader.regions())
    total = sum(size for _, size in regions)
    print(f"{len(regions)} private committed regions, {total / 1e6:.0f} MB "
          f"(regions over {MAX_REGION >> 20} MB skipped)")

    first = snapshot(reader, regions)
    print(f"pass 1: {len(first)} regions read")
    time.sleep(0.5)
    second = snapshot(reader, regions)

    candidates = []
    for base, before in first.items():
        after = second.get(base)
        if after is None or len(after) != len(before):
            continue
        delta = after.astype(np.int64) - before.astype(np.int64)
        hits = np.nonzero((delta >= MIN_TICK) & (delta <= MAX_TICK))[0]
        for index in hits:
            candidates.append((base + int(index) * 4, int(delta[index])))
    print(f"pass 2: {len(candidates)} u32s moved by {MIN_TICK}-{MAX_TICK} "
          f"in 0.5 s")
    if not candidates:
        print("nothing ticks in that band -- widen MIN_TICK/MAX_TICK or the "
              "counter is not a plain u32")
        return

    # Pass 3 -- the DISCRIMINATOR, and it needs nobody at the keyboard.
    # "Ticks at a steady rate" is useless here: the first run of this scan left
    # 80 survivors, every one of them at a flat 200 Hz, which is what a host
    # timer looks like. The property that separates a PRESENT counter from a
    # wall-clock one is that it is driven by the EMULATOR: when the emulated
    # game slows -- and it does, 29.8/s measured rather than 30.0 -- a present
    # counter slows WITH it and a host timer does not. So bucket both and rank
    # by correlation against gGlobalTimer's own per-bucket delta.
    print(f"pass 3: bucketing {len(candidates)} candidates against the GAME's "
          f"own rate for {BUCKETS * BUCKET_S:.0f}s")
    from sm64_events.memory.layout import layout_for
    from sm64_events.memory.pj64 import Pj64Memory
    game = Pj64Memory()
    if not game.attach():
        print("  cannot attach to read gGlobalTimer -- no discriminator; stop")
        return
    timer_address = layout_for("us").global_timer

    addresses = [address for address, _ in candidates]
    series = {address: [] for address in addresses}
    game_series = []
    previous = {address: reader.read_u32(address) for address in addresses}
    previous_timer = game.read_u32(timer_address)
    for _ in range(BUCKETS):
        time.sleep(BUCKET_S)
        timer = game.read_u32(timer_address)
        game_series.append(timer - previous_timer)
        previous_timer = timer
        for address in addresses:
            value = reader.read_u32(address)
            series[address].append(
                None if value is None or previous[address] is None
                else value - previous[address])
            previous[address] = value

    game_rate = np.array(game_series, dtype=np.float64)
    print(f"  the game advanced {game_series} frames per {BUCKET_S:.1f}s bucket"
          f" (spread {game_rate.max() - game_rate.min():.0f} frames)")
    if game_rate.std() == 0:
        print("  the game's rate never varied, so nothing can be told apart "
              "this way -- re-run while the machine is under load")
        return

    ranked = []
    for address in addresses:
        values = series[address]
        if any(value is None for value in values):
            continue
        counts = np.array(values, dtype=np.float64)
        if counts.std() == 0 or counts.min() < 0:
            continue
        ranked.append((float(np.corrcoef(counts, game_rate)[0, 1]),
                       address, counts.mean() / BUCKET_S))
    ranked.sort(reverse=True)
    print(f"  {len(ranked)} candidates varied at all; the ones that vary WITH "
          f"the game:")
    print("\n    r vs game    address          implied Hz")
    for correlation, address, rate in ranked[:20]:
        print(f"    {correlation:6.3f}     0x{address:012x}   {rate:8.1f}")
    if not ranked or ranked[0][0] < 0.5:
        print("\n  NOTHING tracks the game's rate. Either the plugin keeps no "
              "such counter as a plain u32, or it lives in a region this scan "
              "skipped (over 32 MB, shared, or non-private). That is a finding "
              "about THIS scan, not a proof that no counter exists.")
    print("\nCONFIRM a survivor with him: pause the emulator, then\n"
          "  uv run python tools/probe_host_present.py --watch <address>\n"
          "A present counter STOPS when the game pauses. A host timer does not.")


def watch(reader, address: int) -> None:
    print(f"watching 0x{address:x} -- pause and unpause the emulator")
    previous = reader.read_u32(address)
    start = time.perf_counter()
    while time.perf_counter() - start < 20.0:
        time.sleep(1.0)
        value = reader.read_u32(address)
        if value is None:
            print("  unreadable now (the region went away)")
            return
        print(f"  +{value - (previous or 0):6d} in 1 s "
              f"({'MOVING' if value != previous else 'STOPPED'})")
        previous = value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", default=None,
                        help="confirm one address while you pause the emulator")
    args = parser.parse_args()
    reader = Reader(pj64_pid())
    if args.watch:
        watch(reader, int(args.watch, 0))
    else:
        scan(reader)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# tools/hunt_exact.py
"""Snapshot-diff hunt for small non-timer values (e.g. gCurrAreaIndex).

hunt_value.py tolerates +/-2 frames — right for timer displays, useless for
telling room ids 1/2/3 apart (each is within tolerance of the others; the
2026-06-12 area hunt converged on door COUNTERS instead). This tool matches
EXACT u16 values across labeled snapshots:

    uv run python tools/hunt_exact.py

Stand somewhere, type the spot's label (e.g. "lobby"), reposition, type the
next label ("upstairs", "basement"...), and REPEAT an earlier label at the
end ("lobby" again) — the repeat is what kills counters, which never return
to their earlier value. Blank input finishes. Survivors must read the SAME
value in same-label snapshots and DIFFERENT values across different labels,
so the hunt is value-agnostic and the output reveals the actual id mapping.
Values are capped small (< 64) to favor indexes over timers/pointers."""
import array
import time

from sm64_events.memory import addresses as A
from sm64_events.memory.objects import describe
from sm64_events.memory.pj64 import Pj64Memory

MAX_REPORT = 24
VALUE_CAP = 64  # an index, not a timer: every snapshot value must stay below


def rdram_size(mem: Pj64Memory) -> int:  # same heuristic as hunt_value.py
    size = mem.read_u32(A.OS_MEM_SIZE)
    if size not in (0x400000, 0x800000):
        return 0x400000
    try:
        mem._read_raw(size - 4, 4)
        return size
    except Exception:
        return 0x400000


def main() -> None:
    mem = Pj64Memory()
    print("Attaching to Project64.exe ...")
    while not mem.attach():
        print("  not found, retrying in 2s")
        time.sleep(2)
    size = rdram_size(mem)
    print(f"Attached ({size // 0x100000} MB RDRAM).\n")

    labels: list[str] = []
    arrays: list[array.array] = []
    while True:
        label = input("Position Mario, then type this spot's label "
                      "(repeat an earlier label at the end; blank = done): ").strip()
        if not label:
            break
        labels.append(label)
        arrays.append(array.array("H", mem._read_raw(0, size)))
        print(f"  snapshot {len(labels)}: {label!r}")

    if len(set(labels)) < 2:
        print("Need snapshots from at least two different spots.")
        return
    if len(labels) == len(set(labels)):
        print("WARNING: no repeated label — counters can survive this hunt.")

    # Seed candidates from the first pair of differently-labeled snapshots,
    # then refine against every snapshot (stability within labels, all-distinct
    # across labels). u16 offsets only: the targets are s16 indexes.
    j = next(i for i, lab in enumerate(labels) if lab != labels[0])
    cand = {i for i, (x, y) in enumerate(zip(arrays[0], arrays[j]))
            if x != y and x < VALUE_CAP and y < VALUE_CAP}
    survivors = []
    for i in cand:
        per: dict[str, int] = {}
        ok = True
        for lab, arr in zip(labels, arrays):
            v = arr[i]
            if v >= VALUE_CAP or per.setdefault(lab, v) != v:
                ok = False
                break
        if ok and len(set(per.values())) == len(per):
            survivors.append((i * 2, per))

    print(f"\n{len(survivors)} candidates (showing up to {MAX_REPORT}):")
    # raw-offset -> N64 address: PJ64 stores RDRAM little-endian, so a
    # big-endian u16 at N64 address a sits at raw offset (a ^ 2) — same
    # convention as hunt_value.py's report line.
    for off, per in survivors[:MAX_REPORT]:
        addr = A.KSEG0_BASE + (off ^ 2)
        vals = "  ".join(f"{lab}={v}" for lab, v in per.items())
        print(f"  {addr:#010x}/u16  {vals}  {describe(mem, addr)}")


if __name__ == "__main__":
    main()

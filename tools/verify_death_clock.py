# tools/verify_death_clock.py
"""Live gate: does Usamune's DISPLAY match the raw counter, or the raw + 1?

    uv run python tools/verify_death_clock.py

Behavioural gate, NOT an address gate — `USAMUNE_OVERALL` is already sampled
every tick and needs no `VERIFY` row, so `tools/verify_addresses.py` is not
the instrument. Read-only: no database, no server, no recorder lock. Safe to
run while you are recording.

## What you do

**Phase 1 is the real gate and needs no reflexes.** Pause the game (Usamune's
pause stops game logic, so the counter and the display both freeze). The probe
notices, prints the frozen counter and the two candidate readings, and asks
you which one the screen shows. Unpause, play a few seconds, pause again — it
prints a fresh pair each time, so one misread cannot decide it.

**Phase 2 watches for actual deaths**, through the REAL `DeathDetector` this
app runs, and reports what a death row WOULD record. Its job is not the
offset (you cannot read the screen mid-death animation) — it is to confirm
Usamune's counter is still advancing normally at the moment the death action
fires. If it stalls or zeroes early, the offset question is moot and there is
a larger bug; this is the only way to find that out.

## What your answer means

- Screen shows the RAW number -> `death.py` is already correct. Leave it, and
  record that `DISPLAY_TICK` is a star-path calibration, not a display-wide
  property.
- Screen shows RAW + 1 -> `death.py` should route through `IgtClock` like
  every other time source. Expect every historical death row to move by one
  frame on the next reproject.

If Usamune's timer display is switched off, turn it on first — there is
nothing to compare against otherwise.

## The one question (background — everything above this line is printed at
## startup, so the terminal alone is enough to run the gate)

`detectors/death.py` stamps `curr.igt_overall` RAW. Every other time in this
project (star grabs, Bowser keys, the pipe touch) goes through
`detectors/igt_clock.py`, whose counter path adds `DISPLAY_TICK = 1` because
Usamune's on-screen number is believed to run one tick ahead of the memory
value. Both cannot be right. A death row is therefore either correct as-is or
one frame (3 centiseconds) under what you saw.

Nobody has ever measured it, which is why the fix is not written yet: it would
re-time every historical death row — stars included, since projection stamps a
star's death attempt from this same payload — on an assumption. Live report
2026-07-31 (the segment-timing round) is what surfaced the inconsistency;
`tracking/segments.py`'s rta_frames clause carries the surrounding rules.
"""
import time

from sm64_events.core.snapshot import SnapshotReader
from sm64_events.core.timefmt import format_igt
from sm64_events.detectors.death import DeathDetector
from sm64_events.detectors.igt_clock import IgtClock
from sm64_events.memory.addresses import LEVEL_NAMES
from sm64_events.memory.pj64 import Pj64Memory

POLL_HZ = 60                # match server/poller.py: every game frame observed
PAUSE_FRAMES = 20           # counter frozen this long while global_timer runs
TRACK_WINDOW = 30           # samples examined behind a death
DEFAULT_PAUSES = 3
DEFAULT_DEATHS = 3


# --- pure core (tests/test_verify_death_clock.py drives these) --------------

def candidates(counter: int) -> tuple[str, str]:
    """The two readings Usamune's timer could be showing for a raw counter of
    `counter`: the raw value, and the value one display tick ahead."""
    return format_igt(counter), format_igt(counter + IgtClock.DISPLAY_TICK)


def is_paused(samples: list[tuple[int, int]]) -> bool:
    """True when the last PAUSE_FRAMES of (global_timer, igt_overall) show the
    game frame advancing while Usamune's counter stands still — the same
    signal detectors/anchors.py calls a pause streak. Both the counter and the
    on-screen number are frozen here, which is what makes a static reading
    possible at all."""
    window = [s for s in samples if s[0] > samples[-1][0] - PAUSE_FRAMES]
    if len(window) < 2 or window[-1][0] - window[0][0] < PAUSE_FRAMES - 1:
        return False
    return len({counter for _, counter in window}) == 1


def counter_tracked_cleanly(samples: list[tuple[int, int]]) -> tuple[bool, str]:
    """Did Usamune's counter advance one-for-one with the game frame across
    this window? A death is supposed to happen with the clock simply running.

    Returns (ok, plain-language detail). Not ok means the offset question is
    moot for this death and something else is going on — say so rather than
    reading a number out of a stalled clock."""
    if len(samples) < 2:
        return False, "not enough samples before the death to tell"
    frames = samples[-1][0] - samples[0][0]
    counted = samples[-1][1] - samples[0][1]
    if counted < 0:
        return False, (f"the counter went BACKWARD ({samples[0][1]} -> "
                       f"{samples[-1][1]}) — a load or reset landed inside the "
                       f"window, so this death's time is not a plain reading")
    if counted != frames:
        return False, (f"the counter moved {counted} while the game frame "
                       f"moved {frames} — it stalled for {frames - counted} "
                       f"frame(s) (a pause, or Usamune froze it early)")
    return True, f"advancing 1:1 with the game frame over {frames} frames"


def death_report(index: int, level: int, cause: str, counter: int,
                 samples: list[tuple[int, int]]) -> str:
    raw, ticked = candidates(counter)
    ok, detail = counter_tracked_cleanly(samples)
    place = LEVEL_NAMES.get(level, f"level {level}")
    lines = [
        f"\nDEATH #{index} — {cause} in {place}",
        f"  a death row would record  {raw}  ({counter} frames, raw counter)",
        f"  through the shared clock  {ticked}  ({counter + IgtClock.DISPLAY_TICK} frames)",
        f"  clock behaviour: {'OK — ' if ok else 'PROBLEM — '}{detail}",
    ]
    if not ok:
        lines.append("  ^ tell the session about this line; it outranks the "
                     "offset question.")
    return "\n".join(lines)


def pause_prompt(index: int, counter: int) -> str:
    raw, ticked = candidates(counter)
    return "\n".join([
        f"\nPAUSE #{index} — the counter is frozen, so the screen is too.",
        f"  Usamune's timer is now showing ONE of these. Which?",
        f"     (a)  {raw}   <- raw counter ({counter} frames). "
        f"death.py is right as it stands.",
        f"     (b)  {ticked}   <- one tick ahead ({counter + IgtClock.DISPLAY_TICK} "
        f"frames). death.py should use the shared clock.",
        "  Note which letter, then unpause and play on for another sample.",
    ])


# --- live shell ------------------------------------------------------------

def main() -> None:
    mem = Pj64Memory()
    print(__doc__.split("## The one question")[0].strip())
    print("\nAttaching to Project64.exe ...")
    while not mem.attach():
        print("  not found (is PJ64 running with the ROM loaded?) retrying in 2s")
        time.sleep(2)
    print("Attached. Read-only — nothing is written and no lock is taken.\n")
    print(f"Phase 1: pause the game {DEFAULT_PAUSES} times, a few seconds "
          f"apart. Ctrl+C when done.")

    reader, detector = SnapshotReader(mem), DeathDetector()
    samples: list[tuple[int, int]] = []
    prev = None
    pauses = deaths = 0
    reported_pause = False
    try:
        while True:
            curr = reader.read()
            if prev is not None and curr.global_timer > prev.global_timer:
                samples.append((curr.global_timer, curr.igt_overall))
                samples = samples[-TRACK_WINDOW:]
                if is_paused(samples):
                    if not reported_pause:
                        pauses += 1
                        reported_pause = True
                        print(pause_prompt(pauses, curr.igt_overall))
                        if pauses == DEFAULT_PAUSES:
                            print(f"\nPhase 2: now go and die {DEFAULT_DEATHS} "
                                  f"times, any level. Ctrl+C when done.")
                else:
                    reported_pause = False
            if prev is not None:
                for event in detector.process(prev, curr):
                    deaths += 1
                    print(death_report(deaths, event.payload["level"],
                                       event.payload["cause"],
                                       event.payload["igt_frames"], samples))
            prev = curr
            time.sleep(1.0 / POLL_HZ)
    except KeyboardInterrupt:
        print(f"\n\nStopped. {pauses} pause reading(s), {deaths} death(s).")
        print("Report back: which letter Phase 1 showed, and any PROBLEM line "
              "from Phase 2.")


if __name__ == "__main__":
    main()

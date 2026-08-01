# tools/verify_death_clock.py
"""Live gate: two Usamune timing questions, one sitting at the emulator.

    uv run python tools/verify_death_clock.py

Behavioural gate, NOT an address gate — `USAMUNE_OVERALL` is already sampled
every tick and needs no `VERIFY` row, so `tools/verify_addresses.py` is not
the instrument. Read-only: no database, no server, no recorder lock. Safe to
run while you are recording.

Nothing here reads your Usamune SETTINGS out of memory — no settings block is
in the address registry and none is being hunted today. So step 0 below asks
you to copy five values off a menu by hand. That is the whole reason this is
one sitting instead of two.

## Step 0 — before you play, write down five settings

Open Usamune's TIMER menu and copy these EXACTLY as shown:

    STOP=____  DISPLAY=____  REALTMR=____  PSSRACE=____  FADETMR=____

STOP, DISPLAY and REALTMR are three INDEPENDENT GRAB-or-XCAM switches, not one
mode, so all three are wanted even though only STOP is under suspicion. Every
reading below is only valid for the settings that were live when you took it —
without these numbers, a reading cannot be interpreted later, only re-taken.

## Step 1 — pause the game three times (this is the death-clock gate)

Needs no reflexes. Usamune's pause stops game logic, so the counter and the
screen freeze together. The probe notices and prints the two candidate
readings; note which one is on screen, unpause, play a few seconds, pause
again. Three samples, so one misread cannot decide it.

**Read the IGT / section timer** — the one that counts your attempt. If your
settings put a second (real-time) timer on screen as well, say which one you
read, because they are not the same clock and only the IGT one is comparable.

**If a PAUSE prompt appears when you did not pause, ignore it and tell us.**
The probe detects a pause by watching Usamune's counter stand still while the
game frame keeps moving — and with **STOP=GRAB** that is exactly what grabbing
a star does. The probe suppresses the window right after a grab it saw, but
that is a courtesy; this sentence is the part that actually covers it, because
anything else that halts the clock produces the same shape. A reading taken
during a grab may be comparing against Usamune's RESULT store rather than the
running counter, which is a different number and would look like a clean
measurement.

## Step 2 — die three times, anywhere

The probe watches through the REAL `DeathDetector` this app runs and reports
what a death row WOULD record. It does NOT ask you to read the screen during a
death — that is not possible through the animation, and it would get a guess.
Its job is to confirm Usamune's counter is still advancing normally when the
death action fires. If it stalls or zeroes early, the offset question is moot
and there is a larger bug; a `PROBLEM —` line says so, and that line outranks
everything else here.

## Step 3 — set STOP=XCAM, grab ONE star (this is the XCAM question)

Change STOP to XCAM in the TIMER menu, note the new settings line, then grab
any star. The probe prints the time WE recorded and where it came from.
Compare it against the time on your screen once the grab settles.

This is the cheap experiment for an UNMEASURED hypothesis, and one star
settles it: Usamune's result store holds whatever Usamune wrote when IT
stopped, so if that store honours STOP=XCAM then our star times already follow
your setting for free and there is far less to fix than it looks.

## What each answer means

- **Step 1, screen shows the RAW number** -> `death.py` is already correct.
  `DISPLAY_TICK` is a star-path calibration, not a display-wide property.
- **Step 1, screen shows RAW + 1** -> `death.py` routes through `IgtClock`
  like every other time source, and every historical death row moves one frame
  (3 centiseconds) on the next reproject, stars included.
- **Step 3, times MATCH** -> the result store follows your STOP setting. Our
  star timing is already XCAM-correct; only the places where WE choose a frame
  are exposed.
- **Step 3, ours is LOWER** -> we stopped at the grab and Usamune stopped at
  the cutscene camera. The gap is the bug, and its SIZE is the measurement —
  note it.

If Usamune's timer display is switched off, turn it on first — there is
nothing to compare against otherwise.

## Background (everything above this line is printed at startup, so the
## terminal alone is enough to run the gate)

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

And the XCAM question (live report 2026-08-01): we key star timing off the
star-dance ACTION EDGE, but Usamune's STOP switch decides whether Usamune
stops at the grab or at the cutscene camera. Where that bites depends on which
`IgtClock` source answered, and the two behave differently:

* `result` — `curr.igt_result` verbatim, i.e. whatever Usamune WROTE when it
  stopped. If Usamune honours STOP, this follows the setting with no code
  change at all. This is the path that answers in practice: measured over the
  2026-07-31 journal, 626 of 626 anchor-armed star successes came back
  `igt_source="result"`.
* `counter` — `USAMUNE_OVERALL` back-computed to OUR touch frame, so it
  reports the action-edge moment regardless of STOP. The pipe touch
  (`detectors/warp.py`) is always on this path, though a pipe entry is not a
  star grab and STOP may not apply to it at all.

Both of those are reasoning, not measurement. Step 3 measures the first one.
"""
import time

from sm64_events.core.snapshot import SnapshotReader
from sm64_events.core.timefmt import format_igt
from sm64_events.detectors.death import DeathDetector
from sm64_events.detectors.igt_clock import IgtClock
from sm64_events.detectors.star_grab import StarGrabDetector
from sm64_events.memory.addresses import LEVEL_NAMES
from sm64_events.memory.pj64 import Pj64Memory

POLL_HZ = 60                # match server/poller.py: every game frame observed
PAUSE_FRAMES = 20           # counter frozen this long while global_timer runs
TRACK_WINDOW = 30           # samples examined behind a death
DEFAULT_PAUSES = 3
DEFAULT_DEATHS = 3
# A star grab under STOP=GRAB halts Usamune's counter while the game frame
# keeps running — the SAME shape is_paused looks for, and a star dance lasts
# 60-90 frames, well past PAUSE_FRAMES. So a grab would otherwise print a
# PAUSE prompt the human never caused, and worse, one whose on-screen number
# may be Usamune's RESULT store rather than the running counter. Suppressing
# the window after a grab we SAW is a courtesy, not the guarantee: under
# STOP=GRAB the freeze can outlast any window, and a cutscene or anything else
# that halts the clock has no detector here at all. The instruction sheet's
# "if a PAUSE prompt appears when you did not pause, ignore it" is the part
# that actually covers this; do not grow this constant instead of that line.
GRAB_QUIET_FRAMES = 300


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


def pause_is_grab_shadow(frame: int, last_grab_frame: int | None) -> bool:
    """Is this apparent pause just a star grab halting the clock?

    True inside GRAB_QUIET_FRAMES of a grab we observed. See that constant for
    why this is a courtesy rather than the guarantee — the instruction sheet
    carries the real coverage."""
    if last_grab_frame is None:
        return False
    return 0 <= frame - last_grab_frame <= GRAB_QUIET_FRAMES


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
        "  If you did NOT just pause, IGNORE this one and say so — a halted",
        "  clock looks the same whatever halted it.",
        "  Read the IGT / SECTION timer (the one timing your attempt, not a",
        "  real-time timer if your settings put one on screen too).",
        "  It is now showing ONE of these. Which?",
        f"     (a)  {raw}   <- raw counter ({counter} frames). "
        f"death.py is right as it stands.",
        f"     (b)  {ticked}   <- one tick ahead ({counter + IgtClock.DISPLAY_TICK} "
        f"frames). death.py should use the shared clock.",
        "  Note which letter, then unpause and play on for another sample.",
    ])


# Usamune's TIMER menu, in the order it lists them. Hand-transcribed: no
# settings block is in the address registry, and none is being hunted here
# (that is round-4 work and it would swallow this probe). STOP/DISPLAY/REALTMR
# are three INDEPENDENT GRAB-or-XCAM switches — anything treating "XCAM mode"
# as one boolean is already wrong (live report 2026-08-01, two screenshots of
# the same scene differing only in STOP).
TIMER_SETTINGS = ("STOP", "DISPLAY", "REALTMR", "PSSRACE", "FADETMR")


def settings_prompt() -> str:
    """Asked BEFORE anything is measured, because a reading whose settings are
    unknown cannot be interpreted later — only re-taken, which means a second
    trip to the emulator."""
    return "\n".join([
        "STEP 0 — copy these off Usamune's TIMER menu before you play:",
        "    " + "  ".join(f"{name}=____" for name in TIMER_SETTINGS),
        "  All five, exactly as shown. They are what makes every reading below",
        "  interpretable; without them a reading can only be re-taken.",
        "  Nothing reads these out of memory yet — no settings block is in the",
        "  address registry, and none is being hunted today.",
    ])


def star_report(index: int, course_name: str, star_name: str,
                igt_frames: int, source: str) -> str:
    """What WE recorded for a star grab, for comparison against his screen —
    the XCAM measurement. The `source` is the whole point: `result` is
    Usamune's own written value and may follow STOP for free, `counter` is our
    own action-edge moment and cannot."""
    follows = ("this is Usamune's OWN written value — if it matches your "
               "screen with STOP=XCAM, the setting is already honoured"
               if source == "result" else
               "this is OUR action-edge moment, back-computed — it cannot "
               "follow STOP on its own")
    return "\n".join([
        f"\nSTAR #{index} — {star_name} in {course_name}",
        f"  we recorded  {format_igt(igt_frames)}  ({igt_frames} frames, "
        f"source={source})",
        f"  {follows}.",
        "  >>> Compare with the time on your screen once the grab settles.",
        "      match      -> our star timing already follows your STOP setting.",
        "      ours LOWER -> we stopped at the grab, Usamune at the cutscene",
        "                    camera. Note the SIZE of the gap; that is the",
        "                    measurement, not just the fact of it.",
    ])


# --- live shell ------------------------------------------------------------

def main() -> None:
    mem = Pj64Memory()
    print(__doc__.split("## Background")[0].strip())
    print("\n" + "=" * 72)
    print(settings_prompt())
    print("=" * 72)
    print("\nAttaching to Project64.exe ...")
    while not mem.attach():
        print("  not found (is PJ64 running with the ROM loaded?) retrying in 2s")
        time.sleep(2)
    print("Attached. Read-only — nothing is written and no lock is taken.\n")
    print(f"Watching for all three at once, in any order: {DEFAULT_PAUSES} "
          f"pauses, {DEFAULT_DEATHS} deaths, and one star with STOP=XCAM.")
    print("Ctrl+C when done.")

    reader = SnapshotReader(mem)
    death_detector, star_detector = DeathDetector(), StarGrabDetector()
    samples: list[tuple[int, int]] = []
    prev = None
    pauses = deaths = stars = 0
    reported_pause = False
    last_grab_frame: int | None = None
    try:
        while True:
            curr = reader.read()
            # Detectors FIRST, so a grab on this tick is already known to the
            # pause check below (a STOP=GRAB freeze starts on the grab frame).
            if prev is not None:
                for event in death_detector.process(prev, curr):
                    deaths += 1
                    print(death_report(deaths, event.payload["level"],
                                       event.payload["cause"],
                                       event.payload["igt_frames"], samples))
                # The star watch is the XCAM half: same loop, same real
                # detector the app runs, so what it prints is what we would
                # have journaled.
                for event in star_detector.process(prev, curr):
                    stars += 1
                    last_grab_frame = event.frame
                    print(star_report(stars, event.payload["course_name"],
                                      event.payload["star_name"],
                                      event.payload["igt_frames"],
                                      event.payload["igt_source"]))
            if prev is not None and curr.global_timer > prev.global_timer:
                samples.append((curr.global_timer, curr.igt_overall))
                samples = samples[-TRACK_WINDOW:]
                if is_paused(samples) and not pause_is_grab_shadow(
                        curr.global_timer, last_grab_frame):
                    if not reported_pause:
                        pauses += 1
                        reported_pause = True
                        print(pause_prompt(pauses, curr.igt_overall))
                else:
                    reported_pause = False
            prev = curr
            time.sleep(1.0 / POLL_HZ)
    except KeyboardInterrupt:
        print(f"\n\nStopped. {pauses} pause reading(s), {deaths} death(s), "
              f"{stars} star(s).")
        print("Report back, and the settings line is part of the answer, not "
              "context:")
        print("  1. your STEP 0 settings line (and the STOP=XCAM one if you "
              "changed it)")
        print("  2. which letter the pauses showed")
        print("  3. any PROBLEM line from a death — it outranks the letter")
        print("  4. for the star: did our time match your screen, and if not, "
              "by how much")


if __name__ == "__main__":
    main()

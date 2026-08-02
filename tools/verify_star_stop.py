# tools/verify_star_stop.py
"""Live gate: WHERE does Usamune stop its clock for a star, under YOUR settings?

    uv run python tools/verify_star_stop.py

Read-only: no database, no server, no recorder lock. Safe while recording.

## Why this exists (live report 2026-08-01, seven star grabs across three presets)

We journal a star at the moment WE see the grab. Measured against his screen:

    STOP=GRAB    ours matched exactly, twice        (gap 0 frames)
    STOP=GRABX   ours was LOWER three times         (gap 11, 28, 9 frames)
    STOP=XCAM    ours was LOWER twice               (gap 11, 1 frames)

So the exposure is real, and the gap is NOT a constant — no offset can fix it.
Two further facts from the same sitting, both of which shape this probe:

* STOP is not a GRAB/XCAM boolean. **GRABX** is a third value, and the presets
  move REALTMR (FADE, HIDE) and PSSRACE (RACE) too. Anything modelling "XCAM
  mode" as one flag is already wrong.
* Under GRABX our source was `result` and we were STILL 9-28 frames low, which
  kills the hopeful reading of the last gate: Usamune's result store is not
  simply "the number on screen". Under XCAM the source fell through to
  `counter` instead — i.e. no result write had landed within
  `IgtClock.RESULT_FRESH_FRAMES` of the grab.

Both of those point at the same suspicion, and this probe is built to settle
it: **we read too EARLY.** Usamune stops when its STOP setting says to, which
under GRABX/XCAM is some frames after our action edge. Our clock samples at
the edge and takes whatever is there.

## The measurement

For every star you grab, this watches for 8 seconds AFTERWARDS and prints
three numbers instead of one:

1. what we would journal TODAY (the shipped detector, unchanged),
2. where Usamune's running COUNTER came to rest, and how long after the grab,
3. what Usamune's RESULT store settled on, and when it was written.

Then it asks which of them is on your screen. If (2) or (3) is the answer
across all three STOP values, the fix needs no settings detection at all —
the counter coming to rest IS the STOP setting expressing itself, and we can
watch for it directly instead of reading a menu out of memory.

## How to run it

1. Note your five TIMER settings (STOP DISPLAY REALTMR PSSRACE FADETMR).
2. Grab a star. Let the dance and camera finish. Read the IGT timer.
3. Say which of the three printed numbers is on screen — or, if it is exactly
   3 centiseconds above one of them, say which one and "+1 frame".
4. Repeat for each STOP value you use (GRAB, GRABX, XCAM). One star each is
   enough IF the answer is the same kind of number every time; if it is not,
   that difference is the finding.

Do NOT read the timer during the star dance. Under STOP=GRAB the clock is
already frozen and you would be reading a settled number early; under XCAM it
is still running and you would be reading a moving one. The prompt appears
once the 8-second window has closed, which is the moment to look.
"""
import time

from sm64_events.core.snapshot import SnapshotReader
from sm64_events.core.timefmt import format_igt
from sm64_events.detectors.star_grab import StarGrabDetector
from sm64_events.memory.pj64 import Pj64Memory

POLL_HZ = 60
SETTLE_FRAMES = 240   # 8 s of game time watched after each grab
OVERALL = 1           # sample tuple index of igt_overall
RESULT = 2            # sample tuple index of igt_result

# Usamune's TIMER menu. Hand-transcribed — no settings block is in the address
# registry and none is being hunted here. The 2026-08-01 sitting added values
# the earlier probe's comment did not know about: STOP=GRABX (a THIRD value,
# not a second), REALTMR=FADE/HIDE, PSSRACE=RACE. Presets change several at
# once, which is why all five are still wanted per reading.
TIMER_SETTINGS = ("STOP", "DISPLAY", "REALTMR", "PSSRACE", "FADETMR")


# --- pure core (tests/test_verify_star_stop.py drives these) ----------------

def settle_point(samples: list[tuple[int, int, int]],
                 field: int) -> tuple[int, int, bool]:
    """Where `field` reached the value it still holds at the end of the window.

    Returns `(global_timer_of_settle, final_value, observed_holding)`.
    `observed_holding` is False when the value was still moving on the very
    last sample — the window was too short and the number is not final, which
    must never be reported as "it stopped here"."""
    final = samples[-1][field]
    settle_frame = samples[-1][0]
    for sample in reversed(samples):
        if sample[field] != final:
            break
        settle_frame = sample[0]
    return settle_frame, final, settle_frame < samples[-1][0]


def writes(samples: list[tuple[int, int, int]], field: int,
           touch_frame: int) -> list[tuple[int, int]]:
    """Every CHANGE to `field` in the window, as (frames_after_touch, value).

    The write history is the discriminating evidence: one write at +0 means
    Usamune stopped at the grab, a second write later means it stopped again
    at the camera, and no write at all means the value on screen belongs to an
    earlier star."""
    return [(curr[0] - touch_frame, curr[field])
            for prev, curr in zip(samples, samples[1:])
            if curr[field] != prev[field]]


def reading(label: str, frames: int, settle_frame: int, touch_frame: int,
            holding: bool, changed: bool) -> str:
    """One candidate line: a formatted time, plus when it landed."""
    if not changed:
        return (f"  {label:<22} {format_igt(frames):>9}   ({frames} frames) "
                f"— never moved in the window")
    when = settle_frame - touch_frame
    if not holding:
        return (f"  {label:<22} {format_igt(frames):>9}   ({frames} frames) "
                f"— STILL MOVING at +{when} frames, window too short")
    return (f"  {label:<22} {format_igt(frames):>9}   ({frames} frames) "
            f"— came to rest {when} frames after the grab")


def star_stop_report(index: int, course_name: str, star_name: str,
                     journaled_frames: int, journaled_source: str,
                     touch_frame: int,
                     samples: list[tuple[int, int, int]]) -> str:
    """The whole prompt for one grab. Three numbers, then one question.

    Deliberately does NOT apply `IgtClock.DISPLAY_TICK` to the counter
    readings. The 2026-08-01 pause gate answered (a) — a FROZEN counter reads
    on screen as its raw value — over eight samples spanning three presets, so
    raw is the honest prediction here and adding a tick would put a number on
    screen that nobody measured."""
    counter_at, counter_val, counter_holding = settle_point(samples, OVERALL)
    result_at, result_val, result_holding = settle_point(samples, RESULT)
    counter_writes = writes(samples, OVERALL, touch_frame)
    result_writes = writes(samples, RESULT, touch_frame)
    lines = [
        f"\nSTAR #{index} — {star_name} in {course_name}",
        f"  {'WE JOURNAL TODAY':<22} {format_igt(journaled_frames):>9}   "
        f"({journaled_frames} frames, source={journaled_source})",
        reading("Usamune's COUNTER", counter_val, counter_at, touch_frame,
                counter_holding, bool(counter_writes)),
        reading("Usamune's RESULT", result_val, result_at, touch_frame,
                result_holding, bool(result_writes)),
    ]
    if result_writes:
        lines.append("      result writes seen: " + ", ".join(
            f"+{when} -> {value}" for when, value in result_writes))
    else:
        lines.append("      the RESULT store was never written for this grab "
                     "— what it holds belongs to an earlier star")
    lines += [
        "  >>> The dance and camera are over. Read the IGT / SECTION timer now.",
        "      WHICH of those numbers is on screen? (if it is exactly 3",
        "      centisecond higher than one, say which and '+1 frame')",
        "      And name your STOP value for this grab.",
    ]
    return "\n".join(lines)


def settings_prompt() -> str:
    return "\n".join([
        "STEP 0 — copy these off Usamune's TIMER menu before you play:",
        "    " + "  ".join(f"{name}=____" for name in TIMER_SETTINGS),
        "  All five, and again whenever you switch preset. STOP has at least",
        "  three values (GRAB, GRABX, XCAM) — it is not a boolean, so 'XCAM",
        "  mode' is not a thing a reading can be filed under.",
        "  Nothing reads these out of memory: no settings block is in the",
        "  address registry.",
    ])


# --- live shell ------------------------------------------------------------

class PendingGrab:
    """One grab being watched until its settle window closes."""

    def __init__(self, index: int, event, touch_frame: int):
        self.index = index
        self.event = event
        self.touch_frame = touch_frame
        self.samples: list[tuple[int, int, int]] = []

    def observe(self, snap) -> None:
        self.samples.append((snap.global_timer, snap.igt_overall,
                             snap.igt_result))

    def closed(self, frame: int) -> bool:
        return frame - self.touch_frame >= SETTLE_FRAMES

    def report(self) -> str:
        payload = self.event.payload
        return star_stop_report(self.index, payload["course_name"],
                                payload["star_name"], payload["igt_frames"],
                                payload["igt_source"], self.touch_frame,
                                self.samples)


def main() -> None:
    mem = Pj64Memory()
    print(__doc__.split("## Why this exists")[0].strip())
    print("\n" + "=" * 72)
    print(settings_prompt())
    print("=" * 72)
    print(__doc__.split("## How to run it")[1].strip())
    print("\nAttaching to Project64.exe ...")
    while not mem.attach():
        print("  not found (is PJ64 running with the ROM loaded?) retrying in 2s")
        time.sleep(2)
    print("\nAttached. Read-only — nothing is written and no lock is taken.")
    print(f"Grab stars. Each one prints its report {SETTLE_FRAMES} game frames "
          f"later. Ctrl+C when done.")

    reader = SnapshotReader(mem)
    detector = StarGrabDetector()
    pending: list[PendingGrab] = []
    prev = None
    grabs = 0
    try:
        while True:
            curr = reader.read()
            if prev is not None:
                for event in detector.process(prev, curr):
                    # star_grab.py also emits star_time_corrected (Usamune
                    # revising a subarea star's number after we published it);
                    # this probe is about which MOMENT the screen shows, so a
                    # revision to the number is not a second grab.
                    if event.type != "star_collected":
                        continue
                    grabs += 1
                    pending.append(PendingGrab(grabs, event, event.frame))
            for grab in pending:
                grab.observe(curr)
            still_open = []
            for grab in pending:
                if grab.closed(curr.global_timer):
                    print(grab.report())
                else:
                    still_open.append(grab)
            pending = still_open
            prev = curr
            time.sleep(1.0 / POLL_HZ)
    except KeyboardInterrupt:
        for grab in pending:
            print(grab.report())
            print("  (window was cut short by Ctrl+C — treat 'STILL MOVING' "
                  "as unmeasured, not as a finding)")
        print(f"\n\nStopped. {grabs} grab(s) watched.")
        print("Report back, per grab:")
        print("  1. your five TIMER settings at the time")
        print("  2. which of the three numbers was on screen (or one of them "
              "+1 frame)")


if __name__ == "__main__":
    main()

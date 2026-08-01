# tools/derive_xcam.py
"""Live gate: can we compute the X-CAM time OURSELVES, without his settings?

    uv run python tools/derive_xcam.py

Read-only: no database, no server, no recorder lock. Safe while recording.

## Why this matters more than the last two probes

X-cam is not a display preference, it is a LEGALITY rule. Usamune's manual:

    Grab  : Mario touches a star.
    Xcam  : Mario touches the ground after star-grab.
    GrabX : Stops the timer on star-grab first, then updates it on Xcam.
    * The in-game timer keeps running internally.

Leaderboards accept `STOP` of GrabX or Xcam only (and any `DISPLAY` except
Hide). A time recorded on `STOP=Grab` is not a slightly-off time, it is an
invalid one. So the tool must not depend on the player having configured
Usamune correctly — it should derive the legal number itself, from what the
game is doing, whatever the menu says.

That last footnote is what makes it possible: **the internal counter never
stops.** Measured 2026-08-01 over ten grabs, `USAMUNE_OVERALL` ran on 40-79
frames past every grab under every STOP value, GRAB included. The x-cam moment
is therefore still readable off the counter when Usamune itself has already
stopped displaying it.

## Why this probe needs nothing from you but play

The 2026-08-01 sitting established GROUND TRUTH: with `STOP` at GrabX or Xcam,
Usamune's own RESULT store — once its write settles — was the number on his
screen, 7 times out of 7. So under those settings we already know the right
answer for each grab, and a derivation can be scored against it automatically.

This probe watches each grab, computes every candidate x-cam moment it can see
in Mario's actions, and prints each one's error against Usamune's settled
result. **You do not have to read the screen.** Play with `STOP=XCAM` (or
GRABX) and grab stars — a mix of ground grabs and MIDAIR ones, because the two
are the whole question. Ctrl+C prints the summary.

A candidate whose error is the SAME on every grab is the derivation, and its
error is a calibration constant. A candidate whose error varies is wrong, and
no constant will save it.

## Reading the summary

    CONSTANT  -> this candidate IS the x-cam moment; the offset is a constant
    VARIES    -> not the moment. Discard it; do not average it.

If nothing comes back CONSTANT, that is a real finding too and it means x-cam
is not a Mario-action transition — say so rather than picking the closest.
"""
import time

from sm64_events.core.snapshot import SnapshotReader
from sm64_events.core.timefmt import format_igt
from sm64_events.detectors.star_grab import StarGrabDetector
from sm64_events.memory.addresses import (ACT_FALL_AFTER_STAR_GRAB,
                                          ACT_STAR_DANCE_EXIT,
                                          ACT_STAR_DANCE_NO_EXIT,
                                          ACT_STAR_DANCE_WATER)
from sm64_events.memory.pj64 import Pj64Memory

POLL_HZ = 60
SETTLE_FRAMES = 240   # 8 s; the longest observed result write was +39

# "Mario touches the ground after star-grab" — the dance is what he enters
# when he gets there. A midair grab goes ACT_FALL_AFTER_STAR_GRAB first and
# only reaches a dance on landing (live 2026-08-01: a WF caged-island grab
# settled +39 frames after the touch, which is the fall).
DANCE_ACTIONS = frozenset({ACT_STAR_DANCE_EXIT, ACT_STAR_DANCE_WATER,
                           ACT_STAR_DANCE_NO_EXIT})

FRAME, ACTION, OVERALL, RESULT = 0, 1, 2, 3


# --- pure core (tests/test_derive_xcam.py drives these) ---------------------

def settled_result(samples: list[tuple[int, int, int, int]]) -> tuple[int, int] | None:
    """Usamune's own answer: the value its RESULT store holds at the end of the
    window, and the frame the last write landed on.

    None when no write was seen in the window at all — under `STOP=Grab` the
    write happens before we start watching, and under `STOP=None` there is
    none, so there is no ground truth to score against and this grab must be
    skipped rather than scored as a perfect match against a stale value."""
    final = samples[-1][RESULT]
    written_at = None
    for prev, curr in zip(samples, samples[1:]):
        if curr[RESULT] != prev[RESULT]:
            written_at = curr[FRAME]
    return None if written_at is None else (final, written_at)


def first_frame_in(samples: list[tuple[int, int, int, int]],
                   actions: frozenset[int]) -> int | None:
    """The first frame Mario is in any of `actions`, or None."""
    for sample in samples:
        if sample[ACTION] in actions:
            return sample[FRAME]
    return None


def counter_at(samples: list[tuple[int, int, int, int]],
               frame: int) -> int | None:
    """`USAMUNE_OVERALL` on `frame`. None when the frame is outside the window
    — a candidate we cannot price must not be scored as if we could."""
    for sample in samples:
        if sample[FRAME] == frame:
            return sample[OVERALL]
    return None


def candidates(samples: list[tuple[int, int, int, int]],
               touch_frame: int) -> dict[str, tuple[int, int]]:
    """Every moment that could plausibly BE the x-cam, as name -> (frame,
    counter there). Only candidates we can actually price are returned."""
    found: dict[str, int | None] = {
        "our grab edge": touch_frame,
        "star dance entry": first_frame_in(samples, DANCE_ACTIONS),
    }
    fell = first_frame_in(samples, frozenset({ACT_FALL_AFTER_STAR_GRAB}))
    if fell is not None:
        # A midair grab: the landing is the frame he leaves the fall, which is
        # the dance entry — kept as its own row so a grab that never fell can
        # be told apart from one that did.
        found["landing after a fall"] = first_frame_in(samples, DANCE_ACTIONS)
    priced = {}
    for name, frame in found.items():
        if frame is None:
            continue
        counter = counter_at(samples, frame)
        if counter is not None:
            priced[name] = (frame, counter)
    return priced


def errors(scored: list[dict[str, int]]) -> dict[str, tuple[list[int], bool]]:
    """Per candidate, its per-grab errors and whether they are all equal.

    A candidate seen on only SOME grabs still gets a verdict from the grabs it
    appeared on — a midair-only candidate is exactly that shape, and dropping
    it for being partial would discard the interesting half."""
    names = {name for grab in scored for name in grab}
    out = {}
    for name in sorted(names):
        values = [grab[name] for grab in scored if name in grab]
        out[name] = (values, len(set(values)) == 1 and bool(values))
    return out


def summary(scored: list[dict[str, int]]) -> str:
    if not scored:
        return ("\nNo scoreable grabs. Every grab needs a RESULT write inside "
                "the window to score against, which means STOP=XCAM or "
                "STOP=GRABX — on STOP=GRAB the write lands before we start "
                "watching and there is nothing to compare to.")
    lines = [f"\n{'=' * 72}",
             f"SUMMARY — {len(scored)} scoreable grab(s)",
             "  error = candidate's counter MINUS Usamune's settled result.",
             "  A candidate that is the x-cam moment has the SAME error every "
             "time.", ""]
    for name, (values, constant) in errors(scored).items():
        verdict = (f"CONSTANT {values[0]:+d}" if constant else "VARIES")
        lines.append(f"  {verdict:<16} {name:<22} errors: "
                     + ", ".join(f"{value:+d}" for value in values))
    lines.append("")
    if any(constant for _, constant in errors(scored).values()):
        lines.append("  A CONSTANT row is the derivation, and its number is "
                     "the calibration.")
    else:
        lines.append("  Nothing came back constant. That is a finding: x-cam "
                     "is not one of these")
        lines.append("  action transitions. Report it rather than picking the "
                     "closest.")
    return "\n".join(lines)


def grab_report(index: int, course_name: str, star_name: str,
                journaled_frames: int, journaled_source: str,
                touch_frame: int,
                samples: list[tuple[int, int, int, int]]) -> tuple[str, dict[str, int]]:
    """One grab: every candidate priced against Usamune's own settled answer.

    Returns the text and the per-candidate errors (empty when this grab has no
    ground truth, i.e. nothing to score against)."""
    truth = settled_result(samples)
    priced = candidates(samples, touch_frame)
    lines = [f"\nSTAR #{index} — {star_name} in {course_name}",
             f"  we journal today   {format_igt(journaled_frames):>9}  "
             f"({journaled_frames}f, source={journaled_source})"]
    if truth is None:
        lines += [
            "  Usamune wrote no result inside the window, so this grab has no",
            "  ground truth to score against — skipped. (Expected on "
            "STOP=GRAB",
            "  and STOP=None; if you are on XCAM or GRABX, that is itself "
            "news.)",
        ]
        return "\n".join(lines), {}
    truth_frames, truth_at = truth
    lines.append(f"  Usamune's answer   {format_igt(truth_frames):>9}  "
                 f"({truth_frames}f, written +{truth_at - touch_frame})")
    scored = {}
    for name, (frame, counter) in priced.items():
        error = counter - truth_frames
        scored[name] = error
        lines.append(f"    {name:<22} +{frame - touch_frame:<4} counter="
                     f"{counter:<6} error {error:+d}")
    return "\n".join(lines), scored


# --- live shell ------------------------------------------------------------

class PendingGrab:
    def __init__(self, index: int, event, touch_frame: int):
        self.index, self.event, self.touch_frame = index, event, touch_frame
        self.samples: list[tuple[int, int, int, int]] = []

    def observe(self, snap) -> None:
        self.samples.append((snap.global_timer, snap.mario_action,
                             snap.igt_overall, snap.igt_result))

    def closed(self, frame: int) -> bool:
        return frame - self.touch_frame >= SETTLE_FRAMES

    def finish(self) -> tuple[str, dict[str, int]]:
        payload = self.event.payload
        return grab_report(self.index, payload["course_name"],
                           payload["star_name"], payload["igt_frames"],
                           payload["igt_source"], self.touch_frame,
                           self.samples)


def main() -> None:
    mem = Pj64Memory()
    print(__doc__.split("## Why this matters")[0].strip())
    print("\n" + "=" * 72)
    print(__doc__.split("## Why this probe needs nothing from you but play")[1]
          .split("## Reading the summary")[0].strip())
    print("=" * 72)
    print("\nAttaching to Project64.exe ...")
    while not mem.attach():
        print("  not found (is PJ64 running with the ROM loaded?) retrying in 2s")
        time.sleep(2)
    print("\nAttached. Read-only — nothing is written and no lock is taken.")
    print("Set STOP=XCAM (or GRABX) and grab stars — some from the GROUND and")
    print("some in MIDAIR. Ctrl+C prints the summary.")

    reader = SnapshotReader(mem)
    detector = StarGrabDetector()
    pending: list[PendingGrab] = []
    scored: list[dict[str, int]] = []
    prev = None
    grabs = 0
    try:
        while True:
            curr = reader.read()
            if prev is not None:
                for event in detector.process(prev, curr):
                    grabs += 1
                    pending.append(PendingGrab(grabs, event, event.frame))
            for grab in pending:
                grab.observe(curr)
            still_open = []
            for grab in pending:
                if grab.closed(curr.global_timer):
                    text, grab_errors = grab.finish()
                    print(text)
                    if grab_errors:
                        scored.append(grab_errors)
                else:
                    still_open.append(grab)
            pending = still_open
            prev = curr
            time.sleep(1.0 / POLL_HZ)
    except KeyboardInterrupt:
        for grab in pending:
            print(f"\nSTAR #{grab.index} — window cut short by Ctrl+C, "
                  f"not scored.")
        print(summary(scored))
        print(f"\nStopped. {grabs} grab(s) seen, {len(scored)} scoreable.")
        print("Paste the SUMMARY block back — the per-grab detail is only "
              "there to argue with it.")


if __name__ == "__main__":
    main()

# tools/derive_xcam.py
"""Live gate: can we compute the X-CAM time OURSELVES, without his settings?

    uv run python tools/derive_xcam.py

Read-only: no database, no server, no recorder lock. Safe while recording.

ANSWERED 2026-08-01 — yes, and `detectors/star_grab.py` now ships it: the
x-cam moment is the star-DANCE entry and Usamune's number is that frame's
overall counter + `IgtClock.DISPLAY_TICK`. So this is now a REGRESSION GATE
first and a derivation second — the summary scores what we actually journal
against Usamune's own answer and prints GATE PASSED / GATE FAILED, alongside
the same candidate table that found the derivation in the first place.

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
from collections import deque

from sm64_events.core.snapshot import SnapshotReader
from sm64_events.core.timefmt import format_igt
from sm64_events.detectors.star_grab import StarGrabDetector
from sm64_events.memory.addresses import (ACT_FALL_AFTER_STAR_GRAB,
                                          STAR_DANCE_ACTIONS)
from sm64_events.memory.pj64 import Pj64Memory

POLL_HZ = 60
# How long this probe WATCHES — deliberately far wider than the emit's own
# settle window, because a write we do not see is a fact we cannot learn. It is
# how the store's zeroing on level exit (+92, live 2026-08-01) was found at all.
# What it does NOT do is decide the ground truth: scoring stops at the emit's
# deadline (see settled_result), or the probe grades us against a cleared store.
SETTLE_FRAMES = 240   # 8 s; the latest MEANINGFUL result write was +41

# "Mario touches the ground after star-grab" — the dance is what he enters
# when he gets there. A midair grab goes ACT_FALL_AFTER_STAR_GRAB first and
# only reaches a dance on landing (live 2026-08-01: a WF caged-island grab
# settled +39 frames after the touch, which is the fall). The action ids live
# in addresses.py because star_grab.py now ships this derivation.
DANCE_ACTIONS = STAR_DANCE_ACTIONS

FRAME, ACTION, OVERALL, RESULT = 0, 1, 2, 3

# The candidate that is the shipped code, scored beside the hypotheses.
JOURNALED = "what we journal"


# --- pure core (tests/test_derive_xcam.py drives these) ---------------------

def settled_result(samples: list[tuple[int, int, int, int]],
                   deadline_frame: int) -> tuple[int, int] | None:
    """Usamune's own answer: the last write that landed at or before
    `deadline_frame`, as (value, frame).

    None when no write was seen in the window at all — under `STOP=Grab` the
    write happens before we start watching, and under `STOP=None` there is
    none, so there is no ground truth to score against and this grab must be
    skipped rather than scored as a perfect match against a stale value.

    **The deadline is not a tidiness measure; without it this probe lies.**
    The observation window is 8 s and the RESULT store does not stay put that
    long: leaving the course ZEROES it. Live 2026-08-01, his subarea run,
    WF "Shoot into the Wild Blue" — writes at +1=328, +3=330, then +92=0 as he
    exited. Reading the store at the end of the window made Usamune's answer
    0'00"00 and scored the shipped derivation at +327, a failure the gate
    invented out of a correct journal entry. Scoring stops where
    `StarGrabDetector.RESULT_SETTLE_FRAMES` stops, so this probe measures the
    same store the emit reads. Everything after is still PRINTED — the zeroing
    write is how it was found."""
    writes = [write for write in result_writes(samples)
              if write[0] <= deadline_frame]
    return (writes[-1][1], writes[-1][0]) if writes else None


def result_writes(samples: list[tuple[int, int, int, int]]
                  ) -> list[tuple[int, int]]:
    """Every change of the result store in the window, as (frame, value).

    Printed rather than summarised because the COUNT decided how long
    star_grab.py has to wait, and **his subarea run of 2026-08-01 answered it:
    Usamune never writes once.** Five grabs, 2-3 writes each. The first one or
    two are ECHOES of our own counter (write value = the counter on that
    sample + 1, at the grab and again at the dance entry); on a SUBAREA star a
    third write lands 26-28 frames after the dance entry carrying the
    whole-star time, and that one is the answer. LLL Elevator Tour:
    +1=465, +14=479, +41=777 against counters of 464 and 478.

    So the emit CANNOT leave on the first write, and it cannot tell a
    single-area star (no correction is coming) from a subarea one (it is) at
    the moment that write lands — which is why the wait is unconditional."""
    out = []
    for prev, curr in zip(samples, samples[1:]):
        if curr[RESULT] != prev[RESULT]:
            out.append((curr[FRAME], curr[RESULT]))
    return out


def first_sample_in(samples: list[tuple[int, int, int, int]],
                    actions: frozenset[int]) -> tuple[int, int, int, int] | None:
    """The first SAMPLE in which Mario is in any of `actions`.

    Returns the whole tuple on purpose, so a caller reads the counter out of
    the SAME memory read as the action. Looking the counter back up by frame
    number is what the first version did, and at a 60 Hz poll over a 30 fps
    game there are two samples per frame — so that lookup could answer with a
    different read than the one that saw the transition, and the counter is
    the number being measured."""
    for sample in samples:
        if sample[ACTION] in actions:
            return sample
    return None


def was_midair(samples: list[tuple[int, int, int, int]]) -> bool:
    """Did Mario fall before reaching the ground? A grab taken while standing
    has its x-cam ON the grab frame — live 2026-08-01, his words: "I just
    simply ran into the star… grabbing and xcam is identical" — and a backflip
    into a star puts tens of frames between the two."""
    return first_sample_in(samples,
                           frozenset({ACT_FALL_AFTER_STAR_GRAB})) is not None


def candidates(samples: list[tuple[int, int, int, int]],
               touch_frame: int) -> dict[str, tuple[int, int]]:
    """Every moment that could plausibly BE the x-cam, as name -> (frame,
    counter read on that same sample)."""
    priced = {}
    edge = next((sample for sample in samples if sample[FRAME] == touch_frame),
                samples[0])
    priced["our grab edge"] = (edge[FRAME], edge[OVERALL])
    dance = first_sample_in(samples, DANCE_ACTIONS)
    if dance is not None:
        priced["star dance entry"] = (dance[FRAME], dance[OVERALL])
    return priced


# Our own measurement noise, not Usamune's. A snapshot is twelve separate
# ReadProcessMemory calls, so the action and the counter in one sample can
# straddle a game frame — the same torn read that made the death-clock gate
# cry PROBLEM on seven of eight healthy deaths (see
# verify_death_clock.READ_SKEW_FRAMES, live 2026-08-01). A verdict that
# demanded EXACT agreement reported "nothing fits" for errors of -1, -2, -1,
# -1, which is a derivation with one noisy sample, not a failed hypothesis.
SAMPLING_SKEW = 1


def errors(scored: list[dict[str, int]]) -> dict[str, tuple[list[int], int | None]]:
    """Per candidate: its per-grab errors, and the calibration constant they
    support (None when they do not support one).

    A candidate seen on only SOME grabs still gets a verdict from the grabs it
    appeared on — dropping partial rows would discard the interesting half."""
    names = {name for grab in scored for name in grab}
    out = {}
    for name in sorted(names):
        values = [grab[name] for grab in scored if name in grab]
        spread = max(values) - min(values) if values else 0
        supported = (max(set(values), key=values.count)
                     if values and spread <= SAMPLING_SKEW else None)
        out[name] = (values, supported)
    return out


def summary(scored: list[dict[str, int]]) -> str:
    if not scored:
        return ("\nNo scoreable grabs. Every grab needs a RESULT write inside "
                "the window to score against, which means STOP=XCAM or "
                "STOP=GRABX — and even then, a star grabbed ON THE GROUND has "
                "its x-cam on the grab frame, so Usamune writes once, before "
                "we start watching. Grab some in MIDAIR.")
    scores = errors(scored)
    lines = [f"\n{'=' * 72}",
             f"SUMMARY — {len(scored)} scoreable grab(s)",
             "  error = candidate's counter MINUS Usamune's settled result.",
             "  A candidate that is the x-cam moment lands on ONE error, give "
             "or take",
             f"  {SAMPLING_SKEW} frame of our own read skew (see "
             f"SAMPLING_SKEW).", ""]
    for name, (values, supported) in scores.items():
        exact = supported is not None and len(set(values)) == 1
        verdict = ("VARIES" if supported is None
                   else f"CONSTANT {supported:+d}" if exact
                   else f"CONSTANT {supported:+d} ±{SAMPLING_SKEW}")
        lines.append(f"  {verdict:<18} {name:<22} errors: "
                     + ", ".join(f"{value:+d}" for value in values))
    lines.append("")
    shipped = scores.get(JOURNALED)
    if shipped is not None:
        values, supported = shipped
        if supported == 0:
            lines.append(f"  GATE PASSED — '{JOURNALED}' matched Usamune on "
                         f"all {len(values)} scoreable grab(s).")
        elif supported is not None:
            lines.append(f"  GATE FAILED — '{JOURNALED}' is off by a CONSTANT "
                         f"{supported:+d}. A constant is a calibration bug, "
                         f"not a wrong")
            lines.append("  moment: check IgtClock.DISPLAY_TICK. An offset of "
                         "one frame over few grabs")
            lines.append("  can still be our own read skew — take more before "
                         "changing anything.")
        else:
            lines.append(f"  GATE FAILED — '{JOURNALED}' VARIES, so we are "
                         f"still choosing the wrong MOMENT,")
            lines.append("  not merely the wrong offset. That is the same "
                         "shape as the original bug.")
        lines.append("")
    winners = [(name, supported) for name, (_, supported) in scores.items()
               if supported is not None and name != JOURNALED]
    if winners:
        name, supported = winners[0]
        lines.append(f"  '{name}' is the x-cam moment, and Usamune's number is "
                     f"its counter {-supported:+d}.")
        lines.append("  A ±  verdict means one sample disagreed by a frame; "
                     "that is OUR read,")
        lines.append("  not Usamune's clock. More midair grabs shrink it.")
    else:
        lines.append("  Nothing landed on one error. That is a finding: x-cam "
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
    priced = candidates(samples, touch_frame)
    # The emit's own deadline, anchored where the emit anchors it: the x-cam,
    # falling back to the touch when no dance was seen (a grab that never
    # reached one — star_grab.py journals that from the grab frame too).
    xcam_frame = priced.get("star dance entry", priced["our grab edge"])[0]
    deadline = xcam_frame + StarGrabDetector.RESULT_SETTLE_FRAMES
    truth = settled_result(samples, deadline)
    shape = "midair grab" if was_midair(samples) else "GROUND grab"
    lines = [f"\nSTAR #{index} — {star_name} in {course_name}  ({shape})",
             f"  we journal today   {format_igt(journaled_frames):>9}  "
             f"({journaled_frames}f, source={journaled_source})"]
    if truth is None:
        lines += [
            "  Usamune wrote no result inside the window — nothing to score "
            "against,",
            "  so this grab is skipped. Expected on STOP=GRAB and STOP=None, "
            "and ALSO",
            "  on a GROUND grab under any setting: x-cam IS the grab frame "
            "there, so",
            "  Usamune writes once, before we start watching. Only a MIDAIR "
            "grab can",
            "  separate the two moments, and only a separated pair can "
            "measure anything.",
        ]
        return "\n".join(lines), {}
    truth_frames, truth_at = truth
    lines.append(f"  Usamune's answer   {format_igt(truth_frames):>9}  "
                 f"({truth_frames}f, written +{truth_at - touch_frame})")
    writes = result_writes(samples)
    if len(writes) > 1:
        detail = ", ".join(f"+{frame - touch_frame}={value}"
                           for frame, value in writes)
        lines.append(f"    result store written {len(writes)}x: {detail}")
    late = [frame for frame, _ in writes if frame > deadline]
    if late:
        # Never silent. A dropped write that is not named reads as a store
        # that held still, which is the opposite of what these mean — leaving
        # the course zeroes it, and that zero is not an answer to anything.
        lines.append(
            f"    ignored {len(late)} write(s) after +"
            f"{deadline - touch_frame} (past the emit's settle window; "
            "the store is cleared on level exit)")
    # What we SHIP is a candidate like any other, and since 2026-08-01 it is
    # the interesting one: star_grab.py now derives the x-cam itself, so this
    # row is the regression gate. A CONSTANT +0 here is the whole answer.
    scored = {JOURNALED: journaled_frames - truth_frames}
    for name, (frame, counter) in priced.items():
        error = counter - truth_frames
        scored[name] = error
        lines.append(f"    {name:<22} +{frame - touch_frame:<4} counter="
                     f"{counter:<6} error {error:+d}")
    return "\n".join(lines), scored


# --- live shell ------------------------------------------------------------

class PendingGrab:
    def __init__(self, index: int, event, touch_frame: int,
                 seed: list[tuple[int, int, int, int]] | None = None):
        self.index, self.event, self.touch_frame = index, event, touch_frame
        # Seeded from the shell's rolling buffer, because star_grab.py emits at
        # the X-CAM now, not at the grab: by the time the event arrives Mario
        # has already landed, and both the grab edge and Usamune's own write
        # under STOP=XCAM are in the past. Watching only forward from the event
        # would see no write at all and silently score nothing.
        self.samples: list[tuple[int, int, int, int]] = list(seed or [])
        # Usamune's later revision of this grab's number, if one is emitted
        # (star_grab.py publishes at the x-cam and corrects a subarea star
        # about a second later). What we SHIP is the corrected value, so that
        # is what has to be scored — grading the first publish would fail this
        # gate on exactly the stars the correction exists to get right.
        self.corrected: tuple[int, str] | None = None

    def observe(self, snap) -> None:
        # ONE sample per game frame. The poll runs at 60 Hz over a 30 fps game,
        # so keeping every read stored two samples per frame — and since the
        # counter is what we are measuring, a duplicate frame is just a second
        # chance to record a torn one.
        if self.samples and snap.global_timer <= self.samples[-1][FRAME]:
            return
        self.samples.append((snap.global_timer, snap.mario_action,
                             snap.igt_overall, snap.igt_result))

    def closed(self, frame: int) -> bool:
        return frame - self.touch_frame >= SETTLE_FRAMES

    def finish(self) -> tuple[str, dict[str, int]]:
        payload = self.event.payload
        shipped, source = payload["igt_frames"], payload["igt_source"]
        if self.corrected is not None:
            shipped, source = self.corrected
            source += " (corrected)"
        return grab_report(self.index, payload["course_name"],
                           payload["star_name"], shipped, source,
                           self.touch_frame, self.samples)


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
    recent: deque[tuple[int, int, int, int]] = deque(maxlen=SETTLE_FRAMES * 2)
    prev = None
    grabs = 0
    try:
        while True:
            curr = reader.read()
            if not recent or curr.global_timer > recent[-1][FRAME]:
                recent.append((curr.global_timer, curr.mario_action,
                               curr.igt_overall, curr.igt_result))
            if prev is not None:
                for event in detector.process(prev, curr):
                    touch = event.payload["grab_frame"]
                    if event.type == "star_time_corrected":
                        # Not a second grab: Usamune revised the one still
                        # open. Matched on the touch frame, the same identity
                        # the projector pairs on.
                        for grab in pending:
                            if grab.touch_frame == touch:
                                grab.corrected = (event.payload["igt_frames"],
                                                  event.payload["igt_source"])
                        continue
                    grabs += 1
                    pending.append(PendingGrab(
                        grabs, event, touch,
                        [s for s in recent if s[FRAME] >= touch]))
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

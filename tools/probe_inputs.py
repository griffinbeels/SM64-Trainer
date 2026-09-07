# tools/probe_inputs.py
"""Locate the controller in RDRAM, then MEASURE what a sampler misses.

Read-only; safe to run beside a live practice session.

ANSWERED 2026-08-20, over four live sessions of his ordinary play:

- gPlayer1Controller was FOUND, by its pointer signature rather than a guess,
  and the address itself now lives in `memory/layout.py` beside its evidence
  (this file may not restate it: a US address baked into a tool is a tool that
  silently misreads JP). The corroboration that pinned it: its statusData
  points at gControllerStatuses, whose [0] reads CONT_TYPE_NORMAL for a pad in
  port 1 while [1..3] read the no-controller errno; its controllerData points
  at gControllerPads, whose four 6-byte entries carry the matching errnos; its
  port reads 0. One struct carries the raw stick, the processed stick and its
  magnitude, and all 14 buttons in two 16-bit masks.
- 30 Hz is the TRUE input rate. The frame counter ticks exactly 30/s and the
  game rewrites that struct exactly once per tick, so one stored state per
  game frame loses nothing.
- NOTHING is missed, at any rate tried. Zero game frames advanced unobserved
  across every window measured (180, 301, 601, 901 and 1348 frames) at 60,
  120, 250 and 500 Hz. Windows' 15.6 ms timer granularity never bit: the
  paced loop held target even before timeBeginPeriod(1).
- THE FINDING: the game rewrites the struct ~62% of the way THROUGH a frame,
  so the rate that matters is not "don't miss a frame", it is "look after the
  rewrite". At 60 Hz the last look of each frame lands at 50% and reads FRESH
  on 0% of frames — one frame late, every single frame, invisibly. 120 Hz is
  97% fresh, 250 Hz and 500 Hz are 99%. 250 Hz is the safe floor.
- Compression is real: 45 s of play = 1348 frames -> 289 readable states
  (6.4/s), with 28 one-frame button states in that span.

Re-run it to re-check any of the above, or with --at scan to re-hunt the
address after a ROM change.

Three questions, one run:

1. WHERE does the controller live?  A shape scan, not a guessed address.
   decomp's `struct Controller` is
       0x00 s16 rawStickX / 0x02 s16 rawStickY
       0x04 f32 stickX    / 0x08 f32 stickY  / 0x0C f32 stickMag
       0x10 u16 buttonDown/ 0x12 u16 buttonPressed
   and `buttonPressed` is ALWAYS a subset of `buttonDown` — an invariant no
   unrelated halfword pair holds across a dozen dumps of live play. That plus
   a raw stick in [-128, 127] and a magnitude float in [0, 64] pins it.

2. Is 30 Hz the TRUE input rate?  If the controller never changes twice inside
   one tick of gGlobalTimer, then storing one state per game frame is lossless
   and the whole input-timeline feature is well founded.

3. What does a sampler actually MISS?  Counts game frames that advanced with
   nobody watching, at several target rates, with and without Windows' timer
   resolution raised (the default 15.6 ms granularity is why a naive fast
   sampler is not actually fast).

Every sample sandwiches the controller read between two reads of the frame
counter and discards the pair when they disagree, so no sample can staple
frame N's timer to frame N+1's input.

    uv run python tools/probe_inputs.py            # find, then measure
    uv run python tools/probe_inputs.py --at 0x...  # skip the scan
"""
import argparse
import array
import ctypes
import struct
import sys
import time
from collections import Counter

from sm64_events.memory import addresses as A
from sm64_events.memory.layout import LAYOUT_ROWS, layout_for
from sm64_events.memory.pj64 import Pj64Memory

# Every controller fact lives in addresses.py (version-independent) and the
# candidate ADDRESS in layout.py (per ROM). Nothing here restates either: a
# probe that bakes in a US address is a probe that silently misreads JP, which
# is what `tests/test_single_source.py`'s "a RAM address" row exists to stop.
BUTTON_BITS = A.BUTTON_BITS
VALID_MASK = A.BUTTON_VALID_MASK
BUTTON_DOWN_OFF = A.CONTROLLER_BUTTON_DOWN_OFF
STRUCT_SIZE = A.CONTROLLER_SIZE
SETTLE_PHASE = A.CONTROLLER_SETTLE_PHASE

_MEGABYTE = 0x100000


def scan_band(layout) -> tuple[int, int]:
    """The RDRAM window the address hunt sweeps, DERIVED from the layout.

    From the lowest global this ROM's layout names to the highest, each
    rounded outward to a megabyte. Everything the tracker reads lives in that
    band by construction, and so does anything sitting beside it — including
    a copy Usamune might keep for its own input display.
    """
    known = [value for value in
             (layout.value(row.field) for row in LAYOUT_ROWS)
             if value is not None]
    if not known:
        raise SystemExit(f"the {layout.version} layout names no address yet")
    return (min(known) & ~(_MEGABYTE - 1),
            (max(known) + _MEGABYTE) & ~(_MEGABYTE - 1))


def button_names(mask: int) -> str:
    hit = [name for bit, name in BUTTON_BITS if mask & bit]
    return "+".join(hit) if hit else "-"


def halfwords(image: bytes, band: tuple[int, int]) -> array.array:
    """The scan region of a whole-RDRAM image, as PJ64 stores it.

    PJ64 keeps big-endian RDRAM as little-endian 32-bit words, so the N64
    halfword at index `i` sits at array index `i ^ 1`. Indexing that way costs
    one XOR and saves the per-word byte swap a `read_block` of a megabyte
    would do in Python.
    """
    arr = array.array("H")
    low, high = band
    arr.frombytes(image[low - A.KSEG0_BASE:high - A.KSEG0_BASE])
    return arr


def take_dumps(mem, count: int, gap_s: float,
               band: tuple[int, int]) -> list[array.array]:
    dumps = []
    for _ in range(count):
        dumps.append(halfwords(mem.read_image(), band))
        time.sleep(gap_s)
    return dumps


def scan_for_controller(views: list[array.array],
                        band: tuple[int, int]) -> list[int]:
    """Halfword addresses that behave like `Controller.buttonDown`."""
    first = views[0]
    limit = len(first) - 2

    # Pass 1 — cheap: valid bits, and buttonPressed a subset, in ONE dump.
    survivors = [
        i for i in range(limit)
        if not (first[i ^ 1] & ~VALID_MASK)
        and not (first[(i + 1) ^ 1] & ~first[i ^ 1])
    ]
    # Pass 2 — the same invariant across every dump, plus real variety.
    for view in views[1:]:
        survivors = [
            i for i in survivors
            if not (view[i ^ 1] & ~VALID_MASK)
            and not (view[(i + 1) ^ 1] & ~view[i ^ 1])
        ]
    seen_values = {i: {view[i ^ 1] for view in views} for i in survivors}
    survivors = [i for i in survivors
                 if len(seen_values[i]) > 1 and any(seen_values[i])]
    return [band[0] + 2 * i for i in survivors]


def deadzone(raw: int) -> float:
    """decomp's adjust_analog_stick: an 8-unit dead zone, shifted back by 6."""
    if raw <= -8:
        return float(raw + 6)
    if raw >= 8:
        return float(raw - 6)
    return 0.0


def fits_controller(block: bytes) -> tuple | None:
    """Decode a `struct Controller` and verify it is internally CONSISTENT.

    The decisive filter, and the reason the probe needs the stick to be moving:
    the processed stick must be the raw stick put through the dead zone, and
    the magnitude must be their hypotenuse, clamped at 64. Nothing else in a
    megabyte of RDRAM satisfies that by accident.
    """
    raw_x, raw_y = struct.unpack_from(">hh", block, 0x00)
    stick_x, stick_y, stick_mag = struct.unpack_from(">fff", block, 0x04)
    down, pressed = struct.unpack_from(">HH", block, 0x10)
    if not (-128 <= raw_x <= 127 and -128 <= raw_y <= 127):
        return None
    for value in (stick_x, stick_y, stick_mag):
        if value != value or abs(value) > 128.0:       # NaN or absurd
            return None
    want_x, want_y = deadzone(raw_x), deadzone(raw_y)
    want_mag = (want_x * want_x + want_y * want_y) ** 0.5
    if want_mag > 64.0:                                 # clamped to the cap
        want_x *= 64.0 / want_mag
        want_y *= 64.0 / want_mag
        want_mag = 64.0
    if (abs(stick_x - want_x) > 0.01 or abs(stick_y - want_y) > 0.01
            or abs(stick_mag - want_mag) > 0.01):
        return None
    return raw_x, raw_y, stick_x, stick_y, stick_mag, down, pressed


def describe_candidate(mem, button_down_addr: int) -> str | None:
    """Decode the whole struct around a candidate; None if it does not fit."""
    base = button_down_addr - BUTTON_DOWN_OFF
    if base % 4:
        return None
    try:
        block = mem.read_block(base, STRUCT_SIZE)
    except Exception:
        return None
    fit = fits_controller(block)
    if fit is None:
        return None
    raw_x, raw_y, stick_x, stick_y, stick_mag, down, pressed = fit
    return (f"  base {base:#010x}  raw({raw_x:+4d},{raw_y:+4d})  "
            f"stick({stick_x:+7.2f},{stick_y:+7.2f}) mag {stick_mag:5.2f}  "
            f"down={down:#06x} [{button_names(down)}]  "
            f"pressed={pressed:#06x} [{button_names(pressed)}]")


def watch_candidates(mem, bases: list[int], reads: int = 40,
                     gap_s: float = 0.05) -> list[int]:
    """Keep only bases that stay a CONSISTENT Controller across live reads.

    A candidate survives when every read decodes cleanly AND the run shows the
    stick actually moving and a button actually held — proof the address is
    the live one rather than a stale copy that happens to be shaped right.
    """
    alive = [base for base in bases if base % 4 == 0]
    moved = {base: False for base in alive}
    pushed = {base: False for base in alive}
    for _ in range(reads):
        still = []
        for base in alive:
            try:
                fit = fits_controller(mem.read_block(base, STRUCT_SIZE))
            except Exception:
                continue
            if fit is None:
                continue
            if fit[0] or fit[1]:
                moved[base] = True
            if fit[5]:
                pushed[base] = True
            still.append(base)
        alive = still
        if not alive:
            break
        time.sleep(gap_s)
    return [base for base in alive if moved[base] and pushed[base]]


# --- phase B: what does a sampler miss? -------------------------------------

def wait_for_input(mem, base: int, timeout_s: float) -> bool:
    """Block until the pad is actually being used, or give up."""
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        block = mem.read_block(base, STRUCT_SIZE)
        raw_x, raw_y = struct.unpack_from(">hh", block, 0x00)
        down, _pressed = struct.unpack_from(">HH", block, 0x10)
        if down or abs(raw_x) >= 8 or abs(raw_y) >= 8:
            return True
        time.sleep(0.05)
    return False


def timer_resolution(period_ms: int | None):
    """Raise Windows' scheduler granularity for the duration, or leave it."""
    if period_ms is None:
        return lambda: None
    winmm = ctypes.windll.winmm
    winmm.timeBeginPeriod(period_ms)
    return lambda: winmm.timeEndPeriod(period_ms)


OCTANTS = ("R", "UR", "U", "UL", "L", "DL", "D", "DR")


def stick_bucket(raw_x: int, raw_y: int) -> str:
    """A stick reading as a person would say it: direction plus how far."""
    import math
    mag = math.hypot(raw_x, raw_y)
    if mag < 8:
        return "neutral"
    octant = OCTANTS[int(((math.degrees(math.atan2(raw_y, raw_x)) % 360)
                          + 22.5) // 45) % 8]
    band = "full" if mag >= 64 else ("half" if mag >= 32 else "light")
    return f"{octant}/{band}"


def compress(frames: list[tuple[int, tuple]], with_stick: bool) -> list[tuple]:
    """Merge consecutive frames whose readable state is identical.

    This is the compression the input recipe would use, run here so the design
    argues from a real count instead of a guess about how chatty a stick is.
    """
    runs: list[list] = []
    for frame, (raw_x, raw_y, down, _pressed) in frames:
        key = (down, stick_bucket(raw_x, raw_y)) if with_stick else (down,)
        if runs and runs[-1][0] == key and runs[-1][2] + 1 == frame:
            runs[-1][2] = frame
        else:
            runs.append([key, frame, frame])
    return [(key, lo, hi) for key, lo, hi in runs]


def sample_run(mem, base: int, frame_addr: int, hz: float, seconds: float,
               period_ms: int | None, keep: list | None = None) -> dict:
    """Sample as close to `hz` as we can and count what the game did unseen."""
    release = timer_resolution(period_ms)
    interval = 1.0 / hz
    observed: dict[int, tuple[int, int, int, int]] = {}
    straddles = 0
    samples = 0
    changed_buttons = 0
    changed_stick = 0
    changed_frames: set[int] = set()
    last: dict[int, tuple[int, int, int, int]] = {}
    window: dict[int, list[float]] = {}
    settle_at: list[int] = []
    per_frame = Counter()
    started = time.perf_counter()
    deadline = started
    try:
        while time.perf_counter() - started < seconds:
            # timer -> controller -> timer: a disagreement means the game
            # advanced mid-sample, so the pair is not one coherent frame.
            before = mem.read_u32(frame_addr)
            block = mem.read_block(base, STRUCT_SIZE)
            after = mem.read_u32(frame_addr)
            samples += 1
            if before != after:
                straddles += 1
            else:
                raw_x, raw_y = struct.unpack_from(">hh", block, 0x00)
                down, pressed = struct.unpack_from(">HH", block, 0x10)
                state = (raw_x, raw_y, down, pressed)
                per_frame[before] += 1
                now = time.perf_counter()
                window.setdefault(before, [now, now])[1] = now
                if before in observed:
                    if observed[before] != state:
                        changed_buttons += observed[before][2] != down
                        changed_stick += observed[before][:2] != state[:2]
                        changed_frames.add(before)
                        # WHERE in the frame's sample window it settled — the
                        # thing that decides whether "frame N's input" is the
                        # first reading after the counter ticks or the last.
                        settle_at.append(per_frame[before])
                    last[before] = state
                else:
                    observed[before] = state
                    last[before] = state
            deadline += interval
            slack = deadline - time.perf_counter()
            if slack > 0:
                time.sleep(slack)
            else:
                deadline = time.perf_counter()
    finally:
        release()

    elapsed = time.perf_counter() - started
    frames = sorted(observed)
    span = (frames[-1] - frames[0]) if len(frames) > 1 else 0
    missed = span + 1 - len(frames) if frames else 0
    if keep is not None:
        keep.extend((frame, observed[frame], last[frame]) for frame in frames)
    settle_pct = (sum(settle_at) / len(settle_at) / (samples / max(len(frames), 1))
                  if settle_at else 0.0)
    # How late in a frame does our LAST look at the pad land? The game rewrites
    # the controller ~62% of the way through a frame, so a window whose last
    # sample lands before that reads the PREVIOUS frame's input. This is the
    # number that sets the tap's minimum rate.
    phases = sorted((pair[1] - pair[0]) * 30.0 for frame, pair in window.items()
                    if per_frame[frame] > 1)
    late = (sum(1 for phase in phases if phase >= SETTLE_PHASE) / len(phases)
            if phases else 0.0)
    return {
        "settled_at_sample": round(settle_pct, 2),
        "last_look_phase": phases[len(phases) // 2] if phases else 0.0,
        "frames_read_fresh": late,
        "target_hz": hz,
        "actual_hz": samples / elapsed,
        "samples": samples,
        "straddles": straddles,
        "game_hz": (span / elapsed) if elapsed else 0.0,
        "frames_seen": len(frames),
        "frames_spanned": span + 1 if frames else 0,
        "frames_missed": missed,
        "unstable_frames": len(changed_frames),
        "unstable_buttons": changed_buttons,
        "unstable_stick": changed_stick,
        "samples_per_frame": dict(sorted(Counter(per_frame.values()).items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--at", default="layout",
                        help="struct base; 'layout' reads memory/layout.py, "
                             "'scan' re-runs the address hunt")
    parser.add_argument("--seconds", type=float, default=30.0,
                        help="how long the 500 Hz trace capture runs")
    parser.add_argument("--sweep", type=float, default=8.0,
                        help="how long each rate in the miss sweep runs")
    parser.add_argument("--wait", type=float, default=120.0,
                        help="how long to wait for the player before tracing")
    parser.add_argument("--dumps", type=int, default=14)
    parser.add_argument("--gap", type=float, default=0.25,
                        help="seconds between scan dumps")
    parser.add_argument("--watch", type=int, default=40,
                        help="live re-reads of each candidate")
    args = parser.parse_args()

    mem = Pj64Memory()
    if not mem.attach():
        print("Project64 not attached (is the ROM loaded?)")
        return 1
    layout = layout_for("us")
    band = scan_band(layout)
    frame_addr = layout.global_timer
    print(f"attached; frame counter at {frame_addr:#010x}\n")

    if args.at == "layout":
        if layout.player1_controller is None:
            print(f"the {layout.version} layout has no controller address yet;"
                  " re-run with --at scan")
            return 1
        bases = [layout.player1_controller]
    elif args.at != "scan":
        bases = [int(args.at, 0)]
    else:
        print(f"scanning {band[1] - band[0]:,} bytes x {args.dumps} dumps "
              f"-- PLAY NORMALLY, press a variety of buttons...")
        dumps = take_dumps(mem, args.dumps, args.gap, band)
        found = scan_for_controller(dumps, band)
        print(f"  {len(found)} halfword(s) behave like buttonDown; "
              f"watching each one live...", flush=True)
        bases = watch_candidates(mem, [a - BUTTON_DOWN_OFF for a in found],
                                 reads=args.watch, gap_s=0.25)
        for base in bases:
            print(describe_candidate(mem, base + BUTTON_DOWN_OFF))
        if not bases:
            print("  nothing stayed internally consistent. The stick must be "
                  "MOVING and buttons pressed during the scan -- that is what "
                  "the dead-zone/magnitude check keys on. Re-run while playing.")
            return 2
        print()

    base = bases[0]
    print(f"measuring against {base:#010x}\n", flush=True)
    captured: list = []
    for hz, period in ((60, None), (120, None), (250, 1), (500, 1)):
        result = sample_run(mem, base, frame_addr, hz, args.sweep, period)
        tag = f"{hz:>3} Hz {'(timer 1ms)' if period else '           '}"
        print(f"{tag}  actual {result['actual_hz']:6.1f} Hz | "
              f"game {result['game_hz']:5.2f} fps | "
              f"frames {result['frames_seen']}/{result['frames_spanned']} | "
              f"MISSED {result['frames_missed']} | "
              f"straddles {result['straddles']} | "
              f"unstable frames {result['unstable_frames']} "
              f"(buttons {result['unstable_buttons']}, "
              f"stick {result['unstable_stick']}, "
              f"settles {result['settled_at_sample']:.0%} into the frame) | "
              f"last look at {result['last_look_phase']:.0%}, "
              f"FRESH on {result['frames_read_fresh']:.0%} of frames | "
              f"samples/frame {result['samples_per_frame']}", flush=True)

    # The trace capture WAITS for the player rather than assuming they are
    # already at the controller — the first long run measured 901 frames of a
    # completely idle pad and answered nothing.
    print(f"\nwaiting for input (up to {args.wait:.0f}s) -- PLAY NOW", flush=True)
    if not wait_for_input(mem, base, args.wait):
        print("  no input seen; nothing to trace.")
        return 0
    print(f"  input seen; capturing {args.seconds:.0f}s at 500 Hz...",
          flush=True)
    sample_run(mem, base, frame_addr, 500, args.seconds, 1, keep=captured)
    if not captured:
        return 0
    print(f"\n--- the 500 Hz capture: {len(captured)} frames ---")
    # WHICH observation is frame N's input: the first reading after the frame
    # counter ticks, or the last one before it ticks again? The game answers
    # for itself. buttonPressed is the game's own "newly down THIS frame", so
    # the convention whose own down-edges line up with it is the true one.
    for label, which in (("first-in-window", 1), ("last-in-window", 2)):
        agree = disagree = edges = 0
        for index in range(1, len(captured)):
            if captured[index][0] != captured[index - 1][0] + 1:
                continue
            down = captured[index][which][2]
            prev_down = captured[index - 1][which][2]
            pressed = captured[index][which][3]
            newly = down & ~prev_down
            if not (newly or pressed):
                continue
            edges += 1
            if newly == pressed:
                agree += 1
            else:
                disagree += 1
        share = agree / edges if edges else 0.0
        print(f"  {label:<16} down-edges match the game's own "
              f"buttonPressed on {agree}/{edges} frames ({share:.0%})")

    captured = [(frame, last) for frame, _first, last in captured]
    active = [f for f in captured if f[1][2] or abs(f[1][0]) >= 8
              or abs(f[1][1]) >= 8]
    print(f"  {len(active)} of {len(captured)} frames carry any input")
    buttons_only = compress(captured, with_stick=False)
    readable = compress(captured, with_stick=True)
    print(f"button-only runs: {len(buttons_only)}  |  "
          f"button+stick runs: {len(readable)}  "
          f"({len(readable) / (len(captured) / 30.0):.1f} per second of play)")
    taps = [(lo, hi) for (down,), lo, hi in buttons_only if down and hi == lo]
    print(f"one-frame button states: {len(taps)}")
    print("\nreadable trace (what a recipe would say):")
    shown = [run for run in readable if run[0][0] or run[0][1] != "neutral"]
    if len(shown) > 70:
        print(f"  ...{len(shown) - 70} earlier runs elided...")
        shown = shown[-70:]
    for key, lo, hi in shown:
        down, stick = key
        print(f"  f{lo - captured[0][0]:>5}..{hi - captured[0][0]:<5} "
              f"{hi - lo + 1:>4}f  {button_names(down):<14} stick {stick}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

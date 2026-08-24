"""Live gate (round 32 item 29): does RAM see WHICH frame the screen shows?

His challenge, verbatim: "I'm not convinced that the RAM doesn't contain
the full data picture / contain the inputs perfectly aligned to the
frames. I feel like we haven't searched hard enough. Afterall, Usamune
somehow displays it -- it must be accessible to us."

The counter-hypothesis this probe tests: the display side IS in RAM.
STROOP's full MappingUS.map (fetched 2026-08-23) names, right beside our
verified gGlobalTimer (0x8032D5D4):

    sNumVblanks       0x8032D580   the VI vertical-blank counter (60 Hz --
                                   the DISPLAY's own clock)
    sCurrFBNum        0x8032D5D8   which framebuffer the game is RENDERING
                                   into (the swap chain's write head)
    frameBufferIndex  0x8032D5DC   the swap chain's other index

If (a) sNumVblanks ticks ~2 per game frame and (b) the phase between a
gGlobalTimer advance and its frame's buffer SWAP wobbles by +-1 vblank --
the same wobble the footage shows (maps v1/v2, scored 8/44 and 24/53) --
then the logic->present latency is VISIBLE to RAM after all, the "invisible
to RAM" claim in replay/pixelmap.py is REFUTED, and the frame clock should
tag captures with the present-side state instead of the logic frame.

Read-only, safe beside a live session. Just play; ~20 seconds of samples.

ANSWERED 2026-08-23, live during his session (4,554 samples over 20 s):
  - sNumVblanks is LIVE at 60.01/s, locked 2.000 vblanks per game frame.
  - The swap chain cycles three buffers (sCurrFBNum/frameBufferIndex read
    0/1/2 as u16s in the word's high half).
  - The logic->display phase NEVER moved: 0 phase flips across 399
    consecutive game frames. Inside the emulated N64, logic and VI are in
    rigid lockstep -- so a capture-time read of the swap chain carries
    exactly the same information as a capture-time read of gGlobalTimer,
    and the footage's +-1 wobble arises PAST the emulated VI: in PJ64's
    GPU present, DWM composition, or our capture -- host territory RDRAM
    cannot describe. The "invisible to RAM" claim survives, now measured
    instead of asserted.
  - THE STONE STILL UNTURNED (a finding, not a verdict): PJ64's own video
    plugin keeps HOST-side present state (process memory OUTSIDE the
    RDRAM region). A probe_inputs-style scan for a counter ticking ~60/s
    that pauses with the game could find the true host present counter;
    sampling THAT at capture time would see the wobble from the RAM side.
    Nobody has run that scan.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sm64_events.memory.pj64 import Pj64Memory  # noqa: E402

GLOBAL_TIMER = 0x8032D5D4
S_NUM_VBLANKS = 0x8032D580
S_CURR_FB_NUM = 0x8032D5D8
FRAME_BUFFER_INDEX = 0x8032D5DC

SAMPLE_HZ = 250
SECONDS = 20.0


def main() -> int:
    memory = Pj64Memory()
    if not memory.attach():
        raise SystemExit("PJ64 not running or no ROM loaded")
    print(f"attached; sampling {SAMPLE_HZ} Hz for {SECONDS:.0f}s -- just play")
    samples = []
    deadline = time.perf_counter() + SECONDS
    while time.perf_counter() < deadline:
        try:
            timer = memory.read_u32(GLOBAL_TIMER)
            vblanks = memory.read_u32(S_NUM_VBLANKS)
            curr_fb = memory.read_u32(S_CURR_FB_NUM)
            fb_index = memory.read_u32(FRAME_BUFFER_INDEX)
        except Exception:
            continue
        samples.append((time.perf_counter(), timer, vblanks, curr_fb,
                        fb_index))
        time.sleep(1 / SAMPLE_HZ)
    memory.detach()
    if len(samples) < 100:
        raise SystemExit("too few samples")

    timer_edges = [(t, timer) for (t, timer, *_), (t2, timer2, *_)
                   in zip(samples, samples[1:]) if timer2 == timer + 1
                   for t, timer in [(t2, timer2)]]
    vblank_edges = [(t2, v2) for (t, _tm, v, *_), (t2, _tm2, v2, *_)
                    in zip(samples, samples[1:]) if v2 > v]
    span = samples[-1][0] - samples[0][0]
    timer_rate = (samples[-1][1] - samples[0][1]) / span
    vblank_rate = (samples[-1][2] - samples[0][2]) / span
    print(f"{len(samples)} samples over {span:.1f}s")
    print(f"gGlobalTimer rate: {timer_rate:.2f}/s   "
          f"sNumVblanks rate: {vblank_rate:.2f}/s   "
          f"ratio {vblank_rate / timer_rate if timer_rate else 0:.3f} "
          "(2.000 = the display clock is live and locked)")

    # Does the swap chain MOVE, and does it carry information the timer
    # does not? Count distinct values and how they advance per game frame.
    fb_values = sorted({s[3] for s in samples})
    index_values = sorted({s[4] for s in samples})
    print(f"sCurrFBNum values seen: {fb_values}")
    print(f"frameBufferIndex values seen: {index_values}")

    # Phase: for each timer edge, the vblank count AT that instant, mod 2 --
    # a stable phase means logic locks to the display; a wandering one is
    # the wobble the footage shows.
    phases = []
    vb_at = 0
    vb_iter = iter(vblank_edges)
    current_vb = None
    for t_edge, _timer in timer_edges[:400]:
        vb_here = None
        for (tv, v) in vblank_edges:
            if tv <= t_edge:
                vb_here = v
            else:
                break
        if vb_here is not None:
            phases.append(vb_here % 2)
    if phases:
        ones = sum(phases)
        print(f"timer-edge vblank phase: {ones}/{len(phases)} on odd "
              "vblanks -- near 0% or 100% = locked phase; near 50% with "
              "RUNS of each = the +-1 wobble, visible to RAM")
        flips = sum(1 for a, b in zip(phases, phases[1:]) if a != b)
        print(f"phase flips between consecutive frames: {flips}/"
              f"{len(phases) - 1}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

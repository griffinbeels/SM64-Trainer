"""Live gate (round 32 item 69): does RDRAM hold the PICTURE, not just the clock?

His challenge, 2026-08-31: *"I feel like it's so easy to just hook into the
memory, and feel like we're missing something."*

He is pointing at the one link in the chain that is an inference rather than a
read. `tools/probe_present.py` already settled the clock half on 2026-08-23:
inside the emulated N64, logic and VI run in RIGID LOCKSTEP (0 phase flips in
399 game frames), so reading the swap chain at capture time tells us nothing a
gGlobalTimer read does not. Every frame of wobble we see therefore arises PAST
the emulated VI -- in PJ64's GPU present, DWM composition, or our own capture.

That leaves exactly two ways to know which frame a picture shows, and this
probe tests the better one: STOP PHOTOGRAPHING THE WINDOW. SM64 renders into
three framebuffers at fixed RDRAM addresses, and we already read RDRAM every
frame. If the image is really there, then a frame read out of RDRAM is stamped
BY CONSTRUCTION -- same read, same instant, same counter -- and the whole frame
map, the display-lag constant, the digit anchor and the picture ledger all stop
being necessary.

    gFrameBuffer0  0x8038F800     320x240, 16-bit RGBA5551, 153,600 bytes
    gFrameBuffer1  0x803B5000
    gFrameBuffer2  0x803DA800

THE THING THAT COULD MAKE THIS FAIL, stated up front so a null result is not
read as a bug: PJ64 1.6 runs an HLE graphics plugin that renders on the PC's
GPU. Unless framebuffer emulation is enabled, the plugin never writes the
rendered image back into RDRAM, and these three regions hold stale garbage,
zeros, or only what the game's own CPU code put there. That is a real
possibility and this probe is built to tell the two apart rather than to
confirm a hope: it reports the byte entropy, how much each buffer CHANGES
between game frames, and it writes the decoded images out so they can be
LOOKED AT.

    uv run python tools/probe_framebuffer.py            # ~8 s of samples
    uv run python tools/probe_framebuffer.py --seconds 20

Read-only, safe beside a live session -- it takes no lock and starts no server.
Play normally while it runs; a still screen is the one thing that makes the
"does it change" question unanswerable.
"""
import argparse
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sm64_events.memory.layout import layout_for
from sm64_events.memory.pj64 import Pj64Memory

# The one door for RAM addresses (tests/test_single_source.py): every address
# here is an offset from THIS ROM's verified gGlobalTimer. The framebuffers sit
# in a different data block from the 0x8032 globals, so their offsets are large
# -- but the blocks move together across ROM versions the same way, and a US
# run is what this probe is for.
GLOBAL_TIMER = layout_for("us").global_timer
FRAME_BUFFERS = (
    ("gFrameBuffer0", GLOBAL_TIMER + 0x6222C),   # US 0x8038F800
    ("gFrameBuffer1", GLOBAL_TIMER + 0x87A2C),   # US 0x803B5000
    ("gFrameBuffer2", GLOBAL_TIMER + 0xAD22C),   # US 0x803DA800
)
WIDTH, HEIGHT = 320, 240
FRAME_BYTES = WIDTH * HEIGHT * 2
# A cheap fingerprint: scattered words rather than the whole 150 KB, so the
# sampler stays fast enough to see a per-frame change.
PROBE_OFFSETS = tuple(range(0, FRAME_BYTES - 4, FRAME_BYTES // 64))


def entropy_note(block: bytes) -> str:
    """Does this look like an IMAGE, or like nothing?

    Stated as a description rather than a verdict: a real 5551 framebuffer has
    many distinct byte values and few long runs; an untouched region is mostly
    one value. Both readings are printed so the caller judges.
    """
    if not block:
        return "empty"
    counts = Counter(block)
    top_value, top_count = counts.most_common(1)[0]
    return (f"{len(counts):3d} distinct byte values, "
            f"most common 0x{top_value:02x} at {100 * top_count / len(block):5.1f}%")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=8.0)
    parser.add_argument("--out", default=None,
                        help="directory for the decoded PNGs (default: cwd)")
    args = parser.parse_args()

    memory = Pj64Memory()
    if not memory.attach():
        raise SystemExit("PJ64 not running or no ROM loaded")
    print(f"attached. gGlobalTimer at {GLOBAL_TIMER:#010x}")
    for name, address in FRAME_BUFFERS:
        print(f"  {name} at {address:#010x}")

    # --- 1. is there anything there at all? --------------------------------
    print("\n--- what each buffer HOLDS right now")
    heads = {}
    for name, address in FRAME_BUFFERS:
        try:
            block = memory.read_block(address, 4096)
        except Exception as error:                        # noqa: BLE001
            print(f"  {name}: UNREADABLE ({error})")
            heads[name] = None
            continue
        heads[name] = block
        print(f"  {name}: {entropy_note(block)}")

    # --- 2. does it CHANGE as the game runs? -------------------------------
    print(f"\n--- sampling for {args.seconds:.0f}s -- PLAY, or nothing moves")
    seen = {name: set() for name, _ in FRAME_BUFFERS}
    changes = {name: 0 for name, _ in FRAME_BUFFERS}
    last = {name: None for name, _ in FRAME_BUFFERS}
    timers = []
    samples = 0
    deadline = time.perf_counter() + args.seconds
    while time.perf_counter() < deadline:
        try:
            timers.append(memory.read_u32(GLOBAL_TIMER))
            for name, address in FRAME_BUFFERS:
                finger = bytes(
                    b for offset in PROBE_OFFSETS
                    for b in memory.read_block(address + offset, 4))
                seen[name].add(finger)
                if last[name] is not None and finger != last[name]:
                    changes[name] += 1
                last[name] = finger
            samples += 1
        except Exception as error:                        # noqa: BLE001
            print(f"  read failed mid-sample: {error}")
            break
        time.sleep(0.002)

    game_frames = len(set(timers))
    print(f"\n{samples} samples over {args.seconds:.0f}s; "
          f"{game_frames} distinct game frames "
          f"({game_frames / max(args.seconds, 1e-9):.1f}/s)")
    for name, _ in FRAME_BUFFERS:
        print(f"  {name}: {changes[name]:5d} changes, "
              f"{len(seen[name]):5d} distinct fingerprints")

    # --- 3. LOOK AT IT. The only answer that settles the question. ---------
    out = Path(args.out or ".")
    out.mkdir(parents=True, exist_ok=True)
    print("\n--- decoding each buffer to PNG (5551, big-endian)")
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        print("  numpy/Pillow unavailable -- skipped; the counts above still stand")
        return 0
    for name, address in FRAME_BUFFERS:
        try:
            raw = memory.read_block(address, FRAME_BYTES)
        except Exception as error:                        # noqa: BLE001
            print(f"  {name}: unreadable ({error})")
            continue
        pixels = np.frombuffer(raw, dtype=">u2").reshape(HEIGHT, WIDTH)
        rgb = np.stack([((pixels >> 11) & 0x1F) << 3,
                        ((pixels >> 6) & 0x1F) << 3,
                        ((pixels >> 1) & 0x1F) << 3], axis=-1).astype("uint8")
        path = out / f"{name}.png"
        Image.fromarray(rgb).save(path)
        print(f"  wrote {path}")
    print("\nLOOK AT THOSE THREE IMAGES. A recognisable game screen means the "
          "picture is in RDRAM and the window capture is not needed to identify "
          "a frame. Noise or a flat colour means the plugin never writes back, "
          "and the host-side present counter (probe_present.py's untested "
          "stone) is the remaining route.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

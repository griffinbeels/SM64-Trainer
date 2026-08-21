# src/sm64_events/inputs/overlay.py
"""The transparent input overlay: one file per LAYER, on the clip's own clock.

What this hands an editor, and why each part is the way it is:

**One file per layer, not one file you crop.** He asked for non-overlapping
elements so he could crop out just the stick or just the buttons. Cropping is
the wrong lever: each layer renders on the SAME canvas at the same frame rate
with the same frame 0, so the layers STACK in register with no positioning at
all, and he deletes the one he does not want instead of masking it. That
satisfies "no elements overlapping" structurally rather than by careful layout.

**ProRes 4444, or QuickTime Animation.** His original ask was DNxHR SQ, which
cannot carry transparency at all -- it is 4:2:2 with no alpha channel, in any
wrapper. ProRes 4444 is the default because it is what an editor expects and
scrubs predictably; QuickTime Animation is offered beside it because this
graphic is flat colour on empty space, which RLE compresses losslessly and
often smaller.

**A frame list, not a duration list.** ffmpeg's concat demuxer accepts
`duration` per entry, and it is off by fractions in ways that accumulate. One
line per OUTPUT frame -- all pointing at the handful of distinct pictures -- is
exact by construction, and a 30-second clip is 1,800 lines of text.

**The alignment is measured, not assumed.** The clip is captured off the
emulator window on a wall clock while inputs are sampled from RAM on the frame
counter, so "frame 0 is frame 0" is a claim. `score_overlay_alignment` is the
gate: record a clip with Usamune's own input display turned on, and score our
overlay against Usamune's pixels in that same footage.
"""
from dataclasses import dataclass

GAME_FPS = 30
DEFAULT_VIDEO_FPS = 60

# Which parts of the controller each file draws. The names are the layer's own
# file suffix, so a folder of exports reads without a legend.
LAYERS = ("stick", "buttons", "facing", "combined")

CODECS = {
    # (encoder args) -- both carry an alpha channel; neither is DNxHR, which
    # cannot.
    "prores4444": ("-c:v", "prores_ks", "-profile:v", "4444",
                   "-pix_fmt", "yuva444p10le", "-alpha_bits", "16",
                   "-vendor", "apl0"),
    "qtrle": ("-c:v", "qtrle", "-pix_fmt", "argb"),
}
DEFAULT_CODEC = "prores4444"


@dataclass(frozen=True)
class OverlayPlan:
    """Everything the renderer and the encoder need, decided up front."""

    layer: str
    codec: str
    width: int
    height: int
    video_fps: int
    game_frames: int
    states: tuple[tuple[int, int, int, int], ...]  # buttons, x, y, yaw
    per_frame: tuple[int, ...]                 # game frame -> index in states

    @property
    def video_frames(self) -> int:
        return self.game_frames * self.video_fps // GAME_FPS


def plan_overlay(runs, *, layer: str = "combined", codec: str = DEFAULT_CODEC,
                 width: int = 480, height: int = 240,
                 video_fps: int = DEFAULT_VIDEO_FPS) -> OverlayPlan:
    """Turn a track's runs into the DISTINCT pictures and a per-frame index.

    Distinct pictures rather than one per frame because the pad holds still
    most of the time: 45 s of real play is 1,348 frames and 289 runs, and
    fewer than that once two runs differing only in a field this layer does
    not draw collapse together.
    """
    if layer not in LAYERS:
        raise ValueError(f"unknown overlay layer {layer!r}; have {LAYERS}")
    if codec not in CODECS:
        raise ValueError(f"unknown codec {codec!r}; have {tuple(CODECS)}")
    if video_fps % GAME_FPS:
        raise ValueError(
            f"{video_fps} fps does not divide by the game's {GAME_FPS}, so a "
            "game frame could not be held for a whole number of video frames "
            "-- which is the one thing this export exists to get right")
    span = (runs[-1][0] + runs[-1][1]) if runs else 0
    index: dict[tuple[int, int, int, int], int] = {}
    states: list[tuple[int, int, int, int]] = []
    # A hole in capture draws NOTHING -- not the neighbouring frame's pad. The
    # blank is the honest picture of "we do not know", and it is also what the
    # editor sees as a gap rather than a stuck hand.
    blank = (0, 0, 0, 0)
    per_frame = [0] * span
    index[blank] = 0
    states.append(blank)
    for run in runs:
        start, length, buttons, stick_x, stick_y = run[:5]
        # A run may predate Mario's own capture (a v1 chunk), and 0 is what
        # such a track honestly says about a facing it never recorded.
        yaw = run[5] if len(run) > 5 else 0
        key = _key_for(layer, buttons, stick_x, stick_y, yaw)
        at = index.get(key)
        if at is None:
            at = len(states)
            index[key] = at
            states.append(key)
        for frame in range(start, min(start + length, span)):
            per_frame[frame] = at
    return OverlayPlan(layer=layer, codec=codec, width=width, height=height,
                       video_fps=video_fps, game_frames=span,
                       states=tuple(states), per_frame=tuple(per_frame))


def _key_for(layer: str, buttons: int, stick_x: int, stick_y: int, yaw: int):
    """Two frames that this layer DRAWS identically are one picture.

    Which is also why `facing` is its own layer rather than a corner of the
    combined one: Mario's yaw changes on almost every frame he is moving, so
    folding it into the others would multiply their distinct pictures by the
    length of the run and turn a few dozen screenshots into a few thousand.
    """
    if layer == "stick":
        return (0, stick_x, stick_y, 0)
    if layer == "buttons":
        return (buttons, 0, 0, 0)
    if layer == "facing":
        return (0, 0, 0, yaw)
    return (buttons, stick_x, stick_y, yaw)


def concat_script(plan: OverlayPlan, name_of) -> str:
    """One line per OUTPUT frame, pointing at the picture it shows.

    `name_of(index)` gives the file name for a state. Exact by construction:
    no durations, no accumulating fractions, and the file count IS the frame
    count -- so a mis-encode is a number you can check rather than a drift you
    have to look for.
    """
    hold = plan.video_fps // GAME_FPS
    lines = ["ffconcat version 1.0"]
    for state in plan.per_frame:
        for _ in range(hold):
            lines.append(f"file '{name_of(state)}'")
            lines.append(f"duration {1 / plan.video_fps:.9f}")
    if plan.per_frame:                       # the demuxer drops the last entry
        lines.append(f"file '{name_of(plan.per_frame[-1])}'")
    return "\n".join(lines) + "\n"


def encode_argv(ffmpeg: str, script_path: str, out_path: str,
                plan: OverlayPlan) -> list[str]:
    """The ffmpeg call. Constant frame rate, because an editor lining this up
    against footage needs frame N to be at N/fps and nowhere else.

    `-frames:v` is the one that makes the count a GUARANTEE rather than an
    expectation. Measured 2026-08-21: the concat demuxer's per-entry durations
    cannot express 1/60 exactly, and a real export came out **3 frames long**
    (6,129 for 6,126) -- small enough to look like rounding and quite large
    enough to slide an overlay off its footage by the end of a long clip.
    Asking for the exact number leaves no room for that.
    """
    return [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", script_path,
            "-fps_mode", "cfr", "-r", str(plan.video_fps),
            "-frames:v", str(plan.video_frames),
            *CODECS[plan.codec],
            "-movflags", "+write_colr", out_path]


def output_name(stem: str, plan: OverlayPlan) -> str:
    return f"{stem}.inputs-{plan.layer}.mov"

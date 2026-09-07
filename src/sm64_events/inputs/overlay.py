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

**Use the clip's picture clock.** Mapped exports preserve source-picture
states and their actual intervals, including the final hold. Plain tracks
still use the game's uniform clock. A raw counter alone cannot identify a
picture after a save-state reset.

**The alignment is measured, not assumed.** The clip is captured off the
emulator window on a wall clock while inputs are sampled from RAM on the frame
counter, so "frame 0 is frame 0" is a claim. `score_overlay_alignment` is the
gate: record a clip with Usamune's own input display turned on, and score our
overlay against Usamune's pixels in that same footage.
"""
from dataclasses import dataclass, replace
import math

from sm64_events.core.timefmt import GAME_FPS
from sm64_events.replay.association import valid_picture_times

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
OverlayState = tuple[int, int, int, int | None]


@dataclass(frozen=True)
class OverlayPlan:
    """Everything the renderer and the encoder need, decided up front."""

    layer: str
    codec: str
    width: int
    height: int
    video_fps: int
    game_frames: int
    states: tuple[OverlayState | None, ...]  # buttons, x, y, yaw; None = unknown
    per_frame: tuple[int, ...]                 # game frame or mapped picture -> state
    frame_times: tuple[float, ...] | None = None
    duration_s: float | None = None

    @property
    def video_frames(self) -> int:
        if self.frame_times is not None:
            return len(self.per_frame)
        return self.game_frames * self.video_fps // GAME_FPS


def plan_overlay(runs, *, layer: str = "combined", codec: str = DEFAULT_CODEC,
                 width: int = 480, height: int = 240,
                 video_fps: int = DEFAULT_VIDEO_FPS) -> OverlayPlan:
    """Turn a track's runs (the timeline payload's `runs`, one dict each)
    into the DISTINCT pictures and a per-frame index.

    Distinct pictures rather than one per frame because the pad holds still
    most of the time: 45 s of real play is 1,348 frames and 289 runs, and
    fewer than that once two runs differing only in a field this layer does
    not draw collapse together.
    """
    if layer not in LAYERS:
        raise ValueError(f"unknown overlay layer {layer!r}; have {LAYERS}")
    if codec not in CODECS:
        raise ValueError(f"unknown codec {codec!r}; have {tuple(CODECS)}")
    if video_fps <= 0 or video_fps % GAME_FPS:
        raise ValueError(
            f"{video_fps} fps does not divide by the game's {GAME_FPS}, so a "
            "game frame could not be held for a whole number of video frames "
            "-- which is the one thing this export exists to get right")
    span = (runs[-1]["start"] + runs[-1]["length"]) if runs else 0
    index: dict[OverlayState | None, int] = {}
    states: list[OverlayState | None] = []
    # A hole in capture draws NOTHING -- not the neighbouring frame's pad. The
    # blank is the honest picture of "we do not know", and it is also what the
    # editor sees as a gap rather than a stuck hand.
    blank = None
    per_frame = [0] * span
    index[blank] = 0
    states.append(blank)
    for run in runs:
        key = _key_for(layer, run["buttons"], run["stick_x"], run["stick_y"],
                       run["yaw"])
        at = index.get(key)
        if at is None:
            at = len(states)
            index[key] = at
            states.append(key)
        for frame in range(run["start"],
                           min(run["start"] + run["length"], span)):
            per_frame[frame] = at
    return OverlayPlan(layer=layer, codec=codec, width=width, height=height,
                       video_fps=video_fps, game_frames=span,
                       states=tuple(states), per_frame=tuple(per_frame))


def plan_mapped_overlay(view, **settings) -> OverlayPlan:
    """Use the validated replay view's slot-aligned state, never a raw lookup.

    Missing source state fails closed; a null slot remains transparent. The
    first picture may start after zero, so that leading interval is blank.
    The output clock uses microseconds, the precision served by replay views.
    """
    times, duration = view.get("frame_times"), view.get("duration_s")
    states, mapping = view.get("picture_states"), view.get("frame_map")
    if (not valid_picture_times(times) or times[0] < 0
            or type(duration) not in (int, float) or not math.isfinite(duration)
            or duration <= times[-1]):
        raise ValueError("mapped overlay needs the clip's picture times and final duration")
    if (not isinstance(states, list) or not isinstance(mapping, list)
            or len(states) != len(times) or len(mapping) != len(times)):
        raise ValueError("mapped overlay needs captured state for each picture; raw counters are insufficient")
    ticks = [round(t * 1_000_000) for t in times]
    end = round(duration * 1_000_000)
    if any(a >= b for a, b in zip(ticks, ticks[1:], strict=False)) or end <= ticks[-1]:
        raise ValueError("picture intervals are below the overlay clock's microsecond precision")
    settings.pop("video_fps", None)  # --fps only controls a plain game-clock export
    base = plan_overlay([], **settings)
    pictures, indices = [None], {None: 0}
    per_frame = []
    for raw, state in zip(mapping, states, strict=True):
        key = None
        if raw is not None and state is not None:
            if not isinstance(state, dict) or any(type(state.get(k)) is not int
                    for k in ("buttons", "stick_x", "stick_y")):
                raise ValueError("invalid captured picture state")
            yaw = state.get("yaw")
            if yaw is not None and type(yaw) is not int:
                raise ValueError("invalid captured facing state")
            key = _key_for(base.layer, state["buttons"], state["stick_x"],
                           state["stick_y"], yaw)
        if key not in indices:
            indices[key] = len(pictures)
            pictures.append(key)
        per_frame.append(indices[key])
    if ticks[0] > 0:
        ticks.insert(0, 0)
        per_frame.insert(0, 0)
    return replace(base, states=tuple(pictures), per_frame=tuple(per_frame),
                   frame_times=tuple(t / 1_000_000 for t in ticks),
                   duration_s=end / 1_000_000)


def _key_for(layer: str, buttons: int, stick_x: int, stick_y: int, yaw: int | None):
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
        return (0, 0, 0, yaw) if yaw is not None else None
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


def mapped_concat_script(plan: OverlayPlan, name_of) -> str:
    """Preserve each mapped picture interval on a microsecond input clock."""
    if plan.frame_times is None or plan.duration_s is None:
        raise ValueError("use a mapped overlay plan with captured picture times")
    lines = ["ffconcat version 1.0"]
    ends = (*plan.frame_times[1:], plan.duration_s)
    for state, start, end in zip(plan.per_frame, plan.frame_times, ends, strict=True):
        lines.append(f"file '{name_of(state)}'")
        lines.append("option framerate 1000000")
        lines.append(f"duration {end - start:.6f}")
    return "\n".join(lines) + "\n"


def encode_argv(ffmpeg: str, script_path: str, out_path: str,
                plan: OverlayPlan, frames: int | None = None) -> list[str]:
    """Encode mapped pictures at their timestamps, plain tracks at fixed fps.

    `-frames:v` is the one that makes the count a GUARANTEE rather than an
    expectation. Measured 2026-08-21: the concat demuxer's per-entry durations
    cannot express 1/60 exactly, and a real export came out **3 frames long**
    (6,129 for 6,126) -- small enough to look like rounding and quite large
    enough to slide an overlay off its footage by the end of a long clip.
    Asking for the exact number leaves no room for that.
    """
    timing = ["-fps_mode", "cfr", "-r", str(plan.video_fps)]
    if plan.frame_times is not None:
        # MOV derives interior durations from consecutive PTS. The final
        # packet has no successor: explicitly retain its recorded hold, not
        # image2's one-microsecond nominal packet duration.
        last = plan.duration_s - plan.frame_times[-1]
        timing = ["-fps_mode", "passthrough", "-enc_time_base", "1:1000000",
                  "-video_track_timescale", "1000000", "-bsf:v",
                  f"setts=duration='if(eq(N,{plan.video_frames - 1}),{last:.6f}/TB,DURATION)'"]
    return [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", script_path,
            *timing, "-frames:v", str(frames if frames is not None else plan.video_frames),
            *CODECS[plan.codec],
            "-movflags", "+write_colr", out_path]


def output_name(stem: str, plan: OverlayPlan) -> str:
    return f"{stem}.inputs-{plan.layer}.mov"

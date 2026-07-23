"""All replay tunables in one place (spec: Config section).

The two STORAGE limits (retention_s, max_buffer_bytes) are additionally
user-adjustable from the UI (recording-dot panel): they persist in a tiny
JSON overlay file (settings_path) so changes survive restarts without a db
migration. Everything else stays code-level on purpose."""
import json
import logging
from dataclasses import dataclass, field, replace
from pathlib import Path

from sm64_events.core.paths import (replay_scratch_dir, replay_settings_path,
                                     replays_root)


@dataclass(frozen=True)
class ReplayConfig:
    enabled: bool = True
    retention_s: float | None = None      # None = keep the whole session
    pre_pad_s: float = 3.0                # before the attempt anchor
    post_pad_s: float = 2.0               # after the closing event
    fps: int = 60                         # PJ64 presents per N64 VI (~59.94 Hz,
                                          # user-measured 59.90-60.05); sampling
                                          # at 30 beats against that cadence and
                                          # judders. SM64 LOGIC is 30 fps, but
                                          # capture must follow presents.
    segment_s: float = 2.0                # video segment / audio chunk length
    max_buffer_bytes: int = 20 * 1024**3  # hard disk guard regardless of retention
    save_root: Path = field(default_factory=replays_root)
    scratch_dir: Path = field(default_factory=replay_scratch_dir)
    window_title: str = "Project64"       # substring match on the window title
    audio_rate: int = 48000               # proc-tap delivers 48 kHz stereo
    attach_poll_s: float = 2.0            # window-hunt interval
    extract_wait_s: float = 5.0           # bounded wait for the tail segment
    settings_path: Path = field(default_factory=replay_settings_path)


# -- video encode QUALITY (ONE authoritative place: ring sink + clip extract) -
#
# Both encoders must pin OUTPUT QUALITY, never a bare bitrate — the ring sink
# is the ceiling on every clip, and the clip extractor must not throw that
# ceiling away on the way out.
#
# Evidence (live measurement 2026-07-23, 1600x1224@60 capture; scratch ladder
# in the bug's commit message): the extractor shipped with NO rate control at
# all, so every saved clip fell back to ffmpeg's ~2 Mbps default REGARDLESS of
# how good its source was — a 12.5 Mbps ring segment and a 26 Mbps one both
# re-encoded to 2.1 Mbps, PSNR 38.1 dB against their own source. That is the
# user-visible "the recording is blurry" bug; it read as low resolution because
# H.264 starved of bits smears exactly the hard edges a 2x-upscaled N64 image
# is made of. At cq 20 the same cut costs ~20 Mbps and measures 52.7 dB =
# visually transparent.
#
# Why constant-quality (`-cq`) instead of a bigger `-b:v`: a bitrate target
# over- and under-shoots with scene difficulty (a paused menu was measured at
# 2.5 Mbps of a 12 Mbps budget while gameplay pinned the ceiling), and it would
# have to be re-tuned by hand if the capture resolution ever changes. cq holds
# the picture quality and lets the bitrate follow the content.
#
# Measured knobs, on 1600x1224@60 game content (SSIM/PSNR vs a lossless source):
#   profile high vs main   -8 % bitrate at equal/better SSIM  -> ON
#   -tune hq vs ull        equal quality at a fixed budget, and hq is what
#                          makes cq mode behave; 8x realtime on this GPU -> ON
#   -spatial-aq 1          +21 % bitrate for +0.0003 SSIM               -> OFF
VIDEO_CQ = 20              # NVENC constant-quality target (lower = better)
VIDEO_CRF = 18             # libx264 equivalent (machines without NVENC)
RING_MAXRATE = "30M"       # ring: bounded so the disk cap stays predictable
CLIP_MAXRATE = "60M"       # saved clip: one file on disk, quality wins

# Encoder speed per stage. The ring runs REALTIME (must beat 1/fps per frame,
# measured ~8x headroom at p4); clip extraction is offline, so it can afford a
# slower preset for the same quality target.
_NVENC_PRESET = {"realtime": "p4", "offline": "p6"}
_X264_PRESET = {"realtime": "ultrafast", "offline": "veryfast"}


def video_quality_args(codec: str, stage: str, maxrate: str) -> list[str]:
    """ffmpeg args pinning OUTPUT QUALITY for `codec` at a speed `stage`
    ('realtime' = ring sink, 'offline' = clip extract). Callers add their own
    structural flags (GOP, IDR, CFR) — this owns quality only, so the two
    encoders can never drift apart again. Pure — unit-tested."""
    if codec == "h264_nvenc":
        return ["-preset", _NVENC_PRESET[stage], "-tune", "hq",
                "-profile:v", "high",
                # -b:v 0 is REQUIRED: with a bitrate set, NVENC treats cq as a
                # cap-with-target and the average bitrate wins instead.
                "-rc", "vbr", "-cq", str(VIDEO_CQ), "-b:v", "0",
                "-maxrate", maxrate, "-bufsize", maxrate]
    if codec == "libx264":
        return ["-preset", _X264_PRESET[stage], "-crf", str(VIDEO_CRF)]
    return []


# -- user-adjustable storage limits (UI: recording-dot panel) -----------------

SETTINGS_LIMITS = {
    "retention_s": (60.0, 86400.0),          # 1 min .. 24 h (None = whole session)
    "max_buffer_bytes": (1024**3, 1024**4),  # 1 GiB .. 1 TiB
    "pre_pad_s": (0.0, 10.0),                # clip lead-in before the anchor
    "post_pad_s": (0.0, 10.0),               # clip tail after the closing event
}


def validate_settings(retention_s: float | None, max_buffer_bytes: int,
                      pre_pad_s: float | None = None,
                      post_pad_s: float | None = None) -> None:
    """ValueError on out-of-range values (the API maps it to 409).
    Pads are validated only when provided (None = caller keeps current)."""
    lo, hi = SETTINGS_LIMITS["retention_s"]
    if retention_s is not None and not (lo <= float(retention_s) <= hi):
        raise ValueError(
            f"retention_s must be null or {lo:.0f}..{hi:.0f} seconds")
    lo, hi = SETTINGS_LIMITS["max_buffer_bytes"]
    if not (lo <= int(max_buffer_bytes) <= hi):
        raise ValueError("max_buffer_bytes must be 1 GiB..1 TiB")
    for name, val in (("pre_pad_s", pre_pad_s), ("post_pad_s", post_pad_s)):
        if val is None:
            continue
        lo, hi = SETTINGS_LIMITS[name]
        if not (lo <= float(val) <= hi):
            raise ValueError(f"{name} must be {lo:.0f}..{hi:.0f} seconds")


def save_settings(path: Path, retention_s: float | None,
                  max_buffer_bytes: int, pre_pad_s: float,
                  post_pad_s: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"retention_s": retention_s, "max_buffer_bytes": int(max_buffer_bytes),
         "pre_pad_s": float(pre_pad_s), "post_pad_s": float(post_pad_s)},
        indent=2))


def apply_settings_file(cfg: ReplayConfig) -> ReplayConfig:
    """Overlay the persisted limits onto cfg. Absent, corrupt, or
    out-of-range files are ignored with a log line — defaults win, the
    server must always start."""
    try:
        raw = json.loads(cfg.settings_path.read_text())
    except FileNotFoundError:
        return cfg
    except Exception:
        logging.getLogger("sm64.replay").warning(
            "ignoring unreadable %s", cfg.settings_path)
        return cfg
    retention = raw.get("retention_s", cfg.retention_s)
    cap = raw.get("max_buffer_bytes", cfg.max_buffer_bytes)
    pre = raw.get("pre_pad_s", cfg.pre_pad_s)
    post = raw.get("post_pad_s", cfg.post_pad_s)
    try:
        validate_settings(retention, cap, pre, post)
    except ValueError as e:
        logging.getLogger("sm64.replay").warning(
            "ignoring invalid %s: %s", cfg.settings_path, e)
        return cfg
    return replace(cfg, retention_s=retention, max_buffer_bytes=int(cap),
                   pre_pad_s=float(pre), post_pad_s=float(post))

"""All replay tunables in one place (spec: Config section).

The attempt/time retention limits and max_buffer_bytes are additionally
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
    retention_s: float | None = None      # None = no additional age limit
    retention_attempts: int | None = 10    # completed attempts; saves live separately
    pre_pad_s: float = 3.0                # before the attempt anchor
    post_pad_s: float = 2.0               # after the closing event
    fps: int = 60                         # PJ64 presents per N64 VI (~59.94 Hz,
                                          # user-measured 59.90-60.05); sampling
                                          # at 30 beats against that cadence and
                                          # judders. SM64 LOGIC is 30 fps, but
                                          # capture must follow presents.
    segment_s: float = 2.0                # video segment / audio chunk length
    # THE PICTURE FEED (round 32 item 38, built 2026-09-02): the ffmpeg
    # sink encodes ONE video frame per DISTINCT captured picture, stamped
    # by the wall clock at its write (VFR), instead of re-sending the
    # latest grab at `fps` onto a CFR grid. Video frame k of a clip is
    # then the k-th picture ledger row the cut covers, so the frame map
    # is READ off the feed log rather than inferred from picture runs on
    # a 60 Hz grid (17% of pictures landed one or three slots there).
    # `fps` stays the grab cadence and the CFR fallback's rate. False
    # restores the CFR feed; the in-process fallback writer ignores it.
    picture_feed: bool = True
    max_buffer_bytes: int = 2 * 1024**3   # modest new default; persisted user limits win
    save_root: Path = field(default_factory=replays_root)
    scratch_dir: Path = field(default_factory=replay_scratch_dir)
    window_title: str = "Project64"       # substring match on the window title
    audio_rate: int = 48000               # proc-tap delivers 48 kHz stereo
    attach_poll_s: float = 2.0            # window-hunt interval
    extract_wait_s: float = 5.0           # bounded wait for the tail segment
    settings_path: Path = field(default_factory=replay_settings_path)
    # Shrink a saved replay in the background once Save/PB has published it
    # (replay/compress.py). Only what is saved from now on: a replay already
    # on disk is never rewritten without being asked.
    compress_saved: bool = True


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
# Vendor scales are independent; these are explicit initial quality targets,
# not a claim that QP/ICQ 18 is visually equivalent to NVENC CQ 20.
AMF_QP = 18
QSV_ICQ = 18
RING_MAXRATE = "30M"       # ring: bounded so the disk cap stays predictable
CLIP_MAXRATE = "60M"       # saved clip: one file on disk, quality wins

# THE ARCHIVE STAGE (replay/compress.py): a saved replay re-encoded in the
# background for size. Measured 2026-09-18 on two of his saved replays against
# the saved clip itself (VMAF mean / worst 1 % of pictures, size vs saved):
#   av1_nvenc p7 cq36       99.1 / 85-94   26 %   2 s per 18 s clip
#   h264_nvenc p7 cq28      99.1 / 89-93   41 %   2.5 s
#   libx264 slow crf24      99.2 / 84-91   35 %   9 s on 8 threads
# cq40 / crf42 reached 20 % but dropped the worst pictures to 77; SVT-AV1 on
# the CPU was no smaller than hardware AV1 and worse on the worst pictures.
# Tried in this order; the first the machine's ffmpeg can open AND whose output
# proves interchangeable wins. All run without picture reordering.
ARCHIVE_CODECS = ("av1_nvenc", "h264_nvenc", "libx264")
ARCHIVE_AV1_CQ = 36
ARCHIVE_H264_CQ = 28
ARCHIVE_CRF = 24

# THE RING IN AV1 (RTX 40-series and later). Measured 2026-09-20 on one real
# saved replay re-encoded with the ring's own settings, VMAF against that
# source, `x realtime` with no game rendering beside it:
#   ring today, h264_nvenc p4 cq20   100 %   16.9x   VMAF 99.8 / worst 1 % 96.8
#   av1_nvenc p4 cq28                 60 %   19.0x        99.8 / 96.3
#   av1_nvenc p4 cq36                 32 %   19.0x        99.4 / 94.0   <- this
# Realtime AV1 lands where the OFFLINE archive pass lands and encodes FASTER
# than the H.264 the ring uses now, so a recorder that writes it makes Save
# publish already-small bytes. Deliberately its own constant rather than a
# reuse of ARCHIVE_AV1_CQ: the two stages are independent decisions that
# currently agree, and re-tuning the ring must not move the archive.
VIDEO_AV1_CQ = 36

# Encoder speed per stage. The ring runs REALTIME (must beat 1/fps per frame,
# measured ~8x headroom at p4); clip extraction is offline, so it can afford a
# slower preset for the same quality target.
_NVENC_PRESET = {"realtime": "p4", "offline": "p6", "archive": "p7"}
_X264_PRESET = {"realtime": "ultrafast", "offline": "veryfast", "archive": "slow"}
_AMF_QUALITY = {"realtime": "balanced", "offline": "quality"}
_QSV_PRESET = {"realtime": "medium", "offline": "slow"}


# The quality NUMBERS are read at the call, never captured in a per-stage table.
# A table built at import froze VIDEO_CQ at its module-load value, so the ring
# and the clip stopped following the constant they are defined by — invisible
# in normal use (the value rarely changes) and caught by
# tests/test_gpuencoder_client.py, which sets VIDEO_CQ and asks the native
# encoder's options what it got (2026-09-20).
def _nvenc_cq(stage: str) -> int:
    return ARCHIVE_H264_CQ if stage == "archive" else VIDEO_CQ


def _av1_cq(stage: str) -> int:
    return ARCHIVE_AV1_CQ if stage == "archive" else VIDEO_AV1_CQ


def _x264_crf(stage: str) -> int:
    return ARCHIVE_CRF if stage == "archive" else VIDEO_CRF


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
                "-rc", "vbr", "-cq", str(_nvenc_cq(stage)), "-b:v", "0",
                "-maxrate", maxrate, "-bufsize", maxrate]
    if codec == "av1_nvenc":
        # RTX 40-series and later encode AV1; every other GPU keeps H.264 and
        # the compression pass. Same cq-not-bitrate reasoning as H.264 above;
        # the scales are not comparable. No -profile:v: AV1 NVENC has one.
        return ["-preset", _NVENC_PRESET[stage], "-tune", "hq",
                "-rc", "vbr", "-cq", str(_av1_cq(stage)), "-b:v", "0",
                "-maxrate", maxrate, "-bufsize", maxrate]
    if codec == "libx264":
        return ["-preset", _X264_PRESET[stage], "-crf", str(_x264_crf(stage))]
    if codec == "h264_amf":
        return ["-usage", "transcoding", "-quality", _AMF_QUALITY[stage],
                "-profile:v", "high", "-rc", "cqp",
                "-qp_i", str(AMF_QP), "-qp_p", str(AMF_QP),
                "-qp_b", str(AMF_QP), "-frame_skipping", "0"]
    if codec == "h264_qsv":
        return ["-preset", _QSV_PRESET[stage], "-profile:v", "high",
                "-global_quality", str(QSV_ICQ), "-look_ahead", "0", "-b:v", "0"]
    return []


def forced_idr_args(codec: str) -> list[str]:
    """Forced I pictures must be independent starts for the segment muxer."""
    if codec in ("h264_nvenc", "av1_nvenc"):
        return ["-forced-idr", "1"]
    if codec in ("h264_amf", "h264_qsv"):
        return ["-forced_idr", "1"]
    return []  # x264's closed GOP is already the default


def raw_picture_args(codec: str) -> list[str]:
    """Color signaling for the BGRA capture input, not decoded YUV clips.

    AMF inherited RGB's full-range tag while its GPU produced limited-range
    BT.709: RGB(40,100,220) decoded as (47,101,205) on Radeon driver
    32.0.21045.5002. Setting frame properties fixes signaling without another
    pixel conversion; plain output -color_range/-colorspace flags did not.
    The startup probe verifies the result on the installed build/driver.
    """
    if codec == "h264_amf":
        return ["-vf", "setparams=range=limited:colorspace=bt709"]
    return []


# -- user-adjustable storage limits (UI: recording-dot panel) -----------------

SETTINGS_LIMITS = {
    "retention_attempts": (1, 1000),
    "retention_s": (60.0, 86400.0),          # 1 min .. 24 h (None = whole session)
    "max_buffer_bytes": (1024**3, 1024**4),  # 1 GiB .. 1 TiB
    "pre_pad_s": (0.0, 10.0),                # clip lead-in before the anchor
    "post_pad_s": (0.0, 10.0),               # clip tail after the closing event
}


def validate_settings(retention_s: float | None, max_buffer_bytes: int,
                      pre_pad_s: float | None = None,
                      post_pad_s: float | None = None,
                      retention_attempts: int | None = None) -> None:
    """ValueError on out-of-range values (the API maps it to 409).
    Pads are validated only when provided (None = caller keeps current)."""
    lo, hi = SETTINGS_LIMITS["retention_s"]
    if retention_attempts is not None and (isinstance(retention_attempts, bool)
            or not isinstance(retention_attempts, int) or not 1 <= retention_attempts <= 1000):
        raise ValueError("retention_attempts must be null or an integer from 1 to 1000")
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
                  post_pad_s: float, retention_attempts: int | None = 10) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"retention_s": retention_s, "retention_attempts": retention_attempts,
         "max_buffer_bytes": int(max_buffer_bytes),
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
    attempts = raw.get("retention_attempts", cfg.retention_attempts)
    try:
        validate_settings(retention, cap, pre, post, attempts)
    except ValueError as e:
        logging.getLogger("sm64.replay").warning(
            "ignoring invalid %s: %s", cfg.settings_path, e)
        return cfg
    return replace(cfg, retention_s=retention, retention_attempts=attempts, max_buffer_bytes=int(cap),
                   pre_pad_s=float(pre), post_pad_s=float(post))


AUDIO_RESAMPLE_OPTIONS = "async=1:first_pts=0:min_hard_comp=0.1"


def fragment_mux_options() -> dict[str, str]:
    """Same source-clock fragmented MP4 policy for raw and encoded feeds."""
    return {
        "movflags": "delay_moov+default_base_moof+frag_keyframe",
        "frag_duration": "100000",
        "movie_timescale": "90000",
        "video_track_timescale": "90000",
        "avoid_negative_ts": "disabled",
        "flush_packets": "1",
    }

"""All replay tunables in one place (spec: Config section).

The two STORAGE limits (retention_s, max_buffer_bytes) are additionally
user-adjustable from the UI (recording-dot panel): they persist in a tiny
JSON overlay file (settings_path) so changes survive restarts without a db
migration. Everything else stays code-level on purpose."""
import json
import logging
from dataclasses import dataclass, field, replace
from pathlib import Path


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
    save_root: Path = field(default=Path("replays"))
    scratch_dir: Path = field(default=Path("data") / "replay_buffer")
    window_title: str = "Project64"       # substring match on the window title
    audio_rate: int = 48000               # proc-tap delivers 48 kHz stereo
    attach_poll_s: float = 2.0            # window-hunt interval
    extract_wait_s: float = 5.0           # bounded wait for the tail segment
    settings_path: Path = field(default=Path("data") / "replay_settings.json")


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

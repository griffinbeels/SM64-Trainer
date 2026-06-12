"""All replay tunables in one place (spec: Config section)."""
from dataclasses import dataclass, field
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

# src/sm64_events/main.py
"""Composition root: registry -> memory -> poller -> detectors -> tracking -> app."""
import logging
import sys
from pathlib import Path

from sm64_events.core.logging_setup import configure_logging
from sm64_events.detectors.anchors import AnchorDetector
from sm64_events.detectors.death import DeathDetector
from sm64_events.detectors.level import LevelChangeDetector
from sm64_events.detectors.dust import DustTrickDetector
from sm64_events.detectors.lifecycle import GameResetDetector
from sm64_events.detectors.star_grab import StarGrabDetector
from sm64_events.memory.pj64 import Pj64Memory
from sm64_events.replay.audio import SystemAudioSource
from sm64_events.replay.config import ReplayConfig, apply_settings_file
from sm64_events.replay.extract import ClipExtractor
from sm64_events.replay.recorder import ReplayRecorder
from sm64_events.replay.service import ReplayService
from sm64_events.replay.video import DwmSurfaceVideoSource
from sm64_events.replay.window import find_window
from sm64_events.server.app import create_app
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.poller import Poller
from sm64_events.storage.db import Database
from sm64_events.storage.instance_lock import acquire_instance_lock
from sm64_events.tracking.service import TrackerService

DB_PATH = Path("data") / "tracker.db"

# Held for the process lifetime; releasing it would allow a second instance to
# start journaling concurrently (the incident we're guarding against).
_instance_lock = None


def build():
    global _instance_lock
    configure_logging()
    # Capture threads contend with encode/server threads for the GIL; the
    # default 5 ms switch interval adds whole-frame latency spikes at 60 fps.
    sys.setswitchinterval(0.002)
    memory = Pj64Memory()
    broadcaster = Broadcaster()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock = acquire_instance_lock(DB_PATH.with_suffix(".lock"))
    if lock is None:
        logging.getLogger("sm64.tracker").error(
            "another tracker instance owns %s - running broadcast-only "
            "(events will NOT be recorded twice)", DB_PATH)
        db = None
    else:
        _instance_lock = lock
        try:
            db = Database(DB_PATH)
        except Exception:
            logging.getLogger("sm64.tracker").exception(
                "database unavailable - running broadcast-only")
            db = None
    service = TrackerService(db, broadcaster)
    # User-set storage limits (UI panel) overlay the code defaults.
    replay_cfg = apply_settings_file(ReplayConfig())
    replay = None
    if replay_cfg.enabled:
        from sm64_events.replay.encoder import pick_video_codec
        codec = pick_video_codec()
        # System loopback is PRIMARY (live-audit 2026-06-11): per-process
        # loopback (proctap) "succeeds" but delivers zeros on this machine —
        # it couldn't even hear a beep from its own process, an undetectable
        # false-healthy state. Device-wide loopback verifiably captures the
        # default output (Elgato Wave:XLR, native 48 kHz). Tradeoff: other
        # apps' audio bleeds into replays.
        # Video ENCODING lives in an ffmpeg.exe subprocess when available:
        # in-process PyAV encoding shared the GIL with capture threads and
        # the audio pump — every remaining replay glitch class traced to
        # that coupling (scattered missed slots, rare 100-200 ms gaps,
        # correlated audio hiccups). Fallback: the in-process writer.
        import shutil as _shutil
        import subprocess as _sp
        video_sink_factory = None
        _ffmpeg = _shutil.which("ffmpeg")
        if _ffmpeg:
            try:
                _sp.run([_ffmpeg, "-version"], capture_output=True,
                        timeout=10, check=True,
                        creationflags=_sp.CREATE_NO_WINDOW)
                from sm64_events.replay.ffmpeg_sink import FfmpegVideoSink
                video_sink_factory = (
                    lambda cfg, on_seg, _f=_ffmpeg: FfmpegVideoSink(
                        cfg, on_seg, ffmpeg=_f))
                logging.getLogger("sm64.replay").info(
                    "video backend: ffmpeg subprocess (%s)", _ffmpeg)
            except Exception:
                logging.getLogger("sm64.replay").exception(
                    "ffmpeg probe failed - using in-process encoder")
        recorder = ReplayRecorder(
            cfg=replay_cfg,
            window_finder=find_window,
            video_factory=lambda win: DwmSurfaceVideoSource(win, fps=replay_cfg.fps),
            audio_factory=lambda pid: SystemAudioSource(
                rate=replay_cfg.audio_rate, pid=pid),
            fallback_audio_factory=None,
            codec=codec,
            video_sink_factory=video_sink_factory)
        replay = ReplayService(
            cfg=replay_cfg, recorder=recorder,
            extractor=ClipExtractor(cfg=replay_cfg, codec=codec),
            tracker=service)
    # Order is load-bearing: level changes abandon stale attempts BEFORE the
    # same tick's igt-reset anchor opens the next one; resets before grabs
    # (see projection.py docstring on the same-tick race); dust tricks before
    # grabs so a same-tick rollout/jump attaches to the attempt the grab closes.
    detectors = [GameResetDetector(), LevelChangeDetector(), AnchorDetector(),
                 DeathDetector(), DustTrickDetector(), StarGrabDetector()]
    poller = Poller(memory, detectors, service)  # service IS the event sink
    return create_app(poller, broadcaster, service=service, replay=replay)


app = build()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8064)

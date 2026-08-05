# src/sm64_events/main.py
"""Composition root: registry -> memory -> poller -> detectors -> tracking -> app."""
import json
import logging
import shutil
import sys

from sm64_events.core.logging_setup import configure_logging
from sm64_events.core.paths import (bundled_ffmpeg, compare_cache_dir,
                                    compilations_dir, db_path,
                                    instance_lock_path, migrate_legacy_data_dir,
                                    server_port)
from sm64_events.compare.importer import VideoImporter
from sm64_events.compare.service import CompareService
from sm64_events.core.updater import UpdateService
from sm64_events.core.version import __version__
from sm64_events.detectors.anchors import AnchorDetector
from sm64_events.detectors.area import AreaChangeDetector
from sm64_events.detectors.death import DeathDetector
from sm64_events.detectors.dust import DustTrickDetector
from sm64_events.detectors.key import KeyGrabDetector
from sm64_events.detectors.level import LevelChangeDetector
from sm64_events.detectors.lifecycle import GameResetDetector
from sm64_events.detectors.moment import MomentDetector
from sm64_events.detectors.spawn import SpawnDetector
from sm64_events.detectors.stage import StageChangeDetector
from sm64_events.detectors.star_grab import StarGrabDetector
from sm64_events.detectors.warp import WarpDetector
from sm64_events.memory.pj64 import Pj64Memory
from sm64_events.replay.audio import ProcessAudioSource, SystemAudioSource
from sm64_events.replay.config import ReplayConfig, apply_settings_file
from sm64_events.replay.extract import ClipExtractor
from sm64_events.replay.recorder import ReplayRecorder
from sm64_events.replay.service import ReplayService, saved_attempt_ids
from sm64_events.replay.compilation import CompilationBuilder, CompilationService
from sm64_events.replay.video import DwmSurfaceVideoSource
from sm64_events.replay.window import find_window
from sm64_events.server.app import create_app
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.poller import Poller
from sm64_events.storage.db import Database
from sm64_events.storage.instance_lock import acquire_instance_lock
from sm64_events.tracking.service import TrackerService

# Held for the process lifetime; releasing it would allow a second instance to
# start journaling concurrently (the incident we're guarding against).
_instance_lock = None


def _bootstrap_cleanup_arg(argv=None) -> "str | None":
    """--cleanup-bootstrap <path>: the bootstrap installer hands us its own
    exe path at launch; startup_maintenance deletes it (and its .old) once
    the bootstrap process has exited."""
    argv = sys.argv if argv is None else argv
    if "--cleanup-bootstrap" in argv:
        idx = argv.index("--cleanup-bootstrap")
        if idx + 1 < len(argv):
            return argv[idx + 1]
    return None


def build_detectors(target_active=None) -> list:
    """THE detector chain, in THE order. Extracted from build() 2026-07-31 so
    a test can drive the real one end to end (tests/test_segment_igt.py plays
    snapshots through this list and projects what comes out) — a test that
    listed the detectors itself would keep passing with one of them unwired,
    which is the whole failure mode it exists to catch.

    Order is load-bearing for attempt state: level changes abandon stale
    attempts BEFORE the same tick's igt-reset anchor opens the next one;
    dust tricks before grabs so a same-tick rollout/jump attaches to the
    attempt the grab closes.  New primitives slot between level and anchors:
    area follows level (same establishing discipline); key/spawn are stateless
    edges — key_grabbed and star_collected cannot co-emit on the same level
    (star_grab.py guards KEY_GRAB_LEVELS directly), so their relative order is
    informational only.

    Warp's position stopped being informational on 2026-08-04 (task 0081) and
    it now sits SECOND, for the same reason star_grab is first.  It HOLDS the
    entrance touch until a level or area edge names the destination, because
    the touch cannot name its own: decomp `level_trigger_warp` writes nothing
    to sWarpDest, which `initiate_delayed_warp` fills 77 frames later,
    immediately before the level unloads.  So on the release tick ONE poll
    carries a touch that happened 77 frames ago AND the level change happening
    now.  A held event describes the past and is published before anything
    describing the present, or the level change closes the attempt the touch
    belongs to and one movement records as two.  It has carried an IgtClock of
    its own since 2026-07-31 and is now stateful for this second reason too.

    area_changed reads CURR_AREA (gCurrAreaIndex, live-pinned 2026-06-12);
    castle areas: 1=lobby, 2=upstairs, 3=basement — see addresses.py.

    ## Why star_grab is FIRST (2026-08-01, live report)

    Since the x-cam fix, `star_collected` is never synchronous with the grab:
    the detector holds the grab and emits it 45+ frames later, or the moment a
    reset/level change breaks that wait.  So on the tick the player resets out
    of a star dance, ONE tick carries two events describing two different
    moments — a grab that already happened, and a reset happening now.  This
    list is the order they are journaled in, and journaling them the way we
    LEARNED them rather than the way they HAPPENED gave the reset the open
    attempt and left the grab to open a second row: one star, a reset row and a
    success row (live report 2026-08-01, `#2 ✗ reset 0'14"06` sitting under
    `#3 ✓ 0'13"53` for a single WF grab).  A held event describes the past, so
    it is published before anything describing the present — then the grab
    closes the attempt it belongs to and the reset finds nothing open, which is
    already how a reset AFTER a settled grab behaves.  Same fix, same reason,
    for `game_reset` and a level change out of the dance.

    This is ordering, not frame arithmetic: sorting a tick's events by `frame`
    would look equivalent and is not, because `game_reset` restarts
    gGlobalTimer — its frame is a small post-boot number while the grab it
    raced carries a large pre-reset one, so a frame sort puts them in exactly
    the wrong order (a different epoch, not a later moment).

    It does not disturb the dust rule above: a rollout/jump event needs a
    landing→launch action edge, which Mario cannot produce while he is in a
    star dance, so a dust event and a settling grab cannot share a tick in the
    first place — and a rollout on the GRAB tick is now ~45 frames ahead of the
    emit whatever this order says.  Pinned by
    tests/test_reset_during_star_grab.py, mutation-proved by moving this
    detector back to the end.

    ## MomentDetector's position, and its argument

    A moment (`door_open`, `textbox`) is emitted on the frame it HAPPENED, so
    unlike the two leaders it describes the present and must not jump ahead of
    them — behind star_grab and warp, and otherwise wherever the stateless
    edges sit.

    `target_active` is the task-0087 gate: "these should ONLY be tracked when
    we explicitly select / autoselect a star or segment". It defaults to None
    (permissive) so `build_detectors()` keeps working for every existing
    caller and for the tests that drive the real chain end to end; `build()`
    below injects the predicate that reads the live target, which is the only
    place that knows the service.
    """
    moments = (MomentDetector() if target_active is None
               else MomentDetector(target_active=target_active))
    detectors = [StarGrabDetector(), WarpDetector(), GameResetDetector(),
                 LevelChangeDetector(), AreaChangeDetector(),
                 StageChangeDetector(), AnchorDetector(), DeathDetector(),
                 DustTrickDetector(), KeyGrabDetector(), SpawnDetector(),
                 moments]
    return detectors


def build():
    global _instance_lock
    configure_logging()
    migrate_legacy_data_dir()   # rename the legacy data dir before any data path is read
    # Capture threads contend with encode/server threads for the GIL; the
    # default 5 ms switch interval adds whole-frame latency spikes at 60 fps.
    sys.setswitchinterval(0.002)
    memory = Pj64Memory()
    broadcaster = Broadcaster()
    db_file = db_path()
    db_file.parent.mkdir(parents=True, exist_ok=True)
    lock = acquire_instance_lock(instance_lock_path())
    db_retry = None
    if lock is None:
        logging.getLogger("sm64.tracker").error(
            "another tracker instance owns %s - running broadcast-only "
            "(events will NOT be recorded twice)", db_file)
        db = None

        def db_retry():
            """Self-heal probe for server/app.py's reattach loop. The usual
            cause of a held lock is a restart handoff racing the old
            process's exit — the handoff's wait_lock_free bounds that wait,
            and this closes the gap if it still loses. None while the lock
            is held elsewhere; raising (a broken db) ends the retry."""
            global _instance_lock
            retry_lock = acquire_instance_lock(instance_lock_path())
            if retry_lock is None:
                return None
            _instance_lock = retry_lock
            return Database(db_file)
    else:
        _instance_lock = lock
        try:
            db = Database(db_file)
        except Exception:
            logging.getLogger("sm64.tracker").exception(
                "database unavailable - running broadcast-only")
            db = None
    from sm64_events.ranks.standards import RankStandards
    from sm64_events.core.paths import rank_standards_path, bundled_rank_standards
    ranks = RankStandards(rank_standards_path(), bundled_rank_standards())
    ranks.load()
    if db is not None:
        # Editable-defaults reconcile (spec 2026-07-23-default-routes-
        # foundation): refreshes untouched (seed_dirty=0) seeded routes/
        # segments from the bundled corpus, inserts anything missing, and
        # never touches user-edited or user-created rows. Best-effort — a
        # missing/corrupt seed must never block startup. The except below now
        # only guards a missing or non-JSON FILE: reconcile validates each row
        # itself and returns the ones it skipped, so a wrong-shaped row costs
        # that row instead of every row after it (spec 2026-07-24 §10).
        from sm64_events.tracking.defaults import reconcile_defaults
        from sm64_events.core.paths import bundled_defaults_seed
        try:
            seed_path = bundled_defaults_seed()
            if seed_path is not None:
                seed = json.loads(seed_path.read_text(encoding="utf-8"))
                for problem in reconcile_defaults(db, seed):
                    logging.getLogger("sm64.tracker").warning(
                        "defaults seed row skipped: %s", problem)
        except (OSError, ValueError, KeyError, TypeError):
            logging.getLogger("sm64.tracker").warning(
                "defaults seed unavailable", exc_info=True)
    service = TrackerService(db, broadcaster, ranks=ranks)
    # User-set storage limits (UI panel) overlay the code defaults.
    replay_cfg = apply_settings_file(ReplayConfig())
    # A saved clip's attempt survives the startup prune (tracking/prune.py).
    # Wired off the CONFIG, not off the ReplayService below, so it still holds
    # when replay is disabled this run -- the clips he saved are on disk either
    # way, and their filenames are the only index to them.
    service.saved_clip_ids = lambda: saved_attempt_ids(replay_cfg.save_root)
    replay = None
    if replay_cfg.enabled:
        from sm64_events.replay.encoder import pick_video_codec
        codec = pick_video_codec()
        # Per-process loopback is PRIMARY: a replay must carry the game and
        # nothing else — no Discord call, no music (user report 2026-07-30).
        # Device-wide loopback is the fallback for machines where the
        # per-process tap can't start; it records everything sharing PJ64's
        # output endpoint, so it is a degradation, and status()'s audio_mode
        # is what says which one is live. Both take the pid: the fallback
        # needs it to target PJ64's OWN endpoint (per-app routing, 2026-06-11).
        # Video ENCODING lives in an ffmpeg.exe subprocess when available:
        # in-process PyAV encoding shared the GIL with capture threads and
        # the audio pump — every remaining replay glitch class traced to
        # that coupling (scattered missed slots, rare 100-200 ms gaps,
        # correlated audio hiccups). Fallback: the in-process writer.
        import shutil as _shutil
        import subprocess as _sp
        video_sink_factory = None
        _ffmpeg = bundled_ffmpeg() or _shutil.which("ffmpeg")
        if _ffmpeg:
            try:
                _sp.run([_ffmpeg, "-version"], capture_output=True,
                        timeout=10, check=True,
                        creationflags=_sp.CREATE_NO_WINDOW)
                from sm64_events.replay.ffmpeg_sink import FfmpegAvSink
                video_sink_factory = (
                    lambda cfg, on_seg, _f=_ffmpeg: FfmpegAvSink(
                        cfg, on_seg, ffmpeg=_f))
                logging.getLogger("sm64.replay").info(
                    "replay backend: single ffmpeg A+V mux (%s)", _ffmpeg)
            except Exception:
                logging.getLogger("sm64.replay").exception(
                    "ffmpeg probe failed - using in-process encoder")
        recorder = ReplayRecorder(
            cfg=replay_cfg,
            window_finder=find_window,
            video_factory=lambda win: DwmSurfaceVideoSource(win, fps=replay_cfg.fps),
            audio_factory=lambda pid: ProcessAudioSource(
                pid=pid, rate=replay_cfg.audio_rate),
            fallback_audio_factory=lambda pid: SystemAudioSource(
                rate=replay_cfg.audio_rate, pid=pid),
            codec=codec,
            video_sink_factory=video_sink_factory)
        replay = ReplayService(
            cfg=replay_cfg, recorder=recorder,
            extractor=ClipExtractor(cfg=replay_cfg, codec=codec),
            tracker=service)
    # Compare tab: import comparison videos (yt-dlp/copy -> ffmpeg normalize)
    # into the content cache, then serve them as plain clips. Only built when
    # ffmpeg is available (same binary the replay sink uses).
    compare = None
    _ffmpeg_bin = bundled_ffmpeg() or shutil.which("ffmpeg")
    if db is not None and _ffmpeg_bin:
        importer = VideoImporter(compare_cache_dir(), _ffmpeg_bin)
        compare = CompareService(importer, service, broadcaster,
                                 compare_cache_dir())
    # Failure compilations reuse the replay ring + extractor; only built when
    # replay AND the db are available (needs attempts + footage).
    compilation = None
    if replay is not None and db is not None:
        compilation = CompilationService(
            replay=replay, tracker=service,
            builder=CompilationBuilder(extractor=replay.extractor, codec=codec,
                                       fps=replay_cfg.fps),
            out_dir=compilations_dir())
    # Moments are journaled only while something is being practiced (task
    # 0087). Read as a callable rather than a snapshot: a target set mid
    # session must start recording without a restart.
    detectors = build_detectors(target_active=lambda: service.target is not None)
    # An ordinal means "the Nth since this attempt opened", so the counter
    # restarts when one does. The service sees every event and the detector
    # sees only snapshots, so this is the one place that can join them.
    service.on_attempt_boundary = next(
        d for d in detectors if isinstance(d, MomentDetector)).reset
    if replay is not None:
        # Poll-thread tap (emits no events): tells the recorder the player
        # is providing input so the buffer pauses while idle (activity.py).
        from sm64_events.replay.activity import ActivityTap
        detectors.append(ActivityTap(replay.recorder))
    # service IS the event sink; on_frame is its deferred-judgement heartbeat,
    # so a topological cancel reaches the screen on the next game frame rather
    # than whenever the next event happens to be journaled.
    poller = Poller(memory, detectors, service, on_frame=service.settle_frame)
    updater = UpdateService(current_version=__version__)
    updater.startup_maintenance(bootstrap_path=_bootstrap_cleanup_arg())
    return create_app(poller, broadcaster, service=service, replay=replay,
                      updater=updater, compare=compare, compilation=compilation,
                      db_retry=db_retry)


_app = None


def get_app():
    """Build the app once, lazily. Importing this module must NOT build it —
    build() acquires the instance lock, and the desktop shell needs to call
    build() AFTER its single-instance takeover. Only serving the app builds
    it: `uvicorn sm64_events.main:app` (attribute access via __getattr__) or
    run()."""
    global _app
    if _app is None:
        _app = build()
    return _app


def __getattr__(name):
    if name == "app":
        return get_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def run() -> None:
    """THE canonical launch (`uv run python -m sm64_events.main`).

    timeout_graceful_shutdown is the load-bearing argument: browsers hold
    connections open (5 s status poll, paused <video> Range requests that
    stop reading mid-stream) and uvicorn waits for them BEFORE running
    lifespan teardown — without a deadline that wait is INFINITE and
    CTRL+C hangs with ffmpeg still recording (live incidents 2026-06-12
    and 2026-06-13; repro: a stalled-reader client wedges serve() in
    flow_control.drain() forever). The bare uvicorn CLI defaults the
    timeout to None, which is exactly why the 06-12 fix never took: it
    lived only here while the documented command was `uvicorn
    sm64_events.main:app`. Backstop for non-canonical launches: the
    force-exit watchdog in server/app.py (armed at first CTRL+C, 30 s).

    VERIFY (live): one CTRL+C on `uv run python -m sm64_events.main` must
    return the prompt in <~5 s with ffmpeg gone. If it hangs: "replay
    stop exceeded 15 s" in the log = teardown wedged; watchdog line at
    30 s = drain wedged; neither = a new layer, instrument before fixing.
    """
    import os
    if os.environ.pop("SM64_RESTART", None):
        # A restart relaunch: the old process is exiting — wait for the
        # port, THEN the db instance lock. Both waits are needed: uvicorn
        # frees the port seconds BEFORE process exit releases the lock
        # (replay teardown + window destruction run in between), and a
        # handoff that waited only on the port lost the lock race and came
        # up broadcast-only (post-update incident 2026-07-23).
        from sm64_events.core.relaunch import wait_port_free
        from sm64_events.storage.instance_lock import wait_lock_free
        wait_port_free()
        wait_lock_free(instance_lock_path())
    import uvicorn
    uvicorn.run(get_app(), host="127.0.0.1", port=server_port(),
                timeout_graceful_shutdown=3)


if __name__ == "__main__":
    run()

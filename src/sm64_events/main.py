# src/sm64_events/main.py
"""Composition root: registry -> memory -> poller -> detectors -> tracking -> app."""
import asyncio
import json
import logging
import shutil
import sys

from sm64_events.compare.importer import VideoImporter
from sm64_events.compare.service import CompareService
from sm64_events.core.logging_setup import configure_logging
from sm64_events.core.build_channel import current_build
from sm64_events.core.paths import (
    bundled_ffmpeg,
    compare_cache_dir,
    compilations_dir,
    db_path,
    data_root,
    instance_lock_path,
    is_frozen,
    migrate_legacy_data_dir,
    server_port,
)
from sm64_events.core.snapshot import UnreadyReader, reader_for
from sm64_events.core.updater import UpdateService
from sm64_events.core.version import __version__
from sm64_events.detectors.anchors import AnchorDetector
from sm64_events.detectors.area import AreaChangeDetector
from sm64_events.detectors.caused import CausedMomentDetector
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
from sm64_events.ghost.dpad_toggle import DpadGhostToggle, OverlayFlipper
from sm64_events.ghost.session import GhostSession
from sm64_events.memory.ghost_layout import ghost_supported
from sm64_events.memory.layout import LAYOUT_ROWS, layout_for
from sm64_events.memory.pj64 import Pj64Memory
from sm64_events.replay.audio import ProcessAudioSource, SystemAudioSource
from sm64_events.replay.compilation import CompilationBuilder, CompilationService
from sm64_events.replay.config import ReplayConfig, apply_settings_file
from sm64_events.replay.extract import ClipExtractor
from sm64_events.replay.recorder import ReplayRecorder
from sm64_events.replay.service import ReplayService, saved_attempt_ids
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


def build_detectors(target_active=None, version: str = "us") -> list:
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

    THIRD since round 9 item 4 (2026-08-07): a moment is now a one-poll HELD
    emit — the event is built on the action edge but its landmark is settled
    from the next poll, because the engaged-object pointer can lag the action
    byte by a read (his first WF tree grab resolved to Mario's own spawn
    marker; detectors/moment.py carries the evidence). So on the release tick
    it describes the PAST, and the same rule that put star_grab first and warp
    second applies: a held event is published before anything describing the
    present, or an anchor/level change on the settle tick closes the attempt
    the moment belongs to. Still behind the two leaders — their holds span
    many frames where this one spans one poll, and a star's settle can release
    on the same tick a door moment fires.

    `target_active` was the task-0087 gate ("these should ONLY be tracked when
    we explicitly select / autoselect a star or segment") and `build()` used to
    inject the live target into it. **It injects nothing now, deliberately** —
    the recorder is the surface that consumes moments, and it is used with NO
    target set, because pointing at what you just did is how a definition gets
    made. Full evidence, the two live reports it cost, and why a recorder-open
    gate cannot work: `detectors/moment.py`'s module docstring.

    The parameter stays as an INJECTION SEAM rather than being deleted: a
    future rule that wants to narrow WHEN a moment records has somewhere to go
    that is not a new branch inside the detector.

    `version` ("us"/"jp") is the same kind of seam, added 2026-08-15 with the
    version-sync work: no shipped detector branches on it — every
    version-dependent read is resolved by the snapshot reader through the
    layout and the behaviour symbol tables — but a JP-only variant has a place
    to hang without a second chain. `sync/stack.py` builds the chain through
    this door for the gates, so a detector left out here is left out there.
    """
    del version   # no detector reads it yet; see the docstring
    moments = (MomentDetector() if target_active is None
               else MomentDetector(target_active=target_active))
    # CausedMomentDetector emits the same event on the frame it happened —
    # not a held emit — so like MomentDetector it sits behind the two held
    # leaders and ahead of everything that closes attempts.
    detectors = [StarGrabDetector(), WarpDetector(), moments,
                 CausedMomentDetector(),
                 GameResetDetector(), LevelChangeDetector(),
                 AreaChangeDetector(), StageChangeDetector(),
                 AnchorDetector(), DeathDetector(), DustTrickDetector(),
                 KeyGrabDetector(), SpawnDetector()]
    return detectors


def _game_version() -> str:
    """Which ROM's layout the poller reads: the persisted game-version
    setting's EFFECTIVE value once feature/game-version lands its
    `core/modes.py` (an explicit JP/US wins, AUTO resolves to what the
    emulator has loaded), else "us" — the only ROM the tracker read before
    2026-08-15. The two branches meet in this one function on purpose."""
    try:
        from sm64_events.core.modes import effective_version, load_mode_config
    except ImportError:
        return "us"
    detected = None
    probe = Pj64Memory()
    try:
        from sm64_events.memory.version_probe import detect_version
        if probe.attach():
            detected = detect_version(probe)
    except Exception:            # no emulator yet is the normal boot case
        detected = None
    finally:
        probe.detach()
    return effective_version(load_mode_config(), detected=detected)


def _replay_service(cfg, recorder, codec, tracker):
    """Keep manual PB selection and its media preservation on one command."""
    replay = ReplayService(cfg=cfg, recorder=recorder,
                           extractor=ClipExtractor(cfg=cfg, codec=codec, fragments=getattr(recorder, "fragments", None)),
                           tracker=tracker)
    tracker.on_pb_saved = replay.preserve_pb
    tracker.on_session_ended = replay.session_ended
    return replay


def _ghost_follows_pb(service, ghost):
    """Preserve the new PB's native poses and follow it immediately."""
    service.on_pb_changed = ghost.pb_changed


def _ghost_follows_attempts(service, ghost):
    """A finished run joins the ghost population; a reclassified one moves
    to its new strategy there and in its committed pose clips."""
    service.on_attempt_saved = ghost.attempt_saved
    service.on_attempts_reclassified = ghost.attempts_reclassified


def _build_tracker(broadcaster):
    global _instance_lock
    db_file = db_path()
    db_file.parent.mkdir(parents=True, exist_ok=True)
    lock = acquire_instance_lock(instance_lock_path())
    db = None
    def db_retry():
        """Retain our writer lock across transient opens; never steal another's."""
        nonlocal lock, db
        global _instance_lock
        if db is not None:
            return db  # service.start may fail after a successful open
        if lock is None:
            lock = acquire_instance_lock(instance_lock_path())
        if lock is None:
            return None
        _instance_lock = lock
        db = Database(db_file)
        return db

    if lock is None:
        logging.getLogger("sm64.tracker").error(
            "another tracker instance owns %s - running broadcast-only "
            "(events will NOT be recorded twice)", db_file)
        db = None

    else:
        _instance_lock = lock
        try:
            db = Database(db_file)
        except Exception:
            logging.getLogger("sm64.tracker").exception(
                "database unavailable - running broadcast-only")
            db = None
    from sm64_events.core.paths import (
        bundled_rank_standards,
        bundled_sheet_ladders,
        rank_standards_path,
    )
    from sm64_events.ranks.standards import RankStandards
    ranks = RankStandards(rank_standards_path(), bundled_rank_standards(),
                          bundled_sheet_ladders())
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
        from sm64_events.core.paths import bundled_defaults_seed
        from sm64_events.tracking.defaults import reconcile_defaults
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
    # The persisted game version grades from the first view: the standards
    # store's grading version is set here, before anything serves, exactly as
    # a later PUT /api/mode would leave it (server/mode_api.py).
    from sm64_events.server.mode_api import apply_persisted_mode
    apply_persisted_mode(service)
    return service, db, db_retry



def _ghost_capture_factory(layout):
    """The recorder's pose capture for one build, or None where the native
    ghost cannot capture that build yet."""
    if not ghost_supported(layout.version):
        return None
    from sm64_events.ghost.capture import NativeCapture
    from sm64_events.ghost.sound_player import SoundPlayerProcess
    return lambda **kwargs: NativeCapture(
        layout, sound_player=SoundPlayerProcess(), **kwargs)


def _build_replay(replay_cfg, layout_of, service, *, recorder_lock=None):
    """`layout_of()` is the running build's layout: the picture stamps and
    the pose capture follow a ROM swap (`build`'s `follow_build`)."""
    from sm64_events.replay.encoder import pick_video_codec
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
            from sm64_events.core.childproc import quiet_spawn_kwargs
            _sp.run([_ffmpeg, "-version"], capture_output=True,
                    timeout=10, check=True, **quiet_spawn_kwargs())
            from sm64_events.replay.ffmpeg_sink import FfmpegAvSink
            # codec: the recorder passes pick_video_codec()'s answer, so
            # the sink encodes with what THIS machine has (hardcoded
            # nvenc flashed a non-NVIDIA user's cursor, 2026-08-07).
            video_sink_factory = (
                lambda cfg, on_seg, codec, _f=_ffmpeg: FfmpegAvSink(
                    cfg, on_seg, ffmpeg=_f, codec=codec))
            logging.getLogger("sm64.replay").info(
                "replay backend: single ffmpeg A+V mux (%s)", _ffmpeg)
        except Exception:
            logging.getLogger("sm64.replay").exception(
                "ffmpeg probe failed - using in-process encoder")
    codec = pick_video_codec(_ffmpeg if video_sink_factory is not None else None)
    from sm64_events.replay.sourcefactory import SourceFactory
    sources = SourceFactory(layout_of, replay_cfg, DwmSurfaceVideoSource)
    ghost_capture_factory = _ghost_capture_factory(layout_of())

    recorder_options = {}
    if recorder_lock is not None:
        from sm64_events.core.recorder_lock import acquire_recorder_lock
        reservation = [recorder_lock]

        def reserve_capture():
            return reservation.pop() if reservation else acquire_recorder_lock()

        recorder_options["recorder_lock_factory"] = reserve_capture
    recorder = ReplayRecorder(
        cfg=replay_cfg,
        window_finder=find_window,
        video_factory=sources.create,
        # The ghost sound player is tapped only while Ghosts in recording is
        # on, for the ghost copy; the clean audio is the game alone
        # (replay/audio.py GhostTimeline). `recorder` binds late.
        audio_factory=lambda pid: ProcessAudioSource(
            pid=pid, rate=replay_cfg.audio_rate,
            extra_pid=lambda: recorder.ghost_sound_pid()),
        fallback_audio_factory=lambda pid: SystemAudioSource(
            rate=replay_cfg.audio_rate, pid=pid),
        codec=codec,
        video_sink_factory=video_sink_factory,
        ghost_capture_factory=ghost_capture_factory, **recorder_options)
    replay = _replay_service(replay_cfg, recorder, codec, service)
    return replay, codec


def _build_capture_layer(poller, replay):
    # THE CAPTURE LAYER's installer (item 95): the setup screen's door to
    # installing the wrapper plugin under consent. Built on the real
    # registry and process list; its liveness is the GPU route's control page
    # and capture receipt (core/setup_gpu.py), so "active" means the plugin
    # is presenting.
    from sm64_events.core.capturelayer import CaptureLayer, WinProcesses, WinRegistry
    from sm64_events.core.setup_gpu import GpuSetupProbe
    from sm64_events.core.paths import (bundled_plugin_dll, bundled_renderer_dll, capture_layer_settings_path,
                                        installer_identity, migrate_capture_layer_settings)
    migrate_capture_layer_settings()
    capture_layer = CaptureLayer(
        registry=WinRegistry(), processes=WinProcesses(),
        settings_path=capture_layer_settings_path(),
        dll_source=bundled_plugin_dll,
        renderer_source=bundled_renderer_dll,
        gpu_observation=GpuSetupProbe(lambda: replay.recorder.status() if replay else {}),
        auto_refresh=current_build().channel == 'stable',
        installer=installer_identity())
    from sm64_events.core.setup_runtime import SetupRuntime
    setup_observer = SetupRuntime(WinProcesses(), Pj64Memory, poller, replay)
    if not capture_layer.auto_refresh:
        return capture_layer, setup_observer
    try:
        # A build carrying a newer layer refreshes the installed DLL while
        # Project64 is closed, under the consent already given -- but only an
        # install by an installer like this one (the frozen app's, or this
        # checkout's own) and never with an older build (plugin_ownership).
        # A running PJ64 holds its DLL and the setup screen says so instead.
        # Check at boot and at the refresh loop's interval, independent of
        # which app opens first.
        if capture_layer.refresh_if_stale():
            logging.getLogger("sm64.replay").info(
                "capture layer refreshed to this build's DLL (Project64 was closed)")
        capture_layer.start_refresh(logging.getLogger("sm64.replay"))
    except Exception:
        logging.getLogger("sm64.replay").exception("capture layer refresh failed")
    return capture_layer, setup_observer



def _comparison_services(replay, service, broadcaster, codec, replay_cfg):
    # Compare tab: import comparison videos (yt-dlp/copy -> ffmpeg normalize)
    # into the content cache, then serve them as plain clips. Only built when
    # ffmpeg is available (same binary the replay sink uses). Deliberately NOT
    # gated on the db: a boot that loses the instance-lock race (the reload
    # handoff, live 2026-07-30 + 2026-08-06) boots db-less and self-heals via
    # the reattach loop, but a router that was never mounted 404s forever —
    # the Compare tab swallows that silently and reads as "videos don't load"
    # (task 0053). The service reads tracker.db per call, so it answers 503
    # until the db lands, then works with no restart.
    compare = None
    _ffmpeg_bin = bundled_ffmpeg() or shutil.which("ffmpeg")
    if _ffmpeg_bin:
        importer = VideoImporter(compare_cache_dir(), _ffmpeg_bin)
        compare = CompareService(importer, service, broadcaster,
                                 compare_cache_dir())
    # Failure compilations reuse the replay ring + extractor; only built when
    # replay is available (same db reasoning as compare above).
    compilation = None
    if replay is not None:
        compilation = CompilationService(
            replay=replay, tracker=service,
            builder=CompilationBuilder(extractor=replay.extractor, codec=codec,
                                       fps=replay_cfg.fps),
            out_dir=compilations_dir())
    return compare, compilation


def _local_ghost_population(service):
    from sm64_events.core.paths import ghost_population_root
    from sm64_events.ghost.population import LocalPopulation
    from sm64_events.ghost.bundled_beta import with_bundled_beta
    root = ghost_population_root()
    definitions = lambda: service.db.segment_defs() if service.db else []
    local = LocalPopulation(root, definitions=definitions)
    return with_bundled_beta(local, cache=data_root() / 'data' / 'bundled_ghosts', definitions=definitions)


def _community_population(service):
    """Packaged community configuration creates one cache and IO worker."""
    from sm64_events.community import CommunityAccount, CommunityConfig, CachedPopulation, CommunitySync
    from sm64_events.core.build_channel import current_build
    build = current_build()
    if not build.community_enabled:
        return _local_ghost_population(service), None, None
    config = CommunityConfig(public_url=build.community_url, supabase_url=build.supabase_url,
        publishable_key=build.supabase_publishable_key, oauth_client_id=build.oauth_client_id,
        channel=build.account_namespace, callback_url=build.callback_url, enabled=True,
        **({'friend_invitation': build.friend_invitation} if build.friend_invitation else {}))
    account = CommunityAccount(config)
    population = CachedPopulation(_local_ghost_population(service),
        root=data_root()/'data'/'community_cache', account=account,
        definitions=lambda: service.db.segment_defs() if service.db else [])
    return population, account, CommunitySync(account, population)


_BACKGROUND = set()


def _in_background(coroutine):
    """Run `coroutine` as its own task on the running loop, held until it
    ends: the loop keeps only a weak reference to a task."""
    task = asyncio.get_running_loop().create_task(coroutine)
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)


def _log_boot_layout(version, reader, layout):
    if isinstance(reader, UnreadyReader):
        logging.getLogger("sm64.tracker").error(
            "game version %s: %s", version, reader.reason)
    else:
        logging.getLogger("sm64.tracker").info(
            "game version %s: %d/%d addresses in the layout", version,
            len(LAYOUT_ROWS) - len(layout.missing()), len(LAYOUT_ROWS))


def _build_follower(running, inputs, replay, service):
    """The poller's `on_version_change`: the cartridge's build changed, so
    every memory reader moves to its layout, and grading under AUTO follows
    the cartridge. The poller has already swapped its own reader and rebuilds
    the detectors after this returns; the regrade broadcasts, so it runs as
    its own task on the poller's loop."""
    def follow_build(new_version):
        running["version"] = new_version
        new_layout = layout_for(new_version)
        inputs.set_layout(new_layout)
        if replay is not None:
            replay.recorder.set_ghost_capture_factory(_ghost_capture_factory(new_layout))
            replay.recorder.retarget_capture()
        _in_background(service.note_running_version(new_version))
    return follow_build


def build(*, recorder_lock=None):
    configure_logging()
    migrate_legacy_data_dir()   # rename the legacy data dir before any data path is read
    # Capture threads contend with encode/server threads for the GIL; the
    # default 5 ms switch interval adds whole-frame latency spikes at 60 fps.
    sys.setswitchinterval(0.002)
    memory = Pj64Memory()
    version = _game_version()
    layout = layout_for(version)
    reader = reader_for(memory, version)
    # THE RUNNING BUILD: which ROM's layout every memory reader uses right
    # now. Boot starts from the setting/detection guess; the poller corrects
    # it from the cartridge header and `follow_build` moves every reader.
    running = {"version": version}
    _log_boot_layout(version, reader, layout)
    broadcaster = Broadcaster()
    service, _db, db_retry = _build_tracker(broadcaster)
    # User-set storage limits (UI panel) overlay the code defaults.
    replay_cfg = apply_settings_file(ReplayConfig())
    # A saved clip's attempt survives the startup prune (tracking/prune.py).
    # Wired off the CONFIG, not off the ReplayService below: the clips he saved
    # are on disk whatever the recorder does, and their filenames are the only
    # index to them.
    service.saved_clip_ids = lambda among=None: saved_attempt_ids(replay_cfg.save_root, among=among)
    running_layout = lambda: layout_for(running["version"])  # noqa: E731
    replay, codec = (_build_replay(replay_cfg, running_layout, service) if recorder_lock is None
                     else _build_replay(replay_cfg, running_layout, service, recorder_lock=recorder_lock))
    compare, compilation = _comparison_services(replay, service, broadcaster, codec, replay_cfg)
    # The ghost hotkey flips the Ghost Overlay (ghost/dpad_toggle.py). The
    # flipper outlives detector rebuilds and is bound to the session built below.
    overlay_flipper = OverlayFlipper()
    # NO target gate. This passed `target_active=lambda: service.target is not
    # None` (task 0087) until 2026-08-06, which made the recorder blind exactly
    # when it is used — see `build_detectors`' docstring and moment.py's.
    def detector_stream():
        detectors = build_detectors(version=running["version"])
        moments = [d for d in detectors
                   if isinstance(d, (MomentDetector, CausedMomentDetector))]

        def reset_ordinals():
            for detector in moments:
                detector.reset()

        # Rebind to the new instances after recovery, not the retired chain.
        service.on_attempt_boundary = reset_ordinals
        if replay is not None:
            from sm64_events.replay.activity import ActivityTap
            detectors.append(ActivityTap(replay.recorder))
        # Its hotkey's name reaches the session the flipper is bound to.
        detectors.append(DpadGhostToggle(
            overlay_flipper.request,
            lambda hotkey: overlay_flipper.session and overlay_flipper.session.note_hotkey(hotkey)))
        return detectors

    from sm64_events.server.inputruntime import InputRuntime
    inputs = InputRuntime(service, memory, layout, replay)
    from sm64_events.ghost.native_session import NativeGhost
    ghost_frame = {"frame":None}
    native_ghost = NativeGhost(replay, supported=lambda: ghost_supported(running["version"]),
        origin_of=lambda kind,key:service.native_rival_origin(kind,key,frame=ghost_frame["frame"]))

    follow_build = _build_follower(running, inputs, replay, service)

    def note_ghost_snapshot(snapshot):
        ghost_frame["frame"] = snapshot.global_timer
        native_ghost.note(snapshot)

    async def ghost_capture_gap(reason):
        await service.capture_gap(reason)
        native_ghost.note()

    poller = Poller(memory, detector_stream(), service, on_frame=service.settle_frame,
                    reader=reader, detector_factory=detector_stream,
                    on_gap=ghost_capture_gap, on_snapshot=note_ghost_snapshot,
                    reader_factory=lambda v: reader_for(memory, v),
                    on_version_change=follow_build)
    inputs.bind(poller)
    service.observed_game_version = lambda:poller.observed_game_version
    population, community_account, community_sync = _community_population(service)
    # One session owns native ghost preferences and the practice target.
    ghost = overlay_flipper.session = GhostSession(
        tracker=service, inputs=inputs.get_service,
        replay=replay, broadcaster=broadcaster,
        native=native_ghost,population=population,
        version_of=lambda:poller.observed_game_version, community=community_sync)
    _ghost_follows_attempts(service, ghost)
    _ghost_follows_pb(service, ghost)
    if replay is not None:
        # A real run on vanilla SM64, another hack or another game records
        # nothing, and neither does a practice build whose layout is not
        # verified yet; unknown (no header read yet) keeps the old behaviour.
        replay.recorder.set_capture_gate(poller.capture_allowed)
    updater = UpdateService(current_version=__version__)
    capture_layer, setup_observer = _build_capture_layer(poller, replay)
    return create_app(poller, broadcaster, service=service, replay=replay,
                      inputs=inputs.get_service, on_db_attached=inputs.attach,
                      updater=updater, bootstrap_cleanup_path=_bootstrap_cleanup_arg(),
                      compare=compare, compilation=compilation,
                      db_retry=db_retry, capture_layer=capture_layer,
                      setup_observer=setup_observer, ghost=ghost,
                      refresh_library_on_start=True, community_account=community_account,
                      community_sync=community_sync, protect_local_api=True, local_port=server_port())


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


def _build_stale_natives() -> None:
    """From a checkout, bring the bundled natives up to these sources before
    serving (tools/build_natives.py: a cache copy when another checkout built
    them, else a compile). A frozen app ships its own; a failure only warns,
    and the setup screen then reports the capture layer unavailable."""
    import os
    from pathlib import Path
    tool = Path(__file__).resolve().parents[2] / "tools" / "build_natives.py"
    if is_frozen() or os.environ.get("SM64_BUILD_NATIVES") == "0" or not tool.is_file():
        return
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_natives", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    print("Checking the capture layer and renderer against this checkout's sources...")
    try:
        states = module.ensure()
    except Exception as failure:  # noqa: BLE001 - never stop the server over the bundle
        print(f"  WARNING: the native bundle was not checked: {failure}")
        return
    print("  " + ", ".join(f"{name} {state}" for name, state in states.items()))


def run(*, recorder_lock=None) -> None:
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
    from sm64_events.core.reporting_setup import initialize_reporting
    initialize_reporting()
    _build_stale_natives()
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
    app = build(recorder_lock=recorder_lock) if recorder_lock is not None else get_app()
    uvicorn.run(app, host="127.0.0.1", port=server_port(),
                timeout_graceful_shutdown=3)


if __name__ == "__main__":
    run()

# tests/test_composition.py
"""Composition-root contracts the runtime depends on (see build_detectors'
own docstring: the held grab precedes everything that closes an attempt;
level_changed precedes anchors)."""
from pathlib import Path

import pytest

import sm64_events
from sm64_events.main import build_detectors
from source_scan import strip_comments


@pytest.fixture(autouse=True)
def _isolate_machine(monkeypatch, tmp_path):
    """Build wiring may never open PJ64, shared mappings or an installed DLL."""
    import subprocess
    import threading
    from types import SimpleNamespace
    layers = []

    def forbidden(*args, **kwargs):
        pytest.fail("composition test attempted a real machine boundary")

    class Layer:
        def __init__(self, **kwargs):
            self.header = kwargs["stream_header"]
            layers.append(self)

        def refresh_if_stale(self):
            return False

        def refresh_loop(self, *args, **kwargs):
            forbidden()

    start = threading.Thread.start

    def isolated_start(thread):
        if thread.name == "capture-layer-refresh":
            assert isinstance(thread._target.__self__, Layer)
            return  # no unowned refresh daemon survives build()
        return start(thread)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sm64_events.memory.pj64.Pj64Memory.attach", forbidden)
    monkeypatch.setattr("sm64_events.replay.framestream.FrameStream", forbidden)
    monkeypatch.setattr("sm64_events.core.capturelayer.CaptureLayer", Layer)
    monkeypatch.setattr("sm64_events.core.capturelayer.WinRegistry", lambda: SimpleNamespace())
    monkeypatch.setattr("sm64_events.core.capturelayer.WinProcesses", lambda: SimpleNamespace())
    monkeypatch.setattr("sm64_events.core.capturelayer_win.WinRegistry.__init__", forbidden)
    monkeypatch.setattr("sm64_events.core.capturelayer_win.WinProcesses.__init__", forbidden)
    monkeypatch.setattr("sm64_events.core.updater.UpdateService.startup_maintenance",
                        lambda *a, **kw: None)
    monkeypatch.setattr(threading.Thread, "start", isolated_start)

    def version_probe(args, **kwargs):
        assert len(args) == 2 and args[1] == "-version"
        return subprocess.CompletedProcess(args, 0, b"test ffmpeg", b"")

    monkeypatch.setattr(subprocess, "run", version_probe)
    return layers


def test_detector_order_is_load_bearing():
    # StarGrabDetector leads because star_collected is HELD and describes a
    # frame already past — published after a same-tick reset it would leave
    # the reset holding the attempt the grab belongs to (live report
    # 2026-08-01; the behaviour itself is pinned by
    # tests/test_reset_during_star_grab.py, this is only the wiring).
    # WarpDetector joins it 2026-08-04 (task 0081) for the same reason: it
    # HOLDS the entrance touch until a level or area edge names where the
    # entrance led, so a released touch describes a frame 77 in the past. Ahead
    # of LevelChangeDetector it closes the movement it belongs to; behind it,
    # the level change closes that attempt first and one movement records as
    # two.
    order = ["StarGrabDetector", "WarpDetector", "GameResetDetector",
             "LevelChangeDetector", "AnchorDetector", "DeathDetector"]
    wired = [type(detector).__name__ for detector in build_detectors()]
    positions = [wired.index(name) for name in order]
    assert positions == sorted(positions)


def test_the_moment_detector_runs_behind_the_held_emitters():
    # A moment is emitted on the frame it HAPPENED, so it is not a held
    # event and must not jump ahead of one. The two held emitters publish
    # the past; everything describing the present follows them.
    wired = [type(detector).__name__ for detector in build_detectors()]
    assert wired.index("MomentDetector") > wired.index("StarGrabDetector")
    assert wired.index("MomentDetector") > wired.index("WarpDetector")


def test_the_moment_detector_takes_the_live_target_predicate():
    """The SEAM, not a live rule. `build_detectors()` with no argument is
    permissive, which is what the composition root now uses (the task-0087
    target gate was retired 2026-08-06 — see the guard below). A caller that
    wants to narrow when a moment records still has somewhere to inject it,
    which is the only reason the parameter outlived the rule."""
    from sm64_events.main import build_detectors as build

    permissive = next(d for d in build()
                      if type(d).__name__ == "MomentDetector")
    assert permissive._target_active() is True

    gated = next(d for d in build(target_active=lambda: False)
                 if type(d).__name__ == "MomentDetector")
    assert gated._target_active() is False


def test_the_composition_root_gates_moments_on_NOTHING():
    """REVERSED 2026-08-06, and stated as a reversal rather than deleted: this
    asserted `build_detectors(target_active=` was in main.py, wiring the live
    target into the gate (task 0087).

    The recorder is what consumes moments and it is used with NO target set --
    pointing at what you just did is HOW a definition gets made -- so the gate
    made the builder blind in the one situation it exists for. Two live reports
    in one message, one cause: "I went into Whomp's Fortress, triggered the
    Whomp King dialogue, and now nothing popped up in the segment recorder
    tool" and "briefly I was able to detect the doors in HMC, but... I lost the
    ability to detect those" (2026-08-06). His journal scored it exactly -- 207
    moments, all inside target windows, then a whole session across three
    levels with zero of any kind.

    A recorder-OPEN gate is not the answer either and this guard is where that
    is written down: he does the thing first and opens the recorder afterwards,
    so detection has to have already happened.
    """
    src = strip_comments((Path(sm64_events.__file__).parent
                          / "main.py").read_text(encoding="utf-8"))
    # The PARAMETER survives as an injection seam (see build_detectors' own
    # docstring), so the scan reads the CALLS and skips the definition — the
    # thing that may not come back is main wiring the live target into it.
    calls = [line.strip() for line in src.splitlines()
             if "build_detectors(" in line and not line.lstrip().startswith("def ")]
    assert calls, "nothing in main.py builds the detector chain any more"
    # `version=` is the other seam (2026-08-15) and IS wired; the target is
    # what may not be.
    assert all("target_active" not in call for call in calls), (
        "the composition root must not gate moments on the practice target — "
        "the recorder is used with no target set, and gating makes it blind "
        f"exactly then. Calls: {calls}")


def test_stage_detector_is_wired():
    src = (Path(sm64_events.__file__).parent / "main.py").read_text(encoding="utf-8")
    # rindex skips the alphabetical import line in favour of the last
    # occurrence, which is inside the detectors = [...] list.
    assert src.rindex("StageChangeDetector()") > src.rindex("detectors = [")


def test_app_is_lazy_not_built_at_import():
    src = (Path(sm64_events.__file__).parent / "main.py").read_text(
        encoding="utf-8")
    # No eager module-level build (which would acquire the instance lock);
    # the app is provided lazily via module __getattr__.
    assert "\napp = build()" not in src
    assert "__getattr__" in src


def test_get_app_builds_once(monkeypatch):
    import importlib

    import sm64_events.main as main_mod
    importlib.reload(main_mod)

    calls = []

    def fake_build():
        from fastapi import FastAPI
        calls.append(True)
        return FastAPI()

    monkeypatch.setattr(main_mod, "build", fake_build)
    a1 = main_mod.get_app()
    a2 = main_mod.get_app()
    assert a1 is a2
    assert calls == [True]


def test_build_gives_the_poller_the_trackers_frame_heartbeat(monkeypatch):
    """The wiring itself, because nothing else can fail if it goes missing.

    A topological cancel is decided at the move and delivered by
    `TrackerService.settle_frame`; the poller's clock is what calls it (live
    report 2026-08-02, a verdict 27.7 s late because the journal happened to
    be quiet). Unwired, every unit below still passes and the symptom comes
    back live — the same shape as the config knob that only worked when the
    documented launch command executed it."""
    from sm64_events.tracking.service import TrackerService
    main_mod = _stubbed_main(monkeypatch)
    captured = {}
    real_poller = main_mod.Poller

    def spy(memory, detectors, sink, **kw):
        captured.update(kw, sink=sink)
        return real_poller(memory, detectors, sink, **kw)

    monkeypatch.setattr(main_mod, "Poller", spy)
    main_mod.build()
    heartbeat = captured.get("on_frame")
    assert heartbeat is not None, "the poller was built with no frame hook"
    assert heartbeat.__self__ is captured["sink"]
    assert heartbeat.__func__ is TrackerService.settle_frame


def test_build_wires_the_pad_stamp_audit_into_replay(monkeypatch):
    """The ONE hook ReplayService still takes, and the only thing that makes
    the timeline's pad-check chip appear. Unit tests below the composition
    root pass even if it is never assigned, so pin the seam explicitly.

    Six sibling hooks used to be pinned here (the footage aligner, the pad
    reader, the clock join, the map quantiser, the digit refit, the ledger
    mapper) -- one per generation of DERIVED frame map. The capture layer
    stamps the frame instead, and they were deleted 2026-09-05."""
    main_mod = _stubbed_main(monkeypatch)
    captured = {}
    real_replay_service = main_mod.ReplayService

    def spy(*args, **kwargs):
        built = real_replay_service(*args, **kwargs)
        captured["replay"] = built
        return built

    monkeypatch.setattr(main_mod, "ReplayService", spy)
    main_mod.build()

    replay = captured.get("replay")
    assert replay is not None, "the enabled replay branch was not composed"
    assert replay.track_pads is not None, (
        "the pad-stamp audit was built but never wired into ReplayService")


def _stubbed_main(monkeypatch):
    """A freshly reloaded `main` with everything build() would really touch
    stubbed out. Every stub here is load-bearing; the reason is on its line."""
    # No real file lock, and no ~100 ms NVENC probe.
    monkeypatch.setattr(
        "sm64_events.storage.instance_lock.acquire_instance_lock",
        lambda path: object())
    monkeypatch.setattr(
        "sm64_events.replay.encoder.pick_video_codec", lambda: "libx264")

    class _DbStub:
        # TrackerService loads segment defs and the time_filters KV eagerly;
        # build() also runs the defaults reconcile, which reads routes() and
        # inserts every bundled segment against an empty segment_defs().
        def segment_defs(self):
            return []

        def get_state(self, key, default):
            return default

        def set_state(self, key, value):
            # Sheet catalog provisioning persists ownership of generated IDs.
            pass

        def routes(self):
            return []

        def insert_segment_def(self, *args, **kwargs):
            return 1

        # build() wires the input timeline on every layout, so the stub
        # carries the stores and journal readers it asks for -- unreachable
        # here, never read (events_between/landmark_names feed the timeline's
        # moment markers, round 32 item 3).
        inputs = None
        input_templates = None
        events_between = None
        landmark_names = None

        def attempts(self):
            return []

    import importlib
    import sm64_events.main as main_mod
    importlib.reload(main_mod)
    # Patched at the name main.py imported it under, so service.py's own
    # already-imported annotation is unaffected.
    monkeypatch.setattr(main_mod, "Database", lambda path: _DbStub())
    monkeypatch.setattr(main_mod, "_game_version", lambda: "us")
    monkeypatch.setattr(main_mod, "configure_logging", lambda: None)
    monkeypatch.setattr(main_mod, "migrate_legacy_data_dir", lambda: None)
    return main_mod


def test_build_wires_replay_endpoints(monkeypatch):
    main_mod = _stubbed_main(monkeypatch)
    app = main_mod.build()
    paths = {r.path for r in app.routes}
    assert "/api/replay/status" in paths
    assert "/api/replay/clips/{name}" in paths


def test_only_the_recorder_owner_can_open_or_reconfigure_the_frame_stream(
        monkeypatch, _isolate_machine):
    from threading import Event
    from types import SimpleNamespace
    from test_replay_recorder import FakeAudioSource, FakeAvSink, FakeVideoSource, WIN

    main_mod = _stubbed_main(monkeypatch)
    writes, streams, recorders = [], [], []
    held = False
    read_entered, read_finish, close_entered = Event(), Event(), Event()

    class Lease:
        def close(self):
            nonlocal held
            assert streams[-1].closed  # close mapping before another owner wins
            held = False

    def acquire():
        nonlocal held
        if held:
            return None
        held = True
        return Lease()

    class Stream:
        def __init__(self):
            assert held
            self.closed = False
            self.block_header = False
            streams.append(self)

        def set_table(self, entries, **kwargs):
            assert held
            writes.append(entries)

        def header(self):
            assert not self.closed
            if self.block_header:
                read_entered.set()
                assert read_finish.wait(5)
                assert not self.closed
            return SimpleNamespace(initiated=False)

        def set_want_frames(self, on):
            assert held and not on

        def close(self):
            close_entered.set()
            self.closed = True

    real_recorder = main_mod.ReplayRecorder

    def recorder(**kwargs):
        kwargs.update(recorder_lock_factory=acquire,
                      audio_factory=lambda pid: FakeAudioSource(),
                      video_sink_factory=lambda *args: FakeAvSink())
        result = real_recorder(**kwargs)
        recorders.append(result)
        return result

    monkeypatch.setattr(main_mod, "ReplayRecorder", recorder)
    monkeypatch.setattr("sm64_events.replay.framestream.FrameStream", Stream)
    monkeypatch.setattr("sm64_events.replay.pluginsource.table_for",
                        lambda layout: [("global_timer", 4 * (len(recorders) + 1), 4)])
    monkeypatch.setattr("sm64_events.replay.pluginsource.DesktopUntilLayerPresents",
                        lambda *args, **kwargs: FakeVideoSource())
    monkeypatch.setattr(main_mod, "DwmSurfaceVideoSource", lambda *a, **kw: FakeVideoSource())
    main_mod.build()
    main_mod.build()
    assert streams == [] and writes == []
    first, second = recorders
    try:
        first._begin_capture(WIN)
        assert writes == [[(4, 4)]]
        second._begin_capture(WIN)
        second._teardown_capture()
        assert len(streams) == 1 and not streams[0].closed
        assert writes == [[(4, 4)]]
        # Pause a real status read inside header() while teardown races it.
        # The mapping must remain open until that read returns.
        streams[0].block_header = True
        _close_during_status_read(_isolate_machine[0].header, first._teardown_capture,
                                  read_entered, read_finish, close_entered)
        assert _isolate_machine[0].header() is None
        second._begin_capture(WIN)
        assert writes == [[(4, 4)], [(8, 4)]]
    finally:
        first.stop()
        second.stop()


def _close_during_status_read(header, teardown, read_entered, read_finish, close_entered):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        reading = pool.submit(header)
        assert read_entered.wait(5)
        closing = pool.submit(teardown)
        try:
            assert not close_entered.wait(0.1)
        finally:
            read_finish.set()
        assert reading.result(timeout=5).initiated is False
        closing.result(timeout=5)


def test_build_joins_the_boundary_hook_to_the_moment_detector(monkeypatch):
    """An ordinal means "the Nth since this attempt opened". The service sees
    every event and the detector sees only snapshots, so if build() does not
    join them the counter never restarts -- and a subsection pinned to an
    ordinal matches the first run and never again.

    Driven through the REAL build() rather than a source scan, for the same
    reason the frame-heartbeat test above is: a scan passes on a line that
    has been commented out, moved into a branch that never runs, or wired to
    a different detector instance than the poller got."""
    main_mod = _stubbed_main(monkeypatch)
    captured = {}
    real_poller = main_mod.Poller

    def spy(memory, detectors, sink, **kw):
        captured["detectors"] = detectors
        captured["sink"] = sink
        return real_poller(memory, detectors, sink, **kw)

    monkeypatch.setattr(main_mod, "Poller", spy)
    main_mod.build()

    hook = captured["sink"].on_attempt_boundary
    assert hook is not None, "the service was built with no boundary hook"
    # BOTH moment detectors count ordinals (Mario-action moments and caused
    # moments), so the hook must clear both — and it must clear the INSTANCES
    # the poller got, not another pair's, which would leave the running
    # counters untouched while looking correct. Proven by driving them: seed
    # each counter directly, fire the hook, require both empty.
    moment = next(d for d in captured["detectors"]
                  if type(d).__name__ == "MomentDetector")
    caused = next(d for d in captured["detectors"]
                  if type(d).__name__ == "CausedMomentDetector")
    moment._counts["door_open"] = 4
    caused._counts["switch_press"] = 2
    hook()
    assert moment._counts == {} and caused._counts == {}
def test_dbless_boot_still_mounts_compare_and_compilation(monkeypatch):
    """A boot that loses the instance-lock race (the reload-handoff race,
    live 2026-07-30 and 2026-08-06) must still mount the compare and
    compilation routers. Both services read tracker.db per call and answer
    503 "database unavailable" until the reattach loop lands the db — but a
    router that was never mounted 404s forever, which the Compare tab
    swallows silently: the whole tab reads as "videos don't load" with
    nothing in any log (task 0053)."""
    monkeypatch.setattr(
        "sm64_events.storage.instance_lock.acquire_instance_lock",
        lambda path: None)                    # lock held elsewhere: db-less boot
    monkeypatch.setattr(
        "sm64_events.replay.encoder.pick_video_codec", lambda: "libx264")
    import importlib
    import sm64_events.main as main_mod
    importlib.reload(main_mod)
    main_mod = _stubbed_main(monkeypatch)
    monkeypatch.setattr(main_mod, "acquire_instance_lock", lambda path: None)
    # ffmpeg must "exist" for the importer; a fake path is fine — the replay
    # sink's -version probe fails closed to the in-process encoder.
    monkeypatch.setattr(main_mod, "bundled_ffmpeg",
                        lambda: "C:/nonexistent/ffmpeg.exe")
    app = main_mod.build()
    paths = {r.path for r in app.routes}
    assert "/api/compare/view" in paths
    assert "/api/compilation" in paths
    from fastapi.testclient import TestClient
    r = TestClient(app).get("/api/compare/view",
                            params={"entity": "star:16:0", "strat": None})
    assert r.status_code == 503              # honest "not yet", never a 404

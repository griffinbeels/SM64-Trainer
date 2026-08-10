# tests/test_composition.py
"""Composition-root contracts the runtime depends on (see build_detectors'
own docstring: the held grab precedes everything that closes an attempt;
level_changed precedes anchors)."""
from pathlib import Path

import sm64_events
from sm64_events.main import build_detectors
from source_scan import strip_comments


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
    assert all(call.endswith("build_detectors()") for call in calls), (
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

        def routes(self):
            return []

        def insert_segment_def(self, *args, **kwargs):
            return 1

    import importlib
    import sm64_events.main as main_mod
    importlib.reload(main_mod)
    # Patched at the name main.py imported it under, so service.py's own
    # already-imported annotation is unaffected.
    monkeypatch.setattr(main_mod, "Database", lambda path: _DbStub())
    return main_mod


def test_build_wires_replay_endpoints(monkeypatch, tmp_path):
    # Stub instance lock so build() doesn't acquire a real file lock.
    monkeypatch.setattr(
        "sm64_events.storage.instance_lock.acquire_instance_lock",
        lambda path: object())
    # Stub pick_video_codec so build() skips the ~100 ms NVENC probe.
    monkeypatch.setattr(
        "sm64_events.replay.encoder.pick_video_codec",
        lambda: "libx264")
    # Patch Database at the name main.py imported it under so the type
    # annotation in service.py (which already imported the real class) is
    # unaffected.  Return a sentinel stub; TrackerService accepts db=None
    # too, but a truthy object exercises the normal path.
    # TrackerService.__init__ now loads segment defs eagerly, so the stub
    # must answer segment_defs(). It also now reads the time_filters KV
    # (via _time_filters()) up front, so the stub must answer get_state()
    # too — mirroring Database.get_state's default-passthrough contract.
    # build() also runs the editable-defaults seed reconcile (spec
    # 2026-07-23-default-routes-foundation) before constructing the service,
    # so the stub must answer routes() (reconcile reads it unconditionally)
    # and insert_segment_def() (every bundled segment reads as "missing"
    # against an empty segment_defs() stub, so reconcile inserts all of them).
    import importlib
    import sm64_events.main as main_mod
    importlib.reload(main_mod)

    class _DbStub:
        def segment_defs(self):
            return []

        def get_state(self, key, default):
            return default

        def routes(self):
            return []

        def insert_segment_def(self, *args, **kwargs):
            return 1

    monkeypatch.setattr(main_mod, "Database", lambda path: _DbStub())
    app = main_mod.build()
    paths = {r.path for r in app.routes}
    assert "/api/replay/status" in paths
    assert "/api/replay/clips/{name}" in paths


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

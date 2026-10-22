"""Actual observer, cached intent probes and real SDK envelopes; no live devices."""
import asyncio
from contextlib import asynccontextmanager
import json
import threading
import time
from types import SimpleNamespace as NS

import pytest
from sentry_sdk.envelope import Envelope

from sm64_events.core import error_reporting as reporting
from sm64_events.core.diagnostic_snapshot import DiagnosticSnapshot
from sm64_events.core.silent_failures import MESSAGES, SilentFailureMonitor
from sm64_events.core.setup_runtime import SetupRuntime
from sm64_events.ghost.native_session import NativeGhost
from sm64_events.server.diagnostics_runtime import make_snapshot, with_diagnostics


def sample():
    return {"game": {"attached": True, "paused": False, "held": False, "frame": 0, "scope": 1},
            "capture": {"recording_enabled": True, "paused": False, "gated": False,
                        "player_active": True, "storage_pressure": False, "pictures": 0, "scope": 1},
            "ghost": {"expected_display": True, "available": True, "state": 4, "decision": 2,
                      "display_pending": True,
                      "displayed": False, "progress": (4, 0, 2, 1, 1, 1), "scope": 1},
            "setup": {"pending": True, "installed": True, "running": True, "rom_supported": True,
                      "checks": {"game": True, "plugin": True, "inputs": True, "pictures": False}, "scope": 1}}


class Harness:
    def __init__(self):
        self.data, self.now, self.epoch, self.events = sample(), 0.0, 1, []
        self.watch = SilentFailureMonitor(lambda: self.data,
            reporter=lambda message, **fields: self.events.append((message, fields)),
            consent=lambda: self.epoch, clock=lambda: self.now, wall_clock=lambda: self.now,
            interval_s=1, grace_s=3, cooldown_s=20)

    def tick(self, *, count=1, refresh=True, moving=True, gap=1):
        for _ in range(count):
            self.now += gap
            if moving:
                self.data["game"]["frame"] += 30
            if refresh:
                for key in ("ghost", "setup"):
                    self.data[key]["observed_unix_s"] = self.now
            self.watch.step()


def test_three_genuine_stalls_report_once_and_use_fixed_safe_contexts():
    h = Harness()
    h.tick(count=4)
    assert not h.events
    h.tick()
    assert {row[0] for row in h.events} == set(MESSAGES.values())
    assert all(row[1]["expected_generation"] == 1 for row in h.events)
    assert all(row[1]["context"]["stalled_for_s"] == 3 for row in h.events)
    h.tick(count=10)
    assert len(h.events) == 3 and len(h.watch._pending) == len(h.watch._reported) == 3


@pytest.mark.parametrize(("section", "field", "value"), [
    ("game", "attached", False), ("game", "paused", True), ("game", "held", True),
    ("capture", "recording_enabled", False), ("capture", "paused", True),
    ("capture", "gated", True), ("capture", "storage_pressure", True),
])
def test_pause_no_game_off_gated_or_storage_pressure_is_not_a_recording_failure(section, field, value):
    h = Harness()
    h.data[section][field] = value
    h.tick(count=8)
    assert MESSAGES["recording"] not in [row[0] for row in h.events]
    assert MESSAGES["setup"] not in [row[0] for row in h.events]


@pytest.mark.parametrize(("field", "value"), [
    ("expected_display", False), ("available", False), ("display_pending", False), ("displayed", True),
])
def test_ordinary_ghost_wait_scene_finished_hidden_or_absent_evidence_does_not_report(field, value):
    h = Harness()
    h.data["ghost"][field] = value
    h.tick(count=8)
    assert MESSAGES["ghost"] not in [row[0] for row in h.events]


@pytest.mark.parametrize("field", ["pending", "installed", "running", "rom_supported"])
def test_setup_requires_actual_incomplete_installed_supported_running_game(field):
    h = Harness()
    h.data["setup"][field] = False
    h.tick(count=8)
    assert MESSAGES["setup"] not in [row[0] for row in h.events]


def test_no_activity_stale_setup_unmounted_wizard_and_static_game_do_not_report():
    h = Harness()
    h.data["capture"]["player_active"] = False
    h.tick()
    h.tick(count=8, refresh=False)
    assert not h.events  # the ghost and wizard evidence expired too
    h = Harness()
    h.tick(count=8, moving=False)
    assert not h.events


def test_fresh_incomplete_setup_and_native_ready_in_an_inactive_static_menu_do_not_report():
    h = Harness()
    h.data["capture"]["player_active"] = False
    h.tick(count=10)  # wizard observations and global_timer stay fresh, as in the Usamune menu
    assert h.data["setup"]["pending"] and h.data["setup"]["observed_unix_s"] == h.now
    assert not h.events
    h.data["capture"]["player_active"] = True
    h.tick(count=4)
    assert {row[0] for row in h.events} == set(MESSAGES.values())


def test_preparation_and_activation_progress_and_source_changes_restart_grace():
    h = Harness()
    h.data["ghost"].update(state=3, decision=1, awaiting_preparation=True)
    h.tick(count=4)
    h.data["ghost"]["progress"] = (3, 0, 1, 2, 0, 2)
    h.data["capture"]["pictures"] = 1
    h.data["setup"]["checks"]["inputs"] = False
    h.tick()
    assert not h.events
    h.data["capture"]["scope"] = h.data["ghost"]["scope"] = h.data["setup"]["scope"] = 2
    h.tick(count=3)
    assert not h.events
    h.tick()
    assert {row[0] for row in h.events} == set(MESSAGES.values())


def test_sleep_missing_samples_and_consent_changes_cannot_backfill_an_old_wait():
    h = Harness()
    h.tick(count=4)
    h.tick(gap=50)
    assert not h.events
    h.epoch = None
    h.tick(count=4)
    h.epoch = 2
    h.tick(count=4)
    assert not h.events
    h.tick()
    assert len(h.events) == 3 and all(row[1]["expected_generation"] == 2 for row in h.events)


def native_probe(**changes):
    status = dict(available=True, state=4, reason=0, playback_reason=2, displayed=False,
                  observed_unix_s=100, producer=NS(configuration=2, epoch=3))
    status.update(changes)
    capture = NS(snapshot_status=lambda **_: status)
    native = NativeGhost(NS(recorder=NS(ghost_capture=capture)))
    native._source, native._target, native._origin = capture, ("star", "24-1"), {"kind": "star"}
    native._requested = native._enabled = True
    native._selected_asset = (object(),)
    native._controls = {"opacity": .7}
    return native, status


@pytest.mark.parametrize("change", ["hidden", "suspended", "no_origin", "no_asset", "opacity"])
def test_actual_native_intent_excludes_inapplicable_visibility(change):
    native, _ = native_probe()
    assert native.silent_failure_status()["expected_display"]
    if change == "hidden":
        native._hidden = True
    elif change == "suspended":
        native._owner_suspended = True
    elif change == "no_origin":
        native._origin = None
    elif change == "no_asset":
        native._selected_asset = None
    else:
        native._controls["opacity"] = 0
    assert not native.silent_failure_status()["expected_display"]


def test_actual_native_preparing_requires_armed_command_and_valid_origin():
    native, status = native_probe(state=3, playback_reason=1)
    native._enabled, native._selected_asset = False, None
    native._adaptive_generation, native._pending_asset = 7, (object(),)
    native._sent = {"enabled": False, "prepare_generation": 7}
    assert native.silent_failure_status()["awaiting_preparation"]
    native._preparation_canceled = True
    assert not native.silent_failure_status()["expected_display"]
    native._preparation_canceled = False
    status["reason"] = 10
    assert not native.silent_failure_status()["awaiting_preparation"]


@pytest.mark.parametrize("decision", [0, 1, 2])
def test_outer_ready_with_an_armed_command_reports_stalled_renderer_adoption_or_display(decision):
    native, status = native_probe(playback_reason=decision)
    native._sent = {"enabled": True}
    h = Harness()
    h.watch._probe = lambda: {**h.data, "ghost": native.silent_failure_status()}
    for _ in range(4):
        status["observed_unix_s"] = h.now + 1
        h.tick(refresh=False)
    assert not h.events
    status["observed_unix_s"] = h.now + 1
    h.tick(refresh=False)
    assert MESSAGES["ghost"] in [row[0] for row in h.events]


def test_model_adoption_progress_restarts_native_ghost_grace():
    native, status = native_probe(playback_reason=1, playback_state=1)
    h = Harness()
    h.watch._probe = lambda: {**h.data, "ghost": native.silent_failure_status()}
    for _ in range(4):
        status["observed_unix_s"] = h.now + 1
        h.tick(refresh=False)
    status["playback_state"] = 2
    for _ in range(3):
        status["observed_unix_s"] = h.now + 1
        h.tick(refresh=False)
    assert MESSAGES["ghost"] not in [row[0] for row in h.events]


@pytest.mark.parametrize("decision", [4, 5, 6, 7, 8, 10, 12])
def test_actual_native_scene_clock_finished_and_context_decisions_do_not_report(decision):
    native, _ = native_probe(playback_reason=decision)
    h = Harness()
    h.data["ghost"] = native.silent_failure_status()
    h.tick(count=8)
    assert MESSAGES["ghost"] not in [row[0] for row in h.events]


def test_setup_cache_completion_and_freshness_do_not_discover_or_attach():
    runtime = SetupRuntime(None, None, NS(), None)
    runtime._cached = {"target": {"state": "ready", "pid": 42}, "rom": {"state": "supported", "region": "us"},
                       "checks": {"plugin": False, "pictures": False, "inputs": True, "game": True}}
    runtime._diagnostic_observed_at = 100
    layer = {"installation_verified": True, "pj64_running": True}
    assert not runtime.silent_failure_status(layer)["pending"]
    runtime.note_setup_state({"started": True})
    assert runtime.silent_failure_status(layer)["pending"]
    runtime.note_setup_state({"started": True, "completed_at": "already complete"})
    assert not runtime.silent_failure_status(layer)["pending"]


def test_cached_composition_never_scans_native_or_logs_while_healthy():
    h = Harness()
    recorder = NS(silent_failure_status=lambda: h.data["capture"], diagnostic_status=lambda: {})
    poller = NS(memory=NS(attached=True), latest=NS(global_timer=1), paused=False, hold_reason=None)
    snapshot = make_snapshot(poller=poller, service=None, monitor=NS(latest={}), replay=NS(recorder=recorder),
        capture_layer=None, ghost=NS(silent_failure_status=lambda: {}), setup_observer=None, polling_state=lambda: {})
    snapshot._native = NS(snapshot=lambda *_: pytest.fail("healthy tick scanned native/logs"), close=lambda: None)
    watch = SilentFailureMonitor(snapshot.progress, reporter=lambda *_a, **_k: pytest.fail("healthy progress reported"),
        consent=lambda: 1, clock=lambda: h.now, wall_clock=lambda: h.now, grace_s=3)
    for _ in range(10):
        h.now += 1
        poller.latest.global_timer += 30
        h.data["capture"]["pictures"] += 1
        watch.step()
    snapshot.close()


@pytest.fixture
def reporter(tmp_path, monkeypatch):
    monkeypatch.setattr("sm64_events.core.error_reporting_transport.Delivery", lambda dsn: lambda payload: (False, 0))
    reporting.configure(dsn="https://public@example.invalid/123", release="synthetic-silent", environment="test", directory=tmp_path)
    yield tmp_path
    reporting.set_diagnostic_source(None)
    reporting.shutdown()


def events(path, *, count=1):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        rows = [Envelope.deserialize(p.read_bytes()).get_event() for p in path.glob("*.envelope")]
        if len(rows) >= count:
            return rows
        time.sleep(.01)
    return rows


def test_fresh_default_reports_use_real_sdk_snapshot_and_existing_optout_stays_private(reporter):
    assert reporting.status()["enabled"] is True
    h = Harness()
    h.data["capture"]["private"] = "PrivatePlayer private-token C:\\Users\\PrivateName"
    snapshot = DiagnosticSnapshot(
        capture=lambda: {"recorder": {"recording_enabled": True, "pictures_fed": 0}},
        ghost=lambda: {"expected_display": True, "overlay_hidden": False, "scope": "PrivatePlayer"},
        native=NS(snapshot=lambda _: {"available": False}, close=lambda: None))
    unbind = reporting.set_diagnostic_source(snapshot.collect)
    h.watch = SilentFailureMonitor(lambda: h.data, clock=lambda: h.now, wall_clock=lambda: h.now,
        interval_s=1, grace_s=3)
    try:
        h.tick(count=5)
        rows = events(reporter, count=3)
        assert len(rows) == 3 and {row["message"] for row in rows} == set(MESSAGES.values())
        assert rows[0]["extra"]["diagnostics"]["capture"]["recorder"]["pictures_fed"] == 0
        assert all(row["extra"]["diagnostics"]["ghost"]["expected_display"] is True for row in rows)
        assert "Private" not in json.dumps(rows) and "private-token" not in json.dumps(rows)
        before = reporting.collection_epoch()
        reporting.set_enabled(False)
        assert reporting.collection_epoch() is None
        reporting.set_enabled(True)
        assert reporting.capture_problem("Old epoch must not upload", expected_generation=before) is None
        reporting.set_enabled(False)
        h.tick(count=20)
        assert not list(reporter.glob("*.envelope"))
    finally:
        unbind()
        snapshot.close()


@pytest.mark.parametrize("preference", [{"enabled": False}, {"enabled": True}, "corrupt"])
def test_default_does_not_override_saved_optout_or_unreadable_choice(tmp_path, monkeypatch, preference):
    monkeypatch.setattr("sm64_events.core.error_reporting_transport.Delivery", lambda dsn: lambda payload: (False, 0))
    (tmp_path / "preference.json").write_text("{" if preference == "corrupt" else json.dumps(preference))
    try:
        reporting.configure(dsn="https://public@example.invalid/123", release="synthetic", environment="test", directory=tmp_path)
        assert reporting.status()["enabled"] is (preference == {"enabled": True})
    finally:
        reporting.shutdown()


def test_lifespan_stops_observer_before_capture_and_closes_snapshot_afterwards(monkeypatch):
    order = []
    class Watch:
        def __init__(self, _probe): pass
        def start(self): order.append("watch_start")
        def stop(self): order.append("watch_stop"); return True
    monkeypatch.setattr("sm64_events.server.diagnostics_runtime.SilentFailureMonitor", Watch)
    @asynccontextmanager
    async def base(_app):
        order.append("app_start")
        try:
            yield
        finally:
            order.append("capture_stop")
    snapshot = NS(has_progress=True, progress=lambda: {}, collect=lambda: {}, close=lambda: order.append("snapshot_close"))
    async def run():
        async with with_diagnostics(base, snapshot)(NS()):
            order.append("ready")
    asyncio.run(run())
    assert order == ["app_start", "watch_start", "ready", "watch_stop", "capture_stop", "snapshot_close"]


def test_inflight_report_owner_retains_snapshot_until_worker_finishes(monkeypatch):
    entered, release, closed = threading.Event(), threading.Event(), []
    def probe():
        entered.set()
        assert release.wait(3)
        return sample()
    watch = SilentFailureMonitor(probe, consent=lambda: 1, interval_s=.01)
    watch.start()
    join = watch._thread.join
    try:
        assert entered.wait(1)
        monkeypatch.setattr(watch._thread, "join", lambda timeout: join(.01))
        assert not watch.stop()
        watch.close_after_stop(lambda: closed.append(True))
        assert not closed
    finally:
        release.set()
        join(2)
    assert not watch._thread.is_alive() and closed == [True]


def test_recording_probe_is_cached_and_disabled_footage_does_not_request_media(tmp_path, monkeypatch):
    from test_ghost_recording_independence import cycle
    from test_ghost_recording import Source
    from test_replay_startup_order import make
    rec, video, audio, *_ = make(tmp_path)
    rec._ghost_capture_factory = Source
    rec.set_recording_enabled(False)
    cycle(rec)
    monkeypatch.setattr(rec._scratch, "prepare", lambda *_: pytest.fail("probe prepared scratch"))
    try:
        before = set(rec._cfg.scratch_dir.rglob("*"))
        result = rec.silent_failure_status()
        assert result["recording_enabled"] is False
        assert set(rec._cfg.scratch_dir.rglob("*")) == before
        assert result["pictures"] == 0 and video.starts == 0 and audio.on_pcm is None
    finally:
        rec.stop(cleanup=False)


def test_setup_entry_point_forwards_existing_completion_record_without_extra_reads():
    from sm64_events.core.modes import ModeConfig
    from sm64_events.server.setup_api import setup_json
    from test_onboarding import installed, observation
    notes, reads = [], []
    record = {"started": True, "completed_at": "complete"}
    class Observer:
        def __call__(self, _layer): return observation()
        def note_setup_state(self, value): notes.append(value)
    result = setup_json(ModeConfig(), NS(status=lambda: installed()), Observer(),
        NS(read=lambda: reads.append(True) or record))
    assert notes == [record] and reads == [True] and result["onboarding"] == record


def test_derived_intent_flags_are_strict_booleans_not_arbitrary_diagnostic_text():
    from sm64_events.core.diagnostic_privacy import safe_snapshot
    clean = safe_snapshot({"ghost": {"expected_display": "PrivatePlayer", "overlay_hidden": False,
                                      "owner_suspended": True, "scope": "private-scope"}})
    assert "expected_display" not in clean["ghost"]
    assert clean["ghost"]["overlay_hidden"] is False and clean["ghost"]["owner_suspended"] is True
    assert "Private" not in json.dumps(clean) and "scope" not in clean["ghost"]


def test_optout_during_blocked_snapshot_is_prompt_and_reenable_cannot_admit_old_event(reporter):
    entered, release, returned = threading.Event(), threading.Event(), threading.Event()
    results = []
    def evidence():
        entered.set()
        assert release.wait(3)
        return {}
    unbind = reporting.set_diagnostic_source(evidence)
    epoch = reporting.collection_epoch()
    worker = threading.Thread(target=lambda: results.append(
        reporting.capture_problem("Synthetic in-flight observation", expected_generation=epoch)), daemon=True)
    def toggle():
        reporting.set_enabled(False)
        reporting.set_enabled(True)
        returned.set()
    switcher = threading.Thread(target=toggle, daemon=True)
    worker.start()
    try:
        assert entered.wait(1)
        switcher.start()
        assert returned.wait(2), "opt-out waited behind diagnostic collection"
    finally:
        release.set()
        worker.join(3)
        switcher.join(3)
        unbind()
    assert not worker.is_alive() and not switcher.is_alive()
    assert results == [None] and not list(reporter.glob("*.envelope"))


@pytest.mark.parametrize("opted_out", [False, True])
def test_actual_startup_uses_default_on_without_overriding_a_previous_optout(tmp_path, monkeypatch, opted_out):
    from sm64_events.core import reporting_setup
    if opted_out:
        location = tmp_path / "error-reporting"
        location.mkdir()
        (location / "preference.json").write_text(json.dumps({"enabled": False}))
    monkeypatch.setattr("sm64_events.core.error_reporting_transport.Delivery", lambda dsn: lambda payload: (False, 0))
    monkeypatch.setattr(reporting_setup, "_started", False)
    monkeypatch.setattr(reporting_setup, "configured_dsn", lambda: "https://public@example.invalid/123")
    monkeypatch.setattr("sm64_events.core.paths.data_root", lambda: tmp_path)
    monkeypatch.setattr("sm64_events.core.paths.migrate_legacy_data_dir", lambda: None)
    monkeypatch.setattr("sm64_events.core.build_channel.current_build", lambda: NS(build_id="test", channel="beta"))
    try:
        reporting_setup.initialize_reporting()
        assert reporting.status()["configured"] and reporting.status()["enabled"] is not opted_out
    finally:
        reporting.shutdown()

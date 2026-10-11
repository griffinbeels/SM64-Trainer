"""Frozen self-check contracts; disposable files, no packaged app or workers."""
import builtins
from dataclasses import replace
import json
import struct
import sys
from types import SimpleNamespace

import pytest

from sm64_events.core.build_channel import BETA_APP_DIR, BETA_PORT, BuildChannel
from sm64_events.desktop import beta_probe as probe
from test_beta_channel import metadata


def pe(machine, *, dll):
    result = bytearray(128)
    result[:2] = b"MZ"
    struct.pack_into("<I", result, 60, 64)
    result[64:68] = b"PE\0\0"
    struct.pack_into("<H", result, 68, machine)
    struct.pack_into("<H", result, 86, 0x2000 if dll else 0)
    return bytes(result)


@pytest.fixture
def runtime(tmp_path):
    build = BuildChannel(**{key: value for key, value in metadata().items() if key != "schema"})
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for relative in probe._ASSETS:
        path = bundle / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"disposable asset")
    (bundle / "build-channel.json").write_text(json.dumps(build.metadata()), encoding="utf-8")
    for name, machine in probe._NATIVES:
        path = bundle / "sm64_events/data/plugin" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pe(machine, dll=True))
    (bundle / "ffmpeg.exe").write_bytes(pe(0x8664, dll=False))
    local = tmp_path / "local"
    paths = SimpleNamespace(
        APP_DIR_NAME=BETA_APP_DIR, APP_DISPLAY_NAME=build.display_name,
        data_root=lambda: local / BETA_APP_DIR, practice_root=lambda: None,
        server_port=lambda: BETA_PORT,
        capture_layer_settings_path=lambda: local / "SM64Trainer/data/capture_layer.json",
        bundled_ffmpeg=lambda: str(bundle / "ffmpeg.exe"),
        bundled_plugin_dll=lambda: bundle / "sm64_events/data/plugin/sm64_trainer_gfx.dll",
        bundled_renderer_dll=lambda: bundle / "sm64_events/data/plugin/GLideN64_SM64Trainer.dll",
        bundled_encoder_dll=lambda: bundle / "sm64_events/data/plugin/SM64GpuEncoderV1.dll",
    )
    return probe.ProbeRuntime(build, bundle, tmp_path / (BETA_APP_DIR + ".exe"),
                              local, paths, lambda: {"state": "disabled"})


def importer(name):
    return SimpleNamespace(**{member: lambda: None for module, members in probe._IMPORTS
                              if module == name for member in members})


def test_complete_fixture_has_bounded_token_free_receipt_and_no_profile_writes(runtime):
    before = {str(path): path.read_bytes() for path in runtime.bundle.rglob("*") if path.is_file()}
    report = probe.collect_report(fixture=runtime, importer=importer)
    assert report["ok"]
    assert report["channel"] == "beta" and report["port"] == BETA_PORT
    assert report["build_id"] == runtime.build.build_id
    assert not runtime.local_appdata.exists()
    assert before == {str(path): path.read_bytes() for path in runtime.bundle.rglob("*") if path.is_file()}
    text = json.dumps(report)
    assert runtime.build.supabase_publishable_key not in text
    assert runtime.build.community_url not in text
    assert "helper execution" in report["not_exercised"]


@pytest.fixture
def normal_runtime(runtime):
    build = replace(runtime.build, installation="normal")
    (runtime.bundle / "build-channel.json").write_text(json.dumps(build.metadata()), encoding="utf-8")
    runtime.paths.APP_DIR_NAME = build.app_dir_name
    runtime.paths.APP_DISPLAY_NAME = build.display_name
    runtime.paths.data_root = lambda: runtime.local_appdata / build.app_dir_name
    runtime.paths.server_port = lambda: build.default_port
    return replace(runtime, build=build, executable=runtime.executable.with_name("SM64Trainer.exe"),
                   update_status=lambda: {"updates_enabled": True, "state": "idle"})


def test_normal_beta_verifies_every_asset_native_import_and_shared_runtime_identity(normal_runtime):
    report = probe.collect_report(fixture=normal_runtime, importer=importer)
    assert report["ok"] and report["installation"] == "normal"
    assert report["profile_directory"] == "SM64Trainer" and report["port"] == 8064
    assert report["updates_enabled"] is True
    checked = {row["name"] for row in report["checks"] if row["ok"]}
    assert {"normal_profile", "normal_port", "public_updates_enabled"} <= checked
    assert all("asset:" + name in checked for name in probe._ASSETS)
    assert all("native:" + name in checked for name, _ in probe._NATIVES)
    assert all("import:" + name in checked for name, _ in probe._IMPORTS)
    assert not normal_runtime.local_appdata.exists()


@pytest.mark.parametrize("fault", ["legacy_exe", "legacy_profile", "legacy_port", "disabled_updates"])
def test_normal_beta_refuses_legacy_runtime_leaks(normal_runtime, fault):
    if fault == "legacy_exe":
        normal_runtime = replace(normal_runtime, executable=normal_runtime.executable.with_name("SM64TrainerBeta.exe"))
    elif fault == "legacy_profile":
        normal_runtime.paths.data_root = lambda: normal_runtime.local_appdata / BETA_APP_DIR
    elif fault == "legacy_port":
        normal_runtime.paths.server_port = lambda: BETA_PORT
    else:
        normal_runtime = replace(normal_runtime, update_status=lambda: {"updates_enabled": False})
    assert not probe.collect_report(fixture=normal_runtime, importer=importer)["ok"]


@pytest.mark.parametrize("problem", ["public-profile", "public-port", "updates", "native", "native-resolver", "shared-metadata", "asset", "twitch-asset", "helper"])
def test_missing_or_incorrect_frozen_contract_fails(runtime, problem):
    if problem == "public-profile":
        runtime.paths.data_root = lambda: runtime.local_appdata / "SM64Trainer"
    elif problem == "public-port":
        runtime.paths.server_port = lambda: 8064
    elif problem == "updates":
        object.__setattr__(runtime, "update_status", lambda: {"state": "offer"})
    elif problem == "native":
        (runtime.bundle / "sm64_events/data/plugin/SM64GpuEncoderV1.dll").write_bytes(pe(0x14C, dll=True))
    elif problem == "native-resolver":
        runtime.paths.bundled_encoder_dll = lambda: runtime.bundle / "wrong.dll"
    elif problem == "shared-metadata":
        runtime.paths.capture_layer_settings_path = lambda: runtime.local_appdata / BETA_APP_DIR / "capture_layer.json"
    elif problem == "asset":
        (runtime.bundle / "sm64_events/ui/assets/community/google-g.png").unlink()
    elif problem == "twitch-asset":
        (runtime.bundle / "sm64_events/ui/assets/community/twitch-glitch-white.svg").unlink()

    def load(name):
        if problem == "helper" and name.endswith("process_worker"):
            raise RuntimeError("private transport details must not appear in a report")
        return importer(name)

    report = probe.collect_report(fixture=runtime, importer=load)
    assert not report["ok"]
    assert any(not check["ok"] for check in report["checks"])
    assert "private transport details" not in json.dumps(report)
    assert not runtime.local_appdata.exists()


def test_default_probe_refuses_source_before_paths_community_or_helper_imports(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    def forbidden(*args):
        pytest.fail("A refused source probe imported runtime dependencies")
    monkeypatch.setattr(probe.importlib, "import_module", forbidden)
    report = probe.collect_report()
    assert not report["ok"] and report["checks"] == [{"name": "frozen_beta_identity", "ok": False}]


def test_stable_identity_is_refused_even_in_explicit_fixture(runtime):
    object.__setattr__(runtime, "build", BuildChannel())
    report = probe.collect_report(fixture=runtime, importer=importer)
    assert not report["ok"]
    assert len(report["checks"]) == 1


def test_main_writes_only_explicit_existing_maintainer_json_path(monkeypatch, tmp_path):
    local = tmp_path / "local"
    monkeypatch.setattr(probe, "_local_appdata", lambda: local)
    monkeypatch.setattr(probe, "collect_report", lambda: {"schema": 1, "ok": True})
    target = tmp_path / "receipt.json"
    assert probe.main([str(target)]) == 0
    assert json.loads(target.read_text()) == {"schema": 1, "ok": True}
    assert probe.main(["relative.json"]) == 2
    assert probe.main([str(tmp_path / "missing" / "receipt.json")]) == 2
    for profile in ("SM64Trainer", "SM64TrainerBeta", "sm64_tracker"):
        forbidden = local / profile / "receipt.json"
        forbidden.parent.mkdir(parents=True)
        assert probe.main([str(forbidden)]) == 2
        assert not forbidden.exists()
    assert probe.main([]) == 2
    assert probe.main([str(target), "extra"]) == 2


def test_failure_receipt_has_failure_exit_code(monkeypatch, tmp_path):
    monkeypatch.setattr(probe, "_local_appdata", lambda: tmp_path / "local")
    monkeypatch.setattr(probe, "collect_report", lambda: {"schema": 1, "ok": False})
    target = tmp_path / "failure.json"
    assert probe.main([str(target)]) == 2
    assert json.loads(target.read_text())["ok"] is False


def test_gui_dispatches_probe_before_desktop_and_worker_imports(monkeypatch, tmp_path):
    import gui_entry
    imported = []
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name in {"sm64_events.desktop.app", "sm64_events.library.background",
                    "sm64_events.replay.gpuprocess.process_worker", "sm64_events.ghost.sound_player"}:
            pytest.fail("Self-check imported a desktop or helper entry before dispatch")
        imported.append(name)
        return original(name, *args, **kwargs)

    seen = []
    monkeypatch.setattr(probe, "main", lambda args: seen.append(args) or 0)
    monkeypatch.setattr(sys, "argv", ["SM64TrainerBeta.exe", "--beta-self-check", str(tmp_path / "receipt.json")])
    monkeypatch.setattr(builtins, "__import__", guarded)
    assert gui_entry.main() == 0
    assert seen == [[str(tmp_path / "receipt.json")]]
    assert "sm64_events.desktop.beta_probe" in imported

"""A protected earlier install must visibly offer the gesture that can elevate."""
import logging
from pathlib import Path
import shutil

from sm64_events.core.capturelayer import WRAPPER_DLL
from test_capturelayer import (
    build, dll_source as dll_source, make_layer, pj64_dir as pj64_dir,
    settings_path as settings_path,
)


class TwoTicks:
    def __init__(self):
        self.turn = 0

    def wait(self, _seconds):
        self.turn += 1
        return self.turn > 2


def deny_wrapper(monkeypatch, directory):
    real_copy, attempts = shutil.copyfile, []

    def copy(source, destination, **kwargs):
        if Path(destination) == directory / "Plugin" / WRAPPER_DLL:
            attempts.append(destination)
            failure = PermissionError(13, "protected plugin folder", str(destination))
            failure.winerror = 5
            raise failure
        return real_copy(source, destination, **kwargs)

    monkeypatch.setattr(shutil, "copyfile", copy)
    monkeypatch.setattr("sm64_events.core.plugin_elevation.elevated_install",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("background UAC")))
    return attempts, real_copy


def test_refused_refresh_exposes_install_without_background_uac_or_error_storm(
    pj64_dir, dll_source, settings_path, monkeypatch, caplog,
):
    layer, _, _ = make_layer(pj64_dir, dll_source, settings_path)
    layer.install(True)
    previous = (pj64_dir / "Plugin" / WRAPPER_DLL).read_bytes()
    dll_source.write_bytes(build(b"new GPU support"))
    attempts, real_copy = deny_wrapper(monkeypatch, pj64_dir)
    logger = logging.getLogger("refresh-recovery")
    with caplog.at_level(logging.ERROR, logger.name):
        layer.refresh_loop(TwoTicks(), log=logger)
    assert len(attempts) == 1
    assert len([row for row in caplog.records if row.message == "capture layer refresh failed"]) == 1
    status = layer.status()
    assert not status.wrapper_current and not status.automatic_update
    assert any(step["action"] == "install" for step in status.steps)
    assert not any("automatically" in problem for problem in status.problems)
    assert (pj64_dir / "Plugin" / WRAPPER_DLL).read_bytes() == previous
    assert "protected plugin folder" in layer.diagnostic_status()["refresh_error"]

    monkeypatch.setattr(shutil, "copyfile", real_copy)
    assert layer.install(True).installation_verified
    assert layer.diagnostic_status()["refresh_error"] is None
    assert layer.status().automatic_update


def test_new_bundle_can_retry_after_an_earlier_refresh_refusal(
    pj64_dir, dll_source, settings_path, monkeypatch,
):
    layer, _, _ = make_layer(pj64_dir, dll_source, settings_path)
    layer.install(True)
    dll_source.write_bytes(build(b"first update"))
    attempts, real_copy = deny_wrapper(monkeypatch, pj64_dir)
    layer.refresh_loop(TwoTicks(), log=None)
    assert len(attempts) == 1 and not layer.status().automatic_update
    monkeypatch.setattr(shutil, "copyfile", real_copy)
    dll_source.write_bytes(build(b"subsequent update"))
    assert layer.refresh_if_stale()
    assert layer.status().installation_verified


def test_cached_setup_diagnostics_never_observe_or_modify_the_installation(
    pj64_dir, dll_source, settings_path, monkeypatch,
):
    layer, _, processes = make_layer(pj64_dir, dll_source, settings_path)
    assert layer.diagnostic_status() is None
    expected = layer.status().as_dict()
    before = settings_path.read_bytes()
    monkeypatch.setattr(layer, "status", lambda: (_ for _ in ()).throw(AssertionError("status read")))
    monkeypatch.setattr(layer, "refresh_if_stale", lambda: (_ for _ in ()).throw(AssertionError("refresh")))
    monkeypatch.setattr(processes, "pj64_image_path", lambda: (_ for _ in ()).throw(AssertionError("process discovery")))
    observed = layer.diagnostic_status()
    assert {key: observed[key] for key in expected} == expected
    observed["problems"].append("consumer cannot mutate retained state")
    assert layer.diagnostic_status()["problems"] == expected["problems"]
    assert settings_path.read_bytes() == before

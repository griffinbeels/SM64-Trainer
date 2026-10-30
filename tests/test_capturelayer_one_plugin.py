"""Project64 lists every DLL in its Plugin folder as a graphics plugin.

A beta.30 tester: "wait there is 2 graphics plugins now: sm64 trainer 1.0 and
link v4.2(sm64 trainer)". The renderer sat beside the wrapper as
GLideN64_SM64Trainer.dll, and selecting it skips capture. It is now installed
without a .dll extension, and older installs move by themselves. Fakes and
disposable folders only; no real Project64, registry or UAC.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil

import pytest

from sm64_events.core import plugin_elevation, plugin_elevation_worker
from sm64_events.core.capturelayer import (
    GRAPHICS_DLL_VALUE, LEGACY_RENDERER_DLL, NEEDS_RESTART, REGISTRY_DLL_SUBKEY, REGRESSED,
    RENDERER_FILE, WRAPPER_DLL, WRAPPER_INI,
)
from sm64_events.core.plugin_installation import PluginInstallError
from test_capturelayer import (
    ORIGINAL_GRAPHICS_DLL, build, dll_source as dll_source, make_layer, pj64_dir as pj64_dir,
    renderer_for, settings_path as settings_path,
)
from test_plugin_elevation import (
    deny_first_copy, install as install, privileged_request as privileged_request,
)

#: beta.30's renderer: built by this trainer (its build id carries the
#: -renderer role) and linked before the bundle, so moving it is no downgrade
OLDER_RENDERER = build(b"beta.30 renderer " + b"c" * 64 + b"-renderer\0", linked=1_000)


def assert_one_plugin(plugin: Path, bundled_renderer: Path, *others: str):
    """What Project64's picker lists, the ini the wrapper reads, the file it loads."""
    assert sorted(path.name for path in plugin.glob("*.dll")) == sorted([WRAPPER_DLL, *others])
    assert (plugin / WRAPPER_INI).read_text().strip() == f"wrapped={RENDERER_FILE}"
    assert (plugin / RENDERER_FILE).read_bytes() == bundled_renderer.read_bytes()


def older_install(pj64_dir, dll_source, settings_path, *, legacy_bytes=OLDER_RENDERER, **kwargs):
    """What beta.30 left: the renderer as GLideN64_SM64Trainer.dll, the ini naming it."""
    layer, registry, processes = make_layer(pj64_dir, dll_source, settings_path, **kwargs)
    layer.install(True)
    plugin = pj64_dir / "Plugin"
    (plugin / RENDERER_FILE).unlink()
    (plugin / LEGACY_RENDERER_DLL).write_bytes(legacy_bytes)
    (plugin / WRAPPER_INI).write_text(f"wrapped={LEGACY_RENDERER_DLL}\n")
    overlay = json.loads(settings_path.read_text())
    overlay["wrapped"] = LEGACY_RENDERER_DLL
    settings_path.write_text(json.dumps(overlay))
    return layer, registry, plugin


def snapshot(plugin: Path, *paths: Path) -> dict:
    return {path: path.read_bytes() for path in (*plugin.iterdir(), *paths)}


def test_a_fresh_install_lists_one_graphics_plugin(pj64_dir, dll_source, settings_path):
    layer, _, _ = make_layer(pj64_dir, dll_source, settings_path)
    assert layer.install(True).installation_verified
    assert_one_plugin(pj64_dir / "Plugin", renderer_for(dll_source))


@pytest.mark.parametrize("automatic", [True, False])
def test_an_older_install_reads_stale_and_moves_to_one_plugin(pj64_dir, dll_source, settings_path, automatic):
    """The packaged app moves it by itself once Project64 is closed; a source
    checkout offers Install. Neither calls it a broken setup."""
    layer, registry, plugin = older_install(pj64_dir, dll_source, settings_path)
    layer.auto_refresh = automatic
    status = layer.status()
    assert status.state == NEEDS_RESTART and not status.installation_verified
    assert status.automatic_update is automatic
    assert not any("instead of the trainer's renderer" in problem for problem in status.problems)
    assert any("differs from this build" in problem for problem in status.problems)
    assert any(step["action"] == "install" for step in status.steps) is not automatic
    if automatic:
        assert layer.refresh_if_stale() is True
    else:
        layer.install(True)
    assert_one_plugin(plugin, renderer_for(dll_source))
    assert registry.get(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE) == WRAPPER_DLL
    assert layer.status().installation_verified
    overlay = json.loads(settings_path.read_text())
    assert overlay["wrapped"] == RENDERER_FILE
    assert overlay["previous_graphics_dll"] == ORIGINAL_GRAPHICS_DLL
    assert layer.refresh_if_stale() is False
    layer.uninstall()
    assert registry.get(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE) == ORIGINAL_GRAPHICS_DLL


def test_an_older_renderer_left_beside_a_moved_install_is_not_current(pj64_dir, dll_source, settings_path):
    layer, _, _ = make_layer(pj64_dir, dll_source, settings_path)
    layer.install(True)
    plugin = pj64_dir / "Plugin"
    (plugin / LEGACY_RENDERER_DLL).write_bytes(OLDER_RENDERER)
    assert not layer.status().renderer_current
    assert layer.refresh_if_stale() is True
    assert_one_plugin(plugin, renderer_for(dll_source))
    assert layer.status().installation_verified


def test_a_directly_selected_older_renderer_stays_until_install(pj64_dir, dll_source, settings_path):
    """Removing the file Project64 selects would leave it naming a missing
    plugin. Refresh never changes the selection, so the file waits for Install."""
    layer, registry, plugin = older_install(pj64_dir, dll_source, settings_path)
    registry.set(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE, LEGACY_RENDERER_DLL)
    status = layer.status()
    assert status.state == REGRESSED
    assert any("another graphics plugin is selected" in problem for problem in status.problems)
    layer.refresh_if_stale()
    assert (plugin / LEGACY_RENDERER_DLL).read_bytes() == OLDER_RENDERER
    assert registry.get(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE) == LEGACY_RENDERER_DLL
    assert layer.refresh_if_stale() is False
    layer.install(True)
    assert registry.get(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE) == WRAPPER_DLL
    assert_one_plugin(plugin, renderer_for(dll_source))
    assert json.loads(settings_path.read_text())["previous_graphics_dll"] == ORIGINAL_GRAPHICS_DLL


@pytest.mark.parametrize("operation", ["refresh", "install"])
def test_a_users_own_file_at_the_older_name_is_left_alone(pj64_dir, dll_source, settings_path, operation):
    own = b"MZ the player's own GLideN64 build, no trainer build id"
    layer, _, plugin = older_install(pj64_dir, dll_source, settings_path, legacy_bytes=own)
    assert layer.refresh_if_stale() if operation == "refresh" else layer.install(True)
    assert (plugin / LEGACY_RENDERER_DLL).read_bytes() == own
    assert_one_plugin(plugin, renderer_for(dll_source), LEGACY_RENDERER_DLL)
    assert layer.status().installation_verified


@pytest.mark.parametrize("operation", ["refresh", "install"])
def test_a_failed_copy_puts_the_older_install_back(pj64_dir, dll_source, settings_path, monkeypatch, operation):
    layer, registry, plugin = older_install(pj64_dir, dll_source, settings_path)
    before = snapshot(plugin, settings_path)
    monkeypatch.setattr(shutil, "copyfile", lambda source, dest: Path(dest).write_bytes(b"wrong bytes"))
    with pytest.raises(OSError, match="verification"):
        layer.refresh_if_stale() if operation == "refresh" else layer.install(True)
    assert snapshot(plugin, settings_path) == before
    assert registry.get(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE) == WRAPPER_DLL


def test_a_refused_removal_rolls_back_the_move_and_the_selection(pj64_dir, dll_source, settings_path, monkeypatch):
    """The older DLL goes last, after Install selected the wrapper: a refusal
    there (and a declined UAC retry) leaves the folder and Project64's
    selection exactly as they were."""
    layer, registry, plugin = older_install(pj64_dir, dll_source, settings_path)
    registry.set(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE, ORIGINAL_GRAPHICS_DLL)
    before = snapshot(plugin, settings_path)
    real_unlink = Path.unlink

    def refused(path, *args, **kwargs):
        if path.name == LEGACY_RENDERER_DLL:
            raise PermissionError(13, "Access is denied", str(path))
        return real_unlink(path, *args, **kwargs)

    @contextmanager
    def declined(*_args):
        raise PluginInstallError("request administrator permission", plugin, OSError("declined"))
        yield  # pragma: no cover

    monkeypatch.setattr(Path, "unlink", refused)
    monkeypatch.setattr(plugin_elevation, "elevated_install", declined)
    with pytest.raises(PluginInstallError, match="declined"):
        layer.install(True)
    assert snapshot(plugin, settings_path) == before
    assert registry.get(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE) == ORIGINAL_GRAPHICS_DLL
    registry.set(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE, WRAPPER_DLL)
    before = snapshot(plugin, settings_path)
    with pytest.raises(OSError, match="Access is denied"):
        layer.refresh_if_stale()
    assert snapshot(plugin, settings_path) == before


def test_a_refresh_moves_the_per_user_configuration_too(pj64_dir, dll_source, settings_path, tmp_path, monkeypatch):
    appdata = tmp_path / "original-user"
    monkeypatch.setenv("LOCALAPPDATA", str(appdata))
    layer, _, plugin = older_install(pj64_dir, dll_source, settings_path)
    shadow = appdata / "VirtualStore" / plugin.relative_to(plugin.anchor) / WRAPPER_INI
    shadow.parent.mkdir(parents=True)
    shadow.write_text(f"wrapped={LEGACY_RENDERER_DLL}\n")
    assert layer.status().configuration_consistent
    assert layer.refresh_if_stale() is True
    assert shadow.read_text().strip() == f"wrapped={RENDERER_FILE}"
    assert_one_plugin(plugin, renderer_for(dll_source))
    assert layer.status().installation_verified


# -- the UAC helper reaches the same end state ---------------------------------

def plant_older_install(plugin: Path):
    (plugin / LEGACY_RENDERER_DLL).write_bytes(b"MZ" + b"3" * 64 + b"-renderer\0")
    (plugin / WRAPPER_INI).write_text(f"wrapped={LEGACY_RENDERER_DLL}\n")


@pytest.mark.skipif(os.name != "nt", reason="Windows handle pinning")
def test_the_privileged_helper_moves_an_older_install_and_rolls_it_back(privileged_request, install):
    plugin, renderer = install[3] / "Plugin", install[5]
    plant_older_install(plugin)
    before = {path.name: path.read_bytes() for path in plugin.iterdir()}
    with pytest.raises(OSError, match="parent settings failed"):
        with plugin_elevation_worker.privileged_files(privileged_request):
            raise OSError("parent settings failed")
    assert {path.name: path.read_bytes() for path in plugin.iterdir()} == before
    with plugin_elevation_worker.privileged_files(privileged_request):
        assert (plugin / LEGACY_RENDERER_DLL).exists()  # until the original user commits
    assert_one_plugin(plugin, renderer)


@pytest.mark.skipif(os.name != "nt", reason="Windows handle pinning")
def test_an_elevated_install_selects_the_wrapper_before_the_older_dll_goes(privileged_request, install, monkeypatch):
    layer, registry, _, pj64, source, renderer = install
    plugin = pj64 / "Plugin"
    plant_older_install(plugin)
    deny_first_copy(monkeypatch)
    seen = []

    @contextmanager
    def helper(*_args):
        with plugin_elevation_worker.privileged_files(privileged_request) as receipts:
            yield receipts
            seen.append(registry.selected)
        assert not (plugin / LEGACY_RENDERER_DLL).exists()

    monkeypatch.setattr(plugin_elevation, "elevated_install", helper)
    assert layer.install(True).installation_verified
    assert seen == [WRAPPER_DLL]
    assert_one_plugin(plugin, renderer)

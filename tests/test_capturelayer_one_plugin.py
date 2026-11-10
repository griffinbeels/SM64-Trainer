"""One graphics plugin file in Project64's Plugin folder, and the move to it.

Since 2026-11-07 the trainer installs `sm64_trainer_gfx.dll` alone: the
renderer and the capture layer in one file that Project64 loads itself.
Older installs put a wrapper under that name, which loaded the renderer named
by `sm64_trainer_gfx.ini`: `sm64_trainer_gfx.renderer` (from 2026-10-24) or
`GLideN64_SM64Trainer.dll` (before; a beta.30 tester saw "2 graphics plugins
now"). The plugin keeps the wrapper's name, so Project64's `Graphics Dll`
choice stays valid: the automatic refresh replaces the file under the consent
already given and deletes those leftovers, and nobody picks a plugin again.
Fakes and disposable folders only; no real Project64, registry or UAC.
"""
from contextlib import contextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil

import pytest

from sm64_events.core import plugin_elevation, plugin_elevation_worker
from sm64_events.core.capturelayer import (
    GRAPHICS_DLL_VALUE, LEFTOVERS, LEGACY_RENDERER_DLL, LEGACY_RENDERER_FILE, LEGACY_WRAPPER_INI,
    NEEDS_RESTART, PLUGIN_DLL, REGISTRY_DLL_SUBKEY, REGRESSED,
)
from sm64_events.core.capturelayer import CaptureLayer
from sm64_events.core.plugin_installation import PluginInstallError
from sm64_events.core.renderer_settings import RENDERER_INI
from sm64_events.core.update_plan import RETIRED_NATIVE_TEXT
from test_capturelayer import (
    CHECKOUT_A, FROZEN_APP, ORIGINAL_GRAPHICS_DLL, FakeProcesses, FakeRegistry, _as, build,
    dll_source as dll_source, held_like_a_loaded_plugin, make_layer, pj64_dir as pj64_dir,
    settings_path as settings_path,
)
from test_plugin_elevation import (
    deny_first_copy, install as install, privileged_request as privileged_request,
)

#: what today's trainer (and beta.30's) installed: built by this trainer, each
#: id carrying its role, linked before the bundle, so the move is no downgrade
OLD_WRAPPER = build(b"wrapper " + b"a" * 64 + b"-gpu-runtime\0", linked=1_000)
OLD_RENDERER = build(b"renderer " + b"c" * 64 + b"-renderer\0", linked=1_000)

#: the two older layouts: the renderer under a non-DLL name (2026-10-24 on),
#: and the renderer DLL beside the wrapper (before)
LAYOUTS = {"renderer_file": LEGACY_RENDERER_FILE, "renderer_dll": LEGACY_RENDERER_DLL}


def plugin_files(plugin: Path) -> list[str]:
    return sorted(path.name for path in plugin.iterdir())


def assert_one_plugin(plugin: Path, bundled: Path):
    """What Project64's picker lists and what the folder holds."""
    assert [path.name for path in plugin.glob("*.dll")] == [PLUGIN_DLL]
    assert (plugin / PLUGIN_DLL).read_bytes() == bundled.read_bytes()
    assert not set(plugin_files(plugin)) & set(LEFTOVERS)


def older_install(pj64_dir, dll_source, settings_path, *, renderer_name=LEGACY_RENDERER_FILE,
                  renderer_bytes=OLD_RENDERER, **kwargs):
    """What an older trainer left: its wrapper under the plugin's name, the
    renderer beside it, the ini naming it, and the overlay it wrote."""
    layer, registry, processes = make_layer(pj64_dir, dll_source, settings_path, **kwargs)
    layer.install(True)
    plugin = pj64_dir / "Plugin"
    (plugin / PLUGIN_DLL).write_bytes(OLD_WRAPPER)
    (plugin / renderer_name).write_bytes(renderer_bytes)
    (plugin / LEGACY_WRAPPER_INI).write_text(f"wrapped={renderer_name}\n")
    overlay = json.loads(settings_path.read_text())
    overlay.update(wrapped=renderer_name, owned_sha256=hashlib.sha256(OLD_WRAPPER).hexdigest(),
                   installed_renderer_sha256=hashlib.sha256(renderer_bytes).hexdigest(),
                   renderer_receipt={"reason": "explicit_install"})
    settings_path.write_text(json.dumps(overlay))
    return layer, registry, processes, plugin


def snapshot(plugin: Path, *paths: Path) -> dict:
    return {path: path.read_bytes() for path in (*plugin.iterdir(), *paths)}


def test_a_fresh_install_writes_one_plugin_and_its_settings(pj64_dir, dll_source, settings_path):
    layer, registry, _ = make_layer(pj64_dir, dll_source, settings_path)
    assert layer.install(True).installation_verified
    plugin = pj64_dir / "Plugin"
    assert plugin_files(plugin) == sorted([PLUGIN_DLL, RENDERER_INI])
    assert_one_plugin(plugin, dll_source)
    assert registry.get(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE) == PLUGIN_DLL


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("automatic", [True, False])
def test_an_older_install_reads_stale_and_moves_to_one_plugin(pj64_dir, dll_source, settings_path,
                                                             layout, automatic):
    """The packaged app moves it by itself once Project64 is closed; a source
    checkout offers Install. Neither calls it broken or asks for a new pick."""
    layer, registry, _, plugin = older_install(pj64_dir, dll_source, settings_path,
                                               renderer_name=LAYOUTS[layout])
    layer.auto_refresh = automatic
    status = layer.status()
    assert status.state == NEEDS_RESTART and not status.installation_verified
    assert status.wrapper_selected and not status.wrapper_current
    assert status.leftovers == [LEGACY_WRAPPER_INI, LAYOUTS[layout]]
    assert status.automatic_update is automatic
    assert any("differs from this build" in problem for problem in status.problems)
    assert not any("re-install" in problem or "missing" in problem for problem in status.problems)
    assert any(step["action"] == "install" for step in status.steps) is not automatic
    if automatic:
        assert layer.refresh_if_stale() is True
    else:
        layer.install(True)
    assert plugin_files(plugin) == sorted([PLUGIN_DLL, RENDERER_INI])
    assert_one_plugin(plugin, dll_source)
    assert registry.get(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE) == PLUGIN_DLL
    after = layer.status()
    assert after.installation_verified and after.leftovers == [] and after.problems == ["start Project64 to load the capture layer"]
    overlay = json.loads(settings_path.read_text())
    assert overlay["wrapped"] is None
    assert overlay["previous_graphics_dll"] == ORIGINAL_GRAPHICS_DLL
    assert "installed_renderer_sha256" not in overlay and "renderer_receipt" not in overlay
    assert layer.refresh_if_stale() is False
    layer.uninstall()
    assert registry.get(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE) == ORIGINAL_GRAPHICS_DLL


def test_a_refresh_removes_every_leftover_of_both_older_layouts(pj64_dir, dll_source, settings_path):
    layer, _, _, plugin = older_install(pj64_dir, dll_source, settings_path)
    (plugin / LEGACY_RENDERER_DLL).write_bytes(OLD_RENDERER)
    assert layer.status().leftovers == list(LEFTOVERS)
    assert layer.refresh_if_stale() is True
    assert plugin_files(plugin) == sorted([PLUGIN_DLL, RENDERER_INI])


def test_leftovers_beside_a_current_plugin_are_stale_on_their_own(pj64_dir, dll_source, settings_path):
    layer, _, _ = make_layer(pj64_dir, dll_source, settings_path)
    layer.install(True)
    plugin = pj64_dir / "Plugin"
    (plugin / LEGACY_RENDERER_FILE).write_bytes(OLD_RENDERER)
    status = layer.status()
    assert status.wrapper_current and not status.installation_verified
    assert any("differs from this build" in problem for problem in status.problems)
    assert layer.refresh_if_stale() is True
    assert plugin_files(plugin) == sorted([PLUGIN_DLL, RENDERER_INI])
    assert layer.status().installation_verified


@pytest.mark.parametrize("selected", [LEGACY_RENDERER_DLL, LEGACY_RENDERER_FILE])
def test_a_directly_selected_leftover_stays_until_install(pj64_dir, dll_source, settings_path, selected):
    """Removing the file Project64 selects would leave it naming a missing
    plugin. Refresh never changes the selection, so that file waits for Install."""
    layer, registry, _, plugin = older_install(pj64_dir, dll_source, settings_path, renderer_name=selected)
    registry.set(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE, selected)
    status = layer.status()
    assert status.state == REGRESSED
    assert any("another graphics plugin is selected" in problem for problem in status.problems)
    assert layer.refresh_if_stale() is True              # the plugin and the ini, never the selection
    assert (plugin / selected).read_bytes() == OLD_RENDERER
    assert plugin_files(plugin) == sorted([PLUGIN_DLL, RENDERER_INI, selected])
    assert registry.get(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE) == selected
    assert layer.refresh_if_stale() is False
    layer.install(True)
    assert registry.get(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE) == PLUGIN_DLL
    assert plugin_files(plugin) == sorted([PLUGIN_DLL, RENDERER_INI])
    assert json.loads(settings_path.read_text())["previous_graphics_dll"] == ORIGINAL_GRAPHICS_DLL


@pytest.mark.parametrize("operation", ["refresh", "install"])
def test_a_users_own_files_at_the_older_names_are_left_alone(pj64_dir, dll_source, settings_path, operation):
    own_renderer = b"MZ the player's own GLideN64 build, no trainer build id"
    layer, _, _, plugin = older_install(pj64_dir, dll_source, settings_path,
                                        renderer_name=LEGACY_RENDERER_DLL, renderer_bytes=own_renderer)
    own_ini = "; the player's notes\nwrapped=GLideN64_SM64Trainer.dll\n"
    (plugin / LEGACY_WRAPPER_INI).write_text(own_ini)
    assert layer.status().leftovers == []
    assert layer.refresh_if_stale() if operation == "refresh" else layer.install(True)
    assert (plugin / LEGACY_RENDERER_DLL).read_bytes() == own_renderer
    assert (plugin / LEGACY_WRAPPER_INI).read_text() == own_ini
    assert (plugin / PLUGIN_DLL).read_bytes() == dll_source.read_bytes()
    assert plugin_files(plugin) == sorted([PLUGIN_DLL, RENDERER_INI, LEGACY_RENDERER_DLL, LEGACY_WRAPPER_INI])
    assert layer.status().installation_verified


@pytest.mark.parametrize("installed_by, refreshing", [(CHECKOUT_A, FROZEN_APP), (FROZEN_APP, CHECKOUT_A)])
def test_ownership_still_decides_who_may_move_an_older_install(pj64_dir, dll_source, settings_path,
                                                              installed_by, refreshing):
    _, registry, processes, plugin = older_install(pj64_dir, dll_source, settings_path, installer=installed_by)
    before = snapshot(plugin, settings_path)
    other = _as(refreshing, registry, processes, settings_path, dll_source)
    status = other.status()
    assert status.automatic_update is False
    assert any(step["action"] == "install" for step in status.steps)
    assert other.refresh_if_stale() is False
    assert snapshot(plugin, settings_path) == before


def test_a_replaced_wrapper_is_not_moved_by_itself(pj64_dir, dll_source, settings_path):
    """A wrapper someone copied in since the install transfers no consent."""
    layer, _, _, plugin = older_install(pj64_dir, dll_source, settings_path)
    (plugin / PLUGIN_DLL).write_bytes(build(b"wrapper " + b"e" * 64 + b"-gpu-runtime\0", linked=1_500))
    before = snapshot(plugin, settings_path)
    assert layer.status().automatic_update is False
    assert layer.refresh_if_stale() is False
    assert snapshot(plugin, settings_path) == before


@pytest.mark.parametrize("operation", ["refresh", "install"])
def test_a_failed_copy_puts_the_older_install_back(pj64_dir, dll_source, settings_path, monkeypatch, operation):
    layer, registry, _, plugin = older_install(pj64_dir, dll_source, settings_path)
    before = snapshot(plugin, settings_path)
    monkeypatch.setattr(shutil, "copyfile", lambda source, dest: Path(dest).write_bytes(b"wrong bytes"))
    with pytest.raises(OSError, match="verification"):
        layer.refresh_if_stale() if operation == "refresh" else layer.install(True)
    assert snapshot(plugin, settings_path) == before
    assert registry.get(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE) == PLUGIN_DLL


def test_a_refused_removal_puts_everything_back(pj64_dir, dll_source, settings_path, monkeypatch):
    """The leftovers go last, after Install selected the plugin: a refusal on
    the second one (and a declined UAC retry) leaves the folder, the overlay
    and Project64's selection exactly as they were. The refresh does the same."""
    layer, registry, _, plugin = older_install(pj64_dir, dll_source, settings_path)
    registry.set(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE, ORIGINAL_GRAPHICS_DLL)
    before = snapshot(plugin, settings_path)
    real_unlink = Path.unlink

    def refused(path, *args, **kwargs):
        if path.name == LEGACY_RENDERER_FILE:
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
    assert snapshot(plugin, settings_path) == before         # the ini, deleted first, is back
    assert registry.get(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE) == ORIGINAL_GRAPHICS_DLL
    registry.set(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE, PLUGIN_DLL)
    before = snapshot(plugin, settings_path)
    with pytest.raises(OSError, match="Access is denied"):
        layer.refresh_if_stale()
    assert snapshot(plugin, settings_path) == before


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing semantics")
def test_a_refresh_waits_while_project64_holds_the_old_renderer(pj64_dir, dll_source, settings_path, caplog):
    """A running Project64 maps the old wrapper's renderer too: the same quiet
    "not now", nothing written, and the move lands once it lets go."""
    layer, _, _, plugin = older_install(pj64_dir, dll_source, settings_path)
    before = snapshot(plugin, settings_path)
    with caplog.at_level(logging.INFO), held_like_a_loaded_plugin(plugin / LEGACY_RENDERER_FILE):
        assert layer.refresh_if_stale() is False
        assert layer.refresh_if_stale() is False
    assert snapshot(plugin, settings_path) == before
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING or r.exc_info]
    assert len([r for r in caplog.records if "refresh waits" in r.getMessage()]) == 1
    assert layer.refresh_if_stale() is True
    assert plugin_files(plugin) == sorted([PLUGIN_DLL, RENDERER_INI])


# -- the placeholder text an older stable app may have copied ------------------

#: the frozen apps that may meet that window: the stable one refreshes by
#: itself (main.py, channel 'stable'); a beta one installs only on a click
STABLE_APP = {"kind": "frozen", "version": "1.10.0"}
BETA_APP = {"kind": "frozen", "version": "1.10.0-beta.38", "channel": "beta", "build_id": "beta-38"}


def v191_window(pj64_dir, dll_source, settings_path, *, auto_refresh=True, installer=STABLE_APP):
    """What the stable app v1.9.1 leaves when its refresh ticks between an
    update's file swap and its restart: its bundle is then the new plugin and
    the placeholder text, and it copies both (the text as its renderer), names
    that in the ini and records the new plugin's hash. Its record carries only
    v1.9.1's keys: no owned_sha256, no installed_by."""
    plugin = pj64_dir / "Plugin"
    (plugin / PLUGIN_DLL).write_bytes(dll_source.read_bytes())
    (plugin / LEGACY_RENDERER_DLL).write_bytes(RETIRED_NATIVE_TEXT)
    (plugin / LEGACY_WRAPPER_INI).write_text(f"wrapped={LEGACY_RENDERER_DLL}\n")
    settings_path.write_text(json.dumps({
        "consented_at": "2026-09-19T12:00:00+00:00", "pj64_dir": str(pj64_dir),
        "wrapped": LEGACY_RENDERER_DLL, "uninstalled_at": None,
        "installed_sha256": hashlib.sha256(dll_source.read_bytes()).hexdigest(),
        "installation_receipt": {"reason": "automatic_refresh"},
        "installed_renderer_sha256": hashlib.sha256(RETIRED_NATIVE_TEXT).hexdigest(),
        "renderer_receipt": {"reason": "automatic_refresh"},
        "previous_graphics_dll": ORIGINAL_GRAPHICS_DLL}))
    registry = FakeRegistry({(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE): PLUGIN_DLL})
    layer = CaptureLayer(registry, FakeProcesses(None), settings_path, dll_source,
                         auto_refresh=auto_refresh, installer=installer)
    return layer, registry, plugin


@pytest.mark.parametrize("name", LEFTOVERS)
def test_the_placeholder_text_under_an_older_name_is_ours_and_nothing_else_is(
        pj64_dir, dll_source, settings_path, name):
    layer, _, _ = make_layer(pj64_dir, dll_source, settings_path)
    layer.install(True)
    plugin = pj64_dir / "Plugin"
    (plugin / name).write_bytes(RETIRED_NATIVE_TEXT + b" ")      # not the text: the user's
    assert layer.status().leftovers == []
    (plugin / name).write_bytes(RETIRED_NATIVE_TEXT)
    assert layer.status().leftovers == [name]
    assert layer.refresh_if_stale() is True
    assert plugin_files(plugin) == sorted([PLUGIN_DLL, RENDERER_INI])


@pytest.mark.parametrize("app", ["stable", "beta"])
def test_what_v191_leaves_in_the_update_window_reads_stale_and_install_cleans_it(
        pj64_dir, dll_source, settings_path, app):
    """No rule adopts a record without installed_by (an open item for the
    first 1.10 stable release), and a beta app installs only on a click by
    policy. So both new frozen apps read the window's leftovers as stale, not
    broken, and open on the Install step; one Install click keeps the player's
    Graphics Dll choice and leaves only the plugin and its settings."""
    layer, registry, plugin = v191_window(pj64_dir, dll_source, settings_path, auto_refresh=app == "stable",
                                          installer=STABLE_APP if app == "stable" else BETA_APP)
    status = layer.status()
    assert status.state == NEEDS_RESTART and status.wrapper_selected and status.wrapper_current
    assert status.leftovers == [LEGACY_WRAPPER_INI, LEGACY_RENDERER_DLL]
    assert not status.installation_verified and status.automatic_update is False
    assert any("differs from this build" in problem for problem in status.problems)
    assert not any("re-install" in problem or "missing" in problem for problem in status.problems)
    assert [(step["id"], step["action"]) for step in status.steps] == [
        ("close", None), ("install", "install"), ("start", None)]
    assert layer.refresh_if_stale() is False
    assert layer.install(True).installation_verified
    assert plugin_files(plugin) == sorted([PLUGIN_DLL, RENDERER_INI])
    assert registry.get(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE) == PLUGIN_DLL
    assert layer.status().installation_verified
    layer.uninstall()
    assert registry.get(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE) == ORIGINAL_GRAPHICS_DLL


# -- the UAC helper reaches the same end state ---------------------------------

def plant_older_install(plugin: Path):
    (plugin / LEGACY_RENDERER_FILE).write_bytes(b"MZ" + b"3" * 64 + b"-renderer\0")
    (plugin / LEGACY_RENDERER_DLL).write_bytes(b"MZ" + b"4" * 64 + b"-renderer\0")
    (plugin / LEGACY_WRAPPER_INI).write_text(f"wrapped={LEGACY_RENDERER_FILE}\n")


@pytest.mark.skipif(os.name != "nt", reason="Windows handle pinning")
def test_the_privileged_helper_moves_an_older_install_and_rolls_it_back(privileged_request, install):
    plugin, source = install[3] / "Plugin", install[4]
    (plugin / PLUGIN_DLL).write_bytes(b"MZ old wrapper " + b"5" * 64 + b"-gpu-runtime\0")
    plant_older_install(plugin)
    before = {path.name: path.read_bytes() for path in plugin.iterdir()}
    with pytest.raises(OSError, match="parent settings failed"):
        with plugin_elevation_worker.privileged_files(privileged_request):
            raise OSError("parent settings failed")
    assert {path.name: path.read_bytes() for path in plugin.iterdir()} == before
    with plugin_elevation_worker.privileged_files(privileged_request):
        assert (plugin / LEGACY_RENDERER_FILE).exists()  # until the original user commits
    assert plugin_files(plugin) == sorted([PLUGIN_DLL, RENDERER_INI])
    assert (plugin / PLUGIN_DLL).read_bytes() == source.read_bytes()


@pytest.mark.skipif(os.name != "nt", reason="Windows handle pinning")
def test_an_elevated_install_selects_the_plugin_before_the_leftovers_go(privileged_request, install, monkeypatch):
    layer, registry, _, pj64, source = install
    plugin = pj64 / "Plugin"
    plant_older_install(plugin)
    deny_first_copy(monkeypatch)
    seen = []

    @contextmanager
    def helper(*_args):
        with plugin_elevation_worker.privileged_files(privileged_request) as receipts:
            yield receipts
            seen.append(registry.selected)
        assert not (plugin / LEGACY_RENDERER_FILE).exists()

    monkeypatch.setattr(plugin_elevation, "elevated_install", helper)
    assert layer.install(True).installation_verified
    assert seen == [PLUGIN_DLL]
    assert plugin_files(plugin) == sorted([PLUGIN_DLL, RENDERER_INI])

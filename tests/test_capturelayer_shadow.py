"""A legacy x86 Project64 can read an existing per-user INI before the real one.

Only disposable files and injected registry/process adapters are used here.
Windows' read precedence is a separate documented OS contract.
"""
from contextlib import contextmanager
import pytest

from sm64_events.core import plugin_elevation
from sm64_events.core.capturelayer import (
    GRAPHICS_DLL_VALUE, REGISTRY_DLL_SUBKEY, REGRESSED, RENDERER_FILE, WRAPPER_DLL,
    WRAPPER_INI, LayerRefused,
)
from sm64_events.core.plugin_installation import PluginInstallError
from test_capturelayer import (
    dll_source as dll_source, make_layer, pj64_dir as pj64_dir,
    settings_path as settings_path,
)
from test_plugin_elevation import deny_first_copy


@pytest.fixture
def shadow_install(pj64_dir, dll_source, settings_path, tmp_path, monkeypatch):
    appdata = tmp_path / "original-user"
    monkeypatch.setenv("LOCALAPPDATA", str(appdata))
    layer, registry, processes = make_layer(pj64_dir, dll_source, settings_path)
    shadow = appdata / "VirtualStore" / pj64_dir.relative_to(pj64_dir.anchor) / "Plugin" / WRAPPER_INI
    return layer, registry, processes, pj64_dir, dll_source, settings_path, shadow


def stale_shadow(shadow):
    shadow.parent.mkdir(parents=True, exist_ok=True)
    shadow.write_text("wrapped=old_trainer_renderer.dll\n")


def test_explicit_install_reconciles_existing_shadow_and_preserves_other_files(shadow_install):
    layer, _, _, pj64, _, _, shadow = shadow_install
    stale_shadow(shadow)
    # GLideN64.ini is not unrelated: test_renderer_settings.py owns that file.
    unrelated = shadow.with_name("Jabo_Direct3D8.ini")
    unrelated.write_bytes(b"the player's renderer settings")
    result = layer.install(True)
    assert shadow.read_bytes() == (pj64 / "Plugin" / WRAPPER_INI).read_bytes()
    assert shadow.read_text().strip() == f"wrapped={RENDERER_FILE}"
    assert unrelated.read_bytes() == b"the player's renderer settings"
    assert result.installation_verified


def test_absent_shadow_is_never_created(shadow_install):
    layer, _, _, _, _, _, shadow = shadow_install
    assert layer.install(True).installation_verified
    assert not shadow.parent.exists()
    assert not layer.refresh_if_stale()
    assert not shadow.parent.exists()


def test_current_files_with_disagreeing_shadow_are_not_verified(shadow_install):
    layer, _, _, _, _, _, shadow = shadow_install
    layer.install(True)
    stale_shadow(shadow)
    status = layer.status()
    assert status.wrapper_current and status.renderer_current and status.wrapper_selected
    assert not status.installation_verified
    assert status.state == REGRESSED
    assert any("per-user" in problem for problem in status.problems)


def test_refresh_repairs_only_shadow_when_installed_dlls_are_current(shadow_install, monkeypatch):
    layer, _, _, pj64, _, _, shadow = shadow_install
    layer.install(True)
    before = {p.name: p.read_bytes() for p in (pj64 / "Plugin").glob("*.dll")}
    stale_shadow(shadow)
    monkeypatch.setattr("sm64_events.core.plugin_installation.shutil.copyfile",
                        lambda *_: pytest.fail("current DLLs must not be recopied"))
    assert layer.refresh_if_stale()
    assert shadow.read_text().strip() == f"wrapped={RENDERER_FILE}"
    assert before == {p.name: p.read_bytes() for p in (pj64 / "Plugin").glob("*.dll")}
    assert layer.status().installation_verified


def test_registry_failure_rolls_back_physical_and_shadow_configuration(shadow_install, monkeypatch):
    layer, registry, _, pj64, _, settings, shadow = shadow_install
    layer.install(True)
    physical = pj64 / "Plugin" / WRAPPER_INI
    physical.write_bytes(b"wrapped=previous_renderer.dll\n")
    stale_shadow(shadow)
    registry.set(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE, "original.dll")
    before = {p: p.read_bytes() for p in (physical, shadow, settings)}
    real_set = registry.set

    def failed_select(key, name, value):
        real_set(key, name, value)
        if value == WRAPPER_DLL:
            raise PermissionError("registry selection failed")

    monkeypatch.setattr(registry, "set", failed_select)
    with pytest.raises(PermissionError, match="registry selection failed"):
        layer.install(True)
    assert all(p.read_bytes() == content for p, content in before.items())
    assert registry.get(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE) == "original.dll"


@pytest.mark.parametrize("commit_failure", [False, True])
def test_elevated_files_keep_original_users_shadow_in_transaction(shadow_install, monkeypatch, tmp_path, commit_failure):
    layer, registry, _, pj64, source, settings, shadow = shadow_install
    stale_shadow(shadow)
    original_shadow, original_settings = shadow.read_bytes(), settings.read_bytes()
    admin_data = tmp_path / "other-administrator"
    admin_shadow = admin_data / "VirtualStore" / pj64.relative_to(pj64.anchor) / "Plugin" / WRAPPER_INI
    stale_shadow(admin_shadow)
    admin_before = admin_shadow.read_bytes()
    deny_first_copy(monkeypatch)

    @contextmanager
    def helper(*_args):
        with monkeypatch.context() as admin_context:
            admin_context.setenv("LOCALAPPDATA", str(admin_data))
            with layer._install_plugin_files(pj64, source, layer._renderer_dll(), elevated=False) as receipts:
                yield receipts
                assert shadow.read_text().strip() == f"wrapped={RENDERER_FILE}"
                assert admin_shadow.read_bytes() == admin_before
                if commit_failure:
                    raise PluginInstallError("commit administrator install", pj64 / "Plugin",
                                             OSError("helper acknowledgment failed"))

    monkeypatch.setattr(plugin_elevation, "elevated_install", helper)
    if commit_failure:
        with pytest.raises(PluginInstallError, match="helper acknowledgment failed"):
            layer.install(True)
        assert shadow.read_bytes() == original_shadow and settings.read_bytes() == original_settings
        assert registry.get(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE) == "GLideN64_LINK_4.2.dll"
        assert not list((pj64 / "Plugin").iterdir())
    else:
        assert layer.install(True).installation_verified
    assert admin_shadow.read_bytes() == admin_before


def test_configuration_only_refresh_rolls_back_both_inis_and_overlay(shadow_install, monkeypatch):
    layer, _, _, pj64, _, settings, shadow = shadow_install
    layer.install(True)
    physical = pj64 / "Plugin" / WRAPPER_INI
    physical.write_bytes(b"wrapped=previous_renderer.dll\n")
    stale_shadow(shadow)
    before = {p: p.read_bytes() for p in (physical, shadow, settings)}
    monkeypatch.setattr(layer, "_save_overlay", lambda *_: (_ for _ in ()).throw(OSError("settings write failed")))
    with pytest.raises(OSError, match="settings write failed"):
        layer.refresh_if_stale()
    assert all(p.read_bytes() == content for p, content in before.items())


def test_running_project64_keeps_shadow_unchanged_for_install_and_refresh(shadow_install):
    layer, _, processes, pj64, _, _, shadow = shadow_install
    layer.install(True)
    stale_shadow(shadow)
    before = shadow.read_bytes()
    processes.image_path = str(pj64 / "Project64.exe")
    with pytest.raises(LayerRefused, match="close Project64"):
        layer.install(True)
    assert not layer.refresh_if_stale()
    assert shadow.read_bytes() == before


def test_shadow_write_failure_rolls_back_files_without_requesting_uac(shadow_install, monkeypatch):
    layer, registry, _, pj64, _, settings, shadow = shadow_install
    stale_shadow(shadow)
    original_settings, original_shadow = settings.read_bytes(), shadow.read_bytes()
    write = layer._write_wrapped_name

    def denied_shadow(path, name):
        if path == shadow:
            raise PermissionError("per-user configuration is read-only")
        write(path, name)

    monkeypatch.setattr(layer, "_write_wrapped_name", denied_shadow)
    monkeypatch.setattr(plugin_elevation, "elevated_install",
                        lambda *_: pytest.fail("a per-user failure must not request elevation"))
    with pytest.raises(PluginInstallError, match="per-user configuration is read-only"):
        layer.install(True)
    assert not list((pj64 / "Plugin").iterdir())
    assert settings.read_bytes() == original_settings and shadow.read_bytes() == original_shadow
    assert registry.get(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE) == "GLideN64_LINK_4.2.dll"


def test_undo_restores_previous_selection_without_touching_other_shadow_files(shadow_install):
    layer, registry, _, _, _, _, shadow = shadow_install
    stale_shadow(shadow)
    layer.install(True)
    before = shadow.read_bytes()
    layer.uninstall()
    assert registry.get(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE) == "GLideN64_LINK_4.2.dll"
    assert shadow.read_bytes() == before


@pytest.mark.parametrize("contents", [
    b"\xff\xfe\x00",
    f"wrapped={RENDERER_FILE}\n\twrapped=old_trainer_renderer.dll\n".encode(),
])
def test_unreadable_or_later_override_shadow_is_repaired(shadow_install, contents):
    layer, _, _, _, _, _, shadow = shadow_install
    layer.install(True)
    shadow.parent.mkdir(parents=True)
    shadow.write_bytes(contents)
    assert not layer.status().installation_verified
    assert layer.refresh_if_stale()
    assert layer.status().installation_verified
    assert shadow.read_text().strip() == f"wrapped={RENDERER_FILE}"

"""A legacy x86 Project64 can read an existing per-user copy of a Plugin file
before the real one (Windows VirtualStore).

The one graphics plugin reads no ini of its own, so an older install's
per-user copy of the wrapper's `sm64_trainer_gfx.ini` decides nothing: it is
never read as configuration, never written and never created. The renderer's
`GLideN64.ini` copy still matters; `test_renderer_settings.py` owns it.
Only disposable files and injected registry/process adapters are used here.
"""
import pytest

from sm64_events.core.capturelayer import LEGACY_WRAPPER_INI
from test_capturelayer import (
    dll_source as dll_source, make_layer, pj64_dir as pj64_dir,
    settings_path as settings_path,
)


@pytest.fixture
def shadow_install(pj64_dir, dll_source, settings_path, tmp_path, monkeypatch):
    appdata = tmp_path / "original-user"
    monkeypatch.setenv("LOCALAPPDATA", str(appdata))
    layer, registry, processes = make_layer(pj64_dir, dll_source, settings_path)
    shadow = appdata / "VirtualStore" / pj64_dir.relative_to(pj64_dir.anchor) / "Plugin" / LEGACY_WRAPPER_INI
    return layer, shadow


def test_an_older_per_user_wrapper_ini_neither_blocks_nor_changes(shadow_install):
    layer, shadow = shadow_install
    shadow.parent.mkdir(parents=True)
    shadow.write_text("wrapped=old_trainer_renderer.dll\n")
    before = shadow.read_bytes()
    assert layer.install(True).installation_verified
    assert not layer.refresh_if_stale()
    layer.uninstall()
    assert shadow.read_bytes() == before


def test_absent_shadow_is_never_created(shadow_install):
    layer, shadow = shadow_install
    assert layer.install(True).installation_verified
    assert not shadow.parent.exists()
    assert not layer.refresh_if_stale()
    assert not shadow.parent.exists()

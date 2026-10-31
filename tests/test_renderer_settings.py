"""GLideN64.ini: the trainer's renderer must load frame buffer emulation OFF.

Griffin, 2026-10-30: "We should just ship whatever the default settings are,
and MAKE SURE THAT FRAME BUFFER EMULATION IS DISABLED. This is very
important." With it on, ghosts do not draw and the game lags. Only disposable
files and the fakes from test_capturelayer.py are used: no real Project64
folder, VirtualStore, registry or %LOCALAPPDATA%\\SM64Trainer is touched.
"""
import json
import logging

import pytest

from sm64_events.core import plugin_elevation, renderer_settings
from sm64_events.core.capturelayer import (
    GRAPHICS_DLL_VALUE, NEEDS_RESTART, REGISTRY_DLL_SUBKEY, WRAPPER_DLL,
)
from sm64_events.core.renderer_settings import (
    MINIMAL, RENDERER_INI, disable, frame_buffer_emulation_off, previous_value, restore,
)
from test_capturelayer import (
    ORIGINAL_GRAPHICS_DLL, dll_source as dll_source, make_layer, pj64_dir as pj64_dir,
    settings_path as settings_path,
)

# Griffin's working LINK package ini, abridged, with frame buffer emulation
# ON as GLideN64's compiled defaults write it: Qt's CRLF layout, binary
# @Variant values, a path and a Latin-1 byte, all of which must survive.
LINK_ON = (
    b"[General]\r\nversion=26\r\ntranslation=\r\nprofile=User\r\n\r\n"
    b"[User]\r\nversion=26\r\nvideo\\fullscreenWidth=2560\r\nvideo\\fullscreenRefresh=240\r\n"
    b"frameBufferEmulation\\enable=1\r\nframeBufferEmulation\\aspect=1\r\n"
    b"textureFilter\\txPath=C:/Users/caf\xe9/Desktop/Project64 1.6/Plugin/hires_texture\r\n"
    b"font\\color=@Variant(\\0\\0\\0\\x43\\x1\\xff\\xff\\xb5\\xb5\\xe6\\xe6\\x1d\\x1d\\0\\0)\r\n"
    b"gammaCorrection\\level=@Variant(\\0\\0\\0\\x87@\\0\\0\\0)\r\n"
    b"debug\\dumpMode=0\r\n"
)
LINK_OFF = LINK_ON.replace(b"frameBufferEmulation\\enable=1", b"frameBufferEmulation\\enable=0")


@pytest.fixture(autouse=True)
def own_appdata(tmp_path, monkeypatch):
    """Every layer here looks for VirtualStore copies under a disposable profile."""
    appdata = tmp_path / "original-user"
    monkeypatch.setenv("LOCALAPPDATA", str(appdata))
    return appdata


def changed_lines(before: bytes, after: bytes) -> list[tuple[bytes, bytes]]:
    old, new = before.splitlines(keepends=True), after.splitlines(keepends=True)
    assert len(old) == len(new)
    return [(a, b) for a, b in zip(old, new, strict=True) if a != b]


# -- the file format ------------------------------------------------------

def test_no_file_becomes_the_link_header_with_only_the_one_key():
    assert disable(None) == disable(b"") == disable(b"\r\n") == MINIMAL
    assert frame_buffer_emulation_off(MINIMAL) and not frame_buffer_emulation_off(None)
    # The same General/profile layout as his working package, so GLideN64
    # loads it as-is instead of rewriting it from its own defaults.
    header = LINK_ON[:LINK_ON.index(b"video")]
    assert MINIMAL == header + b"frameBufferEmulation\\enable=0\r\n"


def test_turning_it_off_changes_one_line_and_keeps_every_other_byte():
    assert not frame_buffer_emulation_off(LINK_ON) and previous_value(LINK_ON) == "1"
    after = disable(LINK_ON)
    assert changed_lines(LINK_ON, after) == [(b"frameBufferEmulation\\enable=1\r\n",
                                             b"frameBufferEmulation\\enable=0\r\n")]
    assert after == LINK_OFF and frame_buffer_emulation_off(after)


def test_a_setting_already_off_comes_back_as_the_same_bytes():
    assert disable(LINK_OFF) is LINK_OFF


def test_only_the_profile_glide_loads_changes():
    data = (b"[General]\r\nversion=26\r\nprofile=Speed Runs\r\n\r\n"
            b"[User]\r\nversion=26\r\nframeBufferEmulation\\enable=1\r\n\r\n"
            b"[Speed%20Runs]\r\nversion=26\r\nFRAMEBUFFEREMULATION\\Enable = 2 \r\nvideo\\fxaa=1\r\n")
    after = disable(data)
    assert changed_lines(data, after) == [(b"FRAMEBUFFEREMULATION\\Enable = 2 \r\n",
                                           b"FRAMEBUFFEREMULATION\\Enable =0\r\n")]
    assert frame_buffer_emulation_off(after)
    # A profile section GLideN64 cannot find would make it rewrite the whole
    # file from its defaults, so the missing section is added, versioned.
    missing = data.replace(b"Speed Runs", b"Race")
    added = disable(missing)
    assert added == missing + b"\r\n[Race]\r\nversion=26\r\nframeBufferEmulation\\enable=0\r\n"
    assert frame_buffer_emulation_off(added)


def test_a_missing_key_joins_its_own_section_with_the_files_line_endings():
    data = (b"[General]\nversion=26\nprofile=User\n\n[User]\nversion=26\nvideo\\multisampling=0\n\n"
            b"[Other]\nversion=26\nframeBufferEmulation\\enable=0\n")
    assert not frame_buffer_emulation_off(data)   # [Other] is not the profile GLideN64 loads
    after = disable(data)
    assert after == data.replace(b"multisampling=0\n", b"multisampling=0\nframeBufferEmulation\\enable=0\n")


def test_the_old_flat_format_changes_the_key_glide_carries_into_its_profile():
    # Below version 23 GLideN64 loads the root keys and rewrites them as User.
    data = b"[General]\r\nversion=17\r\nframeBufferEmulation\\enable=1\r\nvideo\\fxaa=0\r\n"
    assert disable(data) == data.replace(b"enable=1", b"enable=0")
    unversioned = b"video\\fxaa=0\r\n[User]\r\nframeBufferEmulation\\enable=0\r\n"
    assert not frame_buffer_emulation_off(unversioned)
    assert frame_buffer_emulation_off(disable(unversioned))


def test_undo_puts_back_the_earlier_value_or_glides_default(tmp_path):
    ini = tmp_path / RENDERER_INI
    ini.write_bytes(LINK_OFF)
    assert restore(ini, {"created": False, "previous": "1"})
    assert ini.read_bytes() == LINK_ON
    # Turned back on since: that choice stays.
    assert not restore(ini, {"created": False, "previous": "1"}) and ini.read_bytes() == LINK_ON
    # Created by the trainer and untouched since: it goes away.
    ini.write_bytes(MINIMAL)
    assert restore(ini, {"created": True, "previous": None}) and not ini.exists()
    # Created, then rewritten by GLideN64: only the key goes, so the default returns.
    ini.write_bytes(LINK_OFF)
    assert restore(ini, {"created": True, "previous": None})
    assert ini.read_bytes() == LINK_ON.replace(b"frameBufferEmulation\\enable=1\r\n", b"")
    assert not frame_buffer_emulation_off(ini.read_bytes())


# -- through the capture layer -------------------------------------------

def settings_file(pj64):
    return pj64 / "Plugin" / RENDERER_INI


def shadow_of(appdata, pj64):
    return appdata / "VirtualStore" / pj64.relative_to(pj64.anchor) / "Plugin" / RENDERER_INI


def test_install_creates_the_minimal_file_and_undo_removes_it(pj64_dir, dll_source, settings_path):
    layer, registry, _ = make_layer(pj64_dir, dll_source, settings_path)
    assert not settings_file(pj64_dir).exists()
    status = layer.install(True)
    assert settings_file(pj64_dir).read_bytes() == MINIMAL
    assert status.frame_buffer_emulation_off and status.installation_verified
    layer.uninstall()
    assert not settings_file(pj64_dir).exists()
    assert registry.get(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE) == ORIGINAL_GRAPHICS_DLL


@pytest.mark.parametrize("profile", ["User", "Speedrun"])
def test_install_turns_it_off_and_undo_restores_the_file_byte_for_byte(
    pj64_dir, dll_source, settings_path, profile,
):
    original = LINK_ON.replace(b"profile=User", b"profile=" + profile.encode()).replace(
        b"[User]", b"[" + profile.encode() + b"]")
    if profile != "User":   # the User section GLideN64 does not load stays as it was
        original += b"\r\n[User]\r\nversion=26\r\nframeBufferEmulation\\enable=1\r\n"
    settings_file(pj64_dir).write_bytes(original)
    layer, _, _ = make_layer(pj64_dir, dll_source, settings_path)
    assert layer.install(True).installation_verified
    after = settings_file(pj64_dir).read_bytes()
    assert changed_lines(original, after) == [(b"frameBufferEmulation\\enable=1\r\n",
                                               b"frameBufferEmulation\\enable=0\r\n")]
    record = json.loads(settings_path.read_text())["renderer_settings"]
    assert record == {str(settings_file(pj64_dir)): {"created": False, "previous": "1"}}
    layer.uninstall()
    assert settings_file(pj64_dir).read_bytes() == original
    assert json.loads(settings_path.read_text())["renderer_settings"] is None


def test_a_setting_already_off_is_never_rewritten_or_restored(pj64_dir, dll_source, settings_path, monkeypatch):
    settings_file(pj64_dir).write_bytes(LINK_OFF)
    monkeypatch.setattr(renderer_settings, "write_atomic",
                        lambda *_: pytest.fail("a file already off must not be written"))
    layer, _, _ = make_layer(pj64_dir, dll_source, settings_path)
    assert layer.install(True).installation_verified
    assert not layer.refresh_if_stale()
    layer.uninstall()
    assert settings_file(pj64_dir).read_bytes() == LINK_OFF


def test_an_existing_virtualstore_copy_is_fixed_and_restored_but_never_created(
    pj64_dir, dll_source, settings_path, own_appdata,
):
    layer, _, _ = make_layer(pj64_dir, dll_source, settings_path)
    shadow = shadow_of(own_appdata, pj64_dir)
    assert layer.install(True).installation_verified
    assert not shadow.parent.exists()            # absent: never created
    layer.uninstall()
    shadow.parent.mkdir(parents=True)
    shadow.write_bytes(LINK_ON)                  # what a virtualized first launch wrote
    settings_file(pj64_dir).write_bytes(LINK_OFF)
    status = layer.status()
    assert not status.frame_buffer_emulation_off   # the copy Project64 reads first is on
    assert layer.install(True).installation_verified
    assert shadow.read_bytes() == LINK_OFF and settings_file(pj64_dir).read_bytes() == LINK_OFF
    layer.uninstall()
    assert shadow.read_bytes() == LINK_ON and settings_file(pj64_dir).read_bytes() == LINK_OFF


def test_a_setting_turned_back_on_is_reported_then_repaired_once_project64_closes(
    pj64_dir, dll_source, settings_path,
):
    layer, _, processes = make_layer(pj64_dir, dll_source, settings_path)
    layer.install(True)
    settings_file(pj64_dir).write_bytes(MINIMAL.replace(b"enable=0", b"enable=1"))
    processes.image_path = str(pj64_dir / "Project64.exe")
    status = layer.status()
    # Stale like an older DLL: no onboarding re-opens, the setup screen says so.
    assert status.state == NEEDS_RESTART and status.automatic_update
    assert not status.frame_buffer_emulation_off and not status.installation_verified
    assert status.problems[-1] == ("Frame buffer emulation is on in Project64's graphics settings "
                                   "(GLideN64.ini). This setting hides ghosts and makes the game lag. "
                                   "Close Project64, and the trainer turns it off by itself.")
    assert [step["id"] for step in status.steps] == ["close", "update", "start"]
    assert not layer.refresh_if_stale()          # never under a running Project64
    assert b"enable=1" in settings_file(pj64_dir).read_bytes()
    processes.image_path = None
    assert layer.refresh_if_stale()
    assert settings_file(pj64_dir).read_bytes() == MINIMAL
    assert layer.status().installation_verified
    layer.uninstall()                            # the first record (created) still holds
    assert not settings_file(pj64_dir).exists()


def deny_settings_write(monkeypatch, path):
    real, attempts = renderer_settings.write_atomic, []

    def denied(target, data):
        if target == path:
            attempts.append(target)
            error = PermissionError(13, "protected plugin folder", str(target))
            error.winerror = 5
            raise error
        real(target, data)

    monkeypatch.setattr(renderer_settings, "write_atomic", denied)
    return attempts, real


def test_a_denied_write_is_reported_plainly_and_install_can_elevate(
    pj64_dir, dll_source, settings_path, monkeypatch,
):
    layer, _, _ = make_layer(pj64_dir, dll_source, settings_path)
    layer.install(True)
    ini = settings_file(pj64_dir)
    ini.write_bytes(LINK_ON)
    attempts, real = deny_settings_write(monkeypatch, ini)
    monkeypatch.setattr(plugin_elevation, "elevated_install",
                        lambda *_: pytest.fail("background refresh must never ask for administrator permission"))
    with pytest.raises(OSError, match="protected plugin folder"):
        layer.refresh_if_stale()
    assert len(attempts) == 1 and ini.read_bytes() == LINK_ON
    status = layer.status()
    assert not status.automatic_update and not status.installation_verified
    assert status.problems[-1].endswith("Choose Install to turn it off.")
    assert any(step["action"] == "install" for step in status.steps)

    elevated = []

    def helper(pj64, plugin, source, renderer):
        elevated.append(plugin)
        monkeypatch.setattr(renderer_settings, "write_atomic", real)   # administrator may write
        return layer._install_plugin_files(pj64, source, renderer, elevated=False)

    monkeypatch.setattr(plugin_elevation, "elevated_install", helper)
    assert layer.install(True).installation_verified
    assert len(elevated) == 1 and ini.read_bytes() == LINK_OFF


def test_a_failed_install_restores_every_settings_file(
    pj64_dir, dll_source, settings_path, own_appdata, monkeypatch,
):
    layer, registry, _ = make_layer(pj64_dir, dll_source, settings_path)
    shadow = shadow_of(own_appdata, pj64_dir)
    shadow.parent.mkdir(parents=True)
    shadow.write_bytes(LINK_ON)
    real_set = registry.set

    def failed_select(key, name, value):
        real_set(key, name, value)
        if value == WRAPPER_DLL:
            raise PermissionError("registry selection failed")

    monkeypatch.setattr(registry, "set", failed_select)
    with pytest.raises(PermissionError, match="registry selection failed"):
        layer.install(True)
    assert not settings_file(pj64_dir).exists()   # created, then removed again
    assert shadow.read_bytes() == LINK_ON
    assert json.loads(settings_path.read_text()).get("renderer_settings") is None


def test_an_undo_that_cannot_write_still_restores_the_plugin_and_keeps_the_record(
    pj64_dir, dll_source, settings_path, monkeypatch, caplog,
):
    settings_file(pj64_dir).write_bytes(LINK_ON)
    layer, registry, _ = make_layer(pj64_dir, dll_source, settings_path)
    layer.install(True)
    deny_settings_write(monkeypatch, settings_file(pj64_dir))
    with caplog.at_level(logging.WARNING, "sm64.capturelayer"):
        layer.uninstall()
    assert registry.get(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE) == ORIGINAL_GRAPHICS_DLL
    assert settings_file(pj64_dir).read_bytes() == LINK_OFF
    assert any("could not restore frame buffer emulation" in row.message for row in caplog.records)
    record = json.loads(settings_path.read_text())["renderer_settings"]
    assert record == {str(settings_file(pj64_dir)): {"created": False, "previous": "1"}}

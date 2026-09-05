# tests/test_capturelayer.py
"""Tests for core/capturelayer.py -- fakes only, no registry key or process
list on the real machine is ever touched. `FakeRegistry` and `FakeProcesses`
stand in for the injected `Registry`/`Processes` protocols; the plugin
folder and the DLL source are real files under `tmp_path`."""
import json

import pytest

from sm64_events.core.capturelayer import (
    ACTIVE,
    GRAPHICS_DLL_VALUE,
    NEEDS_RESTART,
    NOT_INSTALLED,
    REGISTRY_DLL_SUBKEY,
    REGRESSED,
    UNAVAILABLE,
    WRAPPER_DLL,
    WRAPPER_INI,
    CaptureLayer,
    LayerRefused,
)

ORIGINAL_GRAPHICS_DLL = "GLideN64_LINK_4.2.dll"
OTHER_GRAPHICS_DLL = "Jabo_Direct3D8.dll"


class FakeRegistry:
    """An in-memory stand-in for the real registry, keyed exactly like the
    real one: (subkey, value name) -> value."""

    def __init__(self, initial: dict | None = None):
        self._values = dict(initial or {})

    def get(self, subkey, name):
        return self._values.get((subkey, name))

    def set(self, subkey, name, value):
        self._values[(subkey, name)] = value


class FakeProcesses:
    """A process list with exactly one settable fact: whether -- and where
    -- Project64.exe is running."""

    def __init__(self, image_path=None):
        self.image_path = image_path

    def pj64_image_path(self):
        return self.image_path


class FakeHeader:
    """A frame stream header: `alive` is a heartbeat counter, `status` bit 1
    (value 2) is whether a GL context was found."""

    def __init__(self, alive=0, status=0):
        self.alive = alive
        self.status = status


@pytest.fixture
def pj64_dir(tmp_path):
    directory = tmp_path / "PJ64"
    (directory / "Plugin").mkdir(parents=True)
    return directory


@pytest.fixture
def dll_source(tmp_path):
    source = tmp_path / "shipped_sm64_trainer_gfx.dll"
    source.write_bytes(b"the real capture layer bytes")
    return source


@pytest.fixture
def settings_path(tmp_path):
    return tmp_path / "capture_layer.json"


def registry_with_original_plugin():
    return FakeRegistry({(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE): ORIGINAL_GRAPHICS_DLL})


def write_overlay(settings_path, **fields):
    overlay = {"consented_at": None, "pj64_dir": None, "wrapped": None,
               "uninstalled_at": None}
    overlay.update(fields)
    settings_path.write_text(json.dumps(overlay))


def make_layer(pj64_dir, dll_source, settings_path, *, registry=None,
               image_path=None, stream_header=None):
    """A CaptureLayer over fakes, with the overlay pre-seeded to already
    know `pj64_dir` -- the situation after locate() has once seen the
    process running, which is what every install/uninstall test assumes so
    each test can focus on its own scenario."""
    if registry is None:
        registry = registry_with_original_plugin()
    write_overlay(settings_path, pj64_dir=str(pj64_dir))
    processes = FakeProcesses(image_path)
    layer = CaptureLayer(registry, processes, settings_path, dll_source,
                          stream_header=stream_header)
    return layer, registry, processes


# -- install --------------------------------------------------------------

def test_install_writes_dll_ini_registry_and_overlay(pj64_dir, dll_source, settings_path):
    layer, registry, _processes = make_layer(pj64_dir, dll_source, settings_path)

    status = layer.install(consent=True)

    plugin_dir = pj64_dir / "Plugin"
    assert (plugin_dir / WRAPPER_DLL).read_bytes() == dll_source.read_bytes()
    assert (plugin_dir / WRAPPER_INI).read_text().strip() == f"wrapped={ORIGINAL_GRAPHICS_DLL}"
    assert registry.get(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE) == WRAPPER_DLL
    overlay = json.loads(settings_path.read_text())
    assert overlay["consented_at"] is not None
    assert overlay["wrapped"] == ORIGINAL_GRAPHICS_DLL
    assert status.state == NEEDS_RESTART


def test_second_install_leaves_the_ini_wrapped_name_alone(pj64_dir, dll_source, settings_path):
    layer, _registry, _processes = make_layer(pj64_dir, dll_source, settings_path)

    layer.install(consent=True)
    layer.install(consent=True)   # a re-install; the registry now names OUR wrapper

    ini_text = (pj64_dir / "Plugin" / WRAPPER_INI).read_text().strip()
    assert ini_text == f"wrapped={ORIGINAL_GRAPHICS_DLL}"
    assert WRAPPER_DLL not in ini_text


def test_install_refuses_without_consent(pj64_dir, dll_source, settings_path):
    layer, _registry, _processes = make_layer(pj64_dir, dll_source, settings_path)

    with pytest.raises(LayerRefused, match="consent"):
        layer.install(consent=False)


def test_install_refuses_while_project64_is_running(pj64_dir, dll_source, settings_path):
    layer, _registry, _processes = make_layer(
        pj64_dir, dll_source, settings_path,
        image_path=str(pj64_dir / "Project64.exe"))

    with pytest.raises(LayerRefused, match="close Project64"):
        layer.install(consent=True)


def test_install_refuses_with_no_known_pj64_folder(dll_source, settings_path):
    registry = registry_with_original_plugin()
    processes = FakeProcesses(image_path=None)
    layer = CaptureLayer(registry, processes, settings_path, dll_source)   # overlay never seeded

    with pytest.raises(LayerRefused, match="start Project64"):
        layer.install(consent=True)


def test_install_refuses_with_no_shipped_dll(pj64_dir, settings_path):
    layer, _registry, _processes = make_layer(pj64_dir, dll_source=None, settings_path=settings_path)

    with pytest.raises(LayerRefused, match="no capture layer"):
        layer.install(consent=True)


# -- uninstall --------------------------------------------------------------

def test_uninstall_restores_the_original_plugin_and_keeps_the_files(pj64_dir, dll_source, settings_path):
    layer, registry, _processes = make_layer(pj64_dir, dll_source, settings_path)
    layer.install(consent=True)

    status = layer.uninstall()

    assert registry.get(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE) == ORIGINAL_GRAPHICS_DLL
    assert (pj64_dir / "Plugin" / WRAPPER_DLL).exists()
    assert (pj64_dir / "Plugin" / WRAPPER_INI).exists()
    overlay = json.loads(settings_path.read_text())
    assert overlay["uninstalled_at"] is not None
    assert overlay["consented_at"] is None
    assert status.state == NOT_INSTALLED


# -- state walk --------------------------------------------------------------

def test_state_walks_from_not_installed_through_active_to_regressed(pj64_dir, dll_source, settings_path):
    header = FakeHeader(alive=0, status=2)
    layer, registry, _processes = make_layer(
        pj64_dir, dll_source, settings_path, stream_header=lambda: header)

    assert layer.status().state == NOT_INSTALLED

    layer.install(consent=True)
    assert layer.status().state == NEEDS_RESTART   # selected + present, heartbeat not yet seen twice

    header.alive = 1   # the heartbeat advances between this call and the next
    active_status = layer.status()
    assert active_status.state == ACTIVE
    assert active_status.gl_context is True

    registry.set(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE, OTHER_GRAPHICS_DLL)
    regressed_status = layer.status()
    assert regressed_status.state == REGRESSED
    assert any(OTHER_GRAPHICS_DLL in problem for problem in regressed_status.problems)


# -- locate --------------------------------------------------------------

def test_locate_remembers_the_folder_then_finds_it_with_the_process_gone(pj64_dir, dll_source, settings_path):
    registry = registry_with_original_plugin()
    processes = FakeProcesses(image_path=str(pj64_dir / "Project64.exe"))
    layer = CaptureLayer(registry, processes, settings_path, dll_source)

    assert layer.locate() == pj64_dir

    processes.image_path = None
    assert layer.locate() == pj64_dir


# -- overlay resilience --------------------------------------------------------------

def test_corrupt_overlay_loads_as_never_consented(dll_source, settings_path):
    settings_path.write_text("{not json")
    registry = registry_with_original_plugin()
    processes = FakeProcesses(image_path=None)   # not running, and the corrupt
    layer = CaptureLayer(registry, processes, settings_path, dll_source)   # file names no folder

    status = layer.status()   # must not raise despite the corrupt file

    assert status.consented_at is None
    # No pj64 dir survives to be found -- still NOT_INSTALLED, never
    # unavailable: the setup screen must open for a user who has never set
    # up, and its Project64 row is the door to finding the folder.
    assert status.state == NOT_INSTALLED
    assert status.problems == ["start Project64 once so the trainer can find it"]


def test_a_build_with_no_dll_is_unavailable_but_a_missing_folder_is_not(settings_path):
    registry = registry_with_original_plugin()
    processes = FakeProcesses(image_path=None)
    no_dll = CaptureLayer(registry, processes, settings_path, dll_source=None)
    assert no_dll.status().state == UNAVAILABLE

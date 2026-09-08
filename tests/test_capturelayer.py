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
    """A frame stream header: `alive` is a heartbeat counter, `status` bit
    2 is a GL context on the emulation thread, bit 32 the wrapped plugin's
    ReadScreen path, `dropped` the pictures the layer could not read."""

    def __init__(self, alive=0, status=0, dropped=0):
        self.alive = alive
        self.status = status
        self.dropped = dropped


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


def test_failed_install_restores_files_and_does_not_select_a_half_install(pj64_dir, dll_source, settings_path, monkeypatch):
    layer, registry, _ = make_layer(pj64_dir, dll_source, settings_path)
    before = settings_path.read_bytes()
    def fail(*args):
        raise PermissionError("settings folder is read-only")
    monkeypatch.setattr(layer, "_save_overlay", fail)
    with pytest.raises(PermissionError):
        layer.install(True)
    assert registry.get(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE) == ORIGINAL_GRAPHICS_DLL
    assert not (pj64_dir / "Plugin" / WRAPPER_DLL).exists()
    assert not (pj64_dir / "Plugin" / WRAPPER_INI).exists()
    assert settings_path.read_bytes() == before


def test_install_rejects_an_unverified_executable(pj64_dir, dll_source, settings_path):
    layer, registry, processes = make_layer(pj64_dir, dll_source, settings_path)
    processes.check_folder = lambda path: {"state": "unsupported", "message": "Use Project64 v1.6"}
    with pytest.raises(LayerRefused, match="v1.6"):
        layer.install(True)
    assert registry.get(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE) == ORIGINAL_GRAPHICS_DLL


def test_automatic_refresh_also_leaves_an_unsupported_emulator_alone(pj64_dir, dll_source, settings_path):
    layer, _, processes = make_layer(pj64_dir, dll_source, settings_path)
    layer.install(True)
    previous = (pj64_dir / "Plugin" / WRAPPER_DLL).read_bytes()
    dll_source.write_bytes(b"a newer wrapper")
    processes.check_folder = lambda path: {"state": "unsupported", "message": "Use Project64 v1.6"}
    assert not layer.refresh_if_stale()
    assert (pj64_dir / "Plugin" / WRAPPER_DLL).read_bytes() == previous


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


def _installed_and_alive(pj64_dir, dll_source, settings_path, header, image_path=None):
    layer, registry, processes = make_layer(
        pj64_dir, dll_source, settings_path, stream_header=lambda: header,
        image_path=image_path)
    processes.image_path = None
    layer.install(consent=True)
    processes.image_path = image_path
    layer.status()
    header.alive += 1
    return layer, registry, processes


def test_the_first_status_read_after_boot_sees_a_moving_heartbeat(pj64_dir, dll_source, settings_path):
    """The first call has no earlier value to compare with; it takes a
    second reading instead of reporting 'restart Project64' for a game
    that is running (his first Set up after the restart, 2026-09-05)."""
    class MovingHeader:
        alive = 0
        status = 32

        def __call__(self):
            self.alive += 3
            return self
    header = MovingHeader()
    layer, _registry, processes = make_layer(
        pj64_dir, dll_source, settings_path, stream_header=header)
    processes.image_path = None
    layer.install(consent=True)
    assert layer.status().state == ACTIVE


def test_pictures_via_says_which_capture_point_the_layer_used(pj64_dir, dll_source, settings_path):
    header = FakeHeader(status=2)
    layer, _registry, _processes = _installed_and_alive(pj64_dir, dll_source, settings_path, header)
    assert layer.status().pictures_via == "gl"
    header.status = 32
    header.alive += 1
    status = layer.status()
    assert status.pictures_via == "readscreen" and status.gl_context is False
    assert status.problems == []


def test_a_layer_that_refuses_every_picture_says_so_and_names_the_desktop_fallback(
        pj64_dir, dll_source, settings_path):
    """The first live session: heartbeat moving, `dropped` climbing, no
    context and nothing from ReadScreen. The setup screen said Active.
    Now the row carries the problem, and it names where recording went."""
    header = FakeHeader(status=0, dropped=30476)
    layer, _registry, _processes = _installed_and_alive(pj64_dir, dll_source, settings_path, header)
    status = layer.status()
    assert status.state == ACTIVE and status.pictures_via is None
    assert len(status.problems) == 1
    assert "no picture reaches it" in status.problems[0]
    assert "desktop capture" in status.problems[0]


def test_a_newer_build_refreshes_the_installed_dll_only_while_project64_is_closed(
        pj64_dir, dll_source, settings_path):
    header = FakeHeader(status=2)
    layer, _registry, processes = _installed_and_alive(pj64_dir, dll_source, settings_path, header)
    dll_source.write_bytes(b"a newer capture layer")
    installed = pj64_dir / "Plugin" / WRAPPER_DLL

    processes.image_path = str(pj64_dir / "Project64.exe")
    header.alive += 1
    status = layer.status()
    assert status.wrapper_current is False
    assert any("newer capture layer" in problem and "close Project64" in problem
               for problem in status.problems)
    assert layer.refresh_if_stale() is False           # PJ64 holds the file
    assert installed.read_bytes() == b"the real capture layer bytes"

    processes.image_path = None
    assert layer.refresh_if_stale() is True
    assert installed.read_bytes() == b"a newer capture layer"
    assert layer.refresh_if_stale() is False           # current now
    header.alive += 1
    assert layer.status().wrapper_current is True


def test_the_refresh_loop_keeps_trying_until_stopped(pj64_dir, dll_source, settings_path):
    import threading
    header = FakeHeader(status=2)
    layer, _registry, processes = _installed_and_alive(pj64_dir, dll_source, settings_path, header)
    dll_source.write_bytes(b"a newer capture layer")
    processes.image_path = str(pj64_dir / "Project64.exe")
    stop = threading.Event()
    thread = threading.Thread(target=layer.refresh_loop, args=(stop, 0.02), daemon=True)
    thread.start()
    import time
    time.sleep(0.1)
    installed = pj64_dir / "Plugin" / WRAPPER_DLL
    assert installed.read_bytes() == b"the real capture layer bytes"   # PJ64 open: waits
    processes.image_path = None                                          # ...closed
    deadline = time.monotonic() + 2.0
    while installed.read_bytes() != b"a newer capture layer" and time.monotonic() < deadline:
        time.sleep(0.02)
    assert installed.read_bytes() == b"a newer capture layer"
    stop.set()
    thread.join(timeout=2.0)
    assert not thread.is_alive()


def test_the_steps_are_the_exact_path_from_each_state_to_a_live_layer(pj64_dir, dll_source, settings_path):
    """His rule: onboarding is the exact set of steps, close/wait/start
    included. Never installed with PJ64 open: close, install, start. A
    newer build with PJ64 open: close (then the trainer updates), updated,
    start -- and closing PJ64 ticks the first step by itself."""
    layer, _registry, processes = make_layer(
        pj64_dir, dll_source, settings_path, image_path=str(pj64_dir / "Project64.exe"))
    ids = lambda status: [(s["id"], s["done"], s["action"]) for s in status.steps]
    assert ids(layer.status()) == [("close", False, None), ("install", False, "install"),
                                   ("start", False, None)]
    processes.image_path = None
    assert ids(layer.status())[0] == ("close", True, None)
    layer.install(consent=True)
    assert ids(layer.status()) == [("start", False, None)]          # needs_restart, PJ64 closed
    processes.image_path = str(pj64_dir / "Project64.exe")
    dll_source.write_bytes(b"a newer capture layer")
    header = FakeHeader(status=2)
    processes2 = FakeProcesses(str(pj64_dir / "Project64.exe"))
    layer2 = CaptureLayer(_registry, processes2, settings_path, dll_source,
                          stream_header=lambda: header)      # the consent already given stands
    layer2.status()
    header.alive += 1
    stale = layer2.status()
    assert stale.state == ACTIVE and stale.wrapper_current is False
    assert [s["id"] for s in stale.steps] == ["close", "update", "start"]
    assert stale.steps[0]["done"] is False and "updates the capture layer" in stale.steps[0]["label"]
    processes2.image_path = None
    header.alive += 1
    assert layer2.status().steps[0]["done"] is True
    assert layer2.refresh_if_stale() is True
    header.alive += 1
    after = layer2.status()
    assert after.wrapper_current is True


def test_an_active_current_layer_has_no_steps_left(pj64_dir, dll_source, settings_path):
    header = FakeHeader(status=2)
    layer, _registry, _processes = _installed_and_alive(pj64_dir, dll_source, settings_path, header)
    assert layer.status().steps == []


def test_refresh_does_nothing_for_a_user_who_never_consented(pj64_dir, dll_source, settings_path):
    layer, _registry, _processes = make_layer(pj64_dir, dll_source, settings_path)
    (pj64_dir / "Plugin" / WRAPPER_DLL).write_bytes(b"someone else's file")
    assert layer.refresh_if_stale() is False
    assert (pj64_dir / "Plugin" / WRAPPER_DLL).read_bytes() == b"someone else's file"


# -- locate --------------------------------------------------------------

def test_locate_remembers_the_folder_then_finds_it_with_the_process_gone(pj64_dir, dll_source, settings_path):
    registry = registry_with_original_plugin()
    processes = FakeProcesses(image_path=str(pj64_dir / "Project64.exe"))
    layer = CaptureLayer(registry, processes, settings_path, dll_source)

    assert layer.locate() == pj64_dir

    processes.image_path = None
    assert layer.locate() == pj64_dir


def test_matching_install_is_recognized_without_a_local_consent_record(pj64_dir, dll_source, settings_path):
    layer, registry, _ = make_layer(pj64_dir, dll_source, settings_path)
    layer.install(True)
    fresh = CaptureLayer(registry, FakeProcesses(str(pj64_dir / "Project64.exe")),
                         settings_path.with_name("fresh-checkout.json"), dll_source)
    status = fresh.status()
    assert status.consented_at is None
    assert status.installation_verified
    assert status.state == NEEDS_RESTART
    dll_source.write_bytes(b"a different shipped version")
    assert not fresh.status().installation_verified
    (pj64_dir / "Plugin" / WRAPPER_INI).unlink()
    assert not fresh.status().installation_verified
    assert fresh.status().state == NOT_INSTALLED


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

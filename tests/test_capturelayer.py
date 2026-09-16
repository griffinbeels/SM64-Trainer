# tests/test_capturelayer.py
"""Tests for core/capturelayer.py -- fakes only, no registry key or process
list on the real machine is ever touched. `FakeRegistry` and `FakeProcesses`
stand in for the injected `Registry`/`Processes` protocols; the plugin
folder and the DLL source are real files under `tmp_path`."""
import json
from pathlib import Path

import pytest

from sm64_events.core.capturelayer import (
    ACTIVE,
    GRAPHICS_DLL_VALUE,
    NEEDS_RESTART,
    NOT_INSTALLED,
    REGISTRY_DLL_SUBKEY,
    REGRESSED,
    RENDERER_DLL,
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


class FakeGpu:
    """The GPU route's setup evidence (core/setup_gpu.py): `alive` counts
    like a heartbeat (any positive value means the wrapper's control page
    is live), `pictures` says an accepted picture has been delivered. The
    layer reads it through the injected gpu_observation callable."""

    def __init__(self, alive=0, pictures=True):
        self.alive = alive
        self.pictures = pictures

    def __call__(self):
        from sm64_events.core.setup_gpu import GpuSetupEvidence
        return GpuSetupEvidence(producer_pid=123, identity=("control", 1), delivered=1,
                                idle=False, alive=self.alive > 0, pictures=self.pictures)


@pytest.fixture
def pj64_dir(tmp_path):
    directory = tmp_path / "PJ64"
    (directory / "Plugin").mkdir(parents=True)
    return directory


@pytest.fixture
def dll_source(tmp_path):
    """The bundled wrapper; the bundled renderer sits beside it (renderer_for)."""
    source = tmp_path / "shipped_sm64_trainer_gfx.dll"
    source.write_bytes(b"the real capture layer bytes")
    renderer_for(source).write_bytes(b"the real renderer bytes")
    return source


def renderer_for(dll_source):
    return dll_source.with_name("shipped_" + RENDERER_DLL)


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
               image_path=None, gpu=None):
    """A CaptureLayer over fakes, with the overlay pre-seeded to already
    know `pj64_dir` -- the situation after locate() has once seen the
    process running, which is what every install/uninstall test assumes so
    each test can focus on its own scenario."""
    if registry is None:
        registry = registry_with_original_plugin()
    write_overlay(settings_path, pj64_dir=str(pj64_dir))
    processes = FakeProcesses(image_path)
    if dll_source is None:
        renderer = None
    elif isinstance(dll_source, Path):
        renderer = renderer_for(dll_source)
    else:
        renderer = lambda: renderer_for(dll_source()) if dll_source() else None  # noqa: E731
    layer = CaptureLayer(registry, processes, settings_path, dll_source, renderer_source=renderer,
                          gpu_observation=gpu, auto_refresh=True)
    return layer, registry, processes


# -- install --------------------------------------------------------------

def test_install_writes_dll_ini_registry_and_overlay(pj64_dir, dll_source, settings_path):
    layer, registry, _processes = make_layer(pj64_dir, dll_source, settings_path)

    status = layer.install(consent=True)

    plugin_dir = pj64_dir / "Plugin"
    assert (plugin_dir / WRAPPER_DLL).read_bytes() == dll_source.read_bytes()
    assert (plugin_dir / RENDERER_DLL).read_bytes() == renderer_for(dll_source).read_bytes()
    # The wrapper wraps OUR renderer, never the plugin the user had: a stock
    # renderer has no capture export (every frame read "input unavailable",
    # 2026-09-15). The user's plugin is remembered for undo instead.
    assert (plugin_dir / WRAPPER_INI).read_text().strip() == f"wrapped={RENDERER_DLL}"
    assert registry.get(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE) == WRAPPER_DLL
    overlay = json.loads(settings_path.read_text())
    assert overlay["consented_at"] is not None
    assert overlay["wrapped"] == RENDERER_DLL
    assert overlay["previous_graphics_dll"] == ORIGINAL_GRAPHICS_DLL
    assert status.state == NEEDS_RESTART
    assert status.renderer_present and status.renderer_current
    assert status.previous_graphics_dll == ORIGINAL_GRAPHICS_DLL


def test_packaged_refresh_preserves_a_manual_candidate(pj64_dir, dll_source, settings_path):
    layer, _, _ = make_layer(pj64_dir, dll_source, settings_path)
    layer.install(True)
    installed = pj64_dir / "Plugin" / WRAPPER_DLL
    installed.write_bytes(b"manually installed candidate")
    dll_source.write_bytes(b"different packaged build")
    assert layer.refresh_if_stale() is False
    assert installed.read_bytes() == b"manually installed candidate"
    assert any(step["action"] == "install" for step in layer.status().steps)
    assert not any("automatically" in problem for problem in layer.status().problems)
    layer.install(True)
    assert installed.read_bytes() == dll_source.read_bytes()


@pytest.mark.parametrize("automatic", [False, True])
def test_corrupt_copy_is_rejected_and_original_install_restored(
    pj64_dir, dll_source, settings_path, monkeypatch, automatic,
):
    import shutil
    from pathlib import Path
    layer, registry, _ = make_layer(pj64_dir, dll_source, settings_path)
    layer.install(True)
    installed = pj64_dir / "Plugin" / WRAPPER_DLL
    before = installed.read_bytes(), settings_path.read_bytes()
    dll_source.write_bytes(b"new candidate")
    monkeypatch.setattr(shutil, "copyfile", lambda source, dest: Path(dest).write_bytes(b"wrong bytes"))
    with pytest.raises(OSError, match="verification"):
        layer.refresh_if_stale() if automatic else layer.install(True)
    assert (installed.read_bytes(), settings_path.read_bytes()) == before
    assert registry.get(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE) == WRAPPER_DLL


def test_install_receipts_identify_each_successful_copy(pj64_dir, dll_source, settings_path, caplog):
    import hashlib
    layer, _, _ = make_layer(pj64_dir, dll_source, settings_path)
    caplog.set_level("INFO", logger="sm64.capturelayer")
    layer.install(True)
    first = json.loads(settings_path.read_text())["installation_receipt"]
    assert first["reason"] == "explicit_install"
    assert first["before"]["sha256"] is None
    assert first["source"]["sha256"] == first["after"]["sha256"]
    previous = first["after"]["sha256"]
    for content in (b"candidate two", b"candidate three"):
        dll_source.write_bytes(content)
        assert layer.refresh_if_stale()
        overlay = json.loads(settings_path.read_text())
        receipt = overlay["installation_receipt"]
        assert receipt["reason"] == "automatic_refresh"
        assert receipt["before"]["sha256"] == previous
        previous = hashlib.sha256(content).hexdigest()
        assert receipt["after"]["sha256"] == overlay["installed_sha256"] == previous
        assert receipt["source"]["path"] == str(dll_source.resolve())
        assert receipt["server_pid"] > 0 and receipt["utc"]
    # one renderer copy at install, then the wrapper three times (install + two
    # refreshes); a current renderer is never re-copied by a refresh
    assert len([r for r in caplog.records if "installation committed" in r.message]) == 4


def test_unknown_installation_history_requires_explicit_install(pj64_dir, dll_source, settings_path):
    layer, _, _ = make_layer(pj64_dir, dll_source, settings_path)
    layer.install(True)
    overlay = json.loads(settings_path.read_text())
    overlay.pop("installed_sha256")
    overlay.pop("installation_receipt")
    settings_path.write_text(json.dumps(overlay))
    dll_source.write_bytes(b"other bundle")
    assert not layer.refresh_if_stale()
    assert any(step["action"] == "install" for step in layer.status().steps)


def test_uninstall_revokes_automatic_update_guidance(pj64_dir, dll_source, settings_path):
    layer, registry, _ = make_layer(pj64_dir, dll_source, settings_path)
    layer.install(True)
    layer.uninstall()
    registry.set(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE, WRAPPER_DLL)
    dll_source.write_bytes(b"another bundle")
    assert not layer.refresh_if_stale()
    assert any(step["action"] == "install" for step in layer.status().steps)


def test_second_install_leaves_the_ini_wrapped_name_alone(pj64_dir, dll_source, settings_path):
    layer, _registry, _processes = make_layer(pj64_dir, dll_source, settings_path)

    layer.install(consent=True)
    layer.install(consent=True)   # a re-install; the registry now names OUR wrapper

    ini_text = (pj64_dir / "Plugin" / WRAPPER_INI).read_text().strip()
    assert ini_text == f"wrapped={RENDERER_DLL}"
    # ...and undo still knows the user's plugin, though the registry named ours.
    assert json.loads(settings_path.read_text())["previous_graphics_dll"] == ORIGINAL_GRAPHICS_DLL
    assert layer.status().previous_graphics_dll == ORIGINAL_GRAPHICS_DLL


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
    assert not (pj64_dir / "Plugin" / RENDERER_DLL).exists()
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
    layer = CaptureLayer(registry, processes, settings_path, dll_source,
                         renderer_source=renderer_for(dll_source))   # overlay never seeded

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
    header = FakeGpu(alive=0)
    layer, registry, _processes = make_layer(
        pj64_dir, dll_source, settings_path, gpu=header)

    assert layer.status().state == NOT_INSTALLED

    layer.install(consent=True)
    assert layer.status().state == NEEDS_RESTART   # selected + present, heartbeat not yet seen twice

    header.alive = 1   # the heartbeat advances between this call and the next
    active_status = layer.status()
    assert active_status.state == ACTIVE
    assert active_status.pictures_via == "gpu"

    registry.set(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE, OTHER_GRAPHICS_DLL)
    regressed_status = layer.status()
    assert regressed_status.state == REGRESSED
    assert any(OTHER_GRAPHICS_DLL in problem for problem in regressed_status.problems)


def _installed_and_alive(pj64_dir, dll_source, settings_path, header, image_path=None):
    layer, registry, processes = make_layer(
        pj64_dir, dll_source, settings_path, gpu=header,
        image_path=image_path)
    processes.image_path = None
    layer.install(consent=True)
    processes.image_path = image_path
    layer.status()
    header.alive += 1
    return layer, registry, processes


def test_pictures_via_names_the_gpu_route_once_a_picture_was_accepted(pj64_dir, dll_source, settings_path):
    header = FakeGpu(pictures=False)
    layer, _registry, _processes = _installed_and_alive(pj64_dir, dll_source, settings_path, header)
    status = layer.status()
    assert status.state == ACTIVE and status.pictures_via is None and not status.pictures_flowing
    header.pictures = True
    status = layer.status()
    assert status.pictures_via == "gpu" and status.pictures_flowing
    assert status.problems == []


def test_default_refresh_preserves_a_manually_replaced_wrapper(
        pj64_dir, dll_source, settings_path):
    layer, registry, processes = make_layer(pj64_dir, dll_source, settings_path)
    layer.install(consent=True)
    installed = pj64_dir / "Plugin" / WRAPPER_DLL
    installed.write_bytes(b"manually installed candidate from another checkout")
    # The real composition default must protect a shared plugin even after a
    # server restart. Hash inequality establishes difference, not age.
    restarted = CaptureLayer(registry, processes, settings_path, dll_source,
                             renderer_source=renderer_for(dll_source))
    assert restarted.refresh_if_stale() is False
    assert installed.read_bytes() == b"manually installed candidate from another checkout"
    status = restarted.status()
    assert [s["id"] for s in status.steps] == ["close", "install", "start"]
    assert status.steps[1]["action"] == "install"
    assert all("newer" not in problem and "by itself" not in problem
               for problem in status.problems)
    restarted.install(consent=True)
    assert installed.read_bytes() == dll_source.read_bytes()


def test_a_newer_build_refreshes_the_installed_dll_only_while_project64_is_closed(
        pj64_dir, dll_source, settings_path):
    header = FakeGpu()
    layer, _registry, processes = _installed_and_alive(pj64_dir, dll_source, settings_path, header)
    dll_source.write_bytes(b"a newer capture layer")
    installed = pj64_dir / "Plugin" / WRAPPER_DLL

    processes.image_path = str(pj64_dir / "Project64.exe")
    header.alive += 1
    status = layer.status()
    assert status.wrapper_current is False
    assert any("differs from this build" in problem and "close Project64" in problem
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
    header = FakeGpu()
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
    header = FakeGpu()
    processes2 = FakeProcesses(str(pj64_dir / "Project64.exe"))
    layer2 = CaptureLayer(_registry, processes2, settings_path, dll_source,
                          renderer_source=renderer_for(dll_source),
                          gpu_observation=header, auto_refresh=True)
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
    header = FakeGpu()
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
                         settings_path.with_name("fresh-checkout.json"), dll_source,
                         renderer_source=renderer_for(dll_source))
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
    layer = CaptureLayer(registry, processes, settings_path, dll_source,
                         renderer_source=renderer_for(dll_source))   # file names no folder

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


def test_bundle_discovery_recovers_without_recreating_layer(pj64_dir, dll_source, settings_path):
    """A restored installer source must not stay cached as unavailable."""
    available = False
    def source():
        return dll_source if available else None
    layer, registry, processes = make_layer(pj64_dir, source, settings_path)
    plugin = pj64_dir / "Plugin"
    (plugin / WRAPPER_DLL).write_bytes(dll_source.read_bytes())
    (plugin / RENDERER_DLL).write_bytes(renderer_for(dll_source).read_bytes())
    (plugin / WRAPPER_INI).write_text("wrapped=" + RENDERER_DLL)
    registry.set(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE, WRAPPER_DLL)
    processes.image_path = str(pj64_dir / "Project64.exe")
    assert layer.status().state == UNAVAILABLE
    available = True
    assert layer.status().installation_verified
    assert not layer.refresh_if_stale()  # running PJ64 is never overwritten
    available = False
    assert layer.status().state == UNAVAILABLE


def test_source_removed_after_startup_is_unavailable(pj64_dir, dll_source, settings_path):
    layer, _, _ = make_layer(pj64_dir, dll_source, settings_path)
    dll_source.unlink()
    assert layer.status().state == UNAVAILABLE
    with pytest.raises(LayerRefused, match="no capture layer"):
        layer.install(consent=True)


def test_a_wrapper_built_from_the_same_sources_is_current(tmp_path):
    """The linker never produces the same bytes twice, but a wrapper built from
    unchanged native sources carries the same embedded build id. That is what
    "current" means to the setup screen; bytes alone made every rebuild ask
    him to reinstall (2026-09-15)."""
    from sm64_events.core.capturelayer import _files_match
    same_id = b"a" * 64 + b"-gpu-runtime"  # hex digest shape
    installed, bundled, other = (tmp_path / n for n in ("installed.dll", "bundled.dll", "other.dll"))
    installed.write_bytes(b"MZ linked at 17:32 " + same_id + b" tail")
    bundled.write_bytes(b"MZ linked at 19:05 " + same_id + b" different tail")
    other.write_bytes(b"MZ " + b"b" * 64 + b"-gpu-runtime")
    assert _files_match(installed, bundled) is True
    assert _files_match(installed, other) is False
    # No id on either side: bytes decide, as before.
    plain_a, plain_b = tmp_path / "a.dll", tmp_path / "b.dll"
    plain_a.write_bytes(b"the real capture layer bytes")
    plain_b.write_bytes(b"a newer capture layer bytes!")
    assert _files_match(plain_a, plain_b) is False
    plain_b.write_bytes(b"the real capture layer bytes")
    assert _files_match(plain_a, plain_b) is True


# -- the renderer is part of the layer ----------------------------------------

def test_an_older_install_that_wrapped_the_users_plugin_is_not_set_up(pj64_dir, dll_source, settings_path):
    """Before 2026-09-16 the wrapper wrapped whatever plugin the user had. That
    layer loads and heartbeats, but a stock renderer has no capture export, so
    every picture read "input unavailable" (his report, 2026-09-15). The setup
    screen must say so, not "active"."""
    layer, registry, _ = make_layer(pj64_dir, dll_source, settings_path)
    plugin = pj64_dir / "Plugin"
    (plugin / WRAPPER_DLL).write_bytes(dll_source.read_bytes())
    (plugin / WRAPPER_INI).write_text("wrapped=" + ORIGINAL_GRAPHICS_DLL)
    registry.set(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE, WRAPPER_DLL)
    write_overlay(settings_path, pj64_dir=str(pj64_dir), consented_at="2026-09-01T00:00:00+00:00",
                  wrapped=ORIGINAL_GRAPHICS_DLL)
    status = layer.status()
    assert status.state == REGRESSED
    assert not status.installation_verified
    assert any("instead of the trainer's renderer" in problem and ORIGINAL_GRAPHICS_DLL in problem
               for problem in status.problems)
    assert status.previous_graphics_dll == ORIGINAL_GRAPHICS_DLL
    layer.install(True)
    assert (plugin / WRAPPER_INI).read_text().strip() == f"wrapped={RENDERER_DLL}"
    assert (plugin / RENDERER_DLL).exists()
    layer.uninstall()
    assert registry.get(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE) == ORIGINAL_GRAPHICS_DLL


def test_a_packaged_refresh_adds_the_renderer_to_an_older_install(pj64_dir, dll_source, settings_path):
    layer, registry, _ = make_layer(pj64_dir, dll_source, settings_path)
    layer.install(True)
    plugin = pj64_dir / "Plugin"
    # Rewind to the older scheme: no renderer, the ini wrapping the user's plugin.
    (plugin / RENDERER_DLL).unlink()
    (plugin / WRAPPER_INI).write_text("wrapped=" + ORIGINAL_GRAPHICS_DLL)
    overlay = json.loads(settings_path.read_text())
    overlay["previous_graphics_dll"] = None
    overlay["wrapped"] = ORIGINAL_GRAPHICS_DLL
    settings_path.write_text(json.dumps(overlay))
    assert layer.refresh_if_stale() is True
    assert (plugin / RENDERER_DLL).read_bytes() == renderer_for(dll_source).read_bytes()
    assert (plugin / WRAPPER_INI).read_text().strip() == f"wrapped={RENDERER_DLL}"
    assert json.loads(settings_path.read_text())["previous_graphics_dll"] == ORIGINAL_GRAPHICS_DLL
    assert layer.refresh_if_stale() is False
    layer.uninstall()
    assert registry.get(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE) == ORIGINAL_GRAPHICS_DLL


def test_a_stale_renderer_alone_makes_the_layer_stale(pj64_dir, dll_source, settings_path):
    layer, _, _ = make_layer(pj64_dir, dll_source, settings_path)
    layer.install(True)
    renderer_for(dll_source).write_bytes(b"a newer renderer")
    status = layer.status()
    assert status.wrapper_current and not status.renderer_current
    assert any("differs from this build" in problem for problem in status.problems)
    assert layer.refresh_if_stale() is True
    assert (pj64_dir / "Plugin" / RENDERER_DLL).read_bytes() == b"a newer renderer"
    assert layer.status().installation_verified


def test_undo_never_selects_one_of_our_own_files(pj64_dir, dll_source, settings_path):
    """A registry that names the renderer directly (he did that once by hand,
    2026-09-15) is not "the plugin the user had"."""
    registry = FakeRegistry({(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE): RENDERER_DLL})
    layer, registry, _ = make_layer(pj64_dir, dll_source, settings_path, registry=registry)
    layer.install(True)
    assert json.loads(settings_path.read_text())["previous_graphics_dll"] is None
    layer.uninstall()
    assert registry.get(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE) == WRAPPER_DLL   # nothing known to restore

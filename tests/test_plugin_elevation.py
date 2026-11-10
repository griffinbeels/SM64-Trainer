"""Installer retry, real Windows image locks, and the privileged copy boundary.

All files are disposable. No test asks for UAC or touches live Project64.
"""
from contextlib import contextmanager
import ctypes
import errno
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import threading

import pytest

from sm64_events.core.capturelayer import (
    CaptureLayer, GRAPHICS_DLL_VALUE, LayerRefused, PLUGIN_DLL, REGISTRY_DLL_SUBKEY,
)
from sm64_events.core import plugin_elevation, plugin_elevation_worker, plugin_installation
from sm64_events.core.plugin_installation import (
    ElevationCancelled, PluginInstallError, in_use,
)
from sm64_events.core.renderer_settings import MINIMAL, RENDERER_INI


@pytest.fixture
def install(tmp_path):
    pj64 = tmp_path / "PJ64"
    (pj64 / "Plugin").mkdir(parents=True)
    (pj64 / "Project64.exe").write_bytes(b"fake exe; version checker injected")
    source = tmp_path / PLUGIN_DLL
    source.write_bytes(b"MZ" + b"1" * 64 + b"-gpu-runtime\0")
    settings = tmp_path / "capture_layer.json"
    settings.write_text(json.dumps({"pj64_dir": str(pj64)}))

    class Registry:
        selected = "original.dll"

        def get(self, key, name):
            return self.selected if (key, name) == (REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE) else None

        def set(self, key, name, value):
            self.selected = value

        def delete(self, key, name):
            self.selected = None

    class Processes:
        @staticmethod
        def pj64_image_path():
            return None

    registry = Registry()
    layer = CaptureLayer(registry, Processes(), settings, source)
    return layer, registry, settings, pj64, source


def deny_first_copy(monkeypatch):
    copy = plugin_installation.shutil.copyfile
    attempts = []

    def denied_once(source, destination):
        attempts.append(destination)
        if len(attempts) == 1:
            error = PermissionError(errno.EACCES, "Access is denied", str(destination))
            error.winerror = 5
            raise error
        return copy(source, destination)

    monkeypatch.setattr(plugin_installation.shutil, "copyfile", denied_once)
    return attempts


def test_denied_write_immediately_retries_and_commits_in_original_user(install, monkeypatch):
    layer, registry, settings, pj64, source = install
    attempts = deny_first_copy(monkeypatch)
    elevated = []

    @contextmanager
    def helper(*args):
        elevated.append(args)
        assert registry.selected == "original.dll"
        assert not json.loads(settings.read_text()).get("consented_at")
        with layer._install_plugin_files(pj64, source, elevated=False) as receipts:
            yield receipts
        assert registry.selected == PLUGIN_DLL

    monkeypatch.setattr(plugin_elevation, "elevated_install", helper)
    result = layer.install(True)
    assert len(elevated) == 1 and len(attempts) == 2
    assert result.installation_verified
    assert json.loads(settings.read_text())["consented_at"]
    assert (pj64 / "Plugin" / PLUGIN_DLL).read_bytes() == source.read_bytes()


def test_uac_cancel_is_distinct_and_does_not_select_or_record_install(install, monkeypatch):
    layer, registry, settings, pj64, _ = install
    before = settings.read_bytes()
    deny_first_copy(monkeypatch)

    @contextmanager
    def cancelled(*args):
        error = OSError("The operation was canceled by the user")
        error.winerror = 1223
        raise ElevationCancelled("request administrator permission", pj64, error, reason="cancelled")
        yield  # pragma: no cover

    monkeypatch.setattr(plugin_elevation, "elevated_install", cancelled)
    with pytest.raises(ElevationCancelled) as raised:
        layer.install(True)
    assert raised.value.winerror == 1223
    assert raised.value.reason == "cancelled"
    assert registry.selected == "original.dll" and settings.read_bytes() == before
    assert not list((pj64 / "Plugin").iterdir())


def test_per_user_save_failure_never_prompts_and_restores_files(install, monkeypatch):
    layer, registry, settings, pj64, _ = install
    before = settings.read_bytes()

    def failed_save(overlay):
        raise PermissionError("The user's settings folder is read-only")

    def forbidden_helper(*args):
        pytest.fail("Per-user metadata must never cause elevation")

    monkeypatch.setattr(layer, "_save_overlay", failed_save)
    monkeypatch.setattr(plugin_elevation, "elevated_install", forbidden_helper)
    with pytest.raises(PermissionError):
        layer.install(True)
    assert settings.read_bytes() == before and registry.selected == "original.dll"
    assert not list((pj64 / "Plugin").iterdir())


def test_terminal_admin_error_retains_actual_operation_path_and_windows_code(install, monkeypatch):
    layer, _, _, pj64, _ = install
    deny_first_copy(monkeypatch)
    error = OSError(errno.ENOSPC, "Disk full")
    error.winerror = 112

    @contextmanager
    def failed(*args):
        raise PluginInstallError("copy plugin", pj64 / "Plugin" / PLUGIN_DLL, error)
        yield  # pragma: no cover

    monkeypatch.setattr(plugin_elevation, "elevated_install", failed)
    with pytest.raises(PluginInstallError) as raised:
        layer.install(True)
    assert raised.value.operation == "copy plugin"
    assert raised.value.filename == str(pj64 / "Plugin" / PLUGIN_DLL)
    assert raised.value.winerror == 112


@pytest.mark.parametrize("original", ["original.dll", None])
def test_helper_commit_failure_restores_original_user_selection_and_files(install, monkeypatch, original):
    layer, registry, settings, pj64, source = install
    registry.selected = original
    before = settings.read_bytes()
    deny_first_copy(monkeypatch)
    terminal = PluginInstallError("commit administrator install", pj64 / "Plugin",
                                 OSError("Administrator helper acknowledgment failed"))

    @contextmanager
    def failed_commit(*args):
        with layer._install_plugin_files(pj64, source, elevated=False) as receipts:
            yield receipts
            assert registry.selected == PLUGIN_DLL
            assert json.loads(settings.read_text())["consented_at"]
            raise terminal

    monkeypatch.setattr(plugin_elevation, "elevated_install", failed_commit)
    with pytest.raises(PluginInstallError) as raised:
        layer.install(True)
    assert raised.value is terminal
    assert registry.selected == original
    assert settings.read_bytes() == before
    assert not list((pj64 / "Plugin").iterdir())


def test_registry_rollback_failure_logs_separately_and_preserves_root_error(install, monkeypatch, caplog):
    layer, registry, _, pj64, source = install
    deny_first_copy(monkeypatch)
    terminal = PluginInstallError("commit administrator install", pj64 / "Plugin",
                                 OSError("Root helper failure"))

    def fail_restore(key, name, value):
        if value != PLUGIN_DLL:
            raise PermissionError("Registry rollback was refused")
        registry.selected = value

    @contextmanager
    def failed_commit(*args):
        with layer._install_plugin_files(pj64, source, elevated=False) as receipts:
            yield receipts
            raise terminal

    monkeypatch.setattr(registry, "set", fail_restore)
    monkeypatch.setattr(plugin_elevation, "elevated_install", failed_commit)
    with pytest.raises(PluginInstallError) as raised:
        layer.install(True)
    assert raised.value is terminal
    rollback = [record for record in caplog.records if "restore Project64's previous" in record.message]
    assert len(rollback) == 1
    assert isinstance(rollback[0].exc_info[1], PermissionError)


@pytest.mark.skipif(os.name != "nt", reason="Windows mapped-image semantics")
def test_real_mapped_dll_access_denied_is_in_use_and_never_elevates(install, monkeypatch):
    layer, registry, _, pj64, _ = install
    destination = pj64 / "Plugin" / PLUGIN_DLL
    shutil.copyfile(Path(os.environ["SystemRoot"]) / "System32" / "version.dll", destination)
    library = ctypes.WinDLL(str(destination))
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.FreeLibrary.argtypes = [ctypes.c_void_p]

    def forbidden_helper(*args):
        pytest.fail("A real mapped DLL must never trigger UAC")

    monkeypatch.setattr(plugin_elevation, "elevated_install", forbidden_helper)
    try:
        with pytest.raises(PermissionError) as denied:
            destination.open("r+b")
        from sm64_events.core.plugin_elevation_win import write_access_error
        assert denied.value.errno == errno.EACCES
        assert write_access_error(destination) in (5, 32)
        assert in_use(destination)
        with pytest.raises(LayerRefused, match="close Project64"):
            layer.install(True)
        assert registry.selected == "original.dll"
    finally:
        assert kernel.FreeLibrary(library._handle)


@pytest.fixture
def privileged_request(install, monkeypatch):
    _, _, _, pj64, source = install
    from sm64_events.core import capturelayer_win, paths
    monkeypatch.setattr(paths, "bundled_plugin_dll", lambda: source)
    monkeypatch.setattr(capturelayer_win.WinProcesses, "check_folder", lambda folder: {"state": "ready"})
    monkeypatch.setattr(capturelayer_win.WinProcesses, "pj64_image_path", lambda self: None)
    return {"pj64_dir": str(pj64), "plugin_dir": str(pj64 / "Plugin"),
            "plugin_sha256": plugin_installation.sha256(source)}


@pytest.mark.parametrize("change", [
    {"source": "C:/Windows/System32/evil.dll"},
    {"plugin_dir": "C:/Windows/System32"},
    {"plugin_sha256": "0" * 64},
    {"pj64_dir": "C:/somewhere/../Windows"},
])
def test_privileged_boundary_rejects_tampered_request(privileged_request, change):
    with pytest.raises((ValueError, OSError)):
        plugin_elevation_worker.validate_request({**privileged_request, **change})


def test_wrong_bundle_role_is_refused(privileged_request, install):
    source = install[4]
    source.write_bytes(b"MZ" + b"1" * 64 + b"-renderer\0")
    privileged_request["plugin_sha256"] = plugin_installation.sha256(source)
    with pytest.raises(ValueError, match="role"):
        plugin_elevation_worker.validate_request(privileged_request)


@pytest.mark.skipif(os.name != "nt", reason="Windows handle pinning")
def test_privileged_transaction_rolls_back_all_files_on_parent_failure(privileged_request, install):
    plugin = install[3] / "Plugin"
    (plugin / PLUGIN_DLL).write_bytes(b"old wrapper")
    (plugin / RENDERER_INI).write_bytes(MINIMAL.replace(b"enable=0", b"enable=1"))
    before = {path.name: path.read_bytes() for path in plugin.iterdir()}
    with pytest.raises(OSError, match="parent settings failed"):
        with plugin_elevation_worker.privileged_files(privileged_request):
            assert (plugin / PLUGIN_DLL).read_bytes() == install[4].read_bytes()
            assert (plugin / RENDERER_INI).read_bytes() == MINIMAL
            raise OSError("parent settings failed")
    assert {path.name: path.read_bytes() for path in plugin.iterdir()} == before


FIXUPS = b"MZ the tested fixups"


@pytest.fixture
def fixups_request(privileged_request, tmp_path, monkeypatch):
    """The request with a kept PJ64 Fixups download whose hash the bundled
    baseline names (the test stores that baseline, never the shipped one)."""
    import hashlib
    from sm64_events.core import setup_profile
    kept = tmp_path / "downloads" / "pj64fixups-RSP.dll"
    kept.parent.mkdir()
    kept.write_bytes(FIXUPS)
    monkeypatch.setattr(setup_profile, "load_baseline", lambda *a: {
        "items": [{"id": "rsp_plugin", "sha256": hashlib.sha256(FIXUPS).hexdigest()}]})
    return {**privileged_request, "fixups_dll": str(kept)}, kept


@pytest.mark.skipif(os.name != "nt", reason="Windows handle pinning")
def test_privileged_transaction_adds_the_fixups_and_rolls_them_back_with_the_plugin(fixups_request, install):
    request, _ = fixups_request
    plugin = install[3] / "Plugin"
    (plugin / "RSP.dll").write_bytes(b"stock rsp")
    before = {path.name: path.read_bytes() for path in plugin.iterdir()}
    with pytest.raises(OSError, match="parent settings failed"):
        with plugin_elevation_worker.privileged_files(request):
            assert (plugin / "RSP.dll").read_bytes() == FIXUPS
            assert (plugin / "RSP.before-fixups").read_bytes() == b"stock rsp"
            raise OSError("parent settings failed")
    assert {path.name: path.read_bytes() for path in plugin.iterdir()} == before
    with plugin_elevation_worker.privileged_files(request):
        pass
    assert (plugin / "RSP.dll").read_bytes() == FIXUPS
    assert (plugin / "RSP.before-fixups").read_bytes() == b"stock rsp"
    # Already the tested file: a second install touches neither file again.
    (plugin / "RSP.before-fixups").write_bytes(b"kept as it was")
    with plugin_elevation_worker.privileged_files(request):
        pass
    assert (plugin / "RSP.before-fixups").read_bytes() == b"kept as it was"


@pytest.mark.skipif(os.name != "nt", reason="Windows handle pinning")
def test_the_helper_copies_only_the_tested_fixups_file(fixups_request, install):
    request, kept = fixups_request
    plugin = install[3] / "Plugin"
    kept.write_bytes(b"MZ some other dll")
    with pytest.raises(ValueError, match="not the tested release"):
        with plugin_elevation_worker.privileged_files(request):
            pytest.fail("An unverified file passed the privilege boundary")
    assert not (plugin / "RSP.dll").exists() and not (plugin / PLUGIN_DLL).exists()
    with pytest.raises(ValueError):
        plugin_elevation_worker.privileged_files({**request, "fixups_dll": "relative/RSP.dll"}).__enter__()


@pytest.mark.skipif(os.name != "nt", reason="Windows hardlink/handle pinning")
def test_privileged_destination_cannot_be_a_hardlink(privileged_request, install, tmp_path):
    protected = tmp_path / "unrelated.bin"
    protected.write_bytes(b"leave alone")
    os.link(protected, install[3] / "Plugin" / PLUGIN_DLL)
    with pytest.raises(PermissionError, match="linked"):
        with plugin_elevation_worker.privileged_files(privileged_request):
            pytest.fail("Hardlink passed privilege boundary")
    assert protected.read_bytes() == b"leave alone"


def test_ipc_rejects_modified_authenticated_packet():
    secret = b"a" * 32
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        with socket.create_connection(listener.getsockname()) as sender:
            receiver, _ = listener.accept()
            with receiver:
                plugin_elevation.send_packet(sender, secret, {"action": "commit"})
                with pytest.raises(ValueError, match="authentication"):
                    plugin_elevation.receive_packet(receiver, b"b" * 32)


def test_frozen_helper_dispatch_precedes_every_normal_app_import(monkeypatch):
    import gui_entry
    seen = []
    monkeypatch.setattr(sys, "argv", ["SM64Trainer.exe", plugin_elevation.ADMIN_INSTALL_FLAG, "args"])
    monkeypatch.setattr(plugin_elevation_worker, "main", lambda args: seen.append(args) or 17)
    assert gui_entry.main() == 17
    assert seen == [["args"]]


@pytest.mark.skipif(os.name != "nt", reason="Windows privileged file transaction")
def test_authenticated_parent_helper_transaction_without_real_uac(privileged_request, install, monkeypatch):
    from sm64_events.core import plugin_elevation_win
    layer, registry, settings, pj64, _ = install
    outcomes, threads = [], []
    deny_first_copy(monkeypatch)
    monkeypatch.setattr(plugin_elevation_win, "process_image", lambda pid: Path(sys.executable))

    class ThreadProcess:
        pid = os.getpid()

        def exited(self):
            return not threads[0].is_alive()

        def close(self):
            threads[0].join(timeout=5)
            assert not threads[0].is_alive(), "Helper must not outlive its transaction"

    def launch(argv):
        thread = threading.Thread(target=lambda: outcomes.append(plugin_elevation_worker.main(argv[-3:])))
        threads.append(thread)
        thread.start()
        return ThreadProcess()

    monkeypatch.setattr(plugin_elevation_win, "launch_admin", launch)
    assert layer.install(True).installation_verified
    assert outcomes == [0]
    assert registry.selected == PLUGIN_DLL
    assert json.loads(settings.read_text())["consented_at"]
    # The administrator half writes the one plugin file and the renderer's
    # settings file, and nothing else.
    assert sorted(path.name for path in (pj64 / "Plugin").iterdir()) == sorted([PLUGIN_DLL, RENDERER_INI])
    assert (pj64 / "Plugin" / RENDERER_INI).read_bytes() == MINIMAL


@pytest.mark.skipif(os.name != "nt", reason="Windows privileged file transaction")
def test_one_permission_prompt_installs_the_plugin_and_the_fixups(fixups_request, install, monkeypatch):
    """The capture layer hands its kept fixups download to the same helper
    transaction, through the real authenticated channel (no real UAC)."""
    from types import SimpleNamespace
    from sm64_events.core import plugin_elevation_win
    _, kept = fixups_request
    layer, registry, _, pj64, _ = install
    layer.fixups = SimpleNamespace(cached=lambda: kept)
    (pj64 / "Plugin" / "RSP.dll").write_bytes(b"stock rsp")
    outcomes, threads, launches = [], [], []
    deny_first_copy(monkeypatch)
    monkeypatch.setattr(plugin_elevation_win, "process_image", lambda pid: Path(sys.executable))

    class ThreadProcess:
        pid = os.getpid()

        def exited(self):
            return not threads[0].is_alive()

        def close(self):
            threads[0].join(timeout=5)

    def launch(argv):
        launches.append(argv)
        thread = threading.Thread(target=lambda: outcomes.append(plugin_elevation_worker.main(argv[-3:])))
        threads.append(thread)
        thread.start()
        return ThreadProcess()

    monkeypatch.setattr(plugin_elevation_win, "launch_admin", launch)
    assert layer.install(True).installation_verified
    assert outcomes == [0] and len(launches) == 1 and registry.selected == PLUGIN_DLL
    assert (pj64 / "Plugin" / "RSP.dll").read_bytes() == FIXUPS
    assert (pj64 / "Plugin" / "RSP.before-fixups").read_bytes() == b"stock rsp"

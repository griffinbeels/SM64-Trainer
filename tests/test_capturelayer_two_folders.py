"""Two Project64 test folders on one Windows account, the trainer installed in both.

Griffin keeps several Project64 copies side by side and switches between them
(2026-10-30). The capture layer's overlay remembers ONE `pj64_dir` and ONE
`owned_sha256`, while the plugin selection (HKCU) is shared by every copy.
These tests show what survives a switch and what does not.
"""
import json

import pytest

from sm64_events.core.capturelayer import WRAPPER_DLL
from tests.test_capturelayer import build, dll_source, make_layer, renderer_for, settings_path  # noqa: F401 - fixtures


def folder(tmp_path, name):
    path = tmp_path / name
    (path / "Plugin").mkdir(parents=True)
    return path


def run_then_close(processes, layer, pj64):
    """Project64 runs from `pj64` (locate() remembers it), then closes."""
    processes.image_path = str(pj64 / "Project64.exe")
    assert layer.locate() == pj64
    processes.image_path = None


@pytest.fixture
def two_installs(tmp_path, dll_source, settings_path):
    first, second = folder(tmp_path, "01-wermi-v7"), folder(tmp_path, "02-stock-vanilla")
    layer, registry, processes = make_layer(first, dll_source, settings_path)
    layer.install(True)
    run_then_close(processes, layer, second)
    layer.install(True)
    return layer, processes, first, second


def test_switching_back_keeps_the_first_folders_install_and_its_automatic_refresh(two_installs, settings_path):
    layer, processes, first, second = two_installs
    assert json.loads(settings_path.read_text())["pj64_dir"] == str(second)
    run_then_close(processes, layer, first)
    status = layer.status()
    assert status.pj64_dir == str(first)
    assert status.installation_verified and status.automatic_update
    assert (second / "Plugin" / WRAPPER_DLL).read_bytes() == (first / "Plugin" / WRAPPER_DLL).read_bytes()


@pytest.mark.xfail(strict=True, reason=(
    "Known gap, reported 2026-10-30, not redesigned: the overlay keeps one owned_sha256. "
    "An update refreshed into the folder that ran last rewrites it, so the other folder's "
    "older wrapper no longer matches, automatic refresh stops for it, and it asks for Install."))
def test_an_update_refreshed_into_one_folder_still_refreshes_the_other(two_installs, dll_source):
    layer, processes, first, second = two_installs
    dll_source.write_bytes(build(b"a newer capture layer"))
    renderer_for(dll_source).write_bytes(build(b"a newer renderer"))
    assert layer.refresh_if_stale() is True              # updates the remembered folder: the second
    assert (second / "Plugin" / WRAPPER_DLL).read_bytes() == dll_source.read_bytes()
    run_then_close(processes, layer, first)
    assert layer.status().automatic_update is True

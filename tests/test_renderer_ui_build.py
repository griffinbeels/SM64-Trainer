"""The renderer's settings window, GLideNUI.lib, is built from pinned sources.

tools/build_renderer_ui.py builds a static qtbase and upstream's GLideNUI.vcxproj
against it; tools/build_renderer.py finds the result in the shared build cache
by its hash. Since 2026-11-07 the Qt is 5.12.12: from 5.14 on, QtCore's
qcalendar.cpp is GPL v3 or later only, which GLideN64 (GPL v2 only) cannot be
combined with (renderer/README.md). The Qt build itself needs MSVC and takes a
long while, so these tests check the recipe's inputs and staging, not a build.
"""
import importlib.util
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _tool(name):
    spec = importlib.util.spec_from_file_location(f"test_{name}", ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


renderer = _tool("build_renderer")
ui = _tool("build_renderer_ui")


def test_the_pinned_qt_has_no_gpl3_only_calendar():
    """QCalendar (qcalendar.cpp, GPL-3.0-or-later only) is in qtbase 5.14 to
    5.15; Qt 6 offers it under the GPL v2 again. A bump into that range would
    put GPL v3-only code into the GPL v2-only plugin."""
    spec = renderer.inputs()["qt"]
    version = tuple(int(part) for part in spec["version"].split("."))
    assert version < (5, 14) or version >= (6, 0), spec["version"]
    assert spec["version"] in spec["url"] and spec["url"].startswith("https://download.qt.io/")
    assert len(spec["md5"]) == 32 and len(spec["sha256"]) == 64
    assert "-static" in spec["configure"] and "-no-freetype" in spec["configure"]
    assert len(renderer.inputs()["jom"]["sha256"]) == 64


def test_a_prebuilt_library_is_found_by_its_hash_and_a_stale_one_is_refused(tmp_path, monkeypatch):
    new, old = b"the settings window, rebuilt", b"the settings window, first build"
    digest = lambda data: __import__("hashlib").sha256(data).hexdigest()  # noqa: E731
    pinned = {"GLideNUI.lib": digest(new), "osal.lib": digest(old)}
    monkeypatch.setattr(renderer, "inputs", lambda: {"prebuilt_libraries": pinned})
    by_hash = renderer.prebuilt_path(tmp_path, "GLideNUI.lib", digest(new))
    by_hash.parent.mkdir(parents=True)
    by_hash.write_bytes(new)
    (tmp_path / "GLideNUI.lib").write_bytes(old)   # another checkout's older pin, left in place
    (tmp_path / "osal.lib").write_bytes(old)       # a first build, kept at the cache root
    found = renderer.prebuilt_libraries(tmp_path)
    assert found == {"GLideNUI.lib": by_hash, "osal.lib": tmp_path / "osal.lib"}
    by_hash.write_bytes(b"tampered")
    with pytest.raises(FileNotFoundError, match="GLideNUI.lib"):
        renderer.prebuilt_libraries(tmp_path)


@pytest.fixture(scope="module")
def upstream_tree(tmp_path_factory):
    cache = renderer.default_cache()
    archive = cache / f"GLideN64-{renderer.inputs()['upstream']['commit']}.zip"
    if not archive.is_file():
        pytest.skip(f"the pinned upstream archive is not in the build cache ({archive})")
    return renderer.pristine_source(cache, tmp_path_factory.mktemp("upstream"), None)


def test_the_settings_window_headers_get_their_own_include_guards(upstream_tree):
    """Upstream's AboutDialog.h and ConfigDialog.h guard themselves with the
    macro uic writes into ui_AboutDialog.h / ui_configDialog.h, so whichever is
    included second vanishes. The staging renames only the guards."""
    folder = upstream_tree / "src" / "GLideNUI"
    before = {name: (folder / name).read_bytes() for name in ui.GUARDED_HEADERS}
    ui.stage_ui_sources(upstream_tree)
    for name, guard in ui.GUARDED_HEADERS.items():
        after = (folder / name).read_bytes()
        assert not re.search(rb"\b" + guard + rb"\b", after) and after.startswith(b"#ifndef GLIDENUI_")
        assert after == before[name].replace(guard, b"GLIDENUI_" + guard[3:])
    assert {int(os.stat(path).st_mtime) for path in folder.iterdir()} == {ui.FIXED_MTIME}


def test_the_settings_window_project_links_the_support_modules_5_12_needs(upstream_tree, tmp_path):
    project = upstream_tree / "projects" / "msvc" / "GLideNUI.vcxproj"
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "Qt5AccessibilitySupport.lib").write_bytes(b"")
    ui.stage_ui_project(project, tmp_path)
    data = project.read_bytes()
    assert data.count(ui.LIB_ANCHOR + b"Qt5AccessibilitySupport.lib;") == 1
    assert b"<AdditionalOptions>/Brepro %(AdditionalOptions)</AdditionalOptions>" in data
    with pytest.raises(ValueError, match="anchor"):
        ui.stage_ui_project(project, tmp_path)   # staged twice: the anchor no longer stands alone

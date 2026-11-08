"""The renderer's prebuilt libraries are built from pinned sources, without GPL v3-only code.

tools/build_renderer_libs.py builds a static qtbase, then upstream's
libGLideNHQ.vcxproj and GLideNUI.vcxproj from one staged copy of the pinned
tree; tools/build_renderer.py finds each result in the shared build cache by
its hash. GLideN64 is GPL v2 only (renderer/README.md), so since 2026-11-07:
- the Qt is 5.12.12, because from 5.14 on QtCore's qcalendar.cpp is GPL v3 or
  later only;
- xBRZ (GLideNHQ's TextureFilters_xbrz.*, GPL v3 only) is left out by
  renderer/patches/no-xbrz.json.
The Qt build itself needs MSVC and takes a long while, so these tests check
the recipe's inputs and staging, not a build.
"""
import hashlib
import importlib.util
import json
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
libs = _tool("build_renderer_libs")


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
    digest = lambda data: hashlib.sha256(data).hexdigest()  # noqa: E731
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


def test_a_patch_whose_anchor_is_not_unique_stops_the_build(tmp_path):
    (tmp_path / "a.cpp").write_bytes(b"case X:\ncase X:\n")
    patch = tmp_path / "p.json"
    patch.write_text(json.dumps({"edits": [{"file": "a.cpp", "before": "case X:\n", "after": ""}]}),
                     encoding="utf-8")
    with pytest.raises(ValueError, match="exactly once"):
        libs.apply_patch(tmp_path, patch)
    assert (tmp_path / "a.cpp").read_bytes() == b"case X:\ncase X:\n"


@pytest.fixture
def staged_tree(tmp_path):
    cache = renderer.default_cache()
    archive = cache / f"GLideN64-{renderer.inputs()['upstream']['commit']}.zip"
    if not archive.is_file():
        pytest.skip(f"the pinned upstream archive is not in the build cache ({archive})")
    tree = renderer.pristine_source(cache, tmp_path / "upstream", None)
    prefix = tmp_path / "qt"
    (prefix / "lib").mkdir(parents=True)
    (prefix / "lib" / "Qt5AccessibilitySupport.lib").write_bytes(b"")
    libs.stage(tree, prefix)
    return tree


def _compiled_text(tree: Path) -> str:
    """Every source and project file the two library builds read, comments removed."""
    names = [p for folder in ("src/GLideNHQ", "src/GLideNUI") for p in (tree / folder).iterdir()
             if p.suffix in (".cpp", ".h", ".ui") and "xbrz" not in p.name.lower()]
    names += [tree / "projects/msvc" / name for name in libs.LIBRARIES.values()]
    text = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in names)
    return re.sub(r"//[^\n]*|/\*.*?\*/", "", text, flags=re.S)


def test_no_xbrz_code_or_option_reaches_the_staged_libraries(staged_tree):
    text = _compiled_text(staged_tree)
    assert not re.search(r"(?i)xbrz", text), re.findall(r"[^\n]*(?i:xbrz)[^\n]*", text)[:5]
    # The five xBRZ modes enhance nothing, and an ini that names one loads as None.
    txfilter = (staged_tree / "src/GLideNHQ/TxFilter.cpp").read_text(encoding="utf-8")
    assert re.search(r"case BRZ2X_ENHANCEMENT:\s*case BRZ3X_ENHANCEMENT:\s*case BRZ4X_ENHANCEMENT:\s*"
                     r"case BRZ5X_ENHANCEMENT:\s*case BRZ6X_ENHANCEMENT:\s*(//[^\n]*\s*)?break;", txfilter)
    settings = (staged_tree / "src/GLideNUI/Settings.cpp").read_text(encoding="utf-8")
    assert "if (config.textureFilter.txEnhancementMode > 8)" in settings
    # The settings window keeps the other nine modes, in order (their numbers are the ini's).
    ui = (staged_tree / "src/GLideNUI/configDialog.ui").read_text(encoding="utf-8")
    combo = ui[ui.index('name="enhancementComboBox"'):]
    combo = combo[:combo.index("</widget>")]
    assert re.findall(r">([^<>]+)</string>", combo) == [
        "None", "Store", "X2", "X2SAI", "HQ2X", "HQ2XS", "LQ2X", "LQ2XS", "HQ4X"]


def test_the_patch_still_fits_the_pinned_tree_and_only_once(staged_tree, tmp_path):
    """Staging a second time finds no anchor: each edit landed exactly once."""
    with pytest.raises(ValueError, match="exactly once"):
        libs.apply_patch(staged_tree, libs.PATCHES[0])


def test_the_settings_window_headers_get_their_own_include_guards(staged_tree):
    """Upstream's AboutDialog.h and ConfigDialog.h guard themselves with the
    macro uic writes into ui_AboutDialog.h / ui_configDialog.h, so whichever is
    included second vanishes. The staging renames only the guards."""
    folder = staged_tree / "src" / "GLideNUI"
    for name, guard in libs.GUARDED_HEADERS.items():
        after = (folder / name).read_bytes()
        assert not re.search(rb"\b" + guard + rb"\b", after) and after.startswith(b"#ifndef GLIDENUI_")
    assert {int(os.stat(path).st_mtime) for path in folder.iterdir()} == {libs.FIXED_MTIME}


def test_both_library_projects_build_repeatable_and_link_what_5_12_needs(staged_tree):
    msvc = staged_tree / "projects" / "msvc"
    ui = (msvc / "GLideNUI.vcxproj").read_bytes()
    assert ui.count(libs.LIB_ANCHOR + b"Qt5AccessibilitySupport.lib;") == 1
    for project in libs.LIBRARIES.values():
        assert (msvc / project).read_bytes().count(libs.BREPRO) == 1, project

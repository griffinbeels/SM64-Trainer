"""A built bundled native is the one built from THESE sources.

The two are build outputs, never tracked (tools/build_natives.py builds them
at launch, wrap and release). Where a checkout has one, it must match.

The setup screen compares an installed file with the bundled one by embedded
build id (core/capturelayer.py::_files_match). A bundle older than the tree
tells him his freshly installed candidate "differs from this build" and offers
to put the stale DLL back (his report, 2026-09-15); a bundle newer than the
tree ships code the tests never saw. Each id names the exact sources:

- the graphics plugin (one file since 2026-11-07) carries
  tools/build_renderer.py's digest of the pinned upstream tree, the overlay
  and capture layer sources, the staging tools and the recipe;
- the encoder helper carries tools/build_plugin.py's digest of plugin/gfxwrap
  with its own suffix.

No toolchain is needed to check; rebuilding is `tools/build_natives.py`.
"""
import importlib.util
from pathlib import Path

import pytest

from sm64_events.core.capturelayer import _build_identity
from sm64_events.core.paths import bundled_encoder_dll, bundled_plugin_dll

ROOT = Path(__file__).resolve().parents[1]
NOT_BUILT = "native not built in this checkout (tools/build_natives.py)"


def _tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("bundled, expected, rebuild", [
    (bundled_plugin_dll, lambda: _tool("build_renderer").plugin_build_id(), "tools/build_natives.py"),
    (bundled_encoder_dll, lambda: _tool("build_plugin").helper_build_id(), "tools/build_natives.py"),
], ids=["plugin", "encoder-helper"])
def test_the_bundled_native_was_built_from_these_sources(bundled, expected, rebuild):
    path = bundled()
    if path is None:
        pytest.skip(f"{bundled.__name__}: {NOT_BUILT}")
    actual = _build_identity(path)
    assert actual == expected().encode(), (
        f"{path.name} was built from other sources (id {actual!r}): run {rebuild}")


def test_the_packaged_exe_carries_both():
    """tools/build_exe.py must add every bundled native, or the installed app
    installs no plugin or spawns a helper with no DLL.

    Asked of the ARGUMENTS PyInstaller is actually given, not of the file's
    text: a comment naming a DLL satisfies a text search and packages nothing.
    The destination matters as much as the name -- core/paths.py finds these
    under `sm64_events/data/plugin` inside the frozen app."""
    build = _tool("build_exe")
    argv = build.app_args(None)
    packaged = {}
    for flag, value in zip(argv, argv[1:], strict=False):
        if flag == "--add-data":
            source, _, destination = value.rpartition(build.SEP)
            packaged[Path(source).name] = destination
    for name in _tool("native_identity").SHIPPED:
        assert packaged.get(name) == "sm64_events/data/plugin", (
            f"tools/build_exe.py does not package {name} into the plugin "
            f"folder (it lands at {packaged.get(name)!r})")


def test_renderer_inputs_are_pinned_by_hash():
    """The renderer's build inputs are named in one tracked file that the build
    tool refuses to deviate from: the upstream archive, its tree, and the
    three prebuilt libraries."""
    build = _tool("build_renderer")
    inputs = build.inputs()
    assert inputs["upstream"]["commit"] == build.witness.PIN
    assert inputs["upstream"]["tree_sha256"] == build.witness.TREE_HASH
    assert len(inputs["upstream"]["archive_sha256"]) == 64
    assert set(inputs["prebuilt_libraries"]) == {"GLideNUI.lib", "libGLideNHQ.lib", "osal.lib"}
    assert all(len(digest) == 64 for digest in inputs["prebuilt_libraries"].values())

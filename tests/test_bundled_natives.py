"""The three bundled native files are the ones built from THESE sources.

The setup screen compares an installed file with the bundled one by embedded
build id (core/capturelayer.py::_files_match). A bundle older than the tree
tells him his freshly installed candidate "differs from this build" and offers
to put the stale DLL back (his report, 2026-09-15); a bundle newer than the
tree ships code the tests never saw. Each id names the exact sources:

- the wrapper and the encoder helper share tools/build_plugin.py's digest of
  plugin/gfxwrap with their own suffixes;
- the renderer carries tools/build_renderer.py's digest of the pinned upstream
  tree, the overlay sources, the staging tool and the recipe.

No toolchain is needed to check; rebuilding is `tools/build_plugin.py` and
`tools/build_renderer.py`.
"""
import importlib.util
from pathlib import Path

import pytest

from sm64_events.core.capturelayer import _build_identity
from sm64_events.core.paths import bundled_encoder_dll, bundled_plugin_dll, bundled_renderer_dll

ROOT = Path(__file__).resolve().parents[1]


def _tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("bundled, expected, rebuild", [
    (bundled_plugin_dll, lambda: _tool("build_plugin").wrapper_build_id() + "-gpu-runtime", "tools/build_plugin.py"),
    (bundled_encoder_dll, lambda: _tool("build_plugin").helper_build_id(), "tools/build_plugin.py"),
    (bundled_renderer_dll, lambda: _tool("build_renderer").renderer_build_id(), "tools/build_renderer.py"),
], ids=["wrapper", "encoder-helper", "renderer"])
def test_the_bundled_native_was_built_from_these_sources(bundled, expected, rebuild):
    path = bundled()
    assert path is not None, f"{bundled.__name__} is not built: run {rebuild}"
    actual = _build_identity(path)
    assert actual == expected().encode(), (
        f"{path.name} was built from other sources (id {actual!r}): run {rebuild}")


def test_the_packaged_exe_carries_all_three():
    """tools/build_exe.py must add every bundled native, or the installed app
    installs a wrapper with no renderer and spawns a helper with no DLL."""
    text = (ROOT / "tools" / "build_exe.py").read_text(encoding="utf-8")
    for name in ("sm64_trainer_gfx.dll", "GLideN64_SM64Trainer.dll", "SM64GpuEncoderV1.dll"):
        assert name in text, f"tools/build_exe.py does not package {name}"


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

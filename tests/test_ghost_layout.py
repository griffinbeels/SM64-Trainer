"""The one Python statement of the native ghost layout matches the compiled headers.

ghost/native_layout.py restates sizes, offsets, versions and limits once;
tests/native/ghost_layout_host.cpp prints the same names from sizeof, offsetof
and the C macros. A wrong or missing value fails here, not as a misread clip.
"""
import importlib.util
import ctypes as C
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.ghost import clip, native_layout as L, native_protocol as P
from sm64_events.memory import ghost_layout
from sm64_events.memory.layout import US

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def native(tmp_path_factory):
    if sys.platform != "win32":
        pytest.skip("native MSVC layout witness")
    work = tmp_path_factory.mktemp("ghost_layout")
    spec = importlib.util.spec_from_file_location("build_plugin", ROOT / "tools/build_plugin.py")
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    vcvars = compiler.find_vcvars32()
    assert vcvars, "x86 MSVC toolchain is required for this native witness"
    target = work / "ghost_layout_host.exe"
    compiler._cl(vcvars, ["/nologo", "/W4", "/MT", "/EHsc", "/std:c++17", f"/I{ROOT / 'plugin/gfxwrap'}",
        f"/I{ROOT / 'renderer/model'}", str(ROOT / "tests/native/ghost_layout_host.cpp"),
        f"/Fe:{target}", f"/Fo{work}\\"], work)
    result = subprocess.run([str(target)], cwd=work, capture_output=True, text=True, timeout=10,
                            check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def python_names():
    return {name: value for name, value in vars(L).items() if name.isupper()}


def test_every_python_value_is_the_compiled_one(native):
    python = python_names()
    wrong = {name: (python[name], native[name]) for name in native
             if name in python and (python[name].decode() if isinstance(python[name], bytes) else python[name])
             != native[name]}
    assert not wrong, f"native_layout.py differs from the headers (python, native): {wrong}"


def test_every_python_value_is_checked(native):
    """A constant the host does not print would be a restatement nobody compares."""
    assert set(python_names()) == set(native), (set(python_names()) ^ set(native))


def test_python_decoders_fill_the_spans_they_read():
    """Each struct format ends where the next named offset begins."""
    assert clip._HEADER.size == L.HEADER_BYTES
    assert clip._PREFIX.size == L.RECORD_PREFIX_BYTES
    # gc_sample's scalar head (bytes .. live_action) runs up to the pose block.
    assert L.RECORD_PREFIX_BYTES + clip._SAMPLE.size == L.POSE_AT
    assert L.WARP_AT + clip._WARP.size == L.CLOCK_AT
    assert clip._CLOCK.size == L.CLOCK_BYTES and L.CLOCK_AT + L.CLOCK_BYTES == L.SHADOW_AT
    assert clip._SOUND.size == L.SOUND_BYTES
    assert L.SOUND_BITS_AT + 4 * L.SOUND_EVENTS == L.SOUND_BYTES
    assert L.PARTICLES_ENTRIES_AT + L.PARTICLE_EVENTS * L.PARTICLE_BYTES == L.PARTICLES_BYTES
    assert L.HELD_AT + L.HELD_BYTES == L.PAD_AT
    assert L.PAD_AT + L.PAD_BYTES == L.RECORD_BYTES
    assert clip._PAD.size == L.PAD_BYTES
    assert len(ghost_layout.GhostLayout.from_layout(US).to_bytes()) == L.LAYOUT_BYTES
    assert ghost_layout.VERSION == L.SAMPLE_VERSION
    assert P.PAGE_BYTES == L.IPC_RING_AT + L.IPC_SLOTS * L.RECORD_BYTES


def test_annotations_fit_in_the_existing_request_region():
    assert C.sizeof(P.Clip) == L.IPC_CLIP_BYTES
    assert C.sizeof(P.Request) == L.IPC_REQUEST_BYTES
    assert C.sizeof(P.Status) == 256
    assert C.sizeof(P.Status) + C.sizeof(P.Request) <= L.IPC_RING_AT
    assert P.Clip.label.offset == P.Clip.clip_path.offset + 2 * L.IPC_PATH_CHARS
    assert P.Request.names_enabled.offset + 28 == P.Request.path_enabled.offset
    assert P.Request.record_ghosts.offset + 4 == P.Request.path_enabled.offset
    # Generation scalars follow the original ghost-path block, without moving it.
    path = ("path_enabled", "path_buttons", "path_source", "path_clip",
            "path_ahead_ms", "path_width", "path_opacity", "path_spacing_ms")
    assert [getattr(P.Request, name).offset for name in path] == list(
        range(P.Request.path_enabled.offset, P.Request.prepare_generation.offset, 4))
    assert [name for name, *_ in P.Request._fields_][-12:] == [*path, "prepare_generation", "activate_generation", "activate_start", "cancel_preparation"]
    assert P.Status.prepared_generation.offset == 240
    assert P.Status.active_generation.offset == 248
    assert P.Clip().label == b""


def test_pad_root_is_optional_and_uses_the_existing_layout():
    configured = ghost_layout.GhostLayout.from_layout(US)
    assert configured.player1_controller == US.player1_controller
    assert int.from_bytes(configured.to_bytes()[188:192], "little") == US.player1_controller
    without = ghost_layout.GhostLayout.from_layout(replace(US, player1_controller=None))
    assert without.player1_controller == 0 and without.to_bytes()[188:192] == bytes(4)
    with pytest.raises(ValueError, match="player1_controller"):
        replace(configured, player1_controller=configured.player1_controller + 1)


def test_usamune_display_bytes_follow_the_pad_and_are_optional():
    """The corner stack hides while Usamune hides his display (2026-10-02)."""
    configured = ghost_layout.GhostLayout.from_layout(US)
    data = configured.to_bytes()
    assert len(data) == L.LAYOUT_BYTES
    assert int.from_bytes(data[192:196], "little") == US.usamune_menu
    assert int.from_bytes(data[196:200], "little") == US.usamune_input_display
    without = ghost_layout.GhostLayout.from_layout(replace(US, usamune_menu=None, usamune_input_display=None))
    assert without.to_bytes()[192:] == bytes(8)
    with pytest.raises(ValueError, match="usamune_menu"):
        replace(configured, usamune_menu=0x7F000000)

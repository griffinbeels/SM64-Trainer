"""Compile and drive the actual allocation-free N64 capture reader, no emulator."""
from dataclasses import replace
import importlib.util
from pathlib import Path
import struct
import subprocess

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.memory import addresses as A
from sm64_events.memory.ghost_layout import GhostLayout
from sm64_events.memory.layout import JP, US, LayoutIncomplete
from sm64_events.sync.address_gates import (
    _check_area_update_counter,
    _check_current_area_pointer,
)

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "plugin/gfxwrap"


@pytest.fixture(scope="module")
def capture_host(tmp_path_factory):
    work = tmp_path_factory.mktemp("ghost_capture")
    spec = importlib.util.spec_from_file_location("ghost_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vcvars = build.find_vcvars32()
    assert vcvars, "native capture tests require the x86 compiler"
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    flags += ["/std:c++17", "/EHsc", f"/I{NATIVE}"]
    target = work / "ghost_capture_host.exe"
    build._cl(vcvars, flags + [
        str(NATIVE / "ghost_capture.cpp"), str(ROOT / "tests/native/ghost_capture_host.cpp"),
        f"/Fe:{target}", f"/Fo{work}\\",
    ], work)
    # The public header also compiles as C, not merely inside the C++ fixture.
    c_source = work / "header.c"
    c_source.write_text('#include "ghost_capture.h"\nint size(void) { return sizeof(gc_sample); }\n',
                        encoding="utf-8")
    build._cl(vcvars, ["/nologo", "/TC", "/c", f"/I{NATIVE}", str(c_source),
                      f"/Fo{work / 'header.obj'}"], work)
    config = work / "layout.bin"
    config.write_bytes(GhostLayout.from_layout(US).to_bytes())
    return target, config


@pytest.mark.parametrize("scenario", [
    "basic", "swizzle", "bounds", "graph", "duplicate", "limits",
    "skip_objects", "values", "unsupported", "action", "epochs", "transitions", "arguments",
])
def test_compiled_capture(capture_host, scenario):
    target, config = capture_host
    result = subprocess.run(
        [str(target), str(config), scenario], cwd=target.parent,
        capture_output=True, text=True, timeout=15, check=False, **quiet_spawn_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"ghost capture passed: {scenario}; config=64 sample=176" in result.stdout


def test_semantic_config_resolves_selected_layout():
    config = GhostLayout.from_layout(US)
    assert config.mario_state == US.mario_struct
    assert config.current_area_pointer == US.current_area_pointer
    assert config.area_update_counter == US.area_update_counter
    assert len(config.to_bytes()) == 64
    assert struct.unpack_from("<II", config.to_bytes()) == (64, 2)
    with pytest.raises(LayoutIncomplete, match="US layout"):
        GhostLayout.from_layout(JP)
    with pytest.raises(LayoutIncomplete, match="current_area_pointer"):
        GhostLayout.from_layout(replace(US, current_area_pointer=None))


@pytest.mark.parametrize("changes", [
    {"rdram_bytes": 0}, {"rdram_bytes": 4 << 20},
    {"global_timer": True}, {"mario_object": -4},
    {"mario_state": A.KSEG0_BASE + (8 << 20) - 4},
    {"global_timer": US.global_timer + 1},
    {"object_pool": A.KSEG0_BASE + (8 << 20) - 0x260},
])
def test_semantic_config_refuses_invalid_ranges(changes):
    with pytest.raises(ValueError):
        replace(GhostLayout.from_layout(US), **changes)


class GateContext:
    """Independent live-gate fixture; no process attach or wall-clock sleeps."""

    def __init__(self):
        self.time = 0.0
        self.ticking = True
        self.timer_matches = True
        self.area_pointer = A.KSEG0_BASE + 0x10000
        self.root = self.area_pointer + 0x100
        self.camera = self.area_pointer + 0x200
        self.area_index = 1

    def candidate(self, name):
        return US.value(name)

    def now(self):
        return self.time

    def sleep(self, seconds):
        self.time += seconds

    def raw(self):
        return self

    def read_u32(self, address):
        return {
            US.current_area_pointer: self.area_pointer,
            US.mario_struct + A.MARIO_AREA_OFF: self.area_pointer,
            self.area_pointer + A.AREA_GRAPH_ROOT_OFF: self.root,
            self.area_pointer + A.AREA_CAMERA_OFF: self.camera,
            US.mario_object: US.object_pool,
        }.get(address, 0)

    def read_u16(self, address):
        if address in (self.root + A.GRAPH_NODE_TYPE_OFF, self.root + A.GRAPH_NODE_FLAGS_OFF):
            return 1
        timer = 100 + (int(self.time * 30) if self.ticking else 0)
        if address == US.area_update_counter:
            return timer
        if address == US.object_pool + A.MARIO_GFX_ANIM_TIMER_OFF:
            return timer if self.timer_matches else 0
        return 0

    def read_s16(self, address):
        return 1 if address == US.curr_area else self.read_u16(address)

    def read_u8(self, address):
        if address in (self.area_pointer + A.AREA_INDEX_OFF, self.root + A.GRAPH_ROOT_AREA_OFF):
            return self.area_index
        return 1 if address == self.area_pointer + A.AREA_FLAGS_OFF else 0


def test_area_pointer_gate_checks_independent_identity():
    context = GateContext()
    assert _check_current_area_pointer(context).status == "verified"
    context.area_index = 2
    assert _check_current_area_pointer(context).status == "failed"
    context.area_pointer = 4
    assert _check_current_area_pointer(context).status == "failed"


def test_visual_counter_gate_requires_repeated_matching_advances():
    assert _check_area_update_counter(GateContext()).status == "verified"
    context = GateContext()
    context.ticking = False
    assert _check_area_update_counter(context).status == "failed"
    context = GateContext()
    context.timer_matches = False
    assert _check_area_update_counter(context).status == "failed"

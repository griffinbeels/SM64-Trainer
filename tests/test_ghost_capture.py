"""Compile and drive the actual allocation-free N64 capture reader, no emulator."""
from dataclasses import replace
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.detectors import counter_epoch
from sm64_events.detectors.anchors import IGT_WRAP_CEILING, NEAR_ZERO_IGT
from sm64_events.memory import addresses as A
from sm64_events.ghost import native_layout as L
from sm64_events.memory.ghost_layout import GhostLayout
from sm64_events.memory.layout import JP, US, LayoutIncomplete
from sm64_events.sync.address_gates import (
    _check_area_update_counter,
    _check_current_area_pointer,
    _check_sound_code,
    _check_sound_consumer,
    _check_sound_producer,
    _check_sound_ring,
    sound_code_words,
)

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "plugin/gfxwrap"


@pytest.fixture(scope="module")
def capture_host(tmp_path_factory):
    work = tmp_path_factory.mktemp("ghost_capture")
    inputs = [NATIVE / name for name in ("ghost_capture.cpp", "ghost_capture.h", "ghost_root.cpp", "ghost_root.h", "ghost_clock.cpp", "ghost_clock.h")]
    inputs += [ROOT / path for path in ("tests/native/ghost_capture_host.cpp", "tests/test_ghost_capture.py",
        "src/sm64_events/memory/ghost_layout.py", "src/sm64_events/memory/layout.py",
        "src/sm64_events/memory/addresses.py", "src/sm64_events/detectors/anchors.py",
        "src/sm64_events/detectors/counter_epoch.py", "tools/build_plugin.py")]
    hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
    spec = importlib.util.spec_from_file_location("ghost_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vcvars = build.find_vcvars32()
    assert vcvars, "native capture tests require the x86 compiler"
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    flags += ["/std:c++17", "/EHsc", f"/I{NATIVE}"]
    target = work / "ghost_capture_host.exe"
    build._cl(vcvars, flags + [
        str(NATIVE / "ghost_capture.cpp"), str(NATIVE / "ghost_root.cpp"), str(NATIVE / "ghost_clock.cpp"),
        str(ROOT / "tests/native/ghost_capture_host.cpp"),
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
    assert hashes == {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}, \
        "capture source changed during compilation"
    (work / "capture-source.json").write_text(json.dumps({"source_sha256": hashes,
        "executable_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "layout_sha256": hashlib.sha256(config.read_bytes()).hexdigest()}, indent=2), encoding="utf-8")
    return target, config


@pytest.mark.parametrize("scenario", [
    "basic", "swizzle", "bounds", "graph", "duplicate", "limits",
    "skip_objects", "values", "unsupported", "action", "warp", "epochs", "transitions", "arguments",
    "sound", "held",
])
def test_compiled_capture(capture_host, scenario):
    target, config = capture_host
    result = subprocess.run(
        [str(target), str(config), scenario], cwd=target.parent,
        capture_output=True, text=True, timeout=15, check=False, **quiet_spawn_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"ghost capture passed: {scenario}; config={L.LAYOUT_BYTES} sample={L.SAMPLE_BYTES}" in result.stdout


def test_semantic_config_resolves_selected_layout():
    config = GhostLayout.from_layout(US)
    assert config.mario_state == US.mario_struct
    assert config.current_area_pointer == US.current_area_pointer
    assert config.area_update_counter == US.area_update_counter
    assert config.pending_warp_op == US.pending_warp_op
    assert config.delayed_warp_timer == US.delayed_warp_timer
    assert len(config.to_bytes()) == L.LAYOUT_BYTES
    assert struct.unpack_from("<II", config.to_bytes()) == (L.LAYOUT_BYTES, L.SAMPLE_VERSION)
    assert struct.unpack_from("<II", config.to_bytes(), 56) == (US.pending_warp_op, US.delayed_warp_timer)
    with pytest.raises(LayoutIncomplete, match="US layout"):
        GhostLayout.from_layout(JP)
    with pytest.raises(LayoutIncomplete, match="current_area_pointer"):
        GhostLayout.from_layout(replace(US, current_area_pointer=None))
    with pytest.raises(LayoutIncomplete, match="pending_warp_op"):
        GhostLayout.from_layout(replace(US, pending_warp_op=None))


def test_clock_encoder_uses_existing_registries_in_native_field_order():
    words = struct.unpack_from("<26I", GhostLayout.from_layout(US).to_bytes(), 80)
    castles, warps = sorted(A.CASTLE_LEVELS), sorted(counter_epoch.AREA_WARP_OPS)
    assert words[:10] == (1, NEAR_ZERO_IGT, counter_epoch.AREA_LOAD_WINDOW,
                         counter_epoch.TELEPORT_WINDOW, counter_epoch.LEVEL_LOAD_TAIL_FRAMES,
                         counter_epoch.WARP_FIRED_WINDOW, A.ACT_TELEPORT_FADE_OUT,
                         IGT_WRAP_CEILING, len(castles), len(warps))
    assert words[10:18] == (*castles, *([0] * (8 - len(castles))))
    assert words[18:26] == (*warps, *([0] * (8 - len(warps))))


@pytest.mark.parametrize("changes", [
    {"rdram_bytes": 0}, {"rdram_bytes": 4 << 20},
    {"global_timer": True}, {"mario_object": -4},
    {"mario_state": A.KSEG0_BASE + (8 << 20) - 4},
    {"global_timer": US.global_timer + 1},
    {"object_pool": A.KSEG0_BASE + (8 << 20) - 0x260},
    {"pending_warp_op": 0}, {"delayed_warp_timer": US.delayed_warp_timer + 1},
    {"pending_warp_op": A.KSEG0_BASE + (8 << 20)},
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


class SoundContext:
    """play_sound, its ring and Mario, as the live gates read them."""

    def __init__(self, sources=("mario",) * 3, words=None):
        self.time, self.producer, self.issued = 0.0, 250, 0
        self.pending = list(sources)
        self.ring = {}
        self.words = list(words or sound_code_words(US.sound_producer, US.sound_ring))

    def candidate(self, name):
        return US.value(name)

    def now(self):
        return self.time

    def sleep(self, seconds):
        self.time += seconds
        if self.pending and int(self.time * 30) % 10 == 0:  # a jump now and then
            source = self.pending.pop(0)
            owner = US.object_pool + (0 if source == "mario" else A.OBJECT_SIZE)
            self.ring[self.producer & 0xFF] = (0x24228080, owner + 0x54)
            self.producer += 1

    def raw(self):
        return self

    def read_u8(self, address):
        if address in (US.sound_producer, US.sound_consumer):
            return self.producer & 0xFF
        return 0

    def read_u32(self, address):
        if address == US.mario_object:
            return US.object_pool
        if US.sound_code <= address < US.sound_code + 48:
            return self.words[(address - US.sound_code) // 4]
        if US.sound_ring <= address < US.sound_ring + 2048:
            bits, source = self.ring.get((address - US.sound_ring) // 8, (0, 0))
            return source if (address - US.sound_ring) % 8 else bits
        return 0


def test_sound_code_gate_matches_the_qualified_play_sound_words():
    assert sound_code_words(US.sound_producer, US.sound_ring) == (
        0x3C068033, 0x24C62F44, 0x90C20000, 0x3C0F8036, 0x25EF1498, 0x000270C0,
        0x01CF1821, 0x24580001, 0xAC640000, 0xAC650004, 0x03E00008, 0xA0D80000)
    assert _check_sound_code(SoundContext()).status == "verified"
    changed = list(sound_code_words(US.sound_producer, US.sound_ring))
    changed[1] += 4  # a producer four bytes away
    assert _check_sound_code(SoundContext(words=changed)).status == "failed"


def test_sound_ring_gates_need_marios_own_requests():
    for check in (_check_sound_producer, _check_sound_ring, _check_sound_consumer):
        assert check(SoundContext()).status == "verified"
        assert check(SoundContext(sources=("other",) * 5)).status == "failed"
        assert check(SoundContext(sources=())).status == "failed"


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

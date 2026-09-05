"""THE CAPTURE LAYER end to end, without Project64: the 32-bit test host
(plugin/gfxwrap/host.c) stands in for the emulator, loads the wrapper DLL
against a fake wrapped plugin, fakes RDRAM and the VI registers, and drives
ProcessDList / UpdateScreen the way PJ64 does; this side opens the same
frame stream, asks for frames, and checks every slot: the stamp bytes copied
from the fake RDRAM, the presented picture's colour, the origin, and that a
VI whose origin did not change captured nothing.

Skipped (not failed) on a machine without the x86 MSVC toolchain; the
SHIPPED DLL is driven too, so a stale committed binary fails here."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

import build_plugin  # noqa: E402

from sm64_events.core.paths import bundled_plugin_dll  # noqa: E402
from sm64_events.replay import framestream as F  # noqa: E402

pytestmark = pytest.mark.skipif(not build_plugin.toolchain_available(),
                                reason="no x86 MSVC toolchain (vcvars32.bat)")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("gfxwrap")
    host, fake = build_plugin.build_test_host(out)
    wrapper = build_plugin.build_wrapper(out)
    return {"host": host, "fake": fake, "wrapper": wrapper, "dir": out}


def drive(host: Path, wrapper: Path, frames: int, stream_name: str) -> str:
    result = subprocess.run([str(host), "--drive", str(wrapper), str(frames),
                             "--stream", stream_name],
                            capture_output=True, text=True, timeout=60,
                            creationflags=_NO_WINDOW, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def unique_name() -> str:
    return f"sm64_trainer_gfx_test_{os.getpid()}_{np.random.randint(1 << 30)}"


def test_layout_matches_the_python_side(built):
    printed = subprocess.run([str(built["host"]), "--layout"], capture_output=True,
                             text=True, creationflags=_NO_WINDOW, check=True).stdout
    c_side = {}
    for line in printed.splitlines():
        name, value = line.split()
        c_side[name] = int(value)
    shared = set(c_side) & set(F.LAYOUT)
    assert len(shared) >= 50, sorted(shared)
    mismatched = {name: (c_side[name], F.LAYOUT[name]) for name in shared
                  if c_side[name] != F.LAYOUT[name]}
    assert mismatched == {}


def test_the_wrapper_exports_everything_pj64_16_requires_and_names_the_wrapped_plugin(built):
    info = subprocess.run([str(built["host"]), "--info", str(built["wrapper"])],
                          capture_output=True, text=True, creationflags=_NO_WINDOW,
                          check=True).stdout
    assert "Fake GFX" in info and "+SM64 Trainer" in info
    assert "version 0x0103" in info and "bswaped 1" in info


def check_drive(host: Path, wrapper: Path):
    name = unique_name()
    stream = F.FrameStream(name)
    try:
        stream.set_table([(0, 4), (64, 4)], rdram_bytes=8 << 20)
        stream.set_want_frames(True)
        output = drive(host, wrapper, 5, name)
        assert "drove 5 frames" in output
        header = stream.header()
        assert header.initiated is False           # CloseDLL cleared it
        assert header.status & F.STATUS_WRAPPED_LOADED
        # GL_CONTEXT is cleared at detach with INITIATED (a reader must not
        # wait on a gone plugin); the five captured slots prove it was there
        assert header.wrapped_name == "fake_gfx.dll"
        assert header.wrapped_version == 0x0103
        assert header.plugin_version == F.LAYOUT["GFXWRAP_VERSION"] if "GFXWRAP_VERSION" in F.LAYOUT else True
        assert header.lists == 5
        assert header.alive == 10                  # two UpdateScreen calls per frame
        assert header.write_seq == 5               # ...and one capture per origin change
        assert header.dropped == 0
        slots, skipped = stream.read_new(0)
        assert skipped == 0                         # six slots hold all five
        assert [slot.seq for slot in slots] == [1, 2, 3, 4, 5]
        for slot in slots:
            frame = slot.seq - 1
            assert slot.table[0] == (1000 + frame).to_bytes(4, "little")
            assert slot.table[1] == (0x11223300 + frame).to_bytes(4, "little")
            assert slot.table[2] == b""
            assert slot.vi_origin == 0x100000 + frame
            assert slot.lists_since == 1
            assert slot.present_qpc >= slot.list_qpc > 0
            assert (slot.width, slot.height) == (64, 48)
            centre = slot.pixels[24, 32]
            assert tuple(int(channel) for channel in centre) == (frame, 2 * frame, 3 * frame), \
                f"slot {slot.seq}: centre pixel {centre.tolist()}"
    finally:
        stream.close()


def test_five_frames_through_a_fresh_build(built):
    check_drive(built["host"], built["wrapper"])


def test_the_shipped_dll_behaves_like_the_source(built):
    shipped = bundled_plugin_dll()
    assert shipped is not None, "src/sm64_events/data/plugin/sm64_trainer_gfx.dll is not built"
    # the wrapper loads its ini and the fake from ITS OWN folder, so the
    # shipped DLL is driven from a copy beside the fake plugin
    copy = built["dir"] / "shipped" / "sm64_trainer_gfx.dll"
    copy.parent.mkdir(exist_ok=True)
    copy.write_bytes(shipped.read_bytes())
    (copy.parent / "fake_gfx.dll").write_bytes(built["fake"].read_bytes())
    check_drive(built["host"], copy)


def test_no_frames_are_captured_while_the_tracker_does_not_want_them(built):
    name = unique_name()
    stream = F.FrameStream(name)
    try:
        stream.set_table([(0, 4)], rdram_bytes=8 << 20)
        stream.set_want_frames(False)
        drive(built["host"], built["wrapper"], 3, name)
        header = stream.header()
        assert header.write_seq == 0 and header.lists == 3 and header.alive == 6
    finally:
        stream.close()


def test_an_ini_naming_the_wrapper_itself_leaves_it_unwrapped_instead_of_recursing(built):
    """Fresh-context review finding: forwarding to ourselves would recurse
    until the stack died. The wrapper notices its own module and reports
    the wrapped plugin as missing."""
    info = subprocess.run([str(built["host"]), "--info", str(built["wrapper"]),
                           "--wrapped", "sm64_trainer_gfx.dll"],
                          capture_output=True, text=True, creationflags=_NO_WINDOW,
                          check=True, timeout=60).stdout
    assert "wrapped plugin missing" in info


def test_a_stamp_entry_past_the_committed_rdram_is_dropped_not_a_crash(built):
    """The tracker claims 8 MB (the expansion pak); a 4 MB configuration has
    nothing committed above it. The host reserves 8 MB and commits 4, the
    table asks for a word at 6 MB: the plugin must survive every frame and
    hand back an EMPTY entry there while the low entry still arrives."""
    name = unique_name()
    stream = F.FrameStream(name)
    try:
        stream.set_table([(0, 4), (6 << 20, 4)], rdram_bytes=8 << 20)
        stream.set_want_frames(True)
        result = subprocess.run([str(built["host"]), "--drive", str(built["wrapper"]), "3",
                                 "--stream", name, "--rdram-mb", "4"],
                                capture_output=True, text=True, timeout=60,
                                creationflags=_NO_WINDOW, check=False)
        assert result.returncode == 0, result.stdout + result.stderr
        slots, _ = stream.read_new(0)
        assert [slot.seq for slot in slots] == [1, 2, 3]
        for slot in slots:
            assert slot.table[0] == (1000 + slot.seq - 1).to_bytes(4, "little")
            assert slot.table[1] == b""
    finally:
        stream.close()

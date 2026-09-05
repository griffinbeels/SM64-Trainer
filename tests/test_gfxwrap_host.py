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


QUIET = {**os.environ, "SM64_TRAINER_GFX_NO_DIALOGS": "1"}   # no message box under test


def drive(host: Path, wrapper: Path, frames: int, stream_name: str, *extra) -> str:
    result = subprocess.run([str(host), "--drive", str(wrapper), str(frames),
                             "--stream", stream_name, *extra],
                            capture_output=True, text=True, timeout=60, env=QUIET,
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
    # The dialog is answered from the ini alone (loading the wrapped plugin
    # inside PJ64's enumerate-every-DLL pass would leak a reference per pass)
    assert "fake_gfx.dll +SM64 Trainer" in info
    assert "version 0x0103" in info and "bswaped 1" in info


def check_drive(host: Path, wrapper: Path, *flags, pictures_via=F.STATUS_GL_CONTEXT):
    name = unique_name()
    stream = F.FrameStream(name)
    try:
        stream.set_table([(0, 4), (64, 4)], rdram_bytes=8 << 20)
        stream.set_want_frames(True)
        output = drive(host, wrapper, 5, name, *flags)
        assert "drove 5 frames" in output
        header = stream.header()
        assert header.initiated is False           # CloseDLL cleared it
        assert header.status & F.STATUS_WRAPPED_LOADED
        # which capture point the pictures took: the layer's own GL_FRONT
        # read, or the wrapped plugin's ReadScreen when the calling thread
        # has no context (GLideN64_LINK_4.2's shape, measured 2026-09-05)
        assert header.status & (F.STATUS_GL_CONTEXT | F.STATUS_READSCREEN) == pictures_via
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


def test_the_same_frames_when_the_emulator_calls_from_its_own_cpu_thread(built):
    """PJ64 1.6's shape: the window's thread pumps messages while a second
    thread makes every plugin call. The layer keeps no per-thread state, so
    the pictures and stamps are the same."""
    check_drive(built["host"], built["wrapper"], "--cpu-thread")


def test_pictures_come_through_the_wrapped_plugins_readscreen_when_the_thread_has_no_context(built):
    """His GLideN64 (LINK 4.2) runs every GL call on a render thread of its
    own, so the emulation thread never holds a context -- the first live
    session refused 30,000 pictures that way. The second capture point asks
    the wrapped plugin's own ReadScreen: the fake answers without a context,
    from a malloc the layer frees through the process heap, and every slot
    still carries the right colour, stamp and origin, nothing dropped."""
    check_drive(built["host"], built["wrapper"], "--no-context", "--cpu-thread",
                pictures_via=F.STATUS_READSCREEN)


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
    name = unique_name()
    result = subprocess.run([str(built["host"]), "--drive", str(built["wrapper"]), "2",
                             "--stream", name, "--wrapped", "sm64_trainer_gfx.dll"],
                            capture_output=True, text=True, timeout=60, env=QUIET,
                            creationflags=_NO_WINDOW, check=False)
    # InitiateGFX refuses (the host reports it and exits 5) -- no recursion,
    # no crash, and the reason is in the log beside the wrapper
    assert result.returncode == 5, result.stdout + result.stderr
    assert "InitiateGFX failed" in result.stderr
    log = built["wrapper"].parent / "sm64_trainer_gfx.log"
    assert log.exists() and "names the capture layer itself" in log.read_text()


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
        drive(built["host"], built["wrapper"], 3, name, "--rdram-mb", "4")
        slots, _ = stream.read_new(0)
        assert [slot.seq for slot in slots] == [1, 2, 3]
        for slot in slots:
            assert slot.table[0] == (1000 + slot.seq - 1).to_bytes(4, "little")
            assert slot.table[1] == b""
    finally:
        stream.close()


def test_gl_state_the_wrapped_plugin_leaves_bound_does_not_redirect_the_capture(built):
    """Review finding 5's guard: the fake leaves a framebuffer object, a
    pixel-pack buffer and odd pack parameters bound after every present;
    every slot must still hold the window's own clear colour."""
    name = unique_name()
    stream = F.FrameStream(name)
    try:
        stream.set_table([(0, 4)], rdram_bytes=8 << 20)
        stream.set_want_frames(True)
        drive(built["host"], built["wrapper"], 5, name, "--dirty-gl")
        slots, _ = stream.read_new(0)
        assert [slot.seq for slot in slots] == [1, 2, 3, 4, 5]
        for slot in slots:
            frame = slot.seq - 1
            centre = slot.pixels[24, 32]
            assert tuple(int(channel) for channel in centre) == (frame, 2 * frame, 3 * frame)
    finally:
        stream.close()


def test_the_source_stops_within_seconds_when_the_emulator_dies(built):
    """Review finding 10: a plugin that dies with its header bits set must
    not park the recorder. The host is killed mid-drive; the source's
    on_stopped fires within a few seconds."""
    import threading
    import time

    from sm64_events.memory.layout import layout_for
    from sm64_events.replay.pluginsource import PluginVideoSource, table_for

    name = unique_name()
    stream = F.FrameStream(name)
    layout = layout_for("us")
    table = table_for(layout)                       # the host's fake RDRAM is 8 MB of zeros
    stream.set_table([(offset, length) for _n, offset, length in table], rdram_bytes=8 << 20)
    stopped = threading.Event()
    delivered = []
    source = PluginVideoSource(stream, table, layout)
    source.start(lambda bgra, ts, stamp: delivered.append(stamp.frame), stopped.set)
    host = subprocess.Popen([str(built["host"]), "--drive", str(built["wrapper"]), "100000",
                             "--stream", name], env=QUIET, creationflags=_NO_WINDOW,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 15
        while not delivered and time.monotonic() < deadline:
            time.sleep(0.05)
        assert delivered, "no frame arrived from the host"
        host.kill()
        host.wait(timeout=10)
        assert stopped.wait(timeout=4.0), "the source kept waiting on a dead plugin"
    finally:
        if host.poll() is None:
            host.kill()
        source.stop()
        stream.close()

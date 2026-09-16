"""The stamp: a picture's copied RDRAM bytes decode through the sampler's own
decoder, the address table is word-aligned from the live layout, owned pixels
prepare top-down BGRA, and the desktop camera hands over to the GPU route."""
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from sm64_events.inputs.frame import MARIO_BLOCK_OFF
from sm64_events.memory import addresses as A
from sm64_events.memory.buffer import BufferMemory
from sm64_events.memory.layout import layout_for
from sm64_events.replay import pluginsource as P
from sm64_events.replay.pixels import to_bgra_top_down

#: the native table entry size (plugin/gfxwrap/stamp_adapter.h keeps 128-byte entries)
TABLE_ENTRY_BYTES = 128


@pytest.fixture
def layout():
    return layout_for("us")


def rdram_with(layout, frame: int, stick=(12, -34), buttons=0x8000, igt=77,
               action=0x04000440, yaw=-1234, speed=31.5) -> BufferMemory:
    memory = BufferMemory()
    memory.write_u32(layout.global_timer, frame)
    base = layout.player1_controller
    memory.write_u16(base + A.CONTROLLER_RAW_STICK_X_OFF, stick[0] & 0xFFFF)
    memory.write_u16(base + A.CONTROLLER_RAW_STICK_X_OFF + 2, stick[1] & 0xFFFF)
    memory.write_u16(base + A.CONTROLLER_BUTTON_DOWN_OFF, buttons)
    memory.write_u16(base + A.CONTROLLER_BUTTON_DOWN_OFF + 2, buttons)
    memory.write_u16(layout.usamune_overall, igt)
    mario = layout.mario_struct + MARIO_BLOCK_OFF
    memory.write_u32(mario, action)
    memory.write_u16(layout.mario_struct + A.MARIO_YAW_OFF, yaw & 0xFFFF)
    import struct
    speed_word, = struct.unpack(">I", struct.pack(">f", speed))   # the float's bits as the word's value
    memory.write_u32(layout.mario_struct + A.MARIO_FORWARD_VEL_OFF, speed_word)
    return memory


def raw_table(memory: BufferMemory, table: list) -> list:
    """What the plugin copies: the raw words at each entry, PJ64 order."""
    return [memory._read_raw(offset, length) for _name, offset, length in table]


def slot_with(table_bytes, *, lists_since=1, vi_origin=0x100000, list_qpc=5, present_qpc=9):
    """A delivered picture's stamp record, as `decode_stamp` reads it."""
    return SimpleNamespace(table=tuple(table_bytes), lists_since=lists_since, vi_origin=vi_origin,
                           list_qpc=list_qpc, present_qpc=present_qpc)


def test_table_for_is_word_aligned_and_covers_the_halfword(layout):
    table = P.table_for(layout)
    names = [name for name, _offset, _length in table]
    assert names == list(P.TABLE_ORDER)
    for _name, offset, length in table:
        assert offset % 4 == 0 and length % 4 == 0 and 0 < length <= TABLE_ENTRY_BYTES
    igt = dict((name, (offset, length)) for name, offset, length in table)["usamune_overall"]
    assert igt[0] <= layout.usamune_overall - A.KSEG0_BASE < igt[0] + igt[1]


@pytest.mark.parametrize("width", [1, 7, 1190, 1600])
def test_capture_conversion_keeps_every_channel_row_and_owns_its_pixels(width):
    source = np.random.default_rng(82).integers(0, 256, (9, width + 3, 3), dtype=np.uint8)
    cropped = source[:, :width]  # padded rows, including non-aligned widths
    expected = np.concatenate((cropped[::-1], np.full((9, width, 1), 255, dtype=np.uint8)), axis=2)
    actual = to_bgra_top_down(cropped)
    source.fill(0)  # a later producer write cannot change a retained heartbeat
    assert actual.flags.c_contiguous
    np.testing.assert_array_equal(actual, expected)


def test_decode_stamp_reads_the_frame_the_pad_mario_and_the_igt(layout):
    table = P.table_for(layout)
    memory = rdram_with(layout, frame=1234)
    stamp = P.decode_stamp(slot_with(raw_table(memory, table)), table, layout)
    assert stamp.frame == 1234 and stamp.igt_overall == 77
    assert (stamp.pad.stick_x, stamp.pad.stick_y, stamp.pad.buttons) == (12, -34, 0x8000)
    assert stamp.pad.action == 0x04000440 and stamp.pad.yaw == -1234
    assert abs(stamp.pad.speed - 31.5) < 1e-6
    extras = stamp.extras()
    assert extras["exact"] is True and extras["pad"] == [12, -34, 0x8000]
    assert extras["igt_overall"] == 77 and extras["vi_origin"] == 0x100000


def test_decode_stamp_is_none_without_the_counter(layout):
    assert P.decode_stamp(slot_with((b"",) * 16), P.table_for(layout), layout) is None


class FakeDesktop:
    def __init__(self):
        self.started = self.stopped = False
        self.idle_check = None

    def set_idle_check(self, fn):
        self.idle_check = fn

    def start(self, on_frame, on_stopped):
        self.started = True

    def stop(self):
        self.stopped = True

    def status(self):
        return {"grabs": 0}


def test_the_desktop_camera_hands_over_when_the_gpu_route_becomes_discoverable(monkeypatch):
    """Whichever order the game and the trainer were opened in: the desktop
    source asks whether the wrapper's control page is discoverable, and once
    it is, it ends itself like a lost window, so the recorder's next attach
    gets the GPU source."""
    monkeypatch.setattr(P, "LAYER_WATCH_S", 0.01)
    desktop = FakeDesktop()
    ready = threading.Event()
    source = P.DesktopUntilLayerPresents(desktop, note="not set up", backend_ready=ready.is_set)
    stopped = threading.Event()
    source.set_idle_check(lambda: False)
    source.start(lambda *args: None, stopped.set)
    assert desktop.started and desktop.idle_check is not None
    assert source.frame_source == "desktop" and source.frame_source_note == "not set up"
    assert not stopped.wait(0.1)                 # nothing discoverable yet: no handover
    assert source.status() == {"grabs": 0}
    ready.set()
    try:
        assert stopped.wait(2.0), "the desktop camera never handed over"
        assert source.upgraded is True
    finally:
        source.stop()
    assert desktop.stopped


def test_the_desktop_camera_survives_an_unreadable_backend_probe(monkeypatch):
    monkeypatch.setattr(P, "LAYER_WATCH_S", 0.01)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise OSError("control page unreadable")
        return True

    source = P.DesktopUntilLayerPresents(FakeDesktop(), backend_ready=flaky)
    stopped = threading.Event()
    source.start(lambda *args: None, stopped.set)
    try:
        assert stopped.wait(2.0) and len(calls) >= 3
    finally:
        source.stop()
    deadline = time.monotonic() + 1.0
    while source._thread is not None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert source._thread is None


def test_a_present_with_two_lists_or_none_is_not_called_exact(layout):
    table = P.table_for(layout)
    memory = rdram_with(layout, frame=77)
    def stamp(lists_since):
        return P.decode_stamp(slot_with(raw_table(memory, table), lists_since=lists_since), table, layout)
    assert stamp(1).extras()["exact"] is True
    assert stamp(2).extras()["exact"] is False
    assert stamp(0).extras()["exact"] is False


def test_a_stamp_whose_controller_bytes_are_not_a_pad_carries_no_pad(layout):
    """Reset/loading memory can hold a stable counter beside axes that are
    not sign-extended s8 values and button bits nobody can press. The live
    sampler refuses that block; the stamp must not turn it into a pad."""
    table = P.table_for(layout)
    def stamp_with(stick, buttons):
        memory = rdram_with(layout, frame=500, stick=stick, buttons=buttons)
        return P.decode_stamp(slot_with(raw_table(memory, table)), table, layout)
    wild = stamp_with((32767, -32768), 0xFFFF)
    assert wild.frame == 500 and wild.pad is None and wild.pad_invalid is True
    extras = wild.extras()
    assert "pad" not in extras and "mario" not in extras and extras["pad_invalid"] is True
    bad_bits = stamp_with((12, -34), 0x00C0)          # the two bits no controller sets
    assert bad_bits.pad is None and "pad" not in bad_bits.extras()
    real = stamp_with((-128, 127), 0xFF3F)            # every real bit, extreme real axes
    assert real.pad is not None and real.extras()["pad"] == [-128, 127, 0xFF3F]


def test_a_layout_without_mario_stamps_no_facing_or_speed(layout):
    """0 degrees is a real bearing: a table with no Mario entry must not
    claim yaw 0 / action 0 / speed 0 for every picture."""
    table = [entry for entry in P.table_for(layout) if entry[0] != "mario"]
    memory = rdram_with(layout, frame=42)
    stamp = P.decode_stamp(slot_with(raw_table(memory, table)), table, layout)
    assert stamp.pad is not None and stamp.mario_captured is False
    extras = stamp.extras()
    assert extras["pad"] == [12, -34, 0x8000] and "mario" not in extras

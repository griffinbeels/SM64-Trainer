"""The plugin video source: a slot's stamp bytes decode through the
sampler's own decoder, the picture arrives top-down BGRA in WGC's timebase,
and the recorder files the stamp's frame without asking the frame clock."""
import os
import threading
import time

import numpy as np
import pytest

from sm64_events.inputs.frame import MARIO_BLOCK_OFF
from sm64_events.memory import addresses as A
from sm64_events.memory.buffer import BufferMemory
from sm64_events.memory.layout import layout_for
from sm64_events.replay import framestream as F
from sm64_events.replay import pluginsource as P


@pytest.fixture
def layout():
    return layout_for("us")


@pytest.fixture
def stream():
    opened = F.FrameStream(f"sm64_trainer_gfx_test_{os.getpid()}_{np.random.randint(1 << 30)}")
    yield opened
    opened.close()


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


def test_table_for_is_word_aligned_and_covers_the_halfword(layout):
    table = P.table_for(layout)
    names = [name for name, _offset, _length in table]
    assert names == list(P.TABLE_ORDER)
    for _name, offset, length in table:
        assert offset % 4 == 0 and length % 4 == 0 and 0 < length <= F.TABLE_ENTRY_BYTES
    igt = dict((name, (offset, length)) for name, offset, length in table)["usamune_overall"]
    assert igt[0] <= layout.usamune_overall - A.KSEG0_BASE < igt[0] + igt[1]


def test_decode_stamp_reads_the_frame_the_pad_mario_and_the_igt(layout):
    table = P.table_for(layout)
    memory = rdram_with(layout, frame=1234)
    slot = F.Slot(seq=1, list_qpc=5, present_qpc=9, vi_origin=0x100000, width=2, height=2,
                  stride=8, lists_since=1, table=tuple(raw_table(memory, table)),
                  pixels=np.zeros((2, 2, 3), dtype=np.uint8))
    stamp = P.decode_stamp(slot, table, layout)
    assert stamp.frame == 1234 and stamp.igt_overall == 77
    assert (stamp.pad.stick_x, stamp.pad.stick_y, stamp.pad.buttons) == (12, -34, 0x8000)
    assert stamp.pad.action == 0x04000440 and stamp.pad.yaw == -1234
    assert abs(stamp.pad.speed - 31.5) < 1e-6
    extras = stamp.extras()
    assert extras["exact"] is True and extras["pad"] == [12, -34, 0x8000]
    assert extras["igt_overall"] == 77 and extras["vi_origin"] == 0x100000


def test_decode_stamp_is_none_without_the_counter(layout):
    slot = F.Slot(seq=1, list_qpc=0, present_qpc=0, vi_origin=0, width=1, height=1,
                  stride=4, lists_since=1, table=(b"",) * 16,
                  pixels=np.zeros((1, 1, 3), dtype=np.uint8))
    assert P.decode_stamp(slot, P.table_for(layout), layout) is None


def test_the_source_delivers_top_down_bgra_and_the_stamp(layout, stream):
    table = P.table_for(layout)
    memory = rdram_with(layout, frame=4242)
    picture = np.zeros((3, 4, 3), dtype=np.uint8)
    picture[0, :] = (255, 0, 0)                  # the BOTTOM row, as GL stores it
    source = P.PluginVideoSource(stream, table, layout)
    got = []
    stopped = threading.Event()
    source.start(lambda bgra, ts, stamp: got.append((bgra, ts, stamp)), stopped.set)
    assert stream.header().want_frames == 1
    stream.publish(picture, raw_table(memory, table), list_qpc=100, present_qpc=200)
    deadline = time.monotonic() + 3
    while not got and time.monotonic() < deadline:
        time.sleep(0.01)
    source.stop()
    assert len(got) == 1
    bgra, ts_100ns, stamp = got[0]
    assert bgra.shape == (3, 4, 4) and bgra.dtype == np.uint8
    assert tuple(bgra[2, 0]) == (255, 0, 0, 255)     # the last row now: top-down
    assert tuple(bgra[0, 0]) == (0, 0, 0, 255)
    assert ts_100ns == 200 * 10_000_000 // P.QPC_FREQUENCY
    assert stamp.frame == 4242 and stamp.pad.stick_x == 12
    assert stream.header().want_frames == 0                # released at stop
    assert source.status()["delivered"] == 1


def test_idle_turns_the_frames_off(layout, stream):
    source = P.PluginVideoSource(stream, P.table_for(layout), layout)
    source.set_idle_check(lambda: True)
    source.start(lambda *args: None, lambda: None)
    assert stream.header().want_frames == 0
    source.stop()

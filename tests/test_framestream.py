"""The frame stream's Python side against a Python stand-in for the plugin
(the same layout the C side writes; `test_framestream_layout.py` pins the
two against each other)."""
import os

import numpy as np
import pytest

from sm64_events.replay import framestream as F


@pytest.fixture
def stream():
    name = f"sm64_trainer_gfx_test_{os.getpid()}_{np.random.randint(1 << 30)}"
    opened = F.FrameStream(name)
    yield opened
    opened.close()


def picture(height: int, width: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, size=(height, width, 3), dtype=np.uint8)


def test_layout_constants_total():
    assert F.TOTAL_BYTES == F.HEADER_BYTES + F.SLOT_COUNT * F.SLOT_BYTES
    assert F.SLOT_BYTES == F.SLOT_META_BYTES + 3840 * 2160 * 3
    assert F.S_TABLE + F.TABLE_ENTRIES * F.TABLE_ENTRY_BYTES <= F.S_PIXELS
    assert F.H_TABLE + F.TABLE_ENTRIES * 8 <= F.HEADER_BYTES


def test_a_fresh_stream_carries_the_geometry_both_sides_agree_on(stream):
    header = stream.header()
    assert header.version == F.VERSION and header.write_seq == 0
    assert stream._u32(F.H_SLOT_BYTES) == F.SLOT_BYTES
    assert stream._u32(F.H_SLOTS_OFFSET) == F.HEADER_BYTES


def test_reader_sees_a_slot_the_stand_in_wrote(stream):
    pixels = picture(3, 4, seed=1)
    seq = stream.publish(pixels, [b"\x01\x02\x03\x04", b"\xaa" * 8],
                         list_qpc=123, present_qpc=456, vi_origin=0x100000)
    assert seq == 1
    slots, skipped = stream.read_new(0)
    assert skipped == 0 and len(slots) == 1
    slot = slots[0]
    assert slot.seq == 1 and slot.list_qpc == 123 and slot.present_qpc == 456
    assert slot.vi_origin == 0x100000 and (slot.width, slot.height) == (4, 3)
    assert slot.table[0] == b"\x01\x02\x03\x04" and slot.table[1] == b"\xaa" * 8
    assert slot.table[2] == b""
    assert np.array_equal(slot.pixels, pixels)
    assert stream.header().alive == 1


def test_read_new_returns_only_newer_seqs_and_the_event_fires(stream):
    for seed in range(3):
        stream.publish(picture(2, 2, seed), [])
    assert stream.wait(0.5) is True            # the last publish signalled it
    slots, _ = stream.read_new(1)
    assert [slot.seq for slot in slots] == [2, 3]
    assert stream.read_new(3) == ([], 0)


def test_a_torn_slot_is_skipped_and_counted(stream):
    stream.publish(picture(2, 2, 1), [])
    stream.publish(picture(2, 2, 2), [], torn=True)
    slots, skipped = stream.read_new(0)
    assert [slot.seq for slot in slots] == [1] and skipped == 1


def test_a_slot_overwritten_before_the_reader_looked_counts_as_skipped(stream):
    for seed in range(F.SLOT_COUNT + 2):
        stream.publish(picture(2, 2, seed), [])
    slots, skipped = stream.read_new(0)
    assert skipped == 2 and [slot.seq for slot in slots] == list(range(3, F.SLOT_COUNT + 3))


def test_set_table_lands_in_the_header_count_last(stream):
    stream.set_table([(0x32D5D4, 4), (0x33AF90, 32)], rdram_bytes=8 << 20)
    header = stream.header()
    assert header.table_count == 2
    assert stream.table() == [(0x32D5D4, 4), (0x33AF90, 32)]
    assert stream._u32(F.H_RDRAM_BYTES) == 8 << 20
    with pytest.raises(ValueError):
        stream.set_table([(0x32D5D6, 4)], rdram_bytes=8 << 20)     # not word-aligned
    with pytest.raises(ValueError):
        stream.set_table([(0, 132)], rdram_bytes=8 << 20)          # over an entry's size


def test_want_frames_and_the_tracker_heartbeat(stream):
    stream.set_want_frames(True)
    stream.touch(); stream.touch()
    header = stream.header()
    assert header.want_frames == 1 and header.tracker_alive == 2


def test_two_handles_on_one_name_share_the_memory(stream):
    other = F.FrameStream(stream.name)
    try:
        stream.set_plugin_fields(F.STATUS_INITIATED | F.STATUS_GL_CONTEXT,
                                 wrapped_name="GLideN64_LINK_4.2.dll", plugin_pid=42)
        header = other.header()
        assert header.initiated and header.wrapped_name == "GLideN64_LINK_4.2.dll"
        assert header.plugin_pid == 42
        assert other.alive_since(0) is False
        stream.publish(picture(2, 2, 1), [])
        assert other.alive_since(0) is True
    finally:
        other.close()


def test_a_recreated_mapping_does_not_strand_a_reader_holding_a_high_seq(stream):
    """Review finding 12: the plugin restarts on a fresh mapping at seq 1
    while the reader remembers seq 40; the reader must follow it."""
    stream.publish(picture(2, 2, 1), [])
    slots, skipped = stream.read_new(40)
    assert [slot.seq for slot in slots] == [1] and skipped == 0


def test_the_event_handle_is_pointer_sized_and_checked(stream):
    assert stream._event is not None and stream._event != 0
    assert stream.plugin_process_alive() is False       # no plugin wrote a pid
    stream.set_plugin_fields(F.STATUS_INITIATED, plugin_pid=os.getpid())
    assert stream.plugin_process_alive() is True         # this process


def test_overwrite_while_reading_timestamps_cannot_pair_new_time_with_old_pixels(stream, monkeypatch):
    old_pixels = picture(2, 2, 11)
    seq = stream.publish(old_pixels, [b"old"], list_qpc=101, present_qpc=111)
    read_i64 = stream._i64
    overwritten = False

    def overwrite_before_time(offset):
        nonlocal overwritten
        if not overwritten and offset == stream.slot_offset(seq) + F.S_LIST_QPC:
            overwritten = True
            for _ in range(F.SLOT_COUNT):
                stream.publish(picture(2, 2, 99), [b"new"], list_qpc=909, present_qpc=999)
        return read_i64(offset)

    monkeypatch.setattr(stream, "_i64", overwrite_before_time)
    assert stream._read_slot(seq) is None
    assert overwritten

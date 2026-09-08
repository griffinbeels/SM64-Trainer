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
    stream.set_plugin_fields(F.STATUS_INITIATED | F.STATUS_WRAPPED_LOADED, plugin_pid=123)
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
    assert source.status()["plugin_pid"] == 123
    # A later producer cannot inherit this source's delivery receipt.
    stream.set_plugin_fields(F.STATUS_INITIATED | F.STATUS_WRAPPED_LOADED, plugin_pid=456)
    assert source.status()["plugin_pid"] == 123


def test_pictures_flow_answers_true_on_the_first_picture(layout, stream):
    table = P.table_for(layout)
    stream.set_plugin_fields(F.STATUS_INITIATED | F.STATUS_WRAPPED_LOADED)

    def present_soon():
        time.sleep(0.1)
        assert stream.header().want_frames == 1      # the probe asked
        stream.publish(np.zeros((2, 2, 3), dtype=np.uint8), raw_table(rdram_with(layout, 7), table))

    threading.Thread(target=present_soon, daemon=True).start()
    assert P.pictures_flow(stream, timeout_s=2.0) == (True, None)


def test_pictures_flow_names_a_layer_that_refuses_every_picture(layout, stream):
    """The first live session's shape: the heartbeat moves, `dropped` climbs,
    no slot is ever written. The probe says why and turns frames back off,
    so the recorder can take the desktop grab instead of an empty ring."""
    stream.set_plugin_fields(F.STATUS_INITIATED | F.STATUS_WRAPPED_LOADED, dropped=10)

    def refuse_soon():
        time.sleep(0.1)
        stream.set_plugin_fields(F.STATUS_INITIATED | F.STATUS_WRAPPED_LOADED, dropped=25)

    threading.Thread(target=refuse_soon, daemon=True).start()
    flowing, reason = P.pictures_flow(stream, timeout_s=0.5)
    assert flowing is False
    assert "refused 15 pictures" in reason and "ReadScreen" in reason
    assert stream.header().want_frames == 0


def test_pictures_flow_names_a_layer_that_presents_nothing(layout, stream):
    stream.set_plugin_fields(F.STATUS_INITIATED | F.STATUS_WRAPPED_LOADED, dropped=0)
    flowing, reason = P.pictures_flow(stream, timeout_s=0.2)
    assert flowing is False and "no new picture" in reason


class FakeDesktop:
    """A desktop camera that only records what the recorder asked of it."""

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


def _heartbeat(stream, stop, status, dropped_per_beat=0):
    """A stand-in plugin: the heartbeat moves 60/s; optionally it refuses."""
    alive = dropped = 0
    while not stop.is_set():
        alive += 1
        dropped += dropped_per_beat
        stream.set_plugin_fields(status, alive=alive, dropped=dropped)
        time.sleep(1 / 60)


def test_the_desktop_camera_hands_over_when_the_layer_starts_presenting(layout, stream):
    """Whichever order the game and the trainer were opened in: the desktop
    source watches the heartbeat, and once a picture flows it ends itself
    like a lost window, so the recorder's next attach gets the plugin."""
    table = P.table_for(layout)
    desktop = FakeDesktop()
    source = P.DesktopUntilLayerPresents(desktop, stream)
    stopped = threading.Event()
    source.set_idle_check(lambda: False)
    source.start(lambda *args: None, stopped.set)
    assert desktop.started and desktop.idle_check is not None
    assert not stopped.wait(0.3)                 # nothing presenting yet: no handover
    stop_beat = threading.Event()
    status = F.STATUS_INITIATED | F.STATUS_WRAPPED_LOADED

    def present():
        while not stop_beat.is_set():
            if stream.header().want_frames:
                stream.publish(np.zeros((2, 2, 3), dtype=np.uint8),
                               raw_table(rdram_with(layout, 9), table))
            time.sleep(0.02)

    threading.Thread(target=_heartbeat, args=(stream, stop_beat, status), daemon=True).start()
    threading.Thread(target=present, daemon=True).start()
    try:
        assert stopped.wait(4.0), "the desktop camera never handed over"
        assert source.upgraded is True
    finally:
        stop_beat.set()
        source.stop()
    assert desktop.stopped and stream.header().want_frames == 0


def test_the_desktop_camera_stays_when_the_layer_refuses_pictures(layout, stream):
    desktop = FakeDesktop()
    source = P.DesktopUntilLayerPresents(desktop, stream)
    stopped = threading.Event()
    source.start(lambda *args: None, stopped.set)
    stop_beat = threading.Event()
    status = F.STATUS_INITIATED | F.STATUS_WRAPPED_LOADED
    threading.Thread(target=_heartbeat, args=(stream, stop_beat, status, 1), daemon=True).start()
    try:
        deadline = time.monotonic() + 4.0
        while source.frame_source_note is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert source.frame_source_note is not None, "the refusal was never noted"
        assert "refused" in source.frame_source_note
        assert not stopped.is_set() and source.upgraded is False
        assert source.status() == {"grabs": 0}
    finally:
        stop_beat.set()
        source.stop()
    assert desktop.stopped


def test_idle_turns_the_frames_off(layout, stream):
    source = P.PluginVideoSource(stream, P.table_for(layout), layout)
    source.set_idle_check(lambda: True)
    source.start(lambda *args: None, lambda: None)
    assert stream.header().want_frames == 0
    source.stop()


def test_recorder_resume_changes_plugin_demand_before_liveness_poll(tmp_path, layout, stream):
    from test_replay_recorder import make_recorder, FakeAudioSource

    source = P.PluginVideoSource(stream, P.table_for(layout), layout)
    rec = make_recorder(tmp_path, source, FakeAudioSource())
    source.set_idle_check(rec.is_idle)
    rec._video_source = source
    got = threading.Event()
    source.start(lambda *args: got.set(), lambda: None)
    try:
        rec._set_idle(True)
        assert stream.header().want_frames == 0
        rec.set_player_active()
        # This must be true synchronously, before any wait/next heartbeat.
        assert stream.header().want_frames == 1
        table = P.table_for(layout)
        stream.publish(np.zeros((2, 2, 3), dtype=np.uint8),
                       raw_table(rdram_with(layout, 8770), table))
        assert got.wait(0.5), "first resumed picture was not delivered"
        rec.set_session_paused(True)
        rec.set_player_active()
        assert stream.header().want_frames == 0
        rec.set_session_paused(False)
        assert stream.header().want_frames == 1
    finally:
        source.stop()
    rec._set_idle(True)
    rec.set_player_active()
    assert stream.header().want_frames == 0  # late resume cannot revive a stopped source


def test_a_present_with_two_lists_or_none_is_not_called_exact(layout):
    table = P.table_for(layout)
    memory = rdram_with(layout, frame=77)
    def slot_with(lists_since):
        return F.Slot(seq=1, list_qpc=1, present_qpc=2, vi_origin=1, width=1, height=1,
                      stride=4, lists_since=lists_since, table=tuple(raw_table(memory, table)),
                      pixels=np.zeros((1, 1, 3), dtype=np.uint8))
    assert P.decode_stamp(slot_with(1), table, layout).extras()["exact"] is True
    assert P.decode_stamp(slot_with(2), table, layout).extras()["exact"] is False
    assert P.decode_stamp(slot_with(0), table, layout).extras()["exact"] is False

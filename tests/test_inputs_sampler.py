import struct

from sm64_events.inputs.frame import InputFrame
from sm64_events.inputs.sampler import InputSampler
from sm64_events.memory import addresses as A
from sm64_events.memory.base import MemoryReadError
from sm64_events.memory.layout import US


class ScriptedMemory:
    """Replays a script of (frame_counter, buttons, pressed, stick_x, stick_y).

    One script entry is one call to `sample()`. `straddle_at` makes the SECOND
    frame-counter read of that entry differ, which is exactly how a read that
    spans a frame boundary looks from here.
    """

    def __init__(self, script, straddle_at=()):
        self.script = list(script)
        self.straddle_at = set(straddle_at)
        self.index = -1
        self._second_read = False

    def read_u32(self, addr):
        assert addr == US.global_timer
        if not self._second_read:
            self.index += 1
            self._second_read = True
            return self.script[self.index][0]
        self._second_read = False
        counter = self.script[self.index][0]
        return counter + 1 if self.index in self.straddle_at else counter

    def read_block(self, addr, size):
        entry = self.script[self.index]
        if addr == US.mario_struct + A.MARIO_ACTION_OFF:
            # Mario's own state rides in the same window as the pad (round
            # 32). A script row may carry it or not; a short row means "not
            # captured", which is what an older track honestly says.
            action = entry[5] if len(entry) > 5 else 0
            yaw = entry[6] if len(entry) > 6 else 0
            speed = entry[7] if len(entry) > 7 else 0.0
            span = bytearray(size)
            struct.pack_into(">I", span, 0, action & 0xFFFFFFFF)
            struct.pack_into(">h", span,
                             A.MARIO_YAW_OFF - A.MARIO_ACTION_OFF, yaw)
            struct.pack_into(">f", span,
                             A.MARIO_FORWARD_VEL_OFF - A.MARIO_ACTION_OFF,
                             speed)
            return bytes(span)
        assert addr == US.player1_controller and size == A.CONTROLLER_SIZE
        _counter, buttons, pressed, stick_x, stick_y = entry[:5]
        block = bytearray(A.CONTROLLER_SIZE)
        struct.pack_into(">hh", block, A.CONTROLLER_RAW_STICK_X_OFF,
                         stick_x, stick_y)
        struct.pack_into(">HH", block, A.CONTROLLER_BUTTON_DOWN_OFF,
                         buttons, pressed)
        return bytes(block)


def run(script, straddle_at=()):
    got = []
    sampler = InputSampler(ScriptedMemory(script, straddle_at), US,
                           lambda number, frame: got.append((number, frame)))
    for _ in script:
        sampler.sample()
    return got, sampler


def test_a_frame_is_emitted_with_its_LAST_reading_not_its_first():
    """The whole reason the loop samples faster than the game runs.

    The game rewrites the pad ~62% into each frame, so the early samples of
    frame 101 still hold frame 100's input. Emitting the first would log every
    input one frame late, on every frame, invisibly -- measured as exactly
    what a 60 Hz loop does (tools/probe_inputs.py, 2026-08-20).
    """
    got, _ = run([(100, 0x0000, 0, 0, 0),
                  (101, 0x0000, 0, 0, 0),        # pre-rewrite: still stale
                  (101, 0x8000, 0x8000, 0, 0),   # the game wrote frame 101
                  (102, 0x8000, 0, 0, 0)])
    assert got == [(100, InputFrame(0x0000, 0, 0, 0)),
                   (101, InputFrame(0x8000, 0x8000, 0, 0))]


def test_a_straddled_read_is_discarded_rather_than_mis_filed():
    got, sampler = run([(100, 0x8000, 0x8000, 0, 0),
                        (100, 0x4000, 0, 0, 0),
                        (101, 0x4000, 0, 0, 0)],
                       straddle_at={1})
    assert sampler.health()["straddles"] == 1
    assert got == [(100, InputFrame(0x8000, 0x8000, 0, 0))]


def test_the_frame_counter_going_backwards_starts_a_new_run():
    """A console reset restarts the counter. Nothing may be carried across
    that seam, and the frame in hand must still be emitted."""
    got, _ = run([(500, 0x2000, 0, 0, 0),
                  (12, 0x0000, 0, 0, 0),
                  (13, 0x0000, 0, 0, 0)])
    assert [number for number, _frame in got] == [500, 12]


def test_the_games_own_pressed_flag_checks_our_frame_assignment():
    """buttonPressed is the game saying "newly down THIS frame". If our own
    down-edge lands on a different frame, we filed an input under the wrong
    number -- so it is COUNTED, never assumed away by trusting the rate."""
    got, sampler = run([(10, 0x0000, 0, 0, 0),
                        (11, 0x8000, 0x8000, 0, 0),
                        (12, 0x8000, 0, 0, 0)])
    assert len(got) == 2
    assert sampler.health()["edge_checks"] == 1
    assert sampler.health()["edge_mismatches"] == 0


def test_a_down_edge_the_game_did_not_confirm_is_counted_as_a_mismatch():
    _got, sampler = run([(10, 0x0000, 0, 0, 0),
                         (11, 0x8000, 0x0000, 0, 0),   # down, but not "pressed"
                         (12, 0x8000, 0, 0, 0)])
    assert sampler.health()["edge_mismatches"] == 1


def test_a_backward_counter_does_not_fake_a_press_on_the_next_frame():
    """A reset clears what was held, so the first frame after it must not read
    as a fresh down-edge against the pre-reset state."""
    _got, sampler = run([(500, 0x8000, 0x8000, 0, 0),
                         (12, 0x0000, 0, 0, 0),
                         (13, 0x0000, 0, 0, 0)])
    assert sampler.health()["edge_mismatches"] == 0


def test_a_memory_error_is_survived_and_emits_nothing():
    class Broken(ScriptedMemory):
        def read_block(self, addr, size):
            raise MemoryReadError("emulator closed")

    got = []
    sampler = InputSampler(Broken([(100, 0, 0, 0, 0)]), US,
                           lambda number, frame: got.append(number))
    assert sampler.sample() is None
    assert got == []


def test_a_failing_sink_does_not_stop_the_sampler():
    """The sink writes to disk. A write failing is not a reason to stop
    reading the pad, and it must not take the poll loop down with it."""
    def explode(number, frame):
        raise RuntimeError("disk full")

    sampler = InputSampler(ScriptedMemory([(1, 0, 0, 0, 0), (2, 0, 0, 0, 0)]),
                           US, explode)
    sampler.sample()
    sampler.sample()
    assert sampler.health()["frames"] == 1


def test_flush_emits_the_frame_still_in_hand():
    got = []
    sampler = InputSampler(ScriptedMemory([(77, 0x1000, 0, 0, 0)]), US,
                           lambda number, frame: got.append((number, frame)))
    sampler.sample()
    assert got == []          # frame 77 is not finished yet
    sampler.flush()
    assert got == [(77, InputFrame(0x1000, 0, 0, 0))]


def test_marios_state_rides_in_the_SAME_window_as_the_pad():
    """One coherent read, not two: pairing this frame's pad with next frame's
    action would be the same class of error the counter sandwich exists to
    prevent, one field over."""
    got, _sampler = run([(10, 0x8000, 0x8000, 40, 0, A.ACT_DIVE, -12000, 31.5),
                         (11, 0x8000, 0, 40, 0, A.ACT_DIVE, -12000, 31.5)])
    assert got[0][1].action == A.ACT_DIVE
    assert got[0][1].yaw == -12000
    assert round(got[0][1].speed, 2) == 31.5


def test_a_script_row_with_no_mario_state_reads_as_not_captured():
    got, _sampler = run([(10, 0x8000, 0x8000, 0, 0), (11, 0, 0, 0, 0)])
    assert got[0][1].action == 0
    assert got[0][1].speed == 0.0

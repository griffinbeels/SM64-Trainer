# tests/test_sync_calibration_gates.py
"""Offline tests for `sync/calibration_gates.py` — no PJ64, no ROM.

Each check runs the REAL `sm64_events.main.build_detectors` chain
(`sync/stack.py::DetectorRun`) over a hand-built `GameSnapshot` stream, then
compares what the real detectors/fields produced against the constant its
`backs` names. The star scenarios mirror `tests/test_star_grab.py`'s own
`snap()`/settle-tail shape (that file's module docstring is the authority on
why a trailing clone frame is needed for `StarGrabDetector` to settle and
publish) rather than importing it, so this file stays self-contained.
"""
from dataclasses import replace
from datetime import datetime, timezone

from sm64_events.memory import addresses as A
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.sync import calibration_gates as CG
from sm64_events.sync.checks import resolve_backs
from sm64_events.sync.gates import GATES

ACT_IDLE = 0x0C400201

CALIBRATION_IDS = {
    "cal.star.ground_igt", "cal.star.midair_igt", "cal.star.result_write_delay",
    "cal.textbox.open_state", "cal.warp.pipe_dest_delay",
    "cal.warp.painting_at_touch", "cal.moment.display_lag",
}
# Snapshot taken at IMPORT TIME — see test_sync_address_gates.py's identical
# note on why this is immune to test_sync_gates_contract.py's own
# `GATES.clear()` calls, which only run inside ITS test bodies.
_REGISTERED_BY_ID = {gate.id: gate for gate in GATES if gate.id in CALIBRATION_IDS}


def snap(**overrides) -> GameSnapshot:
    defaults = dict(
        wall_time_utc=datetime(2026, 8, 15, tzinfo=timezone.utc),
        global_timer=1000, mario_action=ACT_IDLE, mario_action_timer=0,
        mario_action_state=0, num_stars=5, last_completed_course=1,
        last_completed_star=3, igt_overall=0, igt_result=0, curr_level=24,
        curr_area=1, pending_warp_op=0, delayed_warp_timer=0,
        warp_dest_type=0, warp_dest_level=0, warp_dest_area=0, warp_dest_node=0)
    defaults.update(overrides)
    return GameSnapshot(**defaults)


def _drained(snaps):
    """One trailing clone, advanced past StarGrabDetector.RESULT_SETTLE_FRAMES
    (45) + 1 — the exact technique `tests/test_star_grab.py::drained` uses so
    a fixture that stops at the grab still lets the detector settle."""
    last = snaps[-1]
    ahead = 46
    return [*snaps, replace(last, global_timer=last.global_timer + ahead,
                            igt_overall=last.igt_overall + ahead)]


class FakeMemory:
    """Only `read_u16` at one address is ever asked for by these checks —
    everything else in `ctx.raw()` is unused here, unlike the address-gate
    tests which poll many fields live."""

    def __init__(self, values: dict[int, int] | None = None):
        self._values = dict(values or {})

    def read_u16(self, addr: int) -> int:
        return self._values.get(addr, 0)


class FakeContext:
    def __init__(self, snaps, *, version="us", memory=None, candidates=None):
        self.version = version
        self._snaps = list(snaps)
        self._memory = memory if memory is not None else FakeMemory()
        self._candidates = dict(candidates or {})
        self._clock = 0.0

    def candidate(self, field):
        return self._candidates.get(field)

    def raw(self):
        return self._memory

    def snapshots(self, seconds):
        return iter(self._snaps)

    def now(self):
        return self._clock

    def sleep(self, seconds):
        self._clock += seconds

    def prompt(self, text):
        return ""

    def say(self, text):
        pass


RESULT_ADDR = 0x80417C74   # matches memory.layout.US.usamune_star_result


# --- cal.star.ground_igt / cal.star.midair_igt ------------------------------

def _ground_grab_snaps():
    prev = snap(num_stars=5, global_timer=1001, igt_overall=230, igt_result=0)
    curr = snap(mario_action=A.ACT_STAR_DANCE_EXIT, mario_action_timer=2,
               global_timer=1002, num_stars=6, last_completed_course=1,
               last_completed_star=3, igt_overall=232, igt_result=231)
    return _drained([prev, curr])


def test_cal_star_ground_igt_verifies_when_published_matches_the_settled_result():
    memory = FakeMemory({RESULT_ADDR: 231})
    ctx = FakeContext(_ground_grab_snaps(), memory=memory,
                      candidates={"usamune_star_result": RESULT_ADDR})
    verdict = CG._check_star_igt(ctx, want_ground=True)
    assert verdict.status == "verified"
    assert verdict.measured["published_minus_result"] == 0


def test_cal_star_ground_igt_fails_when_one_frame_off():
    # The settled result store disagrees by exactly one frame with what
    # published — the finding this gate exists to surface.
    memory = FakeMemory({RESULT_ADDR: 230})
    ctx = FakeContext(_ground_grab_snaps(), memory=memory,
                      candidates={"usamune_star_result": RESULT_ADDR})
    verdict = CG._check_star_igt(ctx, want_ground=True)
    assert verdict.status == "failed"
    assert verdict.measured["published_minus_result"] == 1


def _midair_grab_snaps(fall_frames=10):
    grab_frame, grab_counter = 1000, 300
    snaps = [snap(global_timer=grab_frame - 2, igt_overall=grab_counter - 2, igt_result=0),
            snap(global_timer=grab_frame - 1, igt_overall=grab_counter - 1, igt_result=0)]
    for offset in range(fall_frames):
        snaps.append(snap(global_timer=grab_frame + offset,
                          igt_overall=grab_counter + offset, igt_result=0,
                          mario_action=A.ACT_FALL_AFTER_STAR_GRAB,
                          mario_action_timer=offset))
    snaps.append(snap(global_timer=grab_frame + fall_frames,
                      igt_overall=grab_counter + fall_frames, igt_result=0,
                      mario_action=A.ACT_STAR_DANCE_EXIT, mario_action_timer=0))
    return _drained(snaps)


def test_cal_star_midair_igt_verifies_when_published_matches_the_settled_result():
    # Same arithmetic tests/test_star_grab.py::test_midair_grab_is_timed_at_
    # the_landing_not_the_touch pins: counter 310 + DISPLAY_TICK == 311.
    memory = FakeMemory({RESULT_ADDR: 311})
    ctx = FakeContext(_midair_grab_snaps(), memory=memory,
                      candidates={"usamune_star_result": RESULT_ADDR})
    verdict = CG._check_star_igt(ctx, want_ground=False)
    assert verdict.status == "verified"


def test_cal_star_ground_igt_fails_on_an_actual_midair_grab():
    memory = FakeMemory({RESULT_ADDR: 311})
    ctx = FakeContext(_midair_grab_snaps(), memory=memory,
                      candidates={"usamune_star_result": RESULT_ADDR})
    verdict = CG._check_star_igt(ctx, want_ground=True)
    assert verdict.status == "failed"


# --- cal.star.result_write_delay --------------------------------------------

def test_cal_star_result_write_delay_verifies_within_the_constant():
    # RESULT_FRESH_FRAMES == 15. grab_frame back-computes to 1000 (curr's
    # global_timer 1002 minus its action_timer 2, same as star_grab.py's own
    # _identify) and the result store changes at 1004 -- 4 frames later.
    # StarGrabDetector does NOT publish star_collected on this same tick (a
    # single sighting of a changed result is not enough for it to settle
    # early -- confirmed by running this exact fixture through DetectorRun),
    # so the event only appears once the drained tail forces the backstop;
    # this gate's own write_frame latch does not wait for that.
    prev = snap(num_stars=5, global_timer=1001, igt_overall=230, igt_result=0)
    curr = snap(mario_action=A.ACT_STAR_DANCE_EXIT, mario_action_timer=2,
               global_timer=1002, num_stars=6, igt_overall=232, igt_result=0)
    settled = snap(mario_action=A.ACT_STAR_DANCE_EXIT, mario_action_timer=3,
                   global_timer=1004, num_stars=6, igt_overall=234, igt_result=233)
    ctx = FakeContext(_drained([prev, curr, settled]))
    verdict = CG._check_star_result_write_delay(ctx)
    assert verdict.status == "verified"
    assert verdict.measured["delay_frames"] == 4


def test_cal_star_result_write_delay_fails_past_the_constant():
    threshold = resolve_backs(
        "sm64_events.detectors.igt_clock.IgtClock.RESULT_FRESH_FRAMES")
    prev = snap(num_stars=5, global_timer=1001, igt_overall=230, igt_result=0)
    curr = snap(mario_action=A.ACT_STAR_DANCE_EXIT, mario_action_timer=2,
               global_timer=1002, num_stars=6, igt_overall=232, igt_result=0)
    late_frame = 1002 + threshold + 5
    settled = snap(mario_action=A.ACT_STAR_DANCE_EXIT, mario_action_timer=3,
                   global_timer=late_frame, num_stars=6,
                   igt_overall=232 + threshold + 5, igt_result=999)
    ctx = FakeContext(_drained([prev, curr, settled]))
    verdict = CG._check_star_result_write_delay(ctx)
    assert verdict.status == "failed"
    assert verdict.measured["delay_frames"] > threshold


# --- cal.textbox.open_state --------------------------------------------------

def _textbox_snaps(hold_at_state=8, reach=9):
    """Head-turn 0..reach-1, then holds at `hold_at_state` — mirrors
    `tools/probe_textbox.py`'s King Whomp capture (turn 0..7, box at 8)."""
    snaps = [snap(global_timer=2000, mario_action=ACT_IDLE, mario_action_state=0)]
    frame = 2001
    for state in range(reach):
        snaps.append(snap(global_timer=frame, mario_action=A.ACT_READING_NPC_DIALOG,
                          mario_action_state=min(state, hold_at_state)))
        frame += 1
    for _ in range(5):
        snaps.append(snap(global_timer=frame, mario_action=A.ACT_READING_NPC_DIALOG,
                          mario_action_state=hold_at_state))
        frame += 1
    return snaps


def test_cal_textbox_open_state_verifies_at_the_real_threshold():
    ctx = FakeContext(_textbox_snaps(hold_at_state=8, reach=9))
    verdict = CG._check_textbox_open_state(ctx)
    assert verdict.status == "verified"
    assert verdict.measured == {"action_state": 8, "expected": 8}


def test_cal_textbox_open_state_fails_when_it_never_reaches_the_threshold():
    # The turn stalls at state 5 and never opens the box in this window.
    ctx = FakeContext(_textbox_snaps(hold_at_state=5, reach=6))
    verdict = CG._check_textbox_open_state(ctx)
    assert verdict.status == "failed"


# --- cal.warp.pipe_dest_delay / cal.warp.painting_at_touch ------------------

def _warp_snaps(delay_frames: int, dest_before=(0, 0, 0, 0), dest_after=(2, 17, 1, 10)):
    def dest_kwargs(dest):
        return dict(warp_dest_type=dest[0], warp_dest_level=dest[1],
                   warp_dest_area=dest[2], warp_dest_node=dest[3])

    edge_frame = 3001
    snaps = [snap(global_timer=3000, mario_action=ACT_IDLE, **dest_kwargs(dest_before)),
            snap(global_timer=edge_frame, mario_action=A.ACT_DISAPPEARED,
                **dest_kwargs(dest_before))]
    for offset in range(1, delay_frames):
        snaps.append(snap(global_timer=edge_frame + offset,
                          mario_action=A.ACT_DISAPPEARED, **dest_kwargs(dest_before)))
    snaps.append(snap(global_timer=edge_frame + delay_frames,
                      mario_action=A.ACT_DISAPPEARED, **dest_kwargs(dest_after)))
    return snaps


PIPE_CONSTANT = "sm64_events.detectors.warp.WarpDetector.PIPE_TOUCH_TO_DEST_FRAMES"


def _pipe_gate():
    [gate] = [g for g in GATES if g.id == "cal.warp.pipe_dest_delay"]
    return gate


def test_cal_warp_pipe_dest_delay_verifies_at_exactly_the_us_countdown():
    """The gate measures the touch-to-write delay against the NAMED US
    constant (20, probe_warp_block twice) -- equality, not a window, so a JP
    countdown of 24 shows as a failed measurement instead of hiding inside
    RIDE_WINDOW_FRAMES."""
    expected = resolve_backs(PIPE_CONSTANT)
    ctx = FakeContext(_warp_snaps(expected))
    verdict = _pipe_gate().check(ctx)
    assert verdict.status == "verified"
    assert verdict.measured["delay_frames"] == expected


def test_cal_warp_pipe_dest_delay_fails_when_the_countdown_differs():
    expected = resolve_backs(PIPE_CONSTANT)
    ctx = FakeContext(_warp_snaps(expected + 4))
    verdict = _pipe_gate().check(ctx)
    assert verdict.status == "failed"
    assert verdict.measured == {"delay_frames": expected + 4, "window": expected}
    assert _pipe_gate().backs == PIPE_CONSTANT


def test_cal_warp_painting_at_touch_verifies_when_written_at_the_touch():
    ctx = FakeContext(_warp_snaps(0))   # sWarpDest changes on the touch frame itself
    verdict = CG._check_warp_delay(
        ctx, threshold_name="sm64_events.detectors.warp.WarpDetector.FRESH_WINDOW_FRAMES",
        compare=lambda delay, w: delay <= w)
    assert verdict.status == "verified"
    assert verdict.measured["delay_frames"] == 0


def test_cal_warp_painting_at_touch_fails_when_it_writes_late():
    window = resolve_backs(
        "sm64_events.detectors.warp.WarpDetector.FRESH_WINDOW_FRAMES")
    ctx = FakeContext(_warp_snaps(window + 6))
    verdict = CG._check_warp_delay(
        ctx, threshold_name="sm64_events.detectors.warp.WarpDetector.FRESH_WINDOW_FRAMES",
        compare=lambda delay, w: delay <= w)
    assert verdict.status == "failed"


def test_cal_warp_delay_fails_with_no_touch_edge():
    ctx = FakeContext([snap(global_timer=3000, mario_action=ACT_IDLE)] * 3)
    verdict = CG._check_warp_delay(
        ctx, threshold_name="sm64_events.detectors.warp.WarpDetector.RIDE_WINDOW_FRAMES",
        compare=lambda delay, w: delay < w)
    assert verdict.status == "failed"


# --- cal.moment.display_lag (deliberately skipped) --------------------------

def test_cal_moment_display_lag_is_skipped_with_evidence():
    ctx = FakeContext([])
    verdict = _REGISTERED_BY_ID["cal.moment.display_lag"].check(ctx)
    assert verdict.status == "skipped"
    assert "score_moment_clock" in verdict.evidence


# --- every gate's `backs` resolves by import ---------------------------------

def test_every_calibration_gate_is_registered_with_a_resolvable_backs():
    assert CALIBRATION_IDS == set(_REGISTERED_BY_ID)
    for gate in _REGISTERED_BY_ID.values():
        assert gate.kind == "calibration"
        assert gate.backs, f"{gate.id} has no backs"
        resolve_backs(gate.backs)   # raises ImportError/AttributeError on a bad name
        assert gate.instruction.strip()
        assert gate.proves.strip()

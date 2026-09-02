"""Stepping never leaves the clip, and every slot counts through the clip
clock.

His report, 2026-08-31: "when we get to the end of the timeline and i press
the right arrow to advance to the next frame, it jumps backwards a bunch of
frames... I am on frame 770/771, press right arrow, and it jumps to 591."
Seeking to exactly `duration` is PAST the last frame's own interval, so the
element reports itself ended and presents what it likes -- and the panel,
which reads the presented frame, then answers from somewhere else entirely.
His rule: "simply move to the last frame in the video and not allow the user
to move forward (if at the end) or backward (if at the beginning)."

Node evaluates the real declarations out of `ui/frame.js`.
"""
import json
import re
import subprocess
from pathlib import Path

from source_scan import strip_comments

FRAME_JS = (Path(__file__).resolve().parents[1]
            / "src/sm64_events/ui/frame.js")


def run(expression: str):
    code = strip_comments(FRAME_JS.read_text(encoding="utf-8"))
    parts = []
    for name in ("gameFrameOf", "clipClock", "slotAtTime", "timeOfSlot",
                 "nextMappedTime", "stepGameFrame", "clampToFrames"):
        match = re.search(rf"^export function {name}\(.*?^\}}\s*$",
                          code, re.MULTILINE | re.DOTALL)
        assert match, f"no top-level `export function {name}` in frame.js"
        parts.append(match.group(0).replace("export ", "", 1))
    script = ("\n".join(parts)
              + f"\nconsole.log(JSON.stringify({expression}));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            encoding="utf-8", timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout) if result.stdout.strip() != "undefined" \
        else None


VIDEO = ("{currentTime: %s, duration: %s, paused: true, pause() {}}")
CFR = "{fps: 60, start: 0, times: null}"


def stepped(start: float, direction: int, duration: float, fps: int = 30):
    return run(f"(() => {{ const v = {VIDEO % (start, duration)};"
               f" stepGameFrame(v, {direction}, {fps});"
               f" return v.currentTime; }})()")


def test_a_step_forward_at_the_end_stays_on_the_last_frame():
    # 771 game frames at 30 fps = 25.7 s; the last frame's middle is 25.683.
    duration = 771 / 30
    at_last = (770 + 0.5) / 30
    assert stepped(at_last, 1, duration) < duration
    assert abs(stepped(at_last, 1, duration) - (duration - 0.5 / 30)) < 1e-9
    # And it does not move the clip backwards, which is the reported bug.
    assert stepped(at_last, 1, duration) >= at_last - 1e-9


def test_a_step_back_at_the_start_stays_on_the_first_frame():
    duration = 771 / 30
    assert stepped(0.5 / 30, -1, duration) == 0.5 / 30
    assert stepped(0.0, -1, duration) == 0.5 / 30


def test_an_ordinary_step_still_moves_one_game_frame():
    duration = 771 / 30
    assert abs(stepped((100 + 0.5) / 30, 1, duration) - (101.5 / 30)) < 1e-9
    assert abs(stepped((100 + 0.5) / 30, -1, duration) - (99.5 / 30)) < 1e-9


def test_the_clamp_never_returns_the_clips_own_edges():
    duration = 10.0
    assert run(f"clampToFrames(99, {duration}, 30)") < duration
    assert run(f"clampToFrames(-5, {duration}, 30)") > 0
    # A degenerate clip cannot produce a negative time.
    assert run("clampToFrames(5, 0, 30)") > 0


# --- stepping through the MAP, because the clocks are not one rate --------
# His question, 2026-08-31: "does Mario and ur tool measure frames the same
# way?... Are we sure that the FPS is the same? That all parts are in sync?"
# MEASURED on his pyramid clip and the answer is no: the video encodes at
# 59.987 fps while the GAME advanced at 29.800 (cross-checked two ways --
# the map's span over the clip's duration, and the ledger's own RAM frames
# over their own timestamps, both 29.800). That is 2.013 video slots per
# game frame rather than 2.000, and the game's rate is not even constant --
# it sags when his machine is loaded. So no arithmetic on 1/30 can step a
# game frame; the map can.

def mapped_step(current, frame_map, direction, clock=CFR):
    return run(f"nextMappedTime({current}, {json.dumps(frame_map)},"
               f" {clock}, {direction})")


def test_a_step_lands_on_the_NEXT_GAME_FRAME_however_many_slots_it_took():
    # A picture held for three slots, then two, then three: exactly the
    # irregular advance a 29.8 fps game makes in a 60 fps video.
    frame_map = [100, 100, 100, 101, 101, 102, 102, 102, 103, 103]
    # From inside frame 100 (slot 1), forward reaches 101's FIRST slot (3).
    assert mapped_step(1.5 / 60, frame_map, 1) == (3 + 0.5) / 60
    # From inside 102 (slot 6), forward reaches 103's first slot (8).
    assert mapped_step(6.5 / 60, frame_map, 1) == (8 + 0.5) / 60
    # Backward from 102's first slot (5) reaches 101's first slot (3).
    assert mapped_step(5.5 / 60, frame_map, -1) == (3 + 0.5) / 60


def test_the_ends_answer_null_so_the_caller_can_clamp():
    frame_map = [100, 100, 101, 101]
    assert mapped_step(2.5 / 60, frame_map, 1) is None      # on the last
    assert mapped_step(0.5 / 60, frame_map, -1) is None     # on the first


def test_no_map_answers_null_and_the_time_step_stands():
    assert mapped_step(1.0, None, 1) is None
    assert mapped_step(1.0, [], 1) is None
    assert mapped_step(1.0, [None, None], 1) is None


def test_a_clip_whose_first_frame_is_not_at_zero_steps_onto_its_own_slots():
    """His Log Rolling report (2026-09-01): clip 5782's frames sit at
    k/60 + 0.011003 s, so a seek to (k + 0.5)/60 lands 2.7 ms before frame k
    begins and Chromium presents k - 1 -- every step one picture early
    (measured with a Chromium probe on the real clip; on a clip whose first
    frame sits at 0.000 the same arithmetic is exact). The slot arithmetic
    counts from the clip's own first timestamp, in both directions."""
    start = 0.011003
    clock = f"{{fps: 60, start: {start}, times: null}}"
    frame_map = "[" + ",".join(str(1000 + k // 2) for k in range(600)) + "]"
    # A time that is INSIDE slot 496 on this clip: its own pts.
    inside_496 = start + 496 / 60 + 0.001
    assert run(f"slotAtTime({inside_496}, {clock})") == 496
    # Stepping from frame 1248 (slots 496-497) forward lands in slot 498 and
    # AFTER its first pts, never before it.
    landed = run(f"nextMappedTime({inside_496}, {frame_map}, {clock}, 1)")
    assert landed > start + 498 / 60 and landed < start + 499 / 60, landed
    naive = run(f"nextMappedTime({inside_496 - start}, {frame_map}, {CFR}, 1)")
    assert naive < start + 498 / 60, "the old arithmetic lands before frame 498 begins"


# --- the picture feed: one frame per picture, each at its own time --------
# Item 38 (2026-09-02). The ring encodes one video frame per DISTINCT
# captured picture, stamped at its own moment, so the clip is VFR and there
# is no grid: `frame_times[k]` is where frame k begins. A slot is the last
# frame whose start is at or before the time; a seek lands mid-span.

VFR_TIMES = [0.011, 0.045, 0.078, 0.112, 0.178, 0.211]   # a 33 ms cadence with one long gap
VFR = f"{{fps: 60, start: 0.011, times: {json.dumps(VFR_TIMES)}}}"


def test_the_clip_clock_is_built_from_the_view_and_prefers_frame_times():
    built = run(f"clipClock({{fps: 60, video_start_s: 0.011, frame_times: {json.dumps(VFR_TIMES)}}})")
    assert built == {"fps": 60, "start": 0.011, "times": VFR_TIMES}
    cfr = run("clipClock({fps: 60, video_start_s: 0.011, frame_times: null})")
    assert cfr == {"fps": 60, "start": 0.011, "times": None}
    assert run("clipClock(null)") == {"fps": 60, "start": 0, "times": None}


def test_a_vfr_slot_is_the_last_frame_begun_at_or_before_the_time():
    assert run(f"slotAtTime(0.0, {VFR})") == -1          # before the first frame
    assert run(f"slotAtTime(0.011, {VFR})") == 0
    assert run(f"slotAtTime(0.044, {VFR})") == 0
    assert run(f"slotAtTime(0.045, {VFR})") == 1
    assert run(f"slotAtTime(0.150, {VFR})") == 3          # inside the long hold
    assert run(f"slotAtTime(9.0, {VFR})") == 5            # past the end: the last


def test_a_vfr_seek_lands_mid_span_and_reads_back_as_the_same_slot():
    for slot in range(len(VFR_TIMES)):
        at = run(f"timeOfSlot({slot}, {VFR})")
        assert run(f"slotAtTime({at}, {VFR})") == slot
        assert at >= VFR_TIMES[slot]
        if slot + 1 < len(VFR_TIMES):
            assert at < VFR_TIMES[slot + 1]
    # The long hold's seek sits in ITS span, not on the 60 Hz grid.
    assert abs(run(f"timeOfSlot(3, {VFR})") - (0.112 + 0.178) / 2) < 1e-9


def test_a_vfr_step_moves_one_frame_whatever_the_gap_was():
    frame_map = [500, 501, 502, 503, 504, 505]
    landed = run(f"nextMappedTime(0.150, {json.dumps(frame_map)}, {VFR}, 1)")
    assert run(f"slotAtTime({landed}, {VFR})") == 4
    back = run(f"nextMappedTime({landed}, {json.dumps(frame_map)}, {VFR}, -1)")
    assert run(f"slotAtTime({back}, {VFR})") == 3

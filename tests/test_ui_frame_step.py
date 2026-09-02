"""Stepping never leaves the clip.

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
    for name in ("gameFrameOf", "slotAtTime", "timeOfSlot", "nextMappedTime",
                 "stepGameFrame", "clampToFrames"):
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
    return json.loads(result.stdout)


VIDEO = ("{currentTime: %s, duration: %s, paused: true, pause() {}}")


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

def mapped_step(current, frame_map, direction, clip_fps=60):
    return run(f"nextMappedTime({current}, {json.dumps(frame_map)},"
               f" {clip_fps}, {direction})")


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
    frame_map = "[" + ",".join(str(1000 + k // 2) for k in range(600)) + "]"
    # A time that is INSIDE slot 496 on this clip: its own pts.
    inside_496 = start + 496 / 60 + 0.001
    assert run(f"slotAtTime({inside_496}, 60, {start})") == 496
    # The READ side survives a small offset (the instant is still inside the
    # slot); it is the SEEK side that lands early, which is what the next
    # assertions pin.
    # Stepping from frame 1248 (slots 496-497) forward lands in slot 498 and
    # AFTER its first pts, never before it.
    landed = run(f"nextMappedTime({inside_496}, {frame_map}, 60, 1, {start})")
    assert landed > start + 498 / 60 and landed < start + 499 / 60, landed
    naive = run(f"nextMappedTime({inside_496 - start}, {frame_map}, 60, 1)")
    assert naive < start + 498 / 60, "the old arithmetic lands before frame 498 begins"


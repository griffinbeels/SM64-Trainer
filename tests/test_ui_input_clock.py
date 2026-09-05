"""The timeline's clock mapping: clip seconds <-> track frame, through the
anchor offset. Node evaluates the real declarations out of the component.

His report, 2026-08-22, video 0:06 / timeline frame 185 on a clip cut 3 s
before the anchor: "My stick position is U84 0, and i'm not pressing any
buttons (as seen in the gameplay footage), but the input reader shows a
totally different angle and shows me pressing A/B." 6 s into that clip is
frame 90 of the track, not 185.
"""
import json
import re
import subprocess
from pathlib import Path

from source_scan import strip_comments

TIMELINE = (Path(__file__).resolve().parents[1]
            / "src/sm64_events/ui/components/inputtimeline.js")


def declaration(name: str) -> str:
    code = strip_comments(TIMELINE.read_text(encoding="utf-8"))
    # A block-bodied arrow ends on its own `};` line; an expression-bodied
    # one ends at the first line-ending semicolon.
    match = (re.search(rf"^export const {name}\s*=[^\n]*\{{\n.*?^\}};\s*$",
                       code, re.M | re.S)
             or re.search(rf"^export const {name}\s*=.*?;\s*$", code,
                          re.M | re.S))
    assert match, f"no top-level `export const {name} = ...;` in inputtimeline.js"
    return match.group(0).replace("export ", "", 1)


def run(expression: str):
    script = (declaration("frameAtTime") + "\n" + declaration("timeAtFrame")
              + f"\nconsole.log(JSON.stringify({expression}));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def run_function(name, expression: str):
    code = strip_comments(TIMELINE.read_text(encoding="utf-8"))
    names = [name] if isinstance(name, str) else list(name)
    parts = []
    for one in names:
        match = re.search(rf"^export function {one}\(.*?^\}}\s*$", code,
                          re.M | re.S)
        assert match, (
            f"no top-level `export function {one}(...)` in inputtimeline.js")
        parts.append(match.group(0).replace("export ", "", 1))
    # The mapped clock counts slots from the clip's own first timestamp
    # through frame.js's two helpers (2026-09-01); the component imports
    # them, so the evaluated script carries the real declarations too.
    frame_js = strip_comments((TIMELINE.parents[1] / "frame.js").read_text(encoding="utf-8"))
    helpers = []
    for one in ("clipClock", "slotAtTime", "timeOfSlot"):
        match = re.search(rf"^export function {one}\(.*?^\}}\s*$", frame_js,
                          re.M | re.S)
        assert match, f"no top-level `export function {one}(...)` in frame.js"
        helpers.append(match.group(0).replace("export ", "", 1))
    script = ("\n".join(helpers + parts)
              + f"\nconsole.log(JSON.stringify({expression}));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout) if result.stdout.strip() != "undefined" \
        else None


MARKERS = '[{"frame": 10, "label": "a"}, {"frame": 40, "label": "b"}]'


def test_the_inspectors_clock_is_the_stamped_igt_when_the_slot_has_one():
    """A capture-layer clip: the inspector shows the timer the game printed
    in the picture on screen; a slot with no stamp, or no stamps at all,
    counts from the track's first frame; the lead-in with nothing stamped
    is the lead-in."""
    stamped = run_function("inspectorClock", "inspectorClock(47, 0, [null, 45, 46], 2)")
    assert stamped == {"frames": 46, "stamped": True}
    counted = run_function("inspectorClock", "inspectorClock(47, 0, [null, 45, 46], 0)")
    assert counted == {"frames": 47, "stamped": False}
    assert run_function("inspectorClock", "inspectorClock(50, 3, null, 4)") == {"frames": 47, "stamped": False}
    assert run_function("inspectorClock", "inspectorClock(1, 3, null, 0)") is None
    assert run_function("inspectorClock", "inspectorClock(1, 3, [12], 0)") == {"frames": 12, "stamped": True}


def test_the_inspector_reads_the_last_moment_at_or_before_the_frame():
    assert run_function("momentAt", f"momentAt({MARKERS}, 40)")["label"] == "b"
    assert run_function("momentAt", f"momentAt({MARKERS}, 39)")["label"] == "a"


def test_before_the_first_moment_there_is_nothing_to_read():
    assert run_function("momentAt", f"momentAt({MARKERS}, 9)") is None
    assert run_function("momentAt", "momentAt([], 5)") is None


# --- the mapped clock (round 32 item 17) ------------------------------------
# frameMap[k] = the raw game frame video frame k shows. This map holds his
# own measured shape: a duplicate (100 shown twice) and a skip (103 never
# shown) -- the 26, 27, 27, 29 counter he photographed. Track: anchor at raw
# 90, so raw 100 is axis 10. Clip encodes at 60 fps.
CFR = "{fps: 60, start: 0, times: null}"
MAP = "[null, 100, 100, 101, 102, 102, 104, 104], " + CFR + ", [[0, 90, 20]]"
MAPPED = MAP + ", 20"          # mappedFrameAtTime also clamps to the track
# The clip clock object (frame.js::clipClock) is the third argument of both
# directions: the encode rate and first timestamp of a CFR clip, or every
# frame's own time for a picture-feed clip. mappedTimeAtFrame takes no
# track length, so it gets MAP, never MAPPED.
BOTH = ("trackFrameOf", "gameFrameOf", "mappedFrameAtTime", "mappedTimeAtFrame")


def test_a_duplicated_video_frame_maps_both_slots_to_one_game_frame():
    at = lambda slot: run_function(BOTH,
        f"mappedFrameAtTime({(slot + 0.25) / 60}, {MAPPED})")
    assert at(1) == 10 and at(2) == 10       # the duplicate: display holds too
    assert at(3) == 11
    assert at(4) == 12 and at(5) == 12
    assert at(6) == 14                       # 103 was skipped by the capture


def test_before_the_maps_coverage_the_caller_falls_back():
    assert run_function(BOTH, f"mappedFrameAtTime(0.001, {MAPPED})") is None


def test_seeking_a_skipped_game_frame_lands_on_the_first_slot_past_it():
    # axis 13 = raw 103, which the footage never shows: the seek lands on
    # the first slot showing anything at or past it (slot 6, raw 104).
    assert run_function(BOTH, f"mappedTimeAtFrame(13, {MAP})") == 6.5 / 60
    assert run_function(BOTH, f"mappedTimeAtFrame(10, {MAP})") == 1.5 / 60


def test_a_clip_whose_first_picture_is_not_at_zero_seeks_later_by_that_much():
    """His Log Rolling clip (5782): first video pts 0.011 s, so the seek for
    slot k at (k + 0.5) / 60 presented slot k - 1 in Chromium. Both
    directions count from the clip's own start."""
    late = "{fps: 60, start: 0.011, times: null}"
    map_late = MAP.replace(CFR, late)
    mapped_late = MAPPED.replace(CFR, late)
    seek = run_function(BOTH, f"mappedTimeAtFrame(10, {map_late})")
    assert seek == 0.011 + 1.5 / 60
    assert run_function(BOTH, f"mappedFrameAtTime({seek}, {mapped_late})") == 10
    # The OLD seek time for slot 1 (no start) presents slot 0 on this clip --
    # the picture before the map's coverage: one slot early, exactly his report.
    assert run_function(BOTH, f"mappedFrameAtTime({1.5 / 60}, {mapped_late})") is None


def test_a_picture_feed_clip_maps_through_its_own_frame_times():
    """Item 38: one video frame per captured picture, each at its own
    time. The map is one entry per frame and the clock is the frame
    times themselves -- no grid, so a long hold is one slot however long
    it lasted."""
    times = "[0.011, 0.045, 0.078, 0.512, 0.545]"
    vfr = f"{{fps: 60, start: 0.011, times: {times}}}"
    args = f"[100, 101, 102, 103, 104], {vfr}, [[0, 90, 20]]"
    # Inside the long hold (frame 102's span runs 0.078 -> 0.512).
    assert run_function(BOTH, f"mappedFrameAtTime(0.3, {args}, 20)") == 12
    assert run_function(BOTH, f"mappedFrameAtTime(0.511, {args}, 20)") == 12
    assert run_function(BOTH, f"mappedFrameAtTime(0.512, {args}, 20)") == 13
    # The seek for axis 13 lands INSIDE frame 3's span, never on a grid.
    seek = run_function(BOTH, f"mappedTimeAtFrame(13, {args})")
    assert 0.512 <= seek < 0.545
    assert run_function(BOTH, f"mappedFrameAtTime({seek}, {args}, 20)") == 13


def test_the_two_mapped_directions_agree_on_every_shown_frame():
    got = run_function(BOTH, f"""(() => {{
      const out = [];
      for (let axis = 10; axis <= 14; axis += 1) {{
        const t = mappedTimeAtFrame(axis, {MAP});
        out.push(t === null ? null : mappedFrameAtTime(t, {MAPPED}));
      }}
      return out;
    }})()""")
    assert got == [10, 11, 12, 14, 14]       # 13 was never shown; 14 stands in


def test_a_counter_restart_converts_through_its_own_stretch():
    stretches = "[[0, 1000, 3], [3, 50, 2]]"
    assert run_function(BOTH, f"trackFrameOf(51, {stretches})") == 4
    assert run_function(BOTH, f"gameFrameOf(4, {stretches})") == 51
    assert run_function(BOTH, f"trackFrameOf(500, {stretches})") is None


def test_the_lead_in_and_the_tail_clamp_to_the_tracks_ends():
    lead = "[80], 60, [[0, 90, 20]], 20"
    assert run_function(BOTH, f"mappedFrameAtTime(0.001, {lead})") == 0
    tail = "[200], 60, [[0, 90, 20]], 20"
    assert run_function(BOTH, f"mappedFrameAtTime(0.001, {tail})") == 19


def test_six_seconds_into_a_three_second_lead_in_is_frame_ninety():
    assert run("frameAtTime(6.0, 3.0, 30, 598)") == 90


def test_the_lead_in_itself_reads_as_frame_zero_not_a_negative_frame():
    assert run("frameAtTime(1.0, 3.0, 30, 598)") == 0


def test_past_the_track_s_end_holds_the_last_frame():
    assert run("frameAtTime(60.0, 3.0, 30, 598)") == 597


def test_seeking_a_frame_lands_mid_frame_inside_the_clip():
    assert run("timeAtFrame(90, 3.0, 30)") == 3.0 + 90.5 / 30


def test_the_two_directions_agree_on_every_frame():
    assert run("Array.from({length: 598}, (_, f) => f).every("
               "(f) => frameAtTime(timeAtFrame(f, 3.0, 30), 3.0, 30, 598) === f)")


def test_no_lead_in_is_the_old_behaviour():
    assert run("frameAtTime(6.0, 0, 30, 598)") == 180


def test_every_always_drawn_lane_names_a_button_the_server_sends():
    """CORE_BUTTONS is matched against the button TABLE's names, so a name
    the table does not use is a lane that silently never draws."""
    from sm64_events.memory import addresses as A
    code = strip_comments(TIMELINE.read_text(encoding="utf-8"))
    match = re.search(r"^export const CORE_BUTTONS\s*=\s*(\[.*?\]);", code, re.M)
    assert match, "no CORE_BUTTONS in inputtimeline.js"
    core = json.loads(match.group(1))
    names = {name for _bit, name in A.BUTTON_BITS}
    assert set(core) <= names, set(core) - names
    assert {"Cup", "Cdown", "Cleft", "Cright"} <= set(core)


def test_the_inspector_prints_a_stick_reading_however_small():
    """His report 2026-08-23: "U19 L2 in game, but U19 with a -- entry in
    the input display... It should match Usamune identically". Only an axis
    at exactly zero reads as nothing."""
    panel = (Path(__file__).resolve().parents[1]
             / "src/sm64_events/ui/components/controllerpanel.js")
    code = strip_comments(panel.read_text(encoding="utf-8"))
    match = re.search(r"^export function stickWords\(.*?\n\}\n", code, re.M | re.S)
    assert match, "no stickWords in controllerpanel.js"
    script = (match.group(0).replace("export ", "", 1)
              + "\nconsole.log(JSON.stringify([stickWords(-2, 19), stickWords(0, 0), stickWords(84, -7)]));")
    result = subprocess.run(["node", "--input-type=module", "-"], input=script,
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    small, centred, mixed = json.loads(result.stdout)
    assert small == {"vertical": "U19", "horizontal": "L2"}
    assert centred == {"vertical": None, "horizontal": None}
    assert mixed == {"vertical": "D7", "horizontal": "R84"}

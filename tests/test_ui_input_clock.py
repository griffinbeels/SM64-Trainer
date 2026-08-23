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


def run_function(name: str, expression: str):
    code = strip_comments(TIMELINE.read_text(encoding="utf-8"))
    match = re.search(rf"^export function {name}\(.*?^\}}\s*$", code,
                      re.M | re.S)
    assert match, f"no top-level `export function {name}(...)` in inputtimeline.js"
    script = (match.group(0).replace("export ", "", 1)
              + f"\nconsole.log(JSON.stringify({expression}));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout) if result.stdout.strip() != "undefined" \
        else None


MARKERS = '[{"frame": 10, "label": "a"}, {"frame": 40, "label": "b"}]'


def test_the_inspector_reads_the_last_moment_at_or_before_the_frame():
    assert run_function("momentAt", f"momentAt({MARKERS}, 40)")["label"] == "b"
    assert run_function("momentAt", f"momentAt({MARKERS}, 39)")["label"] == "a"


def test_before_the_first_moment_there_is_nothing_to_read():
    assert run_function("momentAt", f"momentAt({MARKERS}, 9)") is None
    assert run_function("momentAt", "momentAt([], 5)") is None


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

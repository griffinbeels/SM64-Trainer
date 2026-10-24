"""About > How it works states facts about the code; the code is the check.

The lesson's words live in `ui/howitworks.js`, written to stay true while
the code around it changes. The few CONCRETE facts it shows -- memory
addresses, action codes, which frame is the grab and which the x-cam, the
message the page receives -- are compared here with the real Python that
owns each one, so changing the code turns this red instead of leaving the
lesson quietly wrong. The prose itself is the release review's job
(docs/release-pipeline.md).
"""
import functools
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from sm64_events.core.timefmt import GAME_FPS, format_igt
from sm64_events.memory import addresses as A
from sm64_events.memory.addresses import course_name, star_name
from sm64_events.memory.layout import US

ROOT = Path(__file__).resolve().parents[1]
LESSON_JS = ROOT / "src" / "sm64_events" / "ui" / "howitworks.js"
STAR_GRAB_PY = ROOT / "src" / "sm64_events" / "detectors" / "star_grab.py"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")

# Each memory cell the lesson shows, and the code that says where it lives.
CELL_SOURCES = {
    "Mario's action": US.mario_struct + A.MARIO_ACTION_OFF,
    "Star count": US.mario_struct + A.MARIO_NUM_STARS_OFF,
    "Current level": US.curr_level,
    "Frame counter": US.global_timer,
}


@functools.cache
def lesson() -> dict:
    script = (f"import * as m from {LESSON_JS.as_uri()!r};\n"
              "console.log(JSON.stringify({actions: m.ACTION_NAMES, cells: m.MEMORY_CELLS,"
              " frames: m.GRAB_FRAMES, message: m.MESSAGE_EXAMPLE, stops: m.STOPS}));")
    result = subprocess.run(["node", "--input-type=module", "-"], input=script,
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_every_named_action_is_a_real_action_code():
    codes = {value for name, value in vars(A).items()
             if name.startswith("ACT_") and isinstance(value, int)}
    named = {int(code) for code in lesson()["actions"]}
    assert named, "parsed no actions"
    assert named <= codes, f"not action codes in memory/addresses.py: {sorted(map(hex, named - codes))}"


def test_every_memory_cell_matches_the_us_layout():
    cells = {cell["label"]: cell["address"] for cell in lesson()["cells"]}
    assert set(cells) == set(CELL_SOURCES), (
        "A lesson memory cell needs its source in CELL_SOURCES, so the address "
        f"it shows is checked: {sorted(set(cells) ^ set(CELL_SOURCES))}")
    for label, address in cells.items():
        assert address == CELL_SOURCES[label], (label, hex(address), hex(CELL_SOURCES[label]))


def test_the_grab_stepper_marks_what_the_detector_marks():
    frames = lesson()["frames"]
    numbers = [f["frame"] for f in frames]
    assert numbers == list(range(numbers[0], numbers[0] + len(numbers))), numbers
    marks = {f.get("mark"): i for i, f in enumerate(frames) if f.get("mark")}
    assert set(marks) == {"grab", "xcam"}
    grab, xcam = marks["grab"], marks["xcam"]
    assert frames[grab]["action"] in A.STAR_GRAB_ACTIONS
    assert frames[grab - 1]["action"] not in A.STAR_GRAB_ACTIONS
    assert frames[grab]["action"] == A.ACT_FALL_AFTER_STAR_GRAB   # a midair grab
    assert frames[xcam]["action"] in A.STAR_DANCE_ACTIONS          # the x-cam
    assert all(f["action"] not in A.STAR_DANCE_ACTIONS for f in frames[:xcam])


def test_the_example_message_is_one_the_detector_sends():
    message = lesson()["message"]
    assert message["type"] == "star_collected"
    payload = message["payload"]
    source = STAR_GRAB_PY.read_text(encoding="utf-8")
    for key in payload:
        assert re.search(rf'"{key}":', source), f"star_grab.py does not send {key!r}"
    assert payload["igt"] == format_igt(payload["igt_frames"])
    assert payload["course_name"] == course_name(1)
    assert any(star_name(1, star) == payload["star_name"] for star in range(7))


def test_the_frame_clock_is_thirty_per_second():
    assert GAME_FPS == 30
    timing = next(stop for stop in lesson()["stops"] if stop["short"] == "Timing")
    assert any("30 per second" in line for line in timing["body"])

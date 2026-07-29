"""ui/marelotuning.js — the registry the overall-rank-up inspector is
generated from. Same two failure modes climbtuning.js has, and neither is
caught by anything else: a row nobody reads is a slider that does nothing, and
a module reading a key the registry lacks gets `undefined`, i.e. a NaN
duration — an animation that never ends."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from source_scan import code_only

REPO = Path(__file__).resolve().parents[1]
UI = REPO / "src" / "sm64_events" / "ui"
TUNING_JS = UI / "marelotuning.js"
# marelocelebrate.js reads every row that shapes the FLIGHT. ranktint.js
# (report 1, 2026-07-28) is a second, legitimate reader -- the ambient tint's
# strength/duration are read there, at rest, never by the celebration itself
# (which only ever overrides the colour) -- same multi-reader shape
# climbtuning.js already has (climbcurve/celebrations/rankclimb).
READERS = (UI / "components" / "marelocelebrate.js", UI / "ranktint.js")

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")


def run_node(imports: str, body: str, module: Path = TUNING_JS):
    script = f"import {{ {imports} }} from {module.as_uri()!r};\n{body}"
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def reader_source() -> str:
    """Comment-free: a prose mention of tune.<key> would otherwise read as a
    real read (tests/source_scan.py -- the false positive this repo has been
    bitten by five times)."""
    return "\n".join(code_only(path) for path in READERS)


def test_every_tunable_is_actually_read_by_the_celebration():
    data = run_node("MARELO_TUNABLES, MARELO_DEFAULTS",
                    "console.log(JSON.stringify({ defaults: MARELO_DEFAULTS }));")
    source = reader_source()
    unread = [key for key in data["defaults"]
              if not re.search(rf"\btun\w*\.{re.escape(key)}\b", source)]
    assert not unread, (
        f"ui/marelotuning.js declares {unread}, which "
        f"{[p.name for p in READERS]} never reads. The inspector would render "
        "a control that changes nothing.")


def test_no_module_reads_a_tunable_that_does_not_exist():
    data = run_node("MARELO_DEFAULTS",
                    "console.log(JSON.stringify(MARELO_DEFAULTS));")
    source = reader_source()
    used = set(re.findall(r"\btun\w*\.([A-Za-z][A-Za-z0-9_]*)\b", source))
    missing = sorted(used - set(data))
    assert not missing, (
        f"{missing} is read but not declared. An undefined tuning value is a "
        "NaN duration, and only the hold ceiling would notice.")


def test_every_row_is_complete_enough_to_render_a_control():
    rows = run_node("MARELO_TUNABLES",
                    "console.log(JSON.stringify(MARELO_TUNABLES));")
    for name, row in rows.items():
        for field in ("group", "label", "value", "min", "max", "step", "why"):
            assert field in row, f"{name} has no {field}"
        assert row["min"] < row["max"], name
        assert row["min"] <= row["value"] <= row["max"], name


def test_the_flight_curve_starts_and_ends_at_rest():
    # NOT an assertion about the shipped VALUES (SAVE rewrites those). It is
    # an assertion about the SHAPE: only the two horizontal control points are
    # tunable at all, so the curve cannot be given a non-zero y1 -- which is
    # what "it should just start moving towards its new destination" (user,
    # 2026-07-27, four screenshots one frame apart) means as a number.
    rows = run_node("MARELO_TUNABLES",
                    "console.log(JSON.stringify(MARELO_TUNABLES));")
    assert "flyEaseIn" in rows and "flyEaseOut" in rows
    assert not [key for key in rows if key.lower().endswith(("y1", "y2"))]


# Two shapes write a custom property in this pair of files: marelocelebrate.js
# builds a wrapper-style STRING (`` `--fly-ms:${x}` ``), ranktint.js calls
# `style.setProperty("--rank-tint-color", x)` directly (report 1,
# 2026-07-28) -- a plain literal at the call site rather than an interpolated
# string, since there is only ever one value to set, not a joined style
# attribute. Both are real ways to write a property; the guard below must see
# either or it is blind to exactly the kind of dead control that shipped once
# already (shakePx, deleted from marelotuning.js the same session this file
# was added).
CUSTOM_PROPERTY = re.compile(r'(?:`|setProperty\(\s*")--([a-z-]+)[:"]')


def test_every_custom_property_the_overlay_writes_is_actually_used():
    """A tunable that reaches a CSS variable NOTHING READS is still a slider
    that does nothing -- and `test_every_tunable_is_actually_read_by_the_
    celebration` above cannot see it, because from the JS side the value IS
    read: it is interpolated into a style string. The property then lands on
    the element and no rule ever consults it.

    That shipped once. `shakePx` wrote `--shake-px`, no rule in index.html
    read it, and it did nothing at any value -- indistinguishable at 24px from
    its shipped 0, which is why "it ships off by default" was not a defence.
    The row is gone; this guard is what stops the next one.

    The pair matters: the JS guard proves the registry reaches the code, and
    this one proves the code reaches a pixel. Neither implies the other."""
    css = (UI / "index.html").read_text(encoding="utf-8")
    written = set(CUSTOM_PROPERTY.findall(
        "\n".join(code_only(path) for path in READERS)))
    assert written, "no custom properties found -- re-point this guard"
    dead = sorted(name for name in written if f"var(--{name}" not in css)
    assert not dead, (
        f"{dead} written by {[p.name for p in READERS]} but read by no rule in "
        "index.html. A control that moves a variable nothing consults changes "
        "nothing on screen.")


def test_the_custom_property_guard_can_still_fail():
    """Mutation proof, both directions: a scan that matches nothing is green
    forever, and one tripped by a comment is worse than none."""
    scan = lambda written, css: sorted(
        name for name in written if f"var(--{name}" not in css)
    assert scan({"ghost"}, ".x { color: red }") == ["ghost"]
    assert scan({"live"}, ".x { color: var(--live, red) }") == []


def test_the_custom_property_regex_catches_both_writer_shapes():
    """The ambient tint (ranktint.js, report 1, 2026-07-28) writes its
    property via `style.setProperty("--name", ...)` rather than the
    wrapper-style backtick string marelocelebrate.js's own flight variables
    use -- a second, equally real way to write a custom property. Probed
    directly so a future edit that narrows the regex back to one shape fails
    here rather than silently going blind to the other."""
    assert CUSTOM_PROPERTY.findall("`--fly-ms:${x}ms`") == ["fly-ms"]
    assert CUSTOM_PROPERTY.findall(
        'document.documentElement.style.setProperty("--rank-tint-color", x)'
    ) == ["rank-tint-color"]
    # A prose mention (no backtick, no setProperty call) must not trip it --
    # the same comment-vs-code trap tests/source_scan.py exists to avoid.
    assert CUSTOM_PROPERTY.findall(
        "the --rank-tint-color property is read by body::after") == []

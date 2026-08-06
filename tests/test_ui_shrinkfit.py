"""ui/shrinkfit.js — the pure sizing math behind `nameOverflow: shrinkToFit`
(Option 1, spec 2026-08-04-rank-variants).

`ui/components/shrinkname.js` is the DOM half (measures a real element,
applies the result as an inline `font-size`) and is verified by rendering,
same as every other Preact component in this codebase; this file drives the
import-free arithmetic directly under node, the same split climbcurve.js
(timing math) and rankclimb.js (the rAF loop that applies it) already draw.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "ui"
SHRINKFIT_JS = UI / "shrinkfit.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")


def run_node(body: str):
    script = (f"import {{ fittedFontSize, MIN_FIT_PX }} from {SHRINKFIT_JS.as_uri()!r};\n"
              f"{body}")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_a_name_that_already_fits_keeps_its_exact_tuned_size():
    """The whole point of "never truncate" starts here: a name narrower than
    its column must render at EXACTLY the tuned size, not a hair smaller --
    byte-identical to what `ellipsis` would have shown unshrunk."""
    result = run_node("console.log(JSON.stringify(fittedFontSize(13, 200, 100)));")
    assert result == 13


def test_a_name_exactly_at_the_edge_keeps_its_size_too():
    """availableWidth === naturalWidth is "fits", not "shrink a hair" -- the
    boundary belongs to the caller that already fits, or a name sitting
    exactly at its column's width would flicker between two font sizes on
    every remeasure (kerning noise could tip it either side of the boundary
    from one frame to the next)."""
    result = run_node("console.log(JSON.stringify(fittedFontSize(13, 100, 100)));")
    assert result == 13


def test_an_overflowing_name_shrinks_by_the_overflow_ratio():
    """Text width scales ~linearly with font size, so the first-pass answer
    is the base size scaled by exactly how far over it ran."""
    result = run_node("console.log(JSON.stringify(fittedFontSize(20, 100, 200)));")
    assert result == 10  # 20 * (100/200)


def test_it_never_returns_above_the_base_size():
    """A caller might (accidentally or not) hand in a naturalWidth smaller
    than availableWidth without the early-return catching it first -- this
    proves the ceiling is enforced by the formula's own clamp too, not only
    the fast path above."""
    result = run_node("console.log(JSON.stringify(fittedFontSize(13, 500, 10)));")
    assert result == 13


def test_it_never_shrinks_past_the_floor():
    """A pathological name (far longer than anything the real corpus
    carries) must stop at the floor rather than shrink toward zero -- the
    floor is what keeps a shrink-to-fit failure legible instead of invisible."""
    result = run_node(
        "console.log(JSON.stringify(fittedFontSize(13, 10, 10000, 8)));")
    assert result == 8


def test_the_default_floor_is_exported_and_used_when_none_is_passed():
    result = run_node(
        "console.log(JSON.stringify([MIN_FIT_PX, fittedFontSize(13, 1, 100000)]));")
    floor, shrunk = result
    assert floor == 8
    assert shrunk == floor


@pytest.mark.parametrize("available,natural", [(0, 100), (100, 0), (-5, 100)])
def test_a_missing_or_nonsensical_measurement_returns_the_base_size(available, natural):
    """A ResizeObserver can fire mid-layout with a transient 0-width box (the
    element not yet attached, or momentarily display:none) -- this must never
    be read as "shrink to nothing", so any non-positive measurement is treated
    as "cannot judge yet" and the base size is returned unchanged."""
    result = run_node(f"console.log(JSON.stringify(fittedFontSize(13, {available}, {natural})));")
    assert result == 13

"""Two pieces of text never overlap while one replaces the other.

User, 2026-07-28, on the route swap rendering "70 Star (Standard)" written
across "16 Star - No LBLJ (Standard)": "there's a brief overlap between the two
route names. It seems like we aren't fading in / out sequentially, which causes
a brief glitch. The current name should fully fade out before the new name
fades in... same for all text here, and generally just for how fades should
work in general."

A simultaneous crossfade puts both strings at 0.5 across the middle of its run,
and two different strings stacked in one grid cell at half opacity read as a
rendering fault rather than a blend. No duration fixes it -- the overlap is the
SHAPE of the curve, not its speed -- which is why this is asserted over the
whole run rather than eyeballed at one frame.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
UI = REPO / "src" / "sm64_events" / "ui"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH")


def _sample(at: float) -> list[dict]:
    """exchangeFade across a full run, at 200 steps."""
    script = (
        f"import {{ exchangeFade }} from {(UI / "climbcurve.js").as_uri()!r};\n"
        "const out = [];\n"
        "for (let i = 0; i <= 200; i += 1) {\n"
        f"  out.push(exchangeFade(i / 200, {at}));\n"
        "}\n"
        "console.log(JSON.stringify(out));")
    done = subprocess.run(["node", "--input-type=module", "-"], input=script,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.mark.parametrize("at", [0.2, 0.5, 0.8])
def test_the_outgoing_text_is_gone_before_the_incoming_one_appears(at):
    frames = _sample(at)
    both = [(i, f) for i, f in enumerate(frames) if f["out"] > 0 and f["in"] > 0]
    assert not both, (
        f"at exchange point {at}, {len(both)} frames render BOTH strings -- "
        f"first at step {both[0][0]}: {both[0][1]}. The outgoing text must "
        "reach 0 before the incoming one leaves it.")


@pytest.mark.parametrize("at", [0.2, 0.5, 0.8])
def test_the_exchange_starts_and_finishes_at_the_ends(at):
    frames = _sample(at)
    assert frames[0] == {"out": 1, "in": 0}, frames[0]
    assert frames[-1] == {"out": 0, "in": 1}, frames[-1]


def test_a_simultaneous_crossfade_would_fail_this():
    """Mutation proof. The curve this replaced -- out = 1-p, in = p -- must be
    caught, or the guard is green against the very thing it exists to stop."""
    crossfade = [{"out": 1 - i / 200, "in": i / 200} for i in range(201)]
    both = [f for f in crossfade if f["out"] > 0 and f["in"] > 0]
    assert both, "the probe itself is broken"
    assert len(both) > 100, (
        "a simultaneous crossfade overlaps across most of its run, which is "
        "exactly what was reported")


def test_no_card_text_pairs_a_value_with_its_own_complement():
    """The shape that shipped the bug, banned at the call site too.

    `opacity: 1 - x` beside `opacity: x` IS a simultaneous crossfade however
    the value is computed, so the guard reads the source rather than trusting
    that every future author will reach for the helper."""
    from source_scan import code_only
    card = code_only(UI / "components" / "marelo.js")
    assert "opacity: 1 - swapEase" not in card
    assert "swap.fade.out" in card and "swap.fade.in" in card

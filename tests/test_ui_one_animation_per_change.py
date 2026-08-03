"""One change to the selector must cost ONE animation, measured as what the eye
actually receives.

Live report 2026-08-02: "when swapping between courses, it briefly flashes the
previous course's stars and then flashes again… there should not be a flicker…
it should only trigger ONE animation, and we should be coalescing during that
period."

The cause was two owners animating one event: a stage swap fades the whole
surface, and the row INSIDE it — the same component for any two courses, so
Preact patched it instead of unmounting it — ran its own cell-set exchange on
top. Neither element alone looks wrong, which is why this measures the PRODUCT
of the two opacities: that product is the visibility of a cell on screen, and a
human sees its dips, not either factor's.

Driven on `/ui/tuneselector.html`, which composes the real `SurfaceExchange`
around the real `CellRow` exactly as `stagebanner.js` does — the composition is
where the bug lived, so a gate on either component alone could not have caught
it.
"""
import contextlib
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from ui_fixture import serve_ui  # noqa: E402
from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab import driver  # noqa: E402

VISIBLE = 0.9      # "he can see this"
HIDDEN = 0.35      # "this is gone"

GONE = 0.02        # "this frame is empty"
SHOWING = 0.05     # "there is something on screen"

_TRACE = """(async (label) => {
  const button = [...document.querySelectorAll('button')]
    .find((b) => b.textContent.includes(label));
  if (!button) return JSON.stringify({error: `no ${label} button`});
  const samples = [];
  let running = true;
  const tick = () => {
    const row = document.querySelector('.starrow');
    const surface = document.querySelector('.selector-exchange');
    if (row && surface) {
      // What a CELL's opacity actually is on screen: every ancestor that fades
      // multiplies in. Either factor alone can look perfectly correct while the
      // product blinks twice.
      samples.push([Number(getComputedStyle(surface).opacity)
                    * Number(getComputedStyle(row).opacity),
                    row.children.length,
                    (row.querySelector('.starname') || {}).textContent || ""]);
    }
    if (running) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
  button.click();
  await new Promise((done) => setTimeout(done, 2000));
  running = false;
  return JSON.stringify({samples});
})(LABEL)"""


@contextlib.contextmanager
def chrome(url: str):
    with driver.get_driver().launch(headless=True) as page:
        page.goto(url)
        page.evaluate("new Promise(r => setTimeout(r, 900))")
        yield page


def flashes(values: list[float]) -> int:
    """How many times the surface went away and came back. A flash is
    VISIBLE -> HIDDEN -> VISIBLE; the two thresholds are deliberately apart so
    ordinary easing noise cannot invent one."""
    count, seen_hidden = 0, False
    for value in values:
        if value <= HIDDEN:
            seen_hidden = True
        elif value >= VISIBLE and seen_hidden:
            count += 1
            seen_hidden = False
    return count


def _trace(page, label: str) -> dict:
    raw = page.evaluate(_TRACE.replace("LABEL", json.dumps(label)))
    out = json.loads(raw)
    assert "error" not in out, out["error"]
    assert len(out["samples"]) > 30, (
        f"only {len(out['samples'])} frames — requestAnimationFrame is not "
        f"running, so this measured nothing")
    return out


def test_the_old_stars_never_come_back_after_they_have_gone(tmp_path):
    """THE bug, in his words: "it briefly flashes the previous course's stars and
    then flashes again."

    The discriminator was taken from a real trace rather than reasoned about, and
    the flash COUNTER below could not see it: with the surface fading back in
    while the row inside it faded out, the effective opacity rose only to 0.21
    before returning to zero, so it never crossed back into "visible" and read as
    one long dip. What it really was: nine frames of the PREVIOUS course's stars,
    on screen, after they had already gone. So the property is about the content,
    not the curve — once a set has left, it may never be seen again."""
    with serve_ui(tmp_path / "one-animation.db") as base, \
         chrome(f"{base}/ui/tuneselector.html") as page:
        trace = _trace(page, "Swap the stage")
    samples = trace["samples"]
    counts = [count for _, count, _ in samples]
    assert counts[0] != counts[-1], (
        "the cell set did not change, so nothing was measured")
    was = samples[0][2]
    assert was, "no cell name to track"
    gone_at = next((at for at, (value, _, _) in enumerate(samples)
                    if value <= GONE), None)
    assert gone_at is not None, "the old set never faded out at all"
    revivals = [(at, round(value, 3)) for at, (value, _, name)
                in enumerate(samples[gone_at:], gone_at)
                if name == was and value > SHOWING]
    assert not revivals, (
        f"{was!r} was visible again at {revivals} after it had gone — that is "
        f"the second flash, and it is the previous course's stars")


def test_swapping_only_the_cells_costs_exactly_one_flash(tmp_path):
    """The inner granularity: one dip, all the way out and all the way back."""
    with serve_ui(tmp_path / "one-animation-cells.db") as base, \
         chrome(f"{base}/ui/tuneselector.html") as page:
        trace = _trace(page, "Swap the set")
    opacities = [value for value, _, _ in trace["samples"]]
    assert flashes(opacities) == 1, [round(value, 2) for value in opacities]


def test_a_burst_of_three_changes_still_costs_exactly_one(tmp_path):
    """His rule: "we should be coalescing during that period." Three changes
    inside one fade window are one event to the person watching — and each one
    arriving re-arms the wait, so the row stays hidden until they stop."""
    with serve_ui(tmp_path / "one-animation-burst.db") as base, \
         chrome(f"{base}/ui/tuneselector.html") as page:
        trace = _trace(page, "Burst ×3")
    opacities = [value for value, _, _ in trace["samples"]]
    assert flashes(opacities) == 1, [round(value, 2) for value in opacities]

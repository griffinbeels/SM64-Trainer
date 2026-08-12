"""A practice-card PB caveat never collides with strategy or PB controls.

The reported failure was data-dependent: ``? Unattributed`` widened the PB
grid track until the strategy select painted into it.  This fixture plants
that longest caveat on both an ordinary top-level star and the Bowser Reds
star that renders as a nested child of its Reds-to-Pipe movement.  Geometry is
measured at the same four widths as the practice-log contact sheet.
"""
import json
import sys
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from ui_fixture import serve_ui  # noqa: E402
from uilab.driver import get_driver  # noqa: E402


WIDTHS = (1500, 1200, 900, 850)


def _get(base: str) -> dict:
    with urllib.request.urlopen(
            f"{base}/api/session?clock=igt&scope=session", timeout=10) as response:
        return json.loads(response.read())


def _post(base: str, path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{base}{path}", data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def _make_unattributed(base: str, attempt_id: int, *, save_clock=None) -> None:
    """Retag a real saved PB to null; optionally save the row first."""
    if save_clock:
        _post(base, f"/api/attempts/{attempt_id}/strat",
              {"strat_tag": "Standard"})
        _post(base, "/api/pb",
              {"attempt_id": attempt_id, "timer_mode": save_clock})
    _post(base, f"/api/attempts/{attempt_id}/strat", {"strat_tag": None})


MEASURE = """
(() => Array.from(document.querySelectorAll(
  '.log-card-head .log-card-caveat-slot > .caveat-chip'
)).map((chip) => {
  const slot = chip.parentElement;
  const head = slot.closest('.log-card-head');
  const card = slot.closest('.log-card');
  const picker = head.querySelector('.log-card-strat-picker select');
  const pb = head.querySelector('.pbtag');
  const box = (el) => {
    const r = el.getBoundingClientRect();
    return {left: r.left, right: r.right, width: r.width};
  };
  return {
    nested: !!card.closest('.log-card-children'),
    label: chip.getAttribute('aria-label'),
    chip: box(chip),
    slot: box(slot),
    picker: box(picker),
    pb: box(pb),
    headClient: head.clientWidth,
    headScroll: head.scrollWidth,
  };
}))()
"""


@pytest.fixture(scope="module")
def measured():
    out = {}
    # This is the only shipped parent/child pairing with a STAR as the child;
    # star PB payloads carry caveats, so it reaches the real nested marker
    # without inventing a fake DOM node or a client-only field.
    with serve_ui(reconcile_full_corpus=True, bowser_stage=(16, 17),
                  enter_level=17, seed_reds_run=True) as base:
        view = _get(base)
        top_star = next(s for s in view["stars"]
                        if s["course_id"] == 2 and s["star_id"] == 4)
        nested_star = next(s for s in view["stars"]
                           if s["course_id"] == 16 and s["star_id"] == 0)
        _make_unattributed(base, top_star["pb"]["igt"]["attempt_id"])
        _make_unattributed(base, nested_star["attempts"][0]["id"],
                           save_clock="igt")

        with get_driver().launch() as page:
            page.goto(base)
            page.wait_for(
                ".log-card-children .log-card-caveat-slot .caveat-chip")
            for width in WIDTHS:
                page.set_viewport(width, 1200)
                page.wait_ms(320)
                out[width] = page.evaluate(MEASURE)
    return out


@pytest.mark.parametrize("width", WIDTHS)
def test_caveat_precedes_strategy_and_pb_without_overlap(measured, width):
    rows = measured[width]
    assert len(rows) == 2, (
        f"expected one top-level and one nested unattributed caveat at {width}px, "
        f"got {rows}")
    assert {row["nested"] for row in rows} == {False, True}, (
        f"fixture did not reach both card depths at {width}px: {rows}")
    for row in rows:
        assert row["label"].startswith("Not attributed to a strategy")
        assert row["slot"]["left"] <= row["chip"]["left"]
        assert row["chip"]["right"] <= row["slot"]["right"] + 0.5
        assert row["chip"]["right"] <= row["picker"]["left"] + 0.5, (
            f"caveat reached into strategy selector at {width}px: {row}")
        assert row["picker"]["right"] <= row["pb"]["left"] + 0.5, (
            f"strategy selector reached into PB at {width}px: {row}")
        assert row["headScroll"] <= row["headClient"] + 1, (
            f"card head overflowed at {width}px: {row}")


def test_the_geometry_guard_can_fail(measured):
    row = measured[min(WIDTHS)][0]
    shifted = {**row["chip"], "right": row["picker"]["left"] + 10}
    assert shifted["right"] > row["picker"]["left"] + 0.5

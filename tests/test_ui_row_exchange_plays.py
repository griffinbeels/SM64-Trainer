"""The selector's card exchange, measured in a real browser at the page's own
frame rate.

Live report 2026-08-02: "when we invalidate / add / remove cards from the menu
here, it feels more like a bug / error than intentional… they should see their
old options fade away, and their new options appear, no intermediate."

`tests/test_ui_exchange.py` proves the state machine's law; this proves the law
reaches the SCREEN, which is a separate claim and the one this project has been
burned on — unit tests plus `node --check` once shipped an entirely invisible
feature. Both end states look correct whether or not anything animates, so the
measurement has to be a trace rather than a screenshot: sample the row every
frame, and require that the number of cards CHANGES only while the row is
effectively invisible. That is his "no intermediate", stated as a number.

The set is changed the way the app really changes it — selecting a route, which
narrows the star selector to that route's own stars — not by poking the DOM.
"""
import contextlib
import json
import sys
import urllib.request
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

# The fixture seeds and targets a Whomp's Fortress star, so a route naming ONE
# WF star narrows a seven-cell row to one cell — a real set change through the
# real path (tools/ui_fixture.py::FIXTURE_STAR).
FIXTURE_COURSE = 2
ROUTE_STAR = 0

# Sampled while the exchange runs. The swap lands at zero opacity with the beat
# still to come, so anything above this counts as "he could see it".
INVISIBLE_ENOUGH = 0.35

_TRACE = """(async (routeId) => {
  const rowOf = () => document.querySelector('.starrow');
  if (!rowOf()) return JSON.stringify({error: 'no .starrow on the page'});
  const samples = [];
  let running = true;
  const tick = () => {
    const row = rowOf();
    if (row) samples.push([Number(getComputedStyle(row).opacity),
                           row.children.length]);
    if (running) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
  const trigger = document.querySelector('.marelo-bar .context-card-trigger');
  if (!trigger) return JSON.stringify({error: 'no card trigger on the page'});
  trigger.click();
  await new Promise((done) => setTimeout(done, 50));
  const option = document.querySelector(
    '.marelo-bar .search-menu-option[data-value="' + routeId + '"]');
  if (!option) return JSON.stringify({error: 'no option for route ' + routeId});
  option.click();
  await new Promise((done) => setTimeout(done, 1600));
  running = false;
  return JSON.stringify({samples});
})(ROUTE_ID)"""


def _post(base: str, path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{base}{path}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


@contextlib.contextmanager
def chrome(url: str):
    with driver.get_driver().launch(headless=True) as page:
        page.goto(url)
        yield page


def test_the_card_set_changes_only_while_the_row_is_invisible(tmp_path):
    with serve_ui(tmp_path / "row-exchange.db") as base:
        route = _post(base, "/api/routes", {
            "name": "One WF star", "steps": [
                {"need": 1, "candidates": [
                    {"type": "star", "course": FIXTURE_COURSE,
                     "star": ROUTE_STAR}]}]})

        with chrome(f"{base}/ui/index.html") as page:
            # Control interaction first: a frozen fixture reads exactly like a
            # feature that never runs (ui-core.md's verification norm).
            before = page.evaluate("document.body.innerHTML.length")
            page.evaluate(
                "(() => { const tab = [...document.querySelectorAll('button,a')]"
                ".find(e => /Segments/i.test(e.textContent));"
                " if (tab) tab.click(); return true; })()")
            page.evaluate("new Promise(r => setTimeout(r, 300))")
            assert before != page.evaluate("document.body.innerHTML.length"), (
                "control interaction did not change the DOM — the harness is "
                "broken, not (necessarily) the feature")
            page.evaluate(
                "(() => { const tab = [...document.querySelectorAll('button,a')]"
                ".find(e => /Practice/i.test(e.textContent));"
                " if (tab) tab.click(); return true; })()")
            page.evaluate("new Promise(r => setTimeout(r, 400))")

            raw = page.evaluate(_TRACE.replace("ROUTE_ID", str(route["id"])))
            trace = json.loads(raw)

    assert "error" not in trace, trace["error"]
    samples = [(opacity, count) for opacity, count in trace["samples"]]
    assert len(samples) > 20, (
        f"only {len(samples)} frames sampled — requestAnimationFrame is not "
        f"running, so this measured nothing")

    counts = [count for _, count in samples]
    assert counts[0] != counts[-1], (
        f"the row still holds {counts[-1]} cards — the route pick did not "
        f"change the set, so nothing was measured")
    assert min(opacity for opacity, _ in samples) <= 0.1, (
        "the row never faded: the set was replaced by a repaint, which is the "
        "bug he reported")

    # THE property. Every frame where the count changed must have been a frame
    # he could not see.
    visible_changes = [
        (previous, opacity, count)
        for (_, previous), (opacity, count) in zip(samples, samples[1:])
        if count != previous and opacity > INVISIBLE_ENOUGH]
    assert not visible_changes, (
        f"the card set changed in plain sight at {visible_changes} — cards "
        f"appearing or leaving while the row is visible is exactly what reads "
        f"as a bug rather than as an intentional swap")

"""Rapid route switching must never leave the header stuck disagreeing with
itself (live report 2026-07-28): "I selected [16 Star — LBLJ (Standard)],
but it's clearly displaying wrong... if I go back and forth and change
routes fast enough, then it breaks the system, it gets confused, and it gets
stuck on one of the routes."

Root cause, found by reproduction: the active route is ONE practice-wide
setting shared by every connected client (browser tab + desktop GUI, rule
10 -- see also .claude/rules/ui-practice.md). The old reconcile effect in
store.js unconditionally re-POSTed a client's OWN remembered pick whenever
the server disagreed, with no way to tell "the server drifted, fix it" from
"another connected client legitimately just changed it". Two (or three)
clients holding different opinions then fight forever: each client's own
corrective POST is itself a disagreement the OTHER client corrects right
back, broadcasting without bound and never settling. A single connected
client alone could not reproduce it (measured 0/25 with one client); three
clients reproduced it 13/20 times even after a 3.5s settle wait.

This test drives THREE connected clients -- a stand-in for browser tab +
desktop GUI + a second browser tab, all explicitly supported (rule 10) --
switches routes rapidly on one of them, and requires the select's value, the
header card's rendered label, and the server's own view.active_route.id to
agree after every switch. It must fail on the code this fixes and pass
after it; run repeatedly (not once) because a race that reproduces
1-in-5 is still reproduced.
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

# uilab replaced tools/cdp.py (deleted on main when the layout rig was
# extracted). Its Page protocol offers the verbs this test needs, and resolving
# uilab BY PATH is what stops `uv sync` pruning the gate into a silent skip --
# the reasoning is in tools/find_uilab.py, which is the ONE door for it.
from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab import driver  # noqa: E402

# This test was SKIPPED for two days on two claims about uilab, both of which
# turned out to be false when finally measured (2026-07-29):
#
#   "launch() yields ONE Page and nesting sync-Playwright contexts raises Sync
#   API inside the asyncio loop"  -- it does not. The driver reference-counts
#   one Playwright instance across nested launches precisely so a second
#   browser can stand beside a live one; three concurrent clients measured
#   working.
#
#   "evaluate does not await a Promise"  -- it does. The real fault was uilab's
#   own evaluate WRAPPER, which chose its form by `startswith("(")` and so
#   dropped the `return` from every plain expression, returning None for all of
#   them. Fixed in uilab; the settle waits below (`new Promise(r =>
#   setTimeout(...))`) depend on the awaiting that was said to be absent.
#
# Neither claim was ever tested. A named coverage gap is better than a silent
# one, but a named gap resting on an unmeasured cause is how a passing test
# stays off for no reason -- so the reason a skip cites now gets measured before
# it is written down. The livelock needs THREE clients (one client: 25 trials, 0
# failures; three: 13 of 20 stuck), which is why it is worth this much fuss.


@contextlib.contextmanager
def chrome_session(url: str):
    """One connected client -- a browser tab, or the desktop GUI (rule 10)."""
    with driver.get_driver().launch(headless=True) as page:
        page.goto(url)
        yield page

# Two confusingly-similar names, matching the live report, plus two more so
# "switch through several routes" is a real cycle rather than a toggle.
ROUTE_NAMES = [
    "16 Star — LBLJ (Standard)",
    "16 Star — No LBLJ (Standard)",
    "70 Star (Standard)",
    "Any% (Standard)",
]

TRIALS = 10
SETTLE_SECONDS = 2.0

_DRIVE = """(async (ids) => {
  const sel = document.getElementById('route-select');
  if (!sel) return JSON.stringify({error: 'no route-select in DOM'});
  for (const id of ids) {
    sel.value = String(id);
    sel.dispatchEvent(new Event('change', {bubbles: true}));
  }
  return JSON.stringify({dispatched: ids});
})(IDS)"""

_MEASURE = """(() => {
  const sel = document.getElementById('route-select');
  const label = document.querySelector('.marelo-bar-body .context-value');
  return JSON.stringify({
    selectValue: sel ? sel.value : null,
    cardLabel: label ? label.textContent : null,
  });
})()"""


def _post(base: str, path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"{base}{path}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _get(base: str, path: str) -> dict:
    with urllib.request.urlopen(f"{base}{path}", timeout=10) as r:
        return json.loads(r.read())


def test_rapid_route_switching_converges_with_multiple_connected_clients(tmp_path):
    with serve_ui(tmp_path / "route-switch.db") as base:
        id_to_name: dict[int, str] = {}
        ids: list[int] = []
        for name in ROUTE_NAMES:
            out = _post(base, "/api/routes", {"name": name, "steps": []})
            ids.append(out["id"])
            id_to_name[out["id"]] = name

        with chrome_session(f"{base}/ui/index.html") as page, \
             chrome_session(f"{base}/ui/index.html") as gui_client, \
             chrome_session(f"{base}/ui/index.html") as second_tab:
            # Control interaction: prove the harness itself is alive before
            # trusting any measurement from it (ui-core.md's verification
            # norm -- a frozen fixture reads exactly like a broken feature).
            before = page.evaluate("document.body.innerHTML.length")
            page.evaluate(
                "(() => { const t = [...document.querySelectorAll('button,a')]"
                ".find(e => /Segments/i.test(e.textContent)); "
                "if (t) t.click(); return true; })()")
            page.evaluate("new Promise(r => setTimeout(r, 300))")
            after = page.evaluate("document.body.innerHTML.length")
            assert before != after, (
                "control interaction did not change the DOM -- the harness "
                "itself is broken, not (necessarily) the feature")

            for other in (gui_client, second_tab):
                other.evaluate("new Promise(r => setTimeout(r, 200))")

            failures = []
            for trial in range(TRIALS):
                order = ids[trial % len(ids):] + ids[:trial % len(ids)]
                page.evaluate(_DRIVE.replace("IDS", json.dumps(order)))
                page.evaluate(
                    f"new Promise(r => setTimeout(r, {int(SETTLE_SECONDS * 1000)}))")

                measured = json.loads(page.evaluate(_MEASURE))
                server_view = _get(base, "/api/session?clock=igt&scope=session")
                server_active = (server_view.get("active_route") or {}).get("id")
                select_id = (int(measured["selectValue"])
                             if measured["selectValue"] else None)

                expected_id = order[-1]
                expected_name = id_to_name[expected_id]
                ok = (select_id == expected_id
                      and server_active == expected_id
                      and measured["cardLabel"] == expected_name)
                if not ok:
                    failures.append(
                        f"trial {trial}: expected route {expected_id} "
                        f"({expected_name!r}) but select={select_id} "
                        f"server_active_route_id={server_active} "
                        f"card_label={measured['cardLabel']!r}")

            assert not failures, (
                f"{len(failures)}/{TRIALS} trial(s) left the header stuck "
                "disagreeing with itself after rapid route switching with "
                "multiple connected clients:\n" + "\n".join(failures))

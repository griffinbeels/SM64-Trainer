"""POST /api/routes/{route_id}/reset — the seeded-route restore — existed
with docs and no UI caller: by his own rule a capability nobody can reach
does not exist, and this exact gap was closed once already for segments
(`segments.js`'s .builder-seeded panel). This drives the route sibling end
to end: edit a shipped route, see the "Edited copy" notice at the point of
the edit, click Reset, and watch the bundled version come back — then checks
a user-created route offers no reset at all (a button that can only 409 is a
dead control).
"""
import json
import shutil
import sys
import time
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from ui_fixture import serve_ui  # noqa: E402
from uilab import driver  # noqa: E402


def _get(base, path):
    return json.loads(urllib.request.urlopen(f"{base}{path}", timeout=10).read())


def _send(base, method, path, body=None):
    request = urllib.request.Request(
        f"{base}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else b"",
        headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(request, timeout=10).read()


def _click_route_row(page, name):
    # Scoped to .routes-page — a bare selector can find another tab's hidden
    # list (the twice-measured trap in .claude/rules/ui-core.md).
    page.evaluate(
        '[...document.querySelectorAll(".routes-page .route-list-item")]'
        f'.find((el) => el.textContent.includes({json.dumps(name)})).click()')


def test_the_reset_button_restores_a_shipped_route():
    with serve_ui(reconcile_full_corpus=True) as base:
        seeded = next(r for r in _get(base, "/api/routes") if r.get("seed_key"))
        original_name = seeded["name"]
        edited_name = original_name + " EDITED"
        _send(base, "PUT", f"/api/routes/{seeded['id']}", {"name": edited_name})

        with driver.get_driver().launch(headless=True) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".practice-page", timeout_ms=15000)
            # The library's groups render collapsed until an open-set is
            # stored; seed it so the edited route's row exists to click.
            parts = (seeded.get("category") or "").split("/")
            open_set = ["/".join(parts[:i + 1]) for i in range(len(parts))
                        if parts[0]]
            page.evaluate(
                'localStorage.setItem("sm64.routeCatsOpen", '
                f'{json.dumps(json.dumps(open_set))})')
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".practice-page", timeout_ms=15000)
            page.evaluate(
                'document.querySelector(\'.nav-item[title="Routes"]\').click()')
            page.wait_for(".routes-page .route-list", timeout_ms=10000)
            _click_route_row(page, edited_name)

            page.wait_for(".routes-page .builder-seeded.is-dirty",
                          timeout_ms=10000)
            label = page.evaluate(
                'document.querySelector('
                '".routes-page .builder-seeded .field-label").textContent')
            assert "Edited copy" in label
            page.evaluate(
                'document.querySelector('
                '".routes-page .builder-seeded button").click()')

            deadline = time.time() + 10
            row = None
            while time.time() < deadline:
                row = next(r for r in _get(base, "/api/routes")
                           if r["id"] == seeded["id"])
                if row["name"] == original_name and not row["seed_dirty"]:
                    break
                time.sleep(0.3)
            else:
                pytest.fail(f"reset never landed server-side: {row}")
            # and the notice itself settles back to the shipped state
            page.wait_for(".routes-page .builder-seeded:not(.is-dirty)",
                          timeout_ms=10000)


def test_a_user_created_route_offers_no_reset():
    with serve_ui(reconcile_full_corpus=True) as base:
        _send(base, "POST", "/api/routes",
              {"name": "My Own Plan", "steps": [],
               "category": "Main Categories/16 Star"})
        with driver.get_driver().launch(headless=True) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".practice-page", timeout_ms=15000)
            page.evaluate(
                'localStorage.setItem("sm64.routeCatsOpen", '
                + json.dumps(json.dumps(
                    ["Main Categories", "Main Categories/16 Star"])) + ')')
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".practice-page", timeout_ms=15000)
            page.evaluate(
                'document.querySelector(\'.nav-item[title="Routes"]\').click()')
            page.wait_for(".routes-page .route-list", timeout_ms=10000)
            _click_route_row(page, "My Own Plan")
            page.wait_for(".routes-page .route-setup-card", timeout_ms=10000)
            assert not page.evaluate(
                '!!document.querySelector(".routes-page .builder-seeded")'), (
                "a user-created route (no seed_key) is offering Reset — that "
                "button can only 409")

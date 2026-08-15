"""/ui/sync.html -- the version-sync coverage dashboard.

A render test, per this project's own rule: unit tests plus `node --check`
once shipped an invisible feature (`.claude/rules/ui-core.md`). Registers two
throwaway gates for the whole module (address_gates.py/calibration_gates.py/
feature_gates.py are still empty stubs in this worktree, filled in by their
own tracks) so there is something for the page to draw -- the same
save/clear/restore discipline tests/test_sync_gates_contract.py uses.

Runs against a REAL uvicorn thread (`tools/ui_fixture.py::serve_ui`), never
the frozen exe and never a server the human might be playing on -- and it is
the one test in this project that writes a real PUT into
data/version_sync/jp.json, so it cleans that file back up afterwards
(ui-core.md: "a driven test writes to the REAL store... restore what you
change").
"""
import json
import shutil
import sys
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH")

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from ui_fixture import serve_ui  # noqa: E402
from uilab import driver  # noqa: E402

from sm64_events.sync import registry as _registry  # noqa: F401  -- load the REAL registry before any fixture clears it
from sm64_events.sync import gates as G  # noqa: E402

GLOBAL_TIMER_GATE = "address.global_timer"
DISPLAY_TICK_GATE = "calibration.igt_clock.DISPLAY_TICK"


def _ok(ctx):
    return G.Verdict("verified")


@pytest.fixture(scope="module", autouse=True)
def _registered_gates():
    saved = list(G.GATES)
    G.GATES.clear()
    G.register(
        G.Gate(GLOBAL_TIMER_GATE, "version", "address",
              "watch the counter tick 30/s", "the address is right", _ok),
        G.Gate(DISPLAY_TICK_GATE, "IGT clock", "calibration",
              "grab a star and compare the two numbers", "the JP tick matches US",
              _ok, backs="detectors.igt_clock.DISPLAY_TICK"))
    yield
    G.GATES.clear()
    G.GATES.extend(saved)


@pytest.fixture(scope="module")
def report_file(request):
    """The one file this module writes into for real. Backed up and restored
    rather than merely deleted, in case a future run of this file lands
    beside a real jp.json a runner has already written."""
    path = REPO / "data" / "version_sync" / "jp.json"
    backup = path.read_bytes() if path.exists() else None
    yield path
    if backup is None:
        path.unlink(missing_ok=True)
    else:
        path.write_bytes(backup)


@pytest.fixture(scope="module")
def sync_page(report_file):
    with serve_ui(seed=False) as base, \
            driver.get_driver().launch(headless=True) as page:
        page.goto(f"{base}/ui/sync.html")
        page.wait_for(".sync-card", timeout_ms=20000)
        yield base, page


def _jp_chip_status(page, gate_tail):
    return page.evaluate(
        "const cells = Array.from(document.querySelectorAll('.sync-gate-id'));"
        f"const cell = cells.find((c) => c.textContent === {gate_tail!r});"
        "if (!cell) return 'NO ROW';"
        "return cell.closest('tr').querySelectorAll('.sync-chip')[1]"
        ".getAttribute('data-status');")


def test_the_page_loads_with_no_console_errors(sync_page):
    _base, page = sync_page
    assert page.problems() == []


def test_one_sync_card_per_registered_feature(sync_page):
    base, page = sync_page
    with urllib.request.urlopen(f"{base}/api/sync", timeout=10) as response:
        body = json.loads(response.read())
    assert page.count(".sync-card") == len(body["features"])


def test_with_an_empty_report_every_jp_chip_reads_missing(sync_page):
    _base, page = sync_page
    statuses = page.evaluate(
        "return Array.from(document.querySelectorAll("
        "'.sync-table tbody tr td:nth-child(5) .sync-chip'))"
        ".map((chip) => chip.getAttribute('data-status'));")
    assert statuses, "no JP chips found -- the table drew no gate rows"
    assert set(statuses) == {"missing"}


def test_a_put_verdict_reaches_the_page_live_with_no_reload(sync_page):
    base, page = sync_page
    assert _jp_chip_status(page, "global_timer") == "missing"

    request = urllib.request.Request(
        f"{base}/api/sync/verdict", method="PUT",
        headers={"Content-Type": "application/json"},
        data=json.dumps({
            "version": "jp", "gate_id": GLOBAL_TIMER_GATE,
            "verdict": {"status": "verified", "value": 0x8032C694},
            "at": "2026-08-15T00:00:00Z",
        }).encode("utf-8"))
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.status == 200

    for _ in range(30):
        if _jp_chip_status(page, "global_timer") == "verified":
            break
        page.wait_ms(100)
    else:
        pytest.fail("JP chip for address.global_timer never reached "
                    "'verified' within 3s of the PUT -- the live sync_verdict "
                    "update did not reach the page")
    assert page.problems() == []


def test_the_dashboard_is_reachable_from_the_settings_drawer():
    """acceptance.md's rule for any tool page: reachable with no typed path,
    and the link must be origin-relative (the server's port moves between a
    dev run and run-test-server.bat -- a hardcoded host/port is wrong the
    moment it does)."""
    header = (REPO / "src" / "sm64_events" / "ui" / "components"
             / "header.js").read_text(encoding="utf-8")
    assert 'href="/ui/sync.html"' in header, \
        "no link to /ui/sync.html in the settings drawer"
    import re
    assert not re.search(r'href="https?://[^"]*sync\.html', header), \
        "the sync.html link must be origin-relative, never a hardcoded host/port"

"""A rank edit reaches the other, still-mounted reader through the real socket.

The API/store tests own equality of the Library and Practice payloads. These
tests own the missing browser hop: an existing panel must actually refetch,
without requiring a reopen, remount, or navigation gesture.
"""
import json
import sys
from pathlib import Path
from urllib.parse import quote

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
from find_uilab import find_uilab  # noqa: E402

if missing := find_uilab():
    pytest.skip(missing, allow_module_level=True)

from uilab.driver import get_driver  # noqa: E402
from ui_fixture import serve_ui_live  # noqa: E402
from standards_panel import OPEN_PANEL, api, standards_payload  # noqa: E402


def _until(page, condition):
    assert page.evaluate(f"""(async () => {{
      const end = performance.now() + 12000;
      while (performance.now() < end) {{
        if ({condition}) return true;
        await new Promise(resolve => setTimeout(resolve, 40));
      }}
      return false;
    }})()"""), condition


def _edit(base, entity, strategy, seconds):
    return api(base, "/api/ranks/standards/"
               f"{quote(entity, safe='')}/{quote(strategy, safe='')}/Mario",
               {"seconds": seconds}, method="PUT")


def _clear_fixture_startup_errors(page, base):
    # This offline fixture omits the replay/update controllers. Account for
    # their known startup 404s explicitly, then require a clean edit flow.
    problems = page.problems()
    omitted = {f"HTTP 404 {base}/api/{name}/status" for name in ("replay", "update")}
    resource_error = "console.error: Failed to load resource: the server responded with a status of 404 (Not Found)"
    requests = sum(problem in omitted for problem in problems)
    assert all(problem in omitted or problem == resource_error for problem in problems), problems
    assert len(problems) == requests * 2, problems


def _practice_cell(strategy):
    return f"""(() => {{
      const table = document.querySelector('.stdpanel .stdtable');
      if (!table) return null;
      const column = [...table.querySelector('thead tr:last-child').children]
        .findIndex(th => th.childNodes[0]?.textContent.trim() === {json.dumps(strategy)});
      const row = [...table.querySelectorAll('tbody > tr')]
        .find(tr => tr.querySelector('.std-tier-name')?.textContent.trim() === 'Mario');
      return column < 0 ? null : row?.children[column]?.textContent.trim();
    }})()"""


@pytest.mark.parametrize("width", [850, 1500])
def test_an_open_practice_panel_receives_another_clients_edit_without_reopening(width):
    with serve_ui_live() as (base, _service):
        with get_driver().launch(headless=True, viewport=(width, 1100)) as page:
            page.goto(base)
            page.wait_for(".log-card .standards-toggle")
            entity = page.evaluate(OPEN_PANEL)
            page.wait_for(".stdpanel .stdtable")
            data = standards_payload(base, entity)
            strategy = next(name for name, ladder in data["strategies"].items()
                            if ladder.get("Mario") and name not in data["jp_strategies"])
            before = page.evaluate(_practice_cell(strategy))
            assert before, "fixture never displayed the chosen strategy's threshold"
            assert before != '12"34', "the edit must visibly change the threshold"
            _clear_fixture_startup_errors(page, base)
            page.evaluate("window.__stdTable = document.querySelector('.stdpanel .stdtable')")
            _edit(base, entity, strategy, 12.34)
            _until(page, f"{_practice_cell(strategy)} === '12\\\"34'")
            assert page.evaluate("window.__stdTable === document.querySelector('.stdpanel .stdtable')")
            assert page.evaluate("document.querySelector('.stdpanel .standards-toggle').getAttribute('aria-expanded')") == "true"
            assert page.problems() == []


def test_a_hidden_library_refetches_in_place_after_a_rank_edit():
    with serve_ui_live() as (base, _service):
        with get_driver().launch(headless=True, viewport=(1500, 1100)) as page:
            page.goto(base)
            page.wait_for(".log-card .standards-toggle")
            entity = page.evaluate(OPEN_PANEL)
            page.wait_for(".stdpanel .stdtable")
            page.evaluate("document.querySelector('.nav-item[title=\"Library\"]').click()")
            page.wait_for(".library-target .library-section.open")
            page.evaluate("""(() => {
              window.__librarySection = document.querySelector('.library-section.open');
              window.__libraryRowFetches = 0;
              const realFetch = window.fetch;
              window.fetch = (url, ...rest) => {
                if (String(url).includes('/api/library/target/')) window.__libraryRowFetches++;
                return realFetch(url, ...rest);
              };
              document.querySelector('.nav-item[title="Practice"]').click();
            })()""")
            data = standards_payload(base, entity)
            strategy = next(name for name, ladder in data["strategies"].items() if ladder.get("Mario"))
            _clear_fixture_startup_errors(page, base)
            _edit(base, entity, strategy, 12.34)
            _until(page, "window.__libraryRowFetches > 0")
            page.wait_ms(500)
            assert page.evaluate("window.__librarySection === document.querySelector('.library-section.open')")
            assert page.evaluate("window.__librarySection.isConnected")
            # An attempt changes standings, but never forces a ladder reload.
            reads = page.evaluate("window.__libraryRowFetches")
            from ui_fixture import _run_coro
            from sm64_events.core.events import Event
            from datetime import datetime, timezone
            _run_coro(_service.broadcaster.publish(Event(type="attempt_completed",
                frame=0, timestamp_utc=datetime.now(timezone.utc), payload={})))
            page.wait_ms(600)
            assert page.evaluate("window.__libraryRowFetches") == reads
            assert page.problems() == []

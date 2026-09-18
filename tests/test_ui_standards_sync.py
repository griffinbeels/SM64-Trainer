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


def test_an_open_practice_panel_receives_another_clients_edit_without_reopening():
    # ONE width: the claim is that the OPEN panel refetches in place -- the
    # cell's text changes, the table node is the same node, the toggle stays
    # expanded. No assertion reads geometry, so a second viewport was a
    # second live server and a second browser launch for nothing.
    width = 1500
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


def test_a_provisioned_manual_piece_shows_its_estimate_in_both_pages(tmp_path):
    # ONE width: the claims are the estimate's presence, its title, the
    # "Add a time below" copy and the Library's own "Estimated" line -- all
    # text, none of it geometry -- so three viewports meant three live
    # servers and three browser launches proving the same three strings.
    width = 1920
    with serve_ui_live(stage=(4, 5), target=(4, 6)) as (base, service):
        piece = next(d for d in service.db.segment_defs()
                     if "star:4:6" in d["parents"] and not d["start_triggers"] and not d["end_triggers"]
                     and service.ranks.estimated_strategies(f"segment:{d['id']}"))
        with get_driver().launch(headless=True, viewport=(width, 1100)) as page:
            page.goto(base)
            page.wait_for(".log-card")
            page.evaluate("document.querySelector('.log-card-fold').click()")
            _until(page, f"[...document.querySelectorAll('.log-card-select')].some(b => b.textContent.includes({json.dumps(piece['name'])}))")
            page.evaluate(f"""(() => {{
              const select = [...document.querySelectorAll('.log-card-select')]
                .find(b => b.textContent.includes({json.dumps(piece['name'])}));
              window.__manualCard = select.closest('.log-card');
              select.click();
            }})()""")
            page.wait_ms(100)
            page.evaluate("window.__manualCard.querySelector('.log-card-fold').click()")
            page.wait_for(".manual-timing-note")
            page.evaluate("""(() => {
              window.__manualCard.querySelector('.standards-toggle').click();
              window.__manualCard.scrollIntoView({block: 'start'});
            })()""")
            page.wait_for(".std-estimate")
            assert page.evaluate("!!window.__manualCard.querySelector('.addtime-open')")
            assert page.evaluate("window.__manualCard.querySelector('.std-estimate').title")
            assert page.evaluate("window.__manualCard.textContent.includes('Add a time below')")
            _clear_fixture_startup_errors(page, base)
            # The run's own tmp_path, never a tracked directory: a test that
            # writes into .planning leaves artefacts in the checkout on every
            # run and makes `git status` lie about what the task changed.
            out = tmp_path
            (out / f"practice-manual-{width}.png").write_bytes(page.screenshot())
            page.evaluate("window.__manualCard.querySelector('.log-card-library-link').click()")
            page.wait_for(".library-section.open .library-ladder-estimate")
            assert page.evaluate("document.querySelector('.library-section.open .library-ladder-estimate').textContent.includes('Estimated')")
            (out / f"library-estimate-{width}.png").write_bytes(page.screenshot())
            assert page.problems() == []

"""The real recorder saves alternate starts from separate journal samples."""
import asyncio
import datetime as dt
import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from sm64_events.core.events import Event
from sm64_events.server import api
from sm64_events.tracking import service as service_module

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from find_uilab import find_uilab

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab.driver import get_driver
from ui_fixture import serve_ui_live


def _seed_recording(service):
    stamp = dt.datetime(2026, 9, 30, 12, tzinfo=dt.timezone.utc)

    async def publish():
        for kind, frame, payload in (
            ("level_changed", 69990, {"from": 16, "to": 6}),
            ("area_changed", 69990, {"level": 6, "from": None, "to": 1}),
            ("practice_reset", 69992, {"igt_frames_before": 0}),
        ):
            await service.publish(Event(type=kind, frame=frame,
                timestamp_utc=stamp, payload=payload))
        # The primary sample is 52 -> 53. Both alternative starts are outside
        # that span, so sorting all selected IDs into one run would move both ends.
        for ordinal, frame in ((51, 70000), (52, 70030), (53, 70120), (54, 70140)):
            await service.publish(Event(type="moment_reached", frame=frame,
                timestamp_utc=stamp, payload={"kind": "door_open", "ordinal": ordinal,
                    "landmark": None, "level": 6, "area": 1, "action": 0,
                    "igt_frames": frame - 69992}))

    asyncio.run(publish())


def _pick_label(page, label):
    page.evaluate(f"""const label = {json.dumps(label)};
      const matches = Array.from(document.querySelectorAll('.modal .record-row'))
        .filter(row => row.querySelector('.record-label').textContent.trim() === label);
      if (matches.length !== 1) throw new Error(`Expected one ${{label}}, got ${{matches.length}}`);
      matches[0].click();""")


def _toolbar_click(page, label):
    page.evaluate(f"""const matches = Array.from(document.querySelectorAll('.record-picks button'))
      .filter(button => button.textContent.trim() === {json.dumps(label)});
      if (matches.length !== 1) throw new Error('Recorder action unavailable');
      matches[0].click();""")


def _ready_to_save(page):
    assert page.evaluate("""return (async () => {
      const deadline = Date.now() + 10000;
      while (Date.now() < deadline) {
        const button = document.querySelector('.modal .builder-actions .primary-button');
        if (button && !button.disabled && button.textContent.trim() === 'Save segment') return true;
        await new Promise(resolve => setTimeout(resolve, 30));
      }
      return false;
    })();"""), "The recorder did not finish synthesis, backtest and lint"


def _capture_review(page, tmp_path, name="recorder-alternate-starts"):
    captures = Path(os.environ.get("SM64_REPORT_DIR") or tmp_path)
    captures.mkdir(parents=True, exist_ok=True)
    for width in (1500, 850):
        page.set_viewport(width, 1180)
        page.wait_ms(250)
        geometry = page.evaluate("""return Array.from(document.querySelectorAll(
          '.record-picks button, .modal .builder-actions button')).map(button => {
            const rect = button.getBoundingClientRect(); return [rect.left, rect.right];
          });""")
        assert geometry and all(0 <= left < right <= width for left, right in geometry)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        shot = captures / f"{name}-{width}.png"
        shot.write_bytes(page.screenshot())
        print(f"Alternate-start screenshot: {shot}")


def test_alternate_starts_persist_through_the_visible_recorder(tmp_path, monkeypatch):
    # The fixture is personal even in a maintainer checkout. This test must
    # exercise the actual save endpoint without changing release seed inputs.
    monkeypatch.setattr(api, "shared_defaults_root", lambda: None)
    monkeypatch.setattr(service_module, "shared_defaults_root", lambda: None)
    with serve_ui_live(seed=False) as (base, service):
        _seed_recording(service)
        with get_driver().launch() as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for('button.nav-item[title="Segments"]')
            page.click('button.nav-item[title="Segments"]')
            page.wait_for(".segments-page .library-search")
            page.evaluate("""const buttons = Array.from(document.querySelectorAll('.segments-page button'));
              buttons.find(button => button.textContent.includes('Record a segment')).click();""")
            page.wait_for(".record-row")
            # Observe the actual HTTP writes without replacing any response.
            page.evaluate("""window.__recordStartRequests = [];
              const original = window.fetch;
              window.fetch = function(url, options = {}) {
                if (options.body && String(url).startsWith('/api/segments'))
                  window.__recordStartRequests.push({url: String(url),
                    method: options.method, body: JSON.parse(options.body)});
                return original.call(this, url, options);
              };""")
            timeline = page.evaluate("return fetch('/api/segments/timeline?limit=200&view=steps').then(r=>r.json())")
            rows = {row["frame"]: row for row in timeline["rows"]
                    if row["type"] == "moment_reached" and row["frame"] >= 70000}
            assert set(rows) == {70000, 70030, 70120, 70140}
            _pick_label(page, rows[70030]["label"])
            _pick_label(page, rows[70120]["label"])
            _ready_to_save(page)
            name = "Recorded alternate starts through the real page"
            page.evaluate(f"""const input = document.querySelector('.record-review .builder-name input');
              input.value = {json.dumps(name)};
              input.dispatchEvent(new Event('input', {{bubbles: true}}));""")
            _toolbar_click(page, "Add another start")
            _pick_label(page, rows[70140]["label"])
            _pick_label(page, rows[70000]["label"])
            _ready_to_save(page)
            assert page.evaluate("document.querySelectorAll('.record-mark.role-start').length") == 3
            assert page.evaluate("document.querySelectorAll('.record-mark.role-finish').length") == 1
            assert page.evaluate("Array.from(document.querySelectorAll('.record-mark.role-start'))"
                                 ".filter(mark=>mark.textContent.trim()==='OR').length") == 2
            _toolbar_click(page, "Done adding starts")
            _capture_review(page, tmp_path)
            # Save opens the Builder, which runs its own diagnostics. Hold the
            # recorder's final requests while this surface is still mounted.
            recorder_requests = page.evaluate("window.__recordStartRequests")
            latest_backtest = [request["body"]["definition"] for request in recorder_requests
                               if request["url"] == "/api/segments/backtest"][-1]
            latest_lint = [request["body"]["definition"] for request in recorder_requests
                           if request["url"] == "/api/segments/lint"][-1]
            page.click(".modal .builder-actions .primary-button")
            page.wait_for(".segbuilder .builder-name input")
            saved_rows = page.evaluate("return fetch('/api/segments').then(r=>r.json())")
            saved = next(row for row in saved_rows if row["name"] == name)
            expected = [{"type": "moment_reached", "kind": "door_open", "level": 6,
                         "ordinal": ordinal} for ordinal in (52, 51, 54)]
            assert saved["start_triggers"] == expected
            assert saved["end_triggers"] == [{"type": "moment_reached", "kind": "door_open",
                                               "level": 6, "ordinal": 53}]
            assert saved["waypoints"] == []
            requests = page.evaluate("window.__recordStartRequests")
            posted = next(request["body"] for request in requests
                          if request["method"] == "POST" and request["url"].startswith("/api/segments?"))
            assert latest_backtest == latest_lint == posted
            assert posted["start_triggers"] == expected
            assert page.evaluate("document.querySelector('.segbuilder .builder-name input').value") == name
            assert page.problems() == []


def test_an_older_finish_keeps_the_newest_primary_and_alternate_start(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "shared_defaults_root", lambda: None)
    monkeypatch.setattr(service_module, "shared_defaults_root", lambda: None)
    with serve_ui_live(seed=False) as (base, service):
        _seed_recording(service)
        with get_driver().launch() as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for('button.nav-item[title="Segments"]')
            page.click('button.nav-item[title="Segments"]')
            page.wait_for(".segments-page .library-search")
            page.evaluate("""const buttons = Array.from(document.querySelectorAll('.segments-page button'));
              buttons.find(button => button.textContent.includes('Record a segment')).click();""")
            page.wait_for(".record-row")
            page.evaluate("""window.__recordStartRequests = [];
              const original = window.fetch;
              window.fetch = function(url, options = {}) {
                if (String(url).startsWith('/api/segments'))
                  window.__recordStartRequests.push({url: String(url),
                    method: options.method || 'GET',
                    body: options.body ? JSON.parse(options.body) : null});
                return original.call(this, url, options);
              };""")
            timeline = page.evaluate("return fetch('/api/segments/timeline?limit=200&view=steps').then(r=>r.json())")
            rows = {row["frame"]: row for row in timeline["rows"]
                    if row["type"] == "moment_reached" and row["frame"] >= 70000}
            assert set(rows) == {70000, 70030, 70120, 70140}
            # This matches a newest-first history pick: primary 54, OR 52,
            # then an older finish 51 after leaving the alternate-start mode.
            _pick_label(page, rows[70140]["label"])
            _toolbar_click(page, "Add another start")
            _pick_label(page, rows[70030]["label"])
            _toolbar_click(page, "Done adding starts")
            _pick_label(page, rows[70000]["label"])
            _ready_to_save(page)
            roles = page.evaluate("""return Array.from(document.querySelectorAll('.modal .record-row'))
              .filter(row => row.classList.contains('picked')).map(row => ({
                label: row.querySelector('.record-label').textContent.trim(),
                marker: row.querySelector('.record-mark').textContent.trim(),
                classes: row.querySelector('.record-mark').className}));""")
            by_label = {row["label"]: row for row in roles}
            _capture_review(page, tmp_path, "recorder-pinned-older-finish")
            assert "role-start" in by_label[rows[70140]["label"]]["classes"]
            assert by_label[rows[70030]["label"]]["marker"] == "OR"
            assert "role-finish" in by_label[rows[70000]["label"]]["classes"]
            recorder_requests = page.evaluate("window.__recordStartRequests")
            synthesis = [request for request in recorder_requests
                         if request["url"].startswith("/api/segments/synthesize?")][-1]
            query = parse_qs(urlsplit(synthesis["url"]).query)
            assert query["primary_start_id"] == [str(rows[70140]["id"])]
            assert query["finish_id"] == [str(rows[70000]["id"])]
            draft = page.evaluate(f"return fetch({json.dumps(synthesis['url'])}).then(r=>r.json())")
            assert draft["steps"] == []
            assert draft["picked"] == []
            assert "primary start" in draft["path_notice"]
            assert draft["path_notice"] in page.evaluate("document.querySelector('.record-review').textContent")
            name = page.evaluate("document.querySelector('.record-review .builder-name input').value")
            recorder_requests = page.evaluate("window.__recordStartRequests")
            latest_backtest = [request["body"]["definition"] for request in recorder_requests
                               if request["url"] == "/api/segments/backtest"][-1]
            latest_lint = [request["body"]["definition"] for request in recorder_requests
                           if request["url"] == "/api/segments/lint"][-1]
            page.click(".modal .builder-actions .primary-button")
            page.wait_for(".segbuilder .builder-name input")
            saved_rows = page.evaluate("return fetch('/api/segments').then(r=>r.json())")
            saved = next(row for row in saved_rows if row["name"] == name)
            expected = [{"type": "moment_reached", "kind": "door_open", "level": 6,
                         "ordinal": ordinal} for ordinal in (54, 52)]
            assert saved["start_triggers"] == expected
            assert saved["end_triggers"] == [{"type": "moment_reached", "kind": "door_open",
                                               "level": 6, "ordinal": 51}]
            assert saved["waypoints"] == []
            requests = page.evaluate("window.__recordStartRequests")
            posted = next(request["body"] for request in requests
                          if request["method"] == "POST" and request["url"].startswith("/api/segments?"))
            assert latest_backtest == latest_lint == posted
            for field in ("start_triggers", "end_triggers", "waypoints", "match_mode", "clock_start"):
                assert latest_backtest[field] == posted[field] == saved[field]
            assert page.problems() == []

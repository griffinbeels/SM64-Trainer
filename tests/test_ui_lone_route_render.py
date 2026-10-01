"""The real selector always selects its sole visible option, including a held hand.

Reference definitions are stored here rather than pinning shipped defaults. The
Lobby regression starts with a hooked BitDW Reds target, the actual retained
target shape from the report. Armed extras count as visible choices too.
"""
import datetime as dt
import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

import pytest

from sm64_events.core.events import Event
from sm64_events.server import api
from sm64_events.tracking import service as service_module

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from find_uilab import find_uilab  # noqa: E402
from ui_fixture import FIXTURE_COURSE, FIXTURE_STAR, _run_coro, serve_ui_live  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab.driver import get_driver  # noqa: E402

CELL_SELECTOR = ".stagebanner .starcell"
PICK_DEADLINE_MS = 3000


@pytest.fixture(autouse=True)
def personal_definitions(monkeypatch):
    """Fixture saves never author the release corpus in a maintainer checkout."""
    monkeypatch.setattr(api, "shared_defaults_root", lambda: None)
    monkeypatch.setattr(service_module, "shared_defaults_root", lambda: None)


def _request(base, path, payload=None, method="POST"):
    request = urllib.request.Request(f"{base}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def _view(base):
    return _request(base, "/api/session", method="GET")


def _publish(service, events):
    async def publish():
        for kind, frame, payload in events:
            await service.publish(Event(type=kind, frame=frame,
                timestamp_utc=dt.datetime(2026, 9, 30, 12, tzinfo=dt.timezone.utc),
                payload=payload))
    _run_coro(publish())


def _place(service, level, course_id, mode, frame):
    previous = service.current_stage.get("level")
    _publish(service, [
        ("level_changed", frame, {"from": previous, "to": level}),
        ("area_changed", frame, {"level": level, "from": None, "to": 1}),
        ("stage_changed", frame, {"level": level, "area": 1,
            "course_id": course_id, "mode": mode}),
    ])


def _lobby_definitions(base):
    for row in _request(base, "/api/segments", method="GET"):
        _request(base, f"/api/segments/{row['id']}", {"enabled": False}, "PUT")
    lblj = _request(base, "/api/segments", {
        "name": "LBLJ", "clock_start": "trigger",
        "start_triggers": [{"type": "moment_reached", "kind": "door_arrived",
            "level": 6, "area": 1, "landmark": "6:1:bhvDoorWarp:-1100,0,2202"}],
        "end_triggers": [{"type": "entrance_touched", "to": 17}],
    })
    reds = _request(base, "/api/segments", {
        "name": "BitDW Reds", "clock_start": "trigger", "match_mode": "strict",
        "start_triggers": [{"type": "level_enter", "to": 17},
            {"type": "attempt_anchor", "level": 17}],
        "waypoints": [[{"type": "star_grabbed", "course": 16, "star": 0}]],
        "end_triggers": [{"type": "warp_entered", "level": 17}],
    })
    return lblj["id"], reds["id"]


def _retained_reds_in_lobby(base, service):
    lblj_id, reds_id = _lobby_definitions(base)
    _place(service, 17, 16, "bowser_course", 60)
    _request(base, "/api/target", {"kind": "segment", "segment_id": reds_id, "auto": True})
    _place(service, 6, None, "castle", 90)
    view = _view(base)
    assert view["target"]["segment_id"] == reds_id
    assert view["stage"]["level"] == 6 and view["stage"]["area"] == 1
    assert _target_writes(service)[-1].payload["auto"] is True
    return lblj_id, reds_id


def _arm_extra(base, service):
    extra = _request(base, "/api/segments", {
        "name": "Armed Lobby detour", "clock_start": "trigger",
        # No area in the start clause: it only joins the row through armed extras.
        "start_triggers": [{"type": "moment_reached", "kind": "door_open",
            "level": 6, "ordinal": 51}],
        "end_triggers": [{"type": "moment_reached", "kind": "door_open",
            "level": 6, "ordinal": 99}],
    })
    _publish(service, [("moment_reached", 100, {"kind": "door_open", "ordinal": 51,
        "level": 6, "area": 1, "action": 0, "landmark": None, "igt_frames": 10})])
    row = next(row for row in _view(base)["segment_targets"] if row["segment_id"] == extra["id"])
    assert row["start_areas"] == []
    assert extra["id"] in service.armed_segment_ids
    return extra["id"]


def _lone_star_route(base, star_id):
    route = _request(base, "/api/routes", {
        "name": "lone-option probe", "steps": [{"need": 1, "candidates": [
            {"type": "star", "course": FIXTURE_COURSE, "star": star_id}]}]})
    return route["id"]


def _open_practice(page, base):
    page.goto(f"{base}/ui/index.html")
    # A known control round trip proves this is the working app before measuring.
    page.wait_for('button.nav-item[title="Segments"]')
    page.click('button.nav-item[title="Segments"]')
    page.wait_for(".segments-page .library-search")
    page.click('.app-sidebar button.nav-item[title="Practice"]')
    page.wait_for(CELL_SELECTOR)


def _wait_for_cells(page, count):
    result = page.evaluate(f"""return (async () => {{
      const deadline = Date.now() + 5000;
      const count = () => Array.from(document.querySelectorAll('{CELL_SELECTOR}'))
        .filter(cell => cell.getBoundingClientRect().width > 0).length;
      while (Date.now() < deadline) {{
        if (count() === {count}) {{
          await new Promise(resolve => setTimeout(resolve, 60));
          if (count() === {count}) return true;
        }}
        await new Promise(resolve => setTimeout(resolve, 30));
      }}
      return count();
    }})();""")
    assert result is True, f"Expected {count} visible selector cells, observed {result}"


def _wait_for_target(page, expected):
    return page.evaluate(f"""return (async () => {{
      const expected = {json.dumps(expected)}, start = performance.now();
      let target;
      do {{
        target = (await (await fetch('/api/session')).json()).target;
        if (Object.entries(expected).every(([key, value]) => target[key] === value))
          return {{matched: true, elapsed_ms: performance.now() - start, target}};
        await new Promise(resolve => setTimeout(resolve, 30));
      }} while (performance.now() - start < {PICK_DEADLINE_MS});
      return {{matched: false, elapsed_ms: performance.now() - start, target}};
    }})();""")


def _capture(page, base, tmp_path, label, result):
    captures = Path(os.environ.get("SM64_REPORT_DIR") or tmp_path)
    captures.mkdir(parents=True, exist_ok=True)
    for width in (1500, 850):
        page.set_viewport(width, 1180)
        page.wait_ms(200)
        shot = captures / f"round3-{label}-{width}.png"
        shot.write_bytes(page.screenshot())
        print(f"Sole-option screenshot: {shot}")
    errors = page.problems()
    cells = page.evaluate(f"""return Array.from(document.querySelectorAll('{CELL_SELECTOR}'))
      .map(cell => ({{text: cell.textContent.trim(), active: cell.classList.contains('active-star')}}));""")
    source = page.evaluate("return fetch('/ui/components/stagebanner.js').then(response => response.text());")
    evidence = {"result": result, "target": _view(base)["target"], "cells": cells,
        "browser_errors": errors, "stagebanner_sha256": hashlib.sha256(source.encode()).hexdigest()}
    (captures / f"round3-{label}.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(f"Sole-option observation: {json.dumps(evidence)}")
    assert errors == []


def _target_writes(service):
    return [row for row in service.db.events() if row.type == "target_set"]


def test_lobby_lone_lblj_replaces_retained_hooked_bitdw_reds(tmp_path):
    # Keep definition writes independent of unrelated bundled-rank recalibration.
    with serve_ui_live(seed=False, bundled_library=False) as (base, service):
        lblj_id, reds_id = _retained_reds_in_lobby(base, service)
        before = len(_target_writes(service))
        with get_driver().launch() as page:
            _open_practice(page, base)
            _wait_for_cells(page, 1)
            result = _wait_for_target(page, {"kind": "segment", "segment_id": lblj_id})
            _capture(page, base, tmp_path, "retained-reds-lobby", result)
            assert result["matched"], (
                f"Lobby offers only LBLJ {lblj_id}, but still targets hooked BitDW Reds {reds_id}: {result}")
            assert result["elapsed_ms"] < PICK_DEADLINE_MS
        writes = _target_writes(service)[before:]
        assert len(writes) == 1 and writes[0].payload["segment_id"] == lblj_id


def test_lone_route_star_replaces_the_previous_star(tmp_path):
    other = 1 if FIXTURE_STAR != 1 else 2
    with serve_ui_live(bundled_library=False) as (base, service):
        route_id = _lone_star_route(base, other)
        assert _view(base)["target"]["star_id"] == FIXTURE_STAR
        with get_driver().launch() as page:
            _open_practice(page, base)
            before = len(_target_writes(service))
            page.evaluate(f"""return fetch('/api/route/select', {{method: 'POST',
              headers: {{'Content-Type': 'application/json'}},
              body: JSON.stringify({{route_id: {route_id}}})}}).then(response => response.json());""")
            _wait_for_cells(page, 1)
            result = _wait_for_target(page, {"kind": "star", "course_id": FIXTURE_COURSE, "star_id": other})
            _capture(page, base, tmp_path, "route-star", result)
            assert result["matched"], f"The lone route star must replace retained star {FIXTURE_STAR}: {result}"
            assert result["elapsed_ms"] < PICK_DEADLINE_MS
        writes = _target_writes(service)[before:]
        assert len(writes) == 1 and writes[0].payload["star_id"] == other


def test_one_base_option_and_an_armed_extra_are_two_visible_choices(tmp_path):
    with serve_ui_live(seed=False, bundled_library=False) as (base, service):
        lblj_id, reds_id = _retained_reds_in_lobby(base, service)
        extra_id = _arm_extra(base, service)
        before = len(_target_writes(service))
        with get_driver().launch() as page:
            _open_practice(page, base)
            _wait_for_cells(page, 2)
            page.wait_ms(400)
            result = {"offered": [lblj_id, extra_id], "target": _view(base)["target"]}
            _capture(page, base, tmp_path, "armed-extra-ambiguous", result)
            assert result["target"]["segment_id"] == reds_id
        assert len(_target_writes(service)) == before


def test_an_already_selected_lone_option_does_not_write_again(tmp_path):
    with serve_ui_live(seed=False, bundled_library=False) as (base, service):
        lblj_id, _reds_id = _lobby_definitions(base)
        _place(service, 6, None, "castle", 90)
        _request(base, "/api/target", {"kind": "segment", "segment_id": lblj_id})
        before = len(_target_writes(service))
        with get_driver().launch() as page:
            _open_practice(page, base)
            _wait_for_cells(page, 1)
            _open_practice(page, base)
            _wait_for_cells(page, 1)
            result = _wait_for_target(page, {"kind": "segment", "segment_id": lblj_id})
            _capture(page, base, tmp_path, "already-selected", result)
            assert result["matched"]
        assert len(_target_writes(service)) == before


def test_the_full_star_row_keeps_its_target_without_a_route(tmp_path):
    with serve_ui_live(bundled_library=False) as (base, service):
        before = len(_target_writes(service))
        with get_driver().launch() as page:
            _open_practice(page, base)
            assert page.evaluate(f"document.querySelectorAll('{CELL_SELECTOR}').length") > 1
            assert page.problems() == []
        assert _view(base)["target"]["star_id"] == FIXTURE_STAR
        assert len(_target_writes(service)) == before

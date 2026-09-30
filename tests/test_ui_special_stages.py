"""A real level read must reach the special-stage selector and target.

The five one-star stages need no click. PSS recalls the last chosen or completed
live star across sessions, with that star's strategy, while an explicit pick wins.
The stage payload is produced by the shipping detectors: a hand-written
``mode='stars'`` fixture would hide the missing-context bug this guards.
"""
import json
import os
import sys
import time
import urllib.request
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.level import LevelChangeDetector
from sm64_events.detectors.stage import StageChangeDetector
from sm64_events.ghost.session import GhostSession
from sm64_events.inputs.templates import TemplateStore

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
from find_uilab import find_uilab  # noqa: E402
from ui_fixture import _run_coro, serve_ui_live  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)
from uilab.driver import get_driver  # noqa: E402

PSS, PSS_LEVEL = 19, 27
# Course identities are deliberately independent of the detector's dispatch.
SINGLE_STARS = [(20, 28), (22, 18), (21, 29), (24, 20), (23, 31)]
NOW = datetime(2026, 9, 30, 12, tzinfo=UTC)
BANNER = ".stagebanner"
CELLS = BANNER + " .starcell"
STRATEGIES = {0: "Box star", 1: "Under 21"}


class _Play:
    """Use real snapshots and both level/context detectors, as the poller does."""

    def __init__(self, service):
        self.service = service
        self.requests = []
        request_target = service.request_target

        async def recorded_request(*args, **kwargs):
            record = {"request": {"kind": args[0] if args else kwargs.get("kind"),
                                   **kwargs}, "stage": service.current_stage}
            self.requests.append(record)
            try:
                result = await request_target(*args, **kwargs)
            except Exception as error:
                record["error"] = str(error)
                raise
            record["accepted"] = True
            return result

        service.request_target = recorded_request
        self.level = LevelChangeDetector()
        self.stage = StageChangeDetector()
        self.snapshot = GameSnapshot(
            wall_time_utc=NOW, global_timer=1000, mario_action=0,
            mario_action_timer=0, num_stars=0, last_completed_course=0,
            last_completed_star=0, curr_level=6, curr_area=1)
        self.enter(6)

    def publish(self, kind, payload):
        self.snapshot = replace(self.snapshot,
                                global_timer=self.snapshot.global_timer + 1000)
        return _run_coro(self.service.publish(Event(
            type=kind, frame=self.snapshot.global_timer,
            timestamp_utc=NOW, payload=payload)))

    def enter(self, level):
        current = replace(self.snapshot, curr_level=level,
                          global_timer=self.snapshot.global_timer + 1000)
        events = (self.level.process(self.snapshot, current)
                  + self.stage.process(self.snapshot, current))
        for event in events:
            _run_coro(self.service.publish(event))
        self.snapshot = current

    def complete(self, star, *, choose=True):
        prior = {a.id for a in self.service.db.attempts()}
        if choose:
            _run_coro(self.service.request_target(
                "star", course_id=PSS, star_id=star, strat_tag=STRATEGIES[star]))
        self.publish("practice_reset", {"igt_frames_before": 0})
        self.publish("star_collected", {
            "course_id": PSS, "star_id": star, "igt_frames": 580 + star,
            "igt_timed_at": "xcam"})
        assert any(a.id not in prior and a.course_id == PSS and a.star_id == star
                   and a.outcome == "success" for a in self.service.db.attempts())


def _read(base, scope="session"):
    with urllib.request.urlopen(base + "/api/session?scope=" + scope,
                                timeout=10) as response:
        return json.loads(response.read())


def _wait(page, expression):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if page.evaluate(expression):
            return
        page.wait_ms(50)
    pytest.fail(f"Page did not reach: {expression}; faults: {page.problems()}")


def _target(page, base, course, star, strat=None):
    _wait(page, "document.querySelectorAll('.stagebanner .active-star').length === 1")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        target = _read(base)["target"]
        if (target and target.get("course_id") == course
                and target.get("star_id") == star
                and (strat is None or target.get("strat_tag") == strat)):
            catalog_course = next(c for c in _read(base)["catalog"]["courses"]
                                  if c["id"] == course)
            name = catalog_course["stars"][star]
            _wait(page, "document.querySelector('.stagebanner .shead b')"
                  f"?.textContent === {json.dumps(catalog_course['name'])}")
            _wait(page, "document.querySelector('.stagebanner .active-star .starname')"
                  f"?.textContent === {json.dumps(name)}")
            if strat is not None:
                _wait(page, "document.querySelector('.stagebanner .active-star .strat')"
                      f"?.textContent === {json.dumps(strat)}")
            _idle(page)
            return target
        page.wait_ms(50)
    pytest.fail(f"Expected target {course}:{star}/{strat}, got {target}")


def _cells(page, expected):
    _wait(page, f"document.querySelectorAll({json.dumps(CELLS)}).length === {expected}")
    # The exchange can still paint the departing course's row at first.
    _wait(page, "[...document.querySelectorAll('.stagebanner .starcell')]"
          ".every(e => e.getBoundingClientRect().height > 0)")


def _pick(page, index):
    page.click(CELLS + f":nth-child({index + 1})")


def _control(page, base, play):
    """A known good click proves the browser writes through the real target API."""
    play.enter(9)
    _open(page, base)
    _cells(page, 7)
    _pick(page, 0)
    _target(page, base, 1, 0)


def _idle(page):
    """A forced reload must not cancel the fetches the preceding pick started."""
    expression = ("window.specialStagePending === 0 && "
                  "[...document.images].every(image => image.complete)")
    for _ in range(2):
        _wait(page, expression)
        page.wait_ms(100)


def _open(page, base):
    page.goto(base)
    page.evaluate("""(() => {
      window.specialStagePending = 0;
      window.specialStageTargets = [];
      const original = window.fetch;
      window.fetch = async (...args) => {
        window.specialStagePending++;
        const target = String(args[0]).endsWith('/api/target')
          ? {body: JSON.parse(args[1].body),
             heading: document.querySelector('.stagebanner .shead b')?.textContent}
          : null;
        if (target) window.specialStageTargets.push(target);
        try {
          const response = await original(...args);
          if (target) {
            target.status = response.status;
            if (!response.ok) target.answer = await response.clone().text();
          }
          return response;
        }
        finally { window.specialStagePending--; }
      };
    })()""")
    # The initial MARELO request can precede instrumentation. Its completed
    # resource entry proves it returned before any later reload is allowed.
    _wait(page, "performance.getEntriesByType('resource').some(entry => "
          "new URL(entry.name).pathname === '/api/marelo')")


def _reload(page, base):
    _idle(page)
    _clean(page)
    _open(page, base)


def _clean(page, play=None):
    problems = page.problems()
    assert not problems, "\n".join(problems) + "\n" + json.dumps(
        {"browser": page.evaluate("window.specialStageTargets"),
         "service": play.requests if play else [],
         "target_set": [{"id": e.id, "payload": e.payload}
                        for e in play.service.db.events() if e.type == "target_set"]
         if play else []})


def _capture(page, tmp_path, name):
    folder = Path(os.environ.get("SM64_SPECIAL_STAGE_EVIDENCE", str(tmp_path)))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(page.screenshot())


def _ghost_target(service, course, star, strat):
    """The real ghost session consumes the browser-selected target identity."""
    store = TemplateStore(service.db._conn, service.db._lock)
    ghost = GhostSession(tracker=service, inputs=SimpleNamespace(templates=store),
                         replay=None, broadcaster=service.broadcaster)
    body = _run_coro(ghost.refresh(publish=False))
    assert body["target"] == {"kind": "star", "entity_key": f"{course}-{star}",
                              "strat": strat}
    assert body["window"]["running"] is False


@pytest.mark.parametrize("width", [1500, 850])
def test_all_five_single_star_stages_select_their_star_without_a_click(width, tmp_path):
    with serve_ui_live(tmp_path / "single.db", seed=False,
                       bundled_library=False) as (base, service), \
            get_driver().launch(headless=True, viewport=(width, 1000)) as page:
        play = _Play(service)
        _control(page, base, play)
        for course, level in SINGLE_STARS:
            play.enter(level)
            _target(page, base, course, 0)
            _cells(page, 1)
            catalog = _read(base)["catalog"]["courses"]
            name = next(c["stars"][0] for c in catalog if c["id"] == course)
            assert page.evaluate("document.querySelector('.stagebanner .starname').textContent") == name
            target_sets = [e for e in service.db.events() if e.type == "target_set"
                           and e.payload.get("course_id") == course]
            assert target_sets and target_sets[-1].payload.get("auto") is True
            _capture(page, tmp_path, f"browser-special-{course}-{width}.png")
        _reload(page, base)
        _target(page, base, 23, 0)
        _cells(page, 1)
        _ghost_target(service, 23, 0, None)
        _clean(page, play)


@pytest.mark.parametrize(("remembered", "width"), [(0, 1500), (1, 850)])
def test_pss_recalls_either_completed_star_and_strategy_across_sessions(remembered, width, tmp_path):
    with serve_ui_live(tmp_path / "remembered.db", seed=False,
                       bundled_library=False) as (base, service), \
            get_driver().launch(headless=True, viewport=(width, 1000)) as page:
        play = _Play(service)
        play.enter(PSS_LEVEL)
        play.complete(1 - remembered)
        play.complete(remembered)
        _run_coro(service.new_session())
        _control(page, base, play)
        assert page.evaluate("localStorage.getItem('scope')") in (None, "session")
        play.enter(PSS_LEVEL)
        _target(page, base, PSS, remembered, STRATEGIES[remembered])
        _cells(page, 2)
        view = _read(base)
        assert view["last_star_choice_by_course"][str(PSS)] == remembered
        assert not any(a.course_id == PSS and a.session_id == service.session_id
                       and a.outcome == "success" for a in service.db.attempts())
        active_name = page.evaluate("document.querySelector('.stagebanner .active-star .starname').textContent")
        assert active_name == next(c["stars"][remembered] for c in view["catalog"]["courses"] if c["id"] == PSS)
        assert page.evaluate("document.querySelector('.stagebanner .active-star .strat').textContent") == STRATEGIES[remembered]
        _capture(page, tmp_path, f"browser-pss-recalled-{remembered}-{width}.png")
        _ghost_target(service, PSS, remembered, STRATEGIES[remembered])

        # A manual choice has priority over completion memory, including after
        # a view refresh and a page reload.
        manual = 1 - remembered
        _pick(page, manual)
        _target(page, base, PSS, manual, STRATEGIES[manual])
        play.publish("attempts_invalidated", {})
        _reload(page, base)
        _target(page, base, PSS, manual, STRATEGIES[manual])
        _ghost_target(service, PSS, manual, STRATEGIES[manual])

        # Resetting without a success still preserves the player's explicit
        # choice on re-entry, as the Bowser family selector does.
        play.publish("practice_reset", {"igt_frames_before": 0})
        play.enter(28)
        _target(page, base, 20, 0)
        play.enter(PSS_LEVEL)
        _target(page, base, PSS, manual, STRATEGIES[manual])
        _ghost_target(service, PSS, manual, STRATEGIES[manual])
        # A different star actually collected later replaces the remembered
        # choice, even without first clicking its selector cell.
        play.complete(remembered, choose=False)
        assert _read(base)["last_star_choice_by_course"][str(PSS)] == remembered
        # Let the browser consume the completion before sending the next
        # level edge: publish() returning is not a WebSocket/render receipt.
        _target(page, base, PSS, remembered, STRATEGIES[remembered])
        _idle(page)
        play.enter(18)
        _target(page, base, 22, 0)
        play.enter(PSS_LEVEL)
        _target(page, base, PSS, remembered, STRATEGIES[remembered])
        _clean(page, play)


def test_pss_without_a_completed_star_offers_both_and_waits_for_a_pick(tmp_path):
    with serve_ui_live(tmp_path / "empty.db", seed=False,
                       bundled_library=False) as (base, service), \
            get_driver().launch(headless=True, viewport=(850, 1000)) as page:
        play = _Play(service)
        _control(page, base, play)
        play.enter(PSS_LEVEL)
        _cells(page, 2)
        for _ in range(2):
            page.wait_ms(300)
            assert page.count(BANNER + " .active-star") == 0
            target = _read(base)["target"] or {}
            assert all(target.get(k) is None for k in
                       ("course_id", "star_id", "segment_id"))
        _capture(page, tmp_path, "browser-pss-no-history-850.png")
        _pick(page, 1)
        _target(page, base, PSS, 1)
        _reload(page, base)
        _target(page, base, PSS, 1)
        _cells(page, 2)
        play.publish("practice_reset", {"igt_frames_before": 0})
        play.enter(28)
        _target(page, base, 20, 0)
        play.enter(PSS_LEVEL)
        _target(page, base, PSS, 1)
        _cells(page, 2)
        _clean(page, play)


def test_pss_route_choice_and_explicit_segment_keep_priority_over_memory(tmp_path):
    with serve_ui_live(tmp_path / "priority.db", seed=False,
                       bundled_library=False) as (base, service), \
            get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
        play = _Play(service)
        play.enter(PSS_LEVEL)
        play.complete(0)
        play.complete(1)
        _control(page, base, play)
        route = service.db.insert_route("PSS box only", [{
            "need": 1, "candidates": [{"type": "star", "course": PSS,
                                       "star": 0}]}], NOW.isoformat())
        _run_coro(service.select_route(route))
        play.enter(PSS_LEVEL)
        _target(page, base, PSS, 0, STRATEGIES[0])
        _cells(page, 1)
        assert _read(base)["last_star_choice_by_course"][str(PSS)] == 1

        _run_coro(service.select_route(None))
        segment = _run_coro(service.create_segment({
            "name": "Slide movement", "enabled": True,
            "start_triggers": [{"type": "level_enter", "to": PSS_LEVEL}],
            "end_triggers": [{"type": "level_enter", "to": 9}],
            "guards": []}))
        _run_coro(service.request_target("segment", segment_id=segment))
        play.publish("attempts_invalidated", {})
        _reload(page, base)
        _wait(page, "document.querySelector('.stagebanner .active-star .starname')"
              "?.textContent === 'Slide movement'")
        for _ in range(2):
            page.wait_ms(150)
            target = _read(base)["target"]
            assert target["kind"] == "segment" and target["segment_id"] == segment
        _capture(page, tmp_path, "browser-pss-explicit-segment.png")
        _clean(page, play)

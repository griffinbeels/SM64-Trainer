"""An automatically selected secret-stage row first appears already selected.

Hold the real target request, then the real selected session response, to open
both formerly visible idle states. The shipping detector, selector and API still
do all the work. uilab's frame recorder measures cell visibility and selection;
a final screenshot alone would miss the reported idle-to-selected flash.
"""
import hashlib
import json
import os
import urllib.request
from pathlib import Path

import pytest

from test_ui_special_stages import (
    BANNER, PSS, PSS_LEVEL, REPO, SINGLE_STARS, STRATEGIES, _Play, _capture,
    _cells, _clean, _control, _idle, _pick, _read, _run_coro, _target, _wait,
    get_driver, serve_ui_live,
)
from uilab.trace import record

CELLS = BANNER + " .starrow > .starcell"
SHOWING = 0.05  # The selector-exchange witness uses the same visibility floor.
WINDOW_MS = 650
WATCH = {
    "heading": BANNER + " .shead b",
    "selected_name": BANNER + " .active-star .starname",
}


class _Arrival:
    """Two controllable delays, preserving every real request/response body."""

    def __init__(self, page, base, course, star, width, tmp_path, strat=None):
        self.page, self.base = page, base
        self.course, self.star, self.strat = course, star, strat
        self.width, self.tmp_path = width, tmp_path
        catalog = _read(base)["catalog"]["courses"]
        selected_course = next(c for c in catalog if c["id"] == course)
        self.heading, self.star_count = selected_course["name"], len(selected_course["stars"])
        self.watch = dict(WATCH)
        # PracticeCell's title is its catalog name, or that name plus Daily
        # Star's suffix. A bare prefix would confuse PSS's two similar names.
        # Slot position would misidentify a segment
        # extra when pending stars are omitted from the row entirely.
        for index, name in enumerate(selected_course["stars"]):
            cell = CELLS + ":is([title=" + json.dumps(name) + "], [title^=" + json.dumps(name + " · ") + "])"
            self.watch.update({f"cell{index}": cell,
                               f"active{index}": cell + ".active-star",
                               f"strat{index}": cell + " .strat"})
        with urllib.request.urlopen(base + "/ui/components/stagebanner.js",
                                    timeout=10) as response:
            served = response.read()
        assert served == (REPO / "src/sm64_events/ui/components/stagebanner.js").read_bytes()
        self.source_hash = hashlib.sha256(served).hexdigest()
        page.evaluate("""((course, star) => {
          const real = window.fetch;
          const probe = window.firstPaintArrival = {
            course, star, writes: [], views: [], inFlight: 0,
            writeOpen: false, viewOpen: false
          };
          const writeGate = new Promise(resolve => {
            window.releaseArrivalWrite = () => { probe.writeOpen = true; resolve(); };
          });
          const viewGate = new Promise(resolve => {
            window.releaseArrivalView = () => { probe.viewOpen = true; resolve(); };
          });
          window.fetch = async (...args) => {
            probe.inFlight++;
            const path = new URL(String(args[0]), location.href).pathname;
            try {
              if (path === '/api/target' && args[1]?.method === 'POST') {
                const body = JSON.parse(args[1].body);
                if (body.auto && body.course_id === course && body.star_id === star) {
                  const write = {body, queued: performance.now()};
                  probe.writes.push(write);
                  await writeGate;
                  write.sent = performance.now();
                }
              }
              const response = await real(...args);
              if (path === '/api/session') {
                const body = await response.clone().json();
                const target = body.target || {};
                if (target.course_id === course && target.star_id === star) {
                  const view = {target, stage: body.stage, held: performance.now()};
                  probe.views.push(view);
                  await viewGate;
                  view.delivered = performance.now();
                }
              }
              return response;
            } finally { probe.inFlight--; }
          };
          window.restoreArrivalFetch = () => { window.fetch = real; };
        })(COURSE, STAR)""".replace("COURSE", str(course)).replace("STAR", str(star)))

    def trace(self, phase, trigger=None):
        trace = record(self.page, watch=self.watch, trigger=trigger,
                       ms=WINDOW_MS, properties=("visibility", "display"))
        assert len(trace.of("heading").frames) >= 8, "Too few browser frames to measure arrival"
        folder = Path(os.environ.get("SM64_SPECIAL_STAGE_EVIDENCE", str(self.tmp_path)))
        folder.mkdir(parents=True, exist_ok=True)
        name = f"browser-first-paint-{self.course}-{self.star}-{self.width}-{phase}"
        body = {"base": self.base, "course": self.course, "star": self.star,
                "strat": self.strat, "phase": phase,
                "source": str(REPO / "src/sm64_events/ui/components/stagebanner.js"),
                "source_sha256": self.source_hash,
                "gates": self.page.evaluate("window.firstPaintArrival"),
                "tracks": {name: track.frames for name, track in trace.tracks.items()}}
        (folder / (name + ".json")).write_text(json.dumps(body), encoding="utf-8")
        _capture(self.page, self.tmp_path, name + ".png")
        return trace

    def rows(self, trace):
        frames = trace.of("heading").frames
        return [{name: track.frames[index] for name, track in trace.tracks.items()}
                for index, frame in enumerate(frames) if frame.get("text") == self.heading]

    def hidden(self, trace, phase):
        # Segment extras may stay usable while a course's STAR choice resolves.
        visible = [(row["heading"]["t"], index, row[f"cell{index}"].get("text"))
                   for row in self.rows(trace) for index in range(self.star_count)
                   if _visible(row[f"cell{index}"])]
        assert not visible, (f"{self.heading} cells appeared before {phase} finalized: "
                             f"{visible[:8]} ({len(visible)} visible samples)")

    def selected(self, trace):
        visible = [row for row in self.rows(trace)
                   if any(_visible(row[f"cell{i}"]) for i in range(self.star_count))]
        assert visible, f"{self.heading} never revealed its target cells"
        for row in visible:
            assert _visible(row[f"active{self.star}"]), (
                f"First visible {self.heading} frame was not selected at {row['heading']['t']}ms")
            if self.strat is not None:
                assert row[f"strat{self.star}"].get("text") == self.strat, row

    def close(self):
        self.page.evaluate("releaseArrivalWrite(); releaseArrivalView();")
        _wait(self.page, "window.firstPaintArrival.inFlight === 0")
        _idle(self.page)
        self.page.evaluate("restoreArrivalFetch()")


def _visible(sample):
    return (sample.get("width", 0) > 0 and sample.get("height", 0) > 0
            and sample.get("effectiveOpacity", 0) > SHOWING
            and sample.get("visibility") not in ("hidden", "collapse")
            and sample.get("display") != "none")


def _finalized_arrival(page, base, play, course, level, star, width, tmp_path, strat=None):
    arrival = _Arrival(page, base, course, star, width, tmp_path, strat)
    try:
        def enter(_page):
            play.enter(level)
            _wait(page, "window.firstPaintArrival.writes.length >= 1")

        write = arrival.trace("write-held", enter)

        def release_write(_page):
            page.evaluate("releaseArrivalWrite()")
            _wait(page, "window.firstPaintArrival.views.length >= 1")

        view = arrival.trace("view-held", release_write)
        shown = arrival.trace("revealed", lambda p: p.evaluate("releaseArrivalView()"))
        _target(page, base, course, star, strat)
    finally:
        arrival.close()
        _clean(page, play)
    arrival.hidden(write, "automatic target write")
    arrival.hidden(view, "authoritative selected view")
    arrival.selected(shown)


@pytest.mark.parametrize("width", [1500, 850])
def test_all_five_singletons_wait_for_the_final_target_before_first_paint(width, tmp_path):
    with serve_ui_live(tmp_path / "single.db", seed=False,
                       bundled_library=False) as (base, service), \
            get_driver().launch(headless=True, viewport=(width, 1000)) as page:
        play = _Play(service)
        _control(page, base, play)
        for course, level in SINGLE_STARS:
            _finalized_arrival(page, base, play, course, level, 0, width, tmp_path)
            _cells(page, 1)


@pytest.mark.parametrize(("star", "width"), [(0, 1500), (1, 850)])
def test_pss_remembered_star_and_strategy_are_selected_on_first_paint(star, width, tmp_path):
    with serve_ui_live(tmp_path / "remembered.db", seed=False,
                       bundled_library=False) as (base, service), \
            get_driver().launch(headless=True, viewport=(width, 1000)) as page:
        play = _Play(service)
        play.enter(PSS_LEVEL)
        play.complete(1 - star)
        play.complete(star)
        _run_coro(service.new_session())
        _control(page, base, play)
        _finalized_arrival(page, base, play, PSS, PSS_LEVEL, star, width,
                           tmp_path, STRATEGIES[star])
        _cells(page, 2)


@pytest.mark.parametrize("delay_ms", [0, 35])
def test_a_quick_auto_selection_is_already_active_during_the_surface_reveal(delay_ms, tmp_path):
    with serve_ui_live(tmp_path / "quick.db", seed=False,
                       bundled_library=False) as (base, service), \
            get_driver().launch(headless=True, viewport=(850, 1000)) as page:
        play = _Play(service)
        _control(page, base, play)
        arrival = _Arrival(page, base, 20, 0, 850, tmp_path)
        try:
            def enter(_page):
                play.enter(28)
                _wait(page, "window.firstPaintArrival.writes.length >= 1")
                page.evaluate("setTimeout(() => { releaseArrivalWrite(); "
                              f"releaseArrivalView(); }}, {delay_ms})")

            trace = arrival.trace(f"quick-{delay_ms}", enter)
            arrival.selected(trace)
            _target(page, base, 20, 0)
        finally:
            arrival.close()
            _clean(page, play)


def test_pss_without_memory_shows_both_idle_choices_and_remains_clickable(tmp_path):
    with serve_ui_live(tmp_path / "empty.db", seed=False,
                       bundled_library=False) as (base, service), \
            get_driver().launch(headless=True, viewport=(850, 1000)) as page:
        play = _Play(service)
        _control(page, base, play)
        arrival = _Arrival(page, base, PSS, 1, 850, tmp_path)
        try:
            trace = arrival.trace("no-history", lambda _p: play.enter(PSS_LEVEL))
            rows = arrival.rows(trace)
            visible = [row for row in rows if _visible(row["cell0"]) and _visible(row["cell1"])]
            assert visible, "PSS with no remembered choice must still offer both stars"
            assert all(not row["active0"].get("width") and not row["active1"].get("width")
                       for row in visible)
            assert not page.evaluate("window.firstPaintArrival.writes")
        finally:
            arrival.close()
        _pick(page, 1)
        _target(page, base, PSS, 1)
        _clean(page, play)


def test_an_explicit_segment_keeps_the_pss_selector_visible_and_usable(tmp_path):
    with serve_ui_live(tmp_path / "segment.db", seed=False,
                       bundled_library=False) as (base, service), \
            get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
        play = _Play(service)
        play.enter(PSS_LEVEL)
        play.complete(1)
        _control(page, base, play)
        play.enter(PSS_LEVEL)
        _target(page, base, PSS, 1, STRATEGIES[1])
        segment = _run_coro(service.create_segment({
            "name": "Slide movement", "enabled": True,
            "start_triggers": [{"type": "level_enter", "to": PSS_LEVEL}],
            "end_triggers": [{"type": "level_enter", "to": 9}], "guards": []}))
        _run_coro(service.request_target("segment", segment_id=segment))
        play.publish("attempts_invalidated", {})
        _wait(page, "document.querySelector('.stagebanner .active-star .starname')"
              "?.textContent === 'Slide movement'")
        _idle(page)
        page.click('.app-sidebar .nav-item[title="Library"]')
        _wait(page, "!document.querySelector('.stagebanner')")
        arrival = _Arrival(page, base, PSS, 1, 1500, tmp_path)
        try:
            trace = arrival.trace("explicit-segment", lambda p: p.click('.app-sidebar .nav-item[title="Practice"]'))
            rows = arrival.rows(trace)
            visible = [row for row in rows if _visible(row["cell0"]) and _visible(row["cell1"])]
            assert visible, "An explicit segment must not hide PSS's selectable stars"
            assert all(row["selected_name"].get("text") == "Slide movement" for row in visible)
            assert not page.evaluate("window.firstPaintArrival.writes")
            target = _read(base)["target"]
            assert target["kind"] == "segment" and target["segment_id"] == segment
        finally:
            arrival.close()
        _pick(page, 0)
        _target(page, base, PSS, 0)
        _clean(page, play)

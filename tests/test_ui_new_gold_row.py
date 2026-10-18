"""A new gold row celebrates once: the gold arrival, never the white new-row flash.

His live report, 2026-10-17 (Lineup round 11): "When I get a new gold, it seems
like the white blink and the gold blink background animations conflict. When
it's a gold, it should OVERRIDE the animation and only do the special gold
background celebratory animation."

Two causes, both measured here per frame on the real page: a new row keeps
`row-new` for 2.6 s while the gold arrival lasts 1.4 s, so the white flash
started over when the arrival ended; and an automatic PB lands as a new row
and is crowned a beat later, so the white flash started first. The run goes
through the real tracking path -- a valid run from a course-start reset,
grabbed faster than the PB, saved by the service's own automatic save -- and a
slower run is the control that proves the instrument sees a white flash.
"""
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from playwright.sync_api import sync_playwright  # noqa: E402
from ui_fixture import (FIXTURE_COURSE, FIXTURE_LEVEL, FIXTURE_STAR, _run_coro,  # noqa: E402
                        serve_ui_live)

from sm64_events.core.events import Event  # noqa: E402

CARD = ".log-card.log-card-active"
TOP = f"{CARD} .attempt-table tr:not(.replay-row):not(.pb-replay-notice)"
# Every frame: the top row's number, classes and the white wash's strength.
RECORD = """
((sel, ms) => {
  const frames = [];
  const t0 = performance.now();
  window.__goldTrace = new Promise((resolve) => {
    const step = () => {
      const tr = document.querySelector(sel);
      if (tr) frames.push({ n: tr.querySelector('.attempt-index').textContent.trim(), cls: tr.className,
                            white: Number(getComputedStyle(tr).getPropertyValue('--row-new')) || 0 });
      if (performance.now() - t0 >= ms) resolve(frames); else requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  });
  return true;
})
"""
T0 = datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)


def ev(type_, frame, payload):
    return Event(type=type_, frame=frame, timestamp_utc=T0, payload=payload)


def _run(service, anchor, igt):
    """A valid run (entered, reset at the course start) grabbed at `igt`, and
    whatever automatic save it earns, settled -- the live order: the row
    first, the crown a beat later."""
    reset = {"igt_frames_before": 300, "mario_acted": True, "area": 1, "prev_area": 1,
             "area_load": False, "teleport": False, "loads_journaled": True}

    async def go():
        for event in (ev("level_changed", anchor, {"from": 6, "to": FIXTURE_LEVEL}),
                      ev("area_changed", anchor, {"level": FIXTURE_LEVEL, "from": 1, "to": 1}),
                      ev("practice_reset", anchor + 47, reset),
                      ev("star_collected", anchor + 47 + igt + 60,
                         {"course_id": FIXTURE_COURSE, "star_id": FIXTURE_STAR,
                          "igt_frames": igt, "igt_timed_at": "xcam"})):
            await service.publish(event)
        if service.auto_pb_tasks:
            await asyncio.gather(*list(service.auto_pb_tasks))
    _run_coro(go())


def _trace(page, ms, act):
    page.evaluate(f"{RECORD}({json.dumps(TOP)}, {ms})")
    act()
    return page.evaluate("window.__goldTrace")


def test_a_new_gold_row_shows_only_the_gold_arrival():
    with serve_ui_live() as (base, service):
        with sync_playwright() as play:
            browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
            try:
                page = browser.new_page(viewport={"width": 1500, "height": 1100})
                page.add_init_script("localStorage.setItem('scope', 'lifetime')")
                page.goto(base)
                page.locator(TOP).nth(2).wait_for(timeout=20000)
                page.wait_for_timeout(800)
                before = page.locator(f"{TOP} .attempt-index").first.text_content().strip()
                # Control: a slower run lands as a new row and flashes white.
                slow = _trace(page, 2500, lambda: _run(service, 40000, 900))
                # The gold: a run faster than any PB, saved automatically.
                gold = _trace(page, 4500, lambda: _run(service, 42000, 150))
            finally:
                browser.close()
    slow_row = [f for f in slow if f["n"] != before]
    assert slow_row and max(f["white"] for f in slow_row) > 0.5, (
        "the instrument never saw the white flash on an ordinary new row", slow_row[-3:])
    newest = slow_row[-1]["n"]
    gold_row = [f for f in gold if f["n"] not in (before, newest)]
    assert any("pb-arrive" in f["cls"].split() for f in gold_row), (
        "the new run was never crowned with the gold arrival", gold_row[-3:])
    white = [(i, f["white"], f["cls"]) for i, f in enumerate(gold_row) if f["white"] > 0.02]
    assert not white, f"the gold row flashed white: {white[:5]}"

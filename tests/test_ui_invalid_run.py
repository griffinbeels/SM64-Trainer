"""The practice log draws an invalid run's reason beside its Save button, and a
valid run that beat the PB arrives already saved (task 0144).

His ruling: invalid runs are marked "and leave it as a manual choice for the
user to decide" -- so the row keeps Save as PB, with the reason on the time.
Rendered through the real app at the supported floor and at a wide width.
Set SM64_SHOT_DIR to keep the screenshots.
"""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from sm64_events.core.events import Event  # noqa: E402
from ui_fixture import (FIXTURE_COURSE, FIXTURE_STAR, _run_coro,  # noqa: E402
                        serve_ui_live)
from uilab.driver import get_driver  # noqa: E402

NOW = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)
WIDTHS = (850, 1500)


def _publish(service, type_, frame, payload):
    _run_coro(service.publish(Event(type=type_, frame=frame,
                                    timestamp_utc=NOW, payload=payload)))


def _reset(frame):
    return {"igt_frames_before": 300, "mario_acted": True, "area": 1,
            "prev_area": 1, "area_load": False, "teleport": False,
            "loads_journaled": True}


def _load():
    return {"igt_frames_restored": 181, "igt_frames_before": 353,
            "load_signature": "igt_rewound", "mario_acted": True, "area": 1,
            "prev_area": 1, "area_load": False, "teleport": False,
            "loads_journaled": True}


def _grab(igt):
    return {"course_id": FIXTURE_COURSE, "star_id": FIXTURE_STAR,
            "igt_frames": igt, "igt_timed_at": "xcam"}


ROWS = """(() => Array.from(document.querySelectorAll('.attempt-table tr'))
  .filter((tr) => tr.querySelector('.attempt-result'))
  .map((tr) => ({
    chip: (tr.querySelector('.attempt-result .caveat-chip') || {}).textContent || null,
    title: (tr.querySelector('.attempt-result .caveat-chip') || {getAttribute: () => null}).getAttribute('title'),
    actions: tr.querySelector('.attempt-actions').textContent.replace(/\\s+/g, ' ').trim(),
    savesWithReason: !!tr.querySelector(
      '.attempt-actions button.pb-invalid:not([disabled]) .caveat-chip'),
  })))()"""

CARD_BOX = """(() => {
  const chip = document.querySelector('.attempt-result .caveat-chip[title^="A save state"]');
  const card = chip.closest('.log-card');
  card.scrollIntoView({block: 'start'});
  const r = card.getBoundingClientRect();
  return {x: r.left, y: Math.max(0, r.top), width: r.width,
          height: Math.min(r.height, window.innerHeight - Math.max(0, r.top))};
})()"""


def test_an_invalid_run_shows_its_reason_and_keeps_save():
    with serve_ui_live() as (base, service):
        # A save-state run (slow enough that it would never be a PB anyway)
        # and a valid run that beats the seeded PB: one arrives marked, the
        # other arrives saved.
        _publish(service, "practice_reset", 20000, _reset(20000))
        _publish(service, "state_loaded", 20300, _load())
        _publish(service, "star_collected", 20600, _grab(900))
        _publish(service, "practice_reset", 21000, _reset(21000))
        _publish(service, "star_collected", 21300, _grab(300))
        for width in WIDTHS:
            with get_driver().launch(viewport=(width, 900)) as page:
                page.goto(base + "/ui/index.html")
                page.wait_for(".attempt-result .caveat-chip")
                page.wait_ms(500)
                rows = page.evaluate(ROWS)
                marked = [r for r in rows if r["title"]
                          and r["title"].startswith("A save state")]
                assert len(marked) == 1, rows
                assert "Save state" in marked[0]["chip"]
                assert "Save" in marked[0]["actions"], marked[0]
                assert marked[0]["savesWithReason"], marked[0]
                assert any("Undo" in r["actions"] or "undo" in r["actions"].lower()
                           for r in rows if not r["title"]), rows
                assert not page.problems(), page.problems()
                shots = os.environ.get("SM64_SHOT_DIR")
                if shots:
                    box = page.evaluate(CARD_BOX)
                    page.wait_ms(300)
                    Path(shots, f"invalid_run_{width}.png").write_bytes(
                        page.screenshot(clip=box))

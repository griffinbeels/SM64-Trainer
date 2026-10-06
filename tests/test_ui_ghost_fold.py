"""Runs that can never play as ghosts fold into one quiet line.

His picker, 2026-09-23: several older-format runs each drew a disabled box
saying the same "older pose format" sentence. Rendered through the real card
at the supported widths; set SM64_SHOT_DIR to keep the screenshots.
"""
import os
from pathlib import Path

from sm64_events.ghost.clip import OLDER_POSE_FORMAT_TEXT
from tests.test_ui_ghost_selector import PB, PICKS, picks
from tests.test_ui_ghost_switch import (FIXTURE_SEGMENT, WIDTHS, get_driver,
                                        ghost_session, serve_ui_with_ghost)


def folded_session():
    initial = ghost_session(enabled=True, available=True)
    strat = initial["target"]["strat"]
    playable = {"attempt_id": 1908, "frames": 540, "strat": strat,
                "native_available": True, "native_reason": None, "native_reason_text": None}
    older = [{"attempt_id": attempt, "frames": frames, "strat": strat,
              "native_available": False, "native_reason": "older_pose_format",
              "native_reason_text": OLDER_POSE_FORMAT_TEXT}
             for attempt, frames in [(1901, 560), (1902, 571), (1903, 590)]]
    initial["selection"] = {"ghosts": {"pb": True, "attempts": []}, "missing": [],
                            "pb": dict(PB), "playing": [], "candidates": [playable, *older]}
    return initial


def test_older_runs_fold_into_one_quiet_line_at_every_width():
    shots = os.environ.get("SM64_SHOT_DIR")
    with serve_ui_with_ghost(folded_session(), arm_segment=FIXTURE_SEGMENT) as (base, _state):
        with get_driver().launch() as page:
            for width in WIDTHS:
                page.set_viewport(width, 1200)
                page.goto(base)
                page.wait_for(PICKS)
                shown = picks(page)
                assert [item["label"] for item in shown["items"]] == ['Your PB · 17"80', '18"00']
                assert all(item["fits"] for item in shown["items"])
                line = page.evaluate("""(() => {
                  const el = document.querySelector('.ghost-picks-folded');
                  const row = el.closest('.ghost-row').getBoundingClientRect();
                  const box = el.getBoundingClientRect();
                  return {text: el.textContent.trim(), title: el.getAttribute('title'),
                          fits: box.right <= row.right + 0.5 && box.width > 0};
                })()""")
                assert line["text"] == "3 older runs can't play as ghosts"
                assert line["title"] == OLDER_POSE_FORMAT_TEXT and line["fits"]
                assert not page.problems(), page.problems()
                if shots:
                    page.evaluate("document.querySelector('.ghost-row').scrollIntoView({block:'center'})")
                    Path(shots, f"ghost_fold_{width}.png").write_bytes(page.screenshot())

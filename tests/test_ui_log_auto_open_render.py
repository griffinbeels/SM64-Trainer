"""The practice log's open card when the target moves, in the real app.

Round 13 (Griffin, 2026-09-28): "Even if we autoselect the next star/segment
(e.g., auto selecting Bowser 1 correctly), it shouldn't immediately expand the
practice log, because we don't have an entry yet." The game moving the target
keeps the card he was reading open until the new target's first row lands;
his own pick (a star cell) opens its card at once. The rule itself is pinned
under node (tests/frontend/logautoopen.test.js); this drives the whole chain:
a gestureless target write, a recorded row, and a real click on the banner.
"""
import json
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from ui_fixture import serve_ui_live, _run_coro, FIXTURE_COURSE, FIXTURE_STAR  # noqa: E402
from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab import driver  # noqa: E402
from sm64_events.core.events import Event  # noqa: E402

PLAYED = FIXTURE_STAR          # the fixture's target, with rows
FRESH = 0                      # Chip off Whomp's Block, no rows yet
NAMES = {PLAYED: "Fall onto the Caged Island", FRESH: "Chip off Whomp's Block"}

OPEN_CARDS = """Array.from(document.querySelectorAll('.log-list > .log-card'))
  .filter(c => !c.classList.contains('is-closed'))
  .map(c => (c.querySelector('.log-card-name') || {}).innerText || '')"""
ACTIVE_CELL = "(document.querySelector('.stagebanner .starcell.active-star') || {}).title || ''"


def _auto_target(base, star_id):
    """The game's own kind of target write: no gesture in the page."""
    request = urllib.request.Request(
        f"{base}/api/target", method="POST",
        data=json.dumps({"course_id": FIXTURE_COURSE, "star_id": star_id,
                         "auto": True}).encode(),
        headers={"Content-Type": "application/json"})
    urllib.request.urlopen(request, timeout=10).read()


def _grab(service, frame, star_id, igt_frames):
    now = datetime.now(timezone.utc)

    async def go():
        await service.publish(Event(type="practice_reset", frame=frame, timestamp_utc=now,
                                    payload={"igt_frames_before": 0}))
        await service.publish(Event(type="star_collected", frame=frame + 400, timestamp_utc=now,
                                    payload={"course_id": FIXTURE_COURSE, "star_id": star_id,
                                             "igt_frames": igt_frames, "igt_timed_at": "xcam"}))
    _run_coro(go())


def _open(page):
    """Which star names have their card open (top-level cards)."""
    texts = page.evaluate(OPEN_CARDS)
    return sorted(star for star, name in NAMES.items() if any(name in text for text in texts))


def _settle(page, want_active, want_open=None, timeout=12.0):
    """Wait until the banner shows `want_active` as the target (and, when
    given, until `want_open` is the open set), then a beat more, so a late
    flip cannot slip past a negative assertion."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if (NAMES[want_active] in page.evaluate(ACTIVE_CELL)
                and (want_open is None or _open(page) == want_open)):
            break
        page.wait_ms(150)
    page.wait_ms(1200)
    return _open(page)


def _click_cell(page, star_id):
    page.evaluate(f"""Array.from(document.querySelectorAll('.stagebanner .starcell'))
        .find(c => c.title.startsWith({json.dumps(NAMES[star_id])})).click()""")


@pytest.fixture(scope="module")
def seen():
    out = {}
    frames = iter(range(20000, 80000, 2000))
    with tempfile.TemporaryDirectory() as scratch:
        with serve_ui_live(Path(scratch) / "autoopen.db") as (base, service):
            with driver.get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
                page.goto(base)
                # A rank climb holds the log still while it plays; this is
                # about the open card, not the celebration.
                page.evaluate("localStorage.setItem('sm64.celebrate', '0')")
                page.goto(base)
                out["loaded"] = _settle(page, PLAYED, [PLAYED])
                _auto_target(base, FRESH)
                out["auto_to_empty"] = _settle(page, FRESH)
                _grab(service, next(frames), FRESH, 300)
                out["first_row"] = _settle(page, FRESH, [FRESH])
                _auto_target(base, PLAYED)
                out["auto_to_older_rows"] = _settle(page, PLAYED)
                _click_cell(page, PLAYED)
                out["picked_the_active"] = _settle(page, PLAYED, [PLAYED])
                _grab(service, next(frames), PLAYED, 330)
                _settle(page, PLAYED)
                _click_cell(page, FRESH)
                out["picked_another"] = _settle(page, FRESH, [FRESH])
    return out


def test_the_card_the_page_opens_on_is_open(seen):
    assert seen["loaded"] == [PLAYED], seen


def test_the_game_moving_the_target_to_an_empty_star_keeps_the_last_card_open(seen):
    assert seen["auto_to_empty"] == [PLAYED], seen


def test_the_new_targets_first_row_opens_it(seen):
    assert seen["first_row"] == [FRESH], seen


def test_the_game_moving_the_target_to_a_star_with_older_rows_keeps_the_last_card_open(seen):
    """The case the rule adds: older rows are not this visit's entry."""
    assert seen["auto_to_older_rows"] == [FRESH], seen


def test_his_pick_opens_its_card_at_once(seen):
    assert seen["picked_the_active"] == [PLAYED], seen
    assert seen["picked_another"] == [FRESH], seen

"""The practice card's goal plate, through the real app on the UI fixture.

Task 0106's approved behaviour (2026-09-23): the card shows the Scorecard's
own goal under the PB, a goal picked from the card's Player tab pins that
entry on the Scorecard too, and a repeat goal counts, breaks and celebrates
inside the plate. The server rules are pinned in test_scorecard_entry_goals.py;
this file pins that the card draws them.
"""
import json
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from ui_fixture import (serve_ui_live, _run_coro,  # noqa: E402
                        FIXTURE_COURSE, FIXTURE_STAR)
from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab import driver  # noqa: E402
from sm64_events.core.events import Event  # noqa: E402

KEY = f"star:{FIXTURE_COURSE}:{FIXTURE_STAR}"
PLATE = "document.querySelector('.log-card .goal-plate')"


def _put(base, path, body):
    request = urllib.request.Request(base.rstrip("/") + path, method="PUT",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(request).read())


def _get(base, path):
    return json.loads(urllib.request.urlopen(base.rstrip("/") + path).read())


def _grab(service, frame, igt_frames):
    now = datetime.now(timezone.utc)

    async def go():
        await service.publish(Event(type="practice_reset", frame=frame, timestamp_utc=now,
                                    payload={"igt_frames_before": 0}))
        await service.publish(Event(type="star_collected", frame=frame + 400, timestamp_utc=now,
                                    payload={"course_id": FIXTURE_COURSE, "star_id": FIXTURE_STAR,
                                             "igt_frames": igt_frames, "igt_timed_at": "xcam"}))
    _run_coro(go())


def _wait(page, ms):
    page.evaluate(f"new Promise(r => setTimeout(r, {ms}))")


# What a real pointer would hit at the middle of each part of the plate: the
# browser's own hit test, so the button's stretched hit area counts.
_HITS = f"""(() => {{ const p = {PLATE};
    const owner = (hit) => !hit ? 'nothing' : hit.closest('.pblink') ? 'pb-time'
      : hit.closest('.goal-plate-trigger') ? 'picker' : hit.closest('.goal-plate') ? 'plate' : 'outside';
    const mid = (el) => {{ const r = el.getBoundingClientRect();
      return owner(document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)); }};
    const box = p.getBoundingClientRect();
    return {{ pb_word: mid(p.querySelector('.goal-plate-word.is-pb')),
      pb_time: mid(p.querySelector('.pblink')),
      clock: mid(p.querySelector('.goal-plate-clock')),
      goal_time: mid(p.querySelector('.goal-plate-time')),
      count_row: mid(p.querySelector('.goal-plate-count')),
      top_left: owner(document.elementFromPoint(box.left + 3, box.top + 3)),
      nested: [...p.querySelectorAll('.goal-plate-trigger *')]
        .filter((el) => el.matches('a, button, input, select, textarea, [tabindex]')).length }}; }})()"""

# A click where the browser's hit test lands, at the middle of `selector`.
_CLICK_AT = """(() => {{ const r = {plate}.querySelector({selector}).getBoundingClientRect();
    document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2).click(); }})()"""


def _click_at(page, selector):
    page.evaluate(_CLICK_AT.format(plate=PLATE, selector=json.dumps(selector)))


def _picker_open(page):
    return page.evaluate(f"!!{PLATE}.querySelector('.search-menu')")


def _whole_plate(page):
    """Round 13: what each part of the plate hits, and whether a click on the
    PB half opens the picker and a second one closes it."""
    hits = page.evaluate(_HITS)
    _click_at(page, ".goal-plate-word.is-pb")
    _wait(page, 800)
    opens = _picker_open(page)
    _click_at(page, ".goal-plate-word.is-pb")
    _wait(page, 500)
    return {"hits": hits, "pb_half_opens": opens, "pb_half_closes": not _picker_open(page)}


def _pb_time_opens_picker(page):
    _click_at(page, ".pblink")
    _wait(page, 500)
    return _picker_open(page)


# While the celebration shows, what paints on top at the centre of the PB time
# (which sits above the plate's click layer) and of the clock word: the
# celebration must. It ignores the pointer, so it is hit-tested with pointer
# events switched on for the probe.
PARTY_ON_TOP = """(() => { const party = p.querySelector('.goal-plate-party');
    if (!party) return null;
    party.style.pointerEvents = 'auto';
    const over = sel => { const el = p.querySelector(sel); if (!el) return null;
      const r = el.getBoundingClientRect();
      return party.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)); };
    const out = { pb_time: over('.pblink'), clock: over('.goal-plate-clock') };
    party.style.pointerEvents = '';
    return out; })()"""


def _plate(page):
    return page.evaluate(f"""(() => {{ const p = {PLATE};
        return p && {{ text: p.innerText.replace(/\\n/g, ' | '), width: p.offsetWidth,
          pips: p.querySelectorAll('.goal-pip').length,
          lit: p.querySelectorAll('.goal-pip.is-on').length,
          party: !!p.querySelector('.goal-plate-party'), party_on_top: {PARTY_ON_TOP} }}; }})()""")


def _watch_celebration(page):
    """Whether the celebration showed within 3 s, and what it painted over
    the first time it did."""
    party, on_top = False, None
    for _ in range(20):
        _wait(page, 150)
        plate = _plate(page)
        party = party or plate["party"]
        if plate["party"] and on_top is None:
            on_top = plate["party_on_top"]
    return party, on_top


@pytest.fixture(scope="module")
def seen():
    """Drive one card through every approved state, recording what it drew."""
    out = {}
    with tempfile.TemporaryDirectory() as scratch:
        with serve_ui_live(Path(scratch) / "plate.db") as (base, service):
            with driver.get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
                page.goto(base)
                _wait(page, 2500)
                out["automatic"] = _plate(page)
                out.update(_whole_plate(page))
                out["automatic_api"] = _get(
                    base, f"/api/scorecard/entry_goals?scope=overall&keys={KEY}")["goals"][KEY]
                # The Player tab: search, pick, and the Scorecard follows.
                runners = _get(base, f"/api/scorecard/entry_goal_options?key={KEY}&scope=overall")["runners"]
                runner = runners[len(runners) // 2]
                page.evaluate(f"{PLATE}.querySelector('.goal-plate-trigger').click()")
                _wait(page, 1200)
                page.evaluate("[...document.querySelectorAll('.goal-picker-kind button')][1].click()")
                _wait(page, 500)
                page.evaluate(f"""(() => {{ const i = {PLATE}.querySelector('.search-menu-filter');
                    i.value = {json.dumps(runner['runner'])};
                    i.dispatchEvent(new Event('input', {{bubbles: true}})); }})()""")
                _wait(page, 400)
                out["search_hits"] = page.evaluate(f"""[...{PLATE}.querySelectorAll('.search-menu-option')]
                    .map(e => e.innerText)""")
                page.evaluate(f"{PLATE}.querySelector('.search-menu-option').click()")
                _wait(page, 1500)
                out["runner"] = runner
                out["runner_plate"] = _plate(page)
                card = _get(base, "/api/scorecard?scope=overall")
                out["runner_tile"] = next(tile for row in card["rows"] for tile in row["tiles"]
                                          if tile["key"] == KEY)
                # A 3-in-a-row goal on the fastest rank -- the longest words the
                # plate carries ("3x streak · Mario 1"), which is what could
                # widen it. The fixture's 11"2x runs beat it; 12"00 does not.
                top = _get(base, f"/api/scorecard/entry_goal_options?key={KEY}&scope=overall")["divisions"][0]
                assert 1134 < top["cs"] < 1200, top
                _put(base, "/api/scorecard/entry_goal",
                     {"key": KEY, "goal": {"kind": "division", "tier": top["tier"],
                                           "division": top["division"], "count": 3, "streak": True}})
                _wait(page, 1500)
                frames = iter(range(20000, 60000, 2000))
                for igt in (340, 341):                       # two under 11"50
                    _grab(service, next(frames), igt)
                    _wait(page, 1500)
                out["two"] = _plate(page)
                _grab(service, next(frames), 360)             # 12"00 breaks it
                _wait(page, 1500)
                out["broken"] = _plate(page)
                for igt in (339, 340):
                    _grab(service, next(frames), igt)
                    _wait(page, 1500)
                _grab(service, next(frames), 338)             # the third in a row
                out["celebrated"], out["party_on_top"] = _watch_celebration(page)
                _wait(page, 3200)
                out["done"] = _plate(page)
                # The longest source words the sheet can put there.
                longest = max(runners, key=lambda r: len(r["runner"]))
                _put(base, "/api/scorecard/entry_goal",
                     {"key": KEY, "goal": {"kind": "runner", "runner": longest["runner"],
                                           "count": 3, "streak": True}})
                _wait(page, 1500)
                out["long_source"] = _plate(page)
                # Last: its jump may move the log's scope.
                out["pb_time_opens"] = _pb_time_opens_picker(page)
    return out


def test_the_plate_draws_the_scorecards_own_goal(seen):
    api = seen["automatic_api"]
    assert api["goal_cs"] is not None, api
    assert "GOAL" in seen["automatic"]["text"] and "Auto" in seen["automatic"]["text"], seen["automatic"]


def test_the_whole_plate_opens_the_picker_except_the_pb_time(seen):
    """His round-13 ask: "click on the whole button (not just the bottom
    half)". The PB time keeps its own jump to the PB's row, and no control
    sits inside another."""
    assert seen["hits"] == {"pb_word": "picker", "pb_time": "pb-time", "clock": "picker",
                            "goal_time": "picker", "count_row": "picker",
                            "top_left": "picker", "nested": 0}, seen["hits"]
    assert seen["pb_half_opens"] and seen["pb_half_closes"], seen
    assert not seen["pb_time_opens"], "the PB time opened the goal picker"


def test_a_player_found_by_search_becomes_this_stars_pinned_goal(seen):
    name = seen["runner"]["runner"]
    assert any(hit.startswith(name) for hit in seen["search_hits"]), seen["search_hits"]
    assert name in seen["runner_plate"]["text"], seen["runner_plate"]
    tile = seen["runner_tile"]
    assert tile["goal_pinned"] is True and tile["goal_cs"] == seen["runner"]["cs"], tile


def test_the_celebration_paints_over_everything_in_the_plate(seen):
    """Round 13 follow-up: "when the goal animation plays, it should be ABOVE
    everything else within that button". The PB time sits above the plate's
    click layer so it keeps its own jump; the celebration must still cover it."""
    assert seen["party_on_top"] == {"pb_time": True, "clock": True}, seen["party_on_top"]


def test_a_streak_goal_counts_breaks_and_celebrates(seen):
    assert (seen["two"]["pips"], seen["two"]["lit"]) == (3, 2), seen["two"]
    assert seen["broken"]["lit"] == 0, seen["broken"]
    assert seen["celebrated"], "the third run in a row never played GOAL! in the plate"
    assert seen["done"]["lit"] == 3 and "✓" in seen["done"]["text"], seen["done"]


def test_the_plate_keeps_one_width_through_every_state(seen):
    """A stream crop of the top card must not shift when a goal is reached."""
    states = ("automatic", "runner_plate", "two", "broken", "done", "long_source")
    widths = {state: seen[state]["width"] for state in states}
    assert len(set(widths.values())) == 1, widths

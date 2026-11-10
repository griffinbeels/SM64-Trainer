"""PBs only: the practice log as a PB history, on the real page.

His words (2026-11-09): "It would also be nice, therefore, to be able to filter
the Practice Log by which Ghosts were PBs -- as in, I'd be able to see my PB
progression directly in the practice log, and be able to look at all of the
ghosts in order of when I achieved the PB, reverse chronological, most recent
PB at the top). I want to be able to see all of my past PB ghosts. That is,
every time I broke my own record."

The fixture's star gets a PB history through the real service: six saved PBs,
each faster than the last and days apart, between ordinary slower runs, resets
and deaths. Each attempt row's `set_pb` (tracking/views.py) names the runs that
set a PB; the toggle in the log's heading (practicelog.js::OnlyPbsToggle)
keeps only those, newest first whatever the sort says, and remembers the
choice (practice.js, localStorage "sm64.onlyPbs"). The expected rows come from
the same /api/session payload the page draws, never a restatement of the rule.
"""
import json
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from source_scan import strip_comments
from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.core.events import Event

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from ui_fixture import FIXTURE_COURSE, FIXTURE_STAR, _run_coro, serve_ui_live  # noqa: E402

UI = ROOT / "src" / "sm64_events" / "ui"
CARD = ".log-card.log-card-active"
ROWS = f"{CARD} .attempt-table tr:not(.replay-row):not(.pb-replay-notice)"
TOGGLE = ".attempts-tools button.log-only-pbs"
UNASSIGNED = '.log-card[data-feed-key="unassigned"]'
FAILURES = ("reset", "hard_reset", "death")
# Every page this file opens: a headless test must never lock his real pointer.
DAY0 = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)
# Oldest first: a time in frames is a finished run, "pb" a run saved as the new
# PB, "reset"/"death" a failure. The fixture's own first run (343) is the first
# PB; these five beat it one after another, days apart.
HISTORY = [(350, None), (336, "pb"), ("reset", None), (345, None), (329, "pb"), ("death", None),
           (340, None), (321, "pb"), (330, None), (315, "pb"), ("reset", None), (308, "pb"), (318, None)]
PB_STEPS = 1 + sum(1 for _, saved in HISTORY if saved)


def _publish(service, type_, frame, payload, day):
    _run_coro(service.publish(Event(type=type_, frame=frame, payload=payload,
                                    timestamp_utc=DAY0 + timedelta(days=day, seconds=frame / 30))))


def seed_pb_history(service):
    """The HISTORY above on the fixture's star, through the real service; the PB
    saves go through `save_pb`, the call the row's Save button makes."""
    anchor = 30000
    for step, (what, saved) in enumerate(HISTORY):
        day = step // 2
        _publish(service, "practice_reset", anchor, {"igt_frames_before": 0}, day)
        if what == "reset":
            _publish(service, "practice_reset", anchor + 300, {"igt_frames_before": 300}, day)
        elif what == "death":
            _publish(service, "death", anchor + 200, {"cause": "fall", "igt_frames": 200}, day)
        else:
            _publish(service, "star_collected", anchor + what, {
                "course_id": FIXTURE_COURSE, "star_id": FIXTURE_STAR,
                "igt_frames": what, "igt_timed_at": "xcam"}, day)
            if saved:
                newest = max(service.db.attempts(), key=lambda row: row.id).id
                _run_coro(service.save_pb(newest, "igt"))
        anchor += 1000


def fixture_star(base, scope):
    with urllib.request.urlopen(f"{base}/api/session?clock=igt&scope={scope}", timeout=15) as answer:
        view = json.load(answer)
    return next(sec for sec in view["stars"]
                if sec["course_id"] == FIXTURE_COURSE and sec["star_id"] == FIXTURE_STAR)


def expected_rows(sec, *, only_pbs=False, hide_resets=True, newest_first=True, page=10):
    """The "#N" each shown row should carry, in order: what the page's filter
    keeps of the payload, ordered by journal_id, first page only."""
    numbered = {row["id"]: index + 1 for index, row in enumerate(sec["attempts"])}
    kept = [row for row in sec["attempts"]
            if not row.get("cleared") and row["outcome"] != "abandoned"
            and not (row["outcome"] == "loaded" or (row.get("state_loads") or 0) > 0
                     or row.get("anchor_type") == "state_loaded" or row.get("invalid_reason") == "save_state")
            and not (hide_resets and row["outcome"] in FAILURES)
            and not (only_pbs and not row["set_pb"])]
    kept.sort(key=lambda row: row["journal_id"], reverse=newest_first)
    return [numbered[row["id"]] for row in kept][:page]


SHOWN = f"""() => [...document.querySelectorAll({json.dumps(ROWS)})].map(tr =>
  Number(tr.querySelector('.attempt-index').textContent.match(/#(\\d+)/)[1]))"""


def wait_for_rows(page, want, timeout=20000):
    page.wait_for_function(f"want => JSON.stringify(({SHOWN})()) === JSON.stringify(want)", arg=want,
                           timeout=timeout)
    return page.evaluate(f"({SHOWN})()")


def _extract(source, name):
    match = re.search(rf"^export function {name}\(.*?^\}}", source, re.S | re.M)
    assert match, f"{name} not found -- renamed?"
    return match.group(0)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_the_filter_keeps_exactly_the_rows_that_set_a_pb_and_lists_them_newest_first():
    """The real functions, extracted: PBs only is its own clause beside the
    other preferences, and it overrides the sort to newest first."""
    filters = _extract(strip_comments((UI / "components/attemptlog.js").read_text(encoding="utf-8")),
                       "attemptMatchesLogFilters")
    sort = _extract(strip_comments((UI / "components/practicelog.js").read_text(encoding="utf-8")), "logSort")
    rows = [{"id": "pb-old", "outcome": "success", "set_pb": True},
            {"id": "slow", "outcome": "success", "set_pb": False},
            {"id": "reset", "outcome": "reset", "set_pb": False},
            {"id": "pb-new", "outcome": "success", "set_pb": True},
            {"id": "pb-loaded", "outcome": "success", "set_pb": True, "state_loads": 1,
             "save_state": True}]     # the server's verdict (views.py), which the filter reads
    script = (f"{filters}\n{sort}\nconst rows = {json.dumps(rows)};\n"
              "const ids = ui => rows.filter(a => attemptMatchesLogFilters(a, ui)).map(a => a.id);\n"
              "console.log(JSON.stringify([ids({onlyPbs: true}), ids({onlyPbs: false}), ids({}),"
              " ids({onlyPbs: true, hideSavestates: false}),"
              " logSort({onlyPbs: true, sort: 'oldest'}), logSort({onlyPbs: false, sort: 'oldest'})]));")
    result = subprocess.run(["node", "--input-type=module", "-"], input=script, capture_output=True,
                            text=True, encoding="utf-8", timeout=30, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        ["pb-old", "pb-new"], ["pb-old", "slow", "reset", "pb-new"], ["pb-old", "slow", "reset", "pb-new"],
        ["pb-old", "pb-new", "pb-loaded"], "newest", "oldest"]


def test_pbs_only_shows_the_pb_history_newest_first_and_survives_a_reload():
    with serve_ui_live() as (base, service):
        seed_pb_history(service)
        session = fixture_star(base, "session")
        assert sum(row["set_pb"] for row in session["attempts"]) == PB_STEPS, "the server's set_pb rows"
        lifetime = fixture_star(base, "lifetime")
        normal = expected_rows(session, newest_first=False)            # his stored sort: oldest first
        history = expected_rows(lifetime, only_pbs=True)                 # newest first anyway
        assert len(history) == PB_STEPS
        with sync_playwright() as play:
            browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 1100})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.add_init_script("localStorage.setItem('sm64.sort', 'oldest');")
                page.goto(base)
                toggle = page.locator(TOGGLE)
                # Control: the ordinary list, resets and deaths hidden by default, his sort.
                assert wait_for_rows(page, normal) == normal
                assert toggle.get_attribute("aria-pressed") == "false"
                assert page.locator(UNASSIGNED).count() == 1
                toggle.click()
                assert wait_for_rows(page, history) == history
                assert toggle.get_attribute("aria-pressed") == "true"
                assert page.locator(UNASSIGNED).count() == 0                # no target: never a PB
                assert page.evaluate("[localStorage.getItem('sm64.onlyPbs'), localStorage.getItem('scope')]") \
                    == ["1", "lifetime"]                                     # every session's PBs
                assert page.locator(f"{ROWS}.is-pb").count() == 1            # the current PB still wears GOLD!!
                page.reload()
                assert wait_for_rows(page, history) == history
                assert page.locator(TOGGLE).get_attribute("aria-pressed") == "true"
                page.locator(TOGGLE).click()
                assert wait_for_rows(page, normal) == normal
                assert page.evaluate("[localStorage.getItem('sm64.onlyPbs'), localStorage.getItem('scope')]") \
                    == ["0", "session"]                                      # back to the session it left
                assert page.locator(UNASSIGNED).count() == 1
                # His ruling (2026-11-09): "if we undo PBs only, it should go back to the user's active
                # session (if they weren't on lifetime)". Already on Lifetime: off leaves him there.
                page.evaluate("localStorage.setItem('scope', 'lifetime')")
                page.reload()
                lifetime_rows = expected_rows(lifetime, newest_first=False)
                assert wait_for_rows(page, lifetime_rows) == lifetime_rows
                page.locator(TOGGLE).click()
                assert wait_for_rows(page, history) == history
                page.locator(TOGGLE).click()
                assert wait_for_rows(page, lifetime_rows) == lifetime_rows
                assert page.evaluate("[localStorage.getItem('sm64.onlyPbs'), localStorage.getItem('scope')]") \
                    == ["0", "lifetime"]
                assert errors == []
            finally:
                browser.close()


def test_a_card_with_no_pb_says_so_in_a_whole_sentence():
    """A card whose runs set no PB is the "all filtered out" case, never
    "nothing recorded": it says why and how to bring the runs back."""
    with serve_ui_live() as (base, service):
        previous = service.db.current_pb(FIXTURE_COURSE, FIXTURE_STAR, "igt", strat_tag="TJ Owlless")
        _run_coro(service.undo_pb(previous["attempt_id"], "igt"))
        assert not any(row["set_pb"] for row in fixture_star(base, "lifetime")["attempts"])
        with sync_playwright() as play:
            browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 1000})
                page.add_init_script("localStorage.setItem('sm64.onlyPbs', '1');"
                                     "localStorage.setItem('scope', 'lifetime');")
                page.goto(base)
                note = page.locator(f"{CARD} .log-no-pbs")
                note.wait_for(timeout=20000)
                assert note.inner_text().strip() == ("No run here has set a new personal best yet. "
                                                     "Turn off PBs only to see every run.")
                assert page.locator(f"{CARD} .empty-state").count() == 0
                assert page.locator(ROWS).count() == 0
            finally:
                browser.close()

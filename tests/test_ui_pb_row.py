# tests/test_ui_pb_row.py
"""The practice-log row that IS the personal best, and when it celebrates.

Task 0147 (2026-09-23), his words: "it should say "GOLD!!" -- it should have a
gold font color, and should be holographic, like a cool pokemon card. not
super distracting, but it should be OBVIOUS", and "If we get a new PB, then
the old one's effects should naturally disappear / animate away back to
normal, of course. Same with undoing."

Four contracts, each of which a screenshot of an end state cannot tell apart
from its failure:

1. WHICH row: exactly one row per card is the gold one, and it is the run the
   card's own PB tag names.
2. WHEN it celebrates: the one-shot arrival (`.pb-arrive`) fires for a live
   `pb_saved` and for nothing else -- not a page load, not a strategy round
   trip, not the quiet hand-back after an Undo. A trigger built on "this row
   just became the PB" passes every end-state check and replays obsolete wins.
3. HOW the gold leaves: the old row drains, and an Undo's restored row lights
   up, through intermediate values of the one number that drives the gold
   (`--pb-lit`) -- a snap looks identical to a fade at both ends.
4. The TIME SAVE, his words the same day: "Time Save: -X.XX (Y frames)... It
   should persist until the next attempt is logged (a reset, a successful
   attempt, etc). Once there's another attempt, we should animate that text
   out and get rid of it, leaving just "GOLD!!"". It shows the margin the save
   beat the old PB by, stands through the save's own run, and fades out --
   through a middle -- on the next logged attempt while GOLD!! stays.

Every duration and strength is a logtuning.js "Personal best" row, so nothing
here pins a number; it pins that the path has a middle and who gets to play.
"""
import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
from ui_fixture import FIXTURE_FOREIGN_STRAT, FIXTURE_STRAT, serve_ui_live  # noqa: E402
from sm64_events.core.events import Event  # noqa: E402
from find_uilab import find_uilab  # noqa: E402

PBCROWN = (REPO / "src" / "sm64_events" / "ui" / "pbcrown.js").as_uri()


# ---- The trigger's pure half (node) -------------------------------------------

def _node(script):
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True,
                         text=True, encoding="utf-8", check=True)
    return json.loads(out.stdout)


NODE_HELPERS = f"""
  import {{ readPbNews, timeSaveText }} from {PBCROWN!r};
  const saved = (id, frames, previous) => ({{ type: "pb_saved",
    payload: {{ attempt_id: id, frames, previous_frames: previous }} }});
  const logged = (id) => ({{ type: "attempt_completed", payload: {{ attempt_id: id }} }});
  const undone = (id) => ({{ type: "pb_undone", payload: {{ attempt_id: id }} }});
  const entries = (news) => ({{ crowned: news.crowned, saves: [...news.timeSaves] }});
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_only_a_pb_saved_crowns_and_each_event_is_read_once():
    result = _node(NODE_HELPERS + """
      const history = [saved(1, 400, 420), logged(9)];
      const seen = new WeakSet(history);        // what the log mounted onto
      const feed = [saved(3, 380, 390), undone(3), saved(2, 390, 400), ...history];  // newest first
      const first = readPbNews(feed, seen, new Map());
      const again = readPbNews(feed, seen, first.timeSaves);
      console.log(JSON.stringify({ first: entries(first), again: entries(again) }));
    """)
    assert result["first"]["crowned"] == [2, 3], (
        "the crown must come from pb_saved alone, oldest first, and never from "
        "history the log mounted onto or from a pb_undone", result)
    assert result["again"]["crowned"] == [], "an event already read crowned a second time"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_a_time_save_stands_until_the_next_logged_attempt():
    result = _node(NODE_HELPERS + """
      const seen = new WeakSet();
      // An automatic PB: its own run is logged BEFORE the save names the margin.
      const saving = readPbNews([saved(5, 340, 349), logged(5)], seen, new Map());
      const nextRun = readPbNews([logged(6)], seen, saving.timeSaves);
      const first = readPbNews([saved(7, 300, null)], seen, new Map());
      const undoing = readPbNews([undone(8), saved(8, 290, 300)], new WeakSet(), new Map());
      console.log(JSON.stringify({ saving: entries(saving), nextRun: entries(nextRun),
        first: entries(first), undoing: entries(undoing),
        text: [timeSaveText(9), timeSaveText(1)] }));
    """)
    assert result["saving"]["saves"] == [[5, 9]], (
        "the save must stand through the run it saved", result["saving"])
    assert result["nextRun"]["saves"] == [], "the next logged attempt did not retire it"
    assert result["first"]["saves"] == [], "a first PB replaced nothing and saved nothing"
    assert result["undoing"]["saves"] == [], "an Undo left its own Time Save standing"
    assert result["text"] == ["Time Save: -0.30 (9 frames)", "Time Save: -0.03 (1 frame)"]


# ---- The rendered row (real app, offline fixture) -----------------------------

def _call(base, method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(f"{base}{path}", data=data, method=method,
                                     headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(request, timeout=15).read())


# Every frame, per watched run (found by the time its row prints): the row's
# classes and its --pb-lit. Sampled in the page on the paint clock -- see
# test_ui_other_strat_dim.py for why a driver-side poll reports false snaps.
RECORD = """
((times, ms) => {
  const find = (time) => Array.from(document.querySelectorAll('.log-card .attempt-table tr'))
    .find((tr) => {
      const b = tr.querySelector('.attempt-result b');
      return b && b.textContent.trim() === time;
    }) || null;
  const frames = [];
  const t0 = performance.now();
  window.__pbRowTrace = new Promise((resolve) => {
    const step = () => {
      const frame = {};
      for (const [name, time] of Object.entries(times)) {
        const tr = find(time);
        const save = tr && tr.querySelector('.pb-save');
        const gold = tr && tr.querySelector('.pb-gold');
        frame[name] = tr ? { cls: tr.className,
          lit: Number(getComputedStyle(tr).getPropertyValue('--pb-lit')),
          save: save ? { shown: save.classList.contains('is-shown'), text: save.textContent.trim(),
                         opacity: Number(getComputedStyle(save).opacity) } : null,
          gold: gold ? Number(getComputedStyle(gold).opacity) : null } : null;
      }
      frames.push(frame);
      if (performance.now() - t0 >= ms) resolve(frames); else requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  });
  return true;
})
"""

REST = """
(() => {
  const rows = Array.from(document.querySelectorAll('.log-card .attempt-table tr.is-pb'));
  return JSON.stringify(rows.map((tr) => {
    const gold = tr.querySelector('.pb-gold');
    const num = tr.querySelector('.pb-swap-num');
    const card = tr.closest('.log-card');
    return {
      time: tr.querySelector('.attempt-result b').textContent.trim(),
      tag: (card.querySelector('.pbtag') || {}).textContent || '',
      word: gold.textContent.trim(),
      gold: Number(getComputedStyle(gold).opacity),
      num: Number(getComputedStyle(num).opacity),
    };
  }));
})()
"""

WINDOW_MS = 3500


def _record(page, times, trigger=None, ms=WINDOW_MS):
    page.evaluate(f"{RECORD}({json.dumps(times)}, {ms})")
    if trigger:
        trigger()
    return page.evaluate("window.__pbRowTrace")


@pytest.fixture(scope="module")
def run():
    missing = find_uilab()
    if missing:
        pytest.skip(missing)
    from uilab.driver import get_driver

    out = {}
    with serve_ui_live() as (base, service):
        view = _call(base, "GET", "/api/session")
        sec = next(s for s in view["stars"]
                   if s.get("last_strat") == FIXTURE_STRAT
                   and (s.get("pb_by_strat") or {}).get("igt"))
        best_id = sec["pb_by_strat"]["igt"]["attempt_id"]
        best = next(a for a in sec["attempts"] if a["id"] == best_id)
        runner_up = min((a for a in sec["attempts"]
                         if a["outcome"] == "success" and a["strat_tag"] == FIXTURE_STRAT
                         and not a["cleared"] and not a.get("caveat")
                         and a["igt_frames"] > best["igt_frames"]),
                        key=lambda a: a["igt_frames"])
        times = {"best": best["igt"], "runner_up": runner_up["igt"]}

        def save(attempt):
            _call(base, "POST", "/api/pb", {"attempt_id": attempt["id"], "timer_mode": "igt"})

        def undo(attempt):
            _call(base, "POST", "/api/pb/undo", {"attempt_id": attempt["id"], "timer_mode": "igt"})

        def log_attempt():
            """One `attempt_completed` broadcast -- what every logged attempt,
            reset or success, closes through -- sent the way
            test_ui_scorecard.py's pb_saved helper sends one: through the live
            broadcaster, in a worker thread, because the browser driver owns
            this thread's loop."""
            import asyncio
            import datetime as dt
            import threading

            async def go():
                await service.broadcaster.publish(Event(
                    type="attempt_completed", frame=0,
                    timestamp_utc=dt.datetime.now(dt.timezone.utc), payload={}))
            worker = threading.Thread(target=lambda: asyncio.run(go()))
            worker.start()
            worker.join(timeout=10)
            assert not worker.is_alive(), "the attempt_completed broadcast did not return"

        def strat(name):
            _call(base, "POST", "/api/strat", {"course_id": sec["course_id"],
                                               "star_id": sec["star_id"], "strat_tag": name})

        with get_driver().launch(headless=True, viewport=(1500, 1200)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-card .attempt-table tr.is-pb", timeout_ms=30000)
            out["load"] = _record(page, times, ms=1500)
            out["rest"] = json.loads(page.evaluate(REST))
            # Hand the crown to the slower run, so the faster one can TAKE it.
            undo(best)
            page.wait_ms(1500)
            save(runner_up)
            page.wait_ms(2500)
            out["takeover"] = _record(page, times, lambda: save(best))
            out["next_attempt"] = _record(page, times, log_attempt, ms=2000)
            out["undo"] = _record(page, times, lambda: undo(best))
            strat(FIXTURE_FOREIGN_STRAT)
            page.wait_ms(1500)
            out["strat_back"] = _record(page, times, lambda: strat(FIXTURE_STRAT))
            out["problems"] = page.problems()
        out["times"] = times
        out["saved_frames"] = runner_up["igt_frames"] - best["igt_frames"]
    return out


def _track(frames, name):
    return [f[name] for f in frames if f.get(name)]


def _arrived(track):
    return any("pb-arrive" in s["cls"].split() for s in track)


def _has_a_middle(track):
    return any(0.05 < s["lit"] < 0.95 for s in track)


def test_one_gold_row_per_card_and_it_is_the_run_the_pb_tag_names(run):
    rest = run["rest"]
    assert len(rest) == 1, ("expected exactly one gold row on the page", rest)
    row = rest[0]
    assert row["time"] == run["times"]["best"], row
    assert row["time"] in row["tag"], ("the gold row and the card's PB tag name two runs", row)
    assert row["word"] == "GOLD!!", row
    assert row["gold"] == 1 and row["num"] == 0, ("the word, not the 0.00s, shows", row)


def test_a_page_load_does_not_replay_the_arrival(run):
    best = _track(run["load"], "best")
    assert best and all("is-pb" in s["cls"].split() for s in best), best[:3]
    assert not _arrived(best), "the PB row celebrated on page load -- an obsolete win replayed"


def test_a_live_save_plays_the_arrival_once_and_the_old_row_drains(run):
    best = _track(run["takeover"], "best")
    old = _track(run["takeover"], "runner_up")
    assert best and old, run["takeover"][:2]
    assert _arrived(best), "a live pb_saved did not play the arrival on its row"
    assert "is-pb" in best[-1]["cls"].split() and abs(best[-1]["lit"] - 1) < 0.01, best[-1]
    assert not _arrived(old), old
    assert "is-pb" not in old[-1]["cls"].split() and old[-1]["lit"] < 0.01, old[-1]
    assert _has_a_middle(old), (
        "the old PB row SNAPPED back to normal -- its gold must drain", [s["lit"] for s in old])


def test_undo_drains_and_hands_the_crown_back_quietly(run):
    undone = _track(run["undo"], "best")
    restored = _track(run["undo"], "runner_up")
    assert undone and restored, run["undo"][:2]
    assert undone[-1]["lit"] < 0.01 and _has_a_middle(undone), [s["lit"] for s in undone]
    assert "is-pb" in restored[-1]["cls"].split() and abs(restored[-1]["lit"] - 1) < 0.01
    assert _has_a_middle(restored), (
        "the restored PB SNAPPED to gold -- the hand-back must fade in", [s["lit"] for s in restored])
    assert not _arrived(restored), "an Undo celebrated the PB it handed back"


def test_a_strategy_round_trip_does_not_celebrate(run):
    back = _track(run["strat_back"], "runner_up")
    assert back and "is-pb" in back[-1]["cls"].split(), back[-1:] or run["strat_back"][-1:]
    assert not _arrived(back), (
        "switching strategy back re-crowned the row -- the arrival must come "
        "from a save, never from a row becoming the PB on screen")
    assert not run["problems"], run["problems"]


def test_the_save_shows_its_time_save_until_the_next_attempt_then_leaves_gold(run):
    frames = run["saved_frames"]
    expected = f"Time Save: -{frames / 30:.2f} ({frames} {'frame' if frames == 1 else 'frames'})"
    shown = _track(run["takeover"], "best")[-1]
    assert shown["save"] and shown["save"]["shown"], ("no Time Save beside the new PB", shown)
    assert shown["save"]["text"] == expected and shown["save"]["opacity"] == 1, shown["save"]
    after = _track(run["next_attempt"], "best")
    assert not after[-1]["save"]["shown"] and after[-1]["save"]["opacity"] == 0, after[-1]
    assert any(0.05 < s["save"]["opacity"] < 0.95 for s in after), (
        "the Time Save SNAPPED away -- it must animate out",
        [s["save"]["opacity"] for s in after])
    assert all(s["gold"] == 1 and "is-pb" in s["cls"].split() for s in after), (
        "GOLD!! must stay when the Time Save leaves", after[-1])

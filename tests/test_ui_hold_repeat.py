"""Press-and-hold stepping (ui/holdrepeat.js): one step on the press, a
steady run while held, nothing after release. Driven in node with fake
timers so the schedule is a fact rather than a feel."""
import json
import subprocess
from pathlib import Path

HOLD_JS = (Path(__file__).resolve().parents[1]
           / "src/sm64_events/ui/holdrepeat.js")

HARNESS = """
import { holdRepeat, HOLD_DELAY_MS, HOLD_INTERVAL_MS } from %s;
// A fake clock: timers fire when `advance` walks past their due time.
const timers = new Map(); let now = 0; let nextId = 1;
const fake = {
  setTimeout: (fn, ms) => { timers.set(nextId, {fn, due: now + ms, every: null}); return nextId++; },
  setInterval: (fn, ms) => { timers.set(nextId, {fn, due: now + ms, every: ms}); return nextId++; },
  clearTimeout: (id) => timers.delete(id),
  clearInterval: (id) => timers.delete(id),
};
const advance = (ms) => {
  const end = now + ms;
  for (;;) {
    let soonest = null;
    for (const [id, t] of timers) if (t.due <= end && (soonest === null || t.due < soonest[1].due)) soonest = [id, t];
    if (!soonest) break;
    const [id, t] = soonest; now = t.due; t.fn();
    if (t.every === null) timers.delete(id); else t.due += t.every;
  }
  now = end;
};
let steps = 0;
const h = holdRepeat(() => { steps += 1; }, fake);
const out = {};
%s
console.log(JSON.stringify(out));
"""


def run(body: str):
    script = HARNESS % (json.dumps(HOLD_JS.as_uri()), body)
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_a_tap_is_exactly_one_step():
    got = run("""
      h.onpointerdown({button: 0}); advance(100); h.onpointerup();
      advance(5000); out.steps = steps;
    """)
    assert got["steps"] == 1


def test_a_hold_steps_once_then_runs_at_the_interval():
    got = run("""
      h.onpointerdown({button: 0});
      out.afterPress = steps;
      advance(HOLD_DELAY_MS - 1); out.beforeRun = steps;
      advance(1 + HOLD_INTERVAL_MS * 10); out.afterRun = steps;
      h.onpointerup(); advance(5000); out.afterRelease = steps;
    """)
    assert got["afterPress"] == 1
    assert got["beforeRun"] == 1
    assert got["afterRun"] == 11
    assert got["afterRelease"] == 11


def test_leaving_the_button_stops_the_run_like_releasing_it():
    got = run("""
      h.onpointerdown({button: 0}); advance(HOLD_DELAY_MS + HOLD_INTERVAL_MS * 3);
      h.onpointerleave(); advance(5000); out.steps = steps;
    """)
    assert got["steps"] == 4


def test_a_keyboard_activation_steps_once_and_a_mouse_click_does_not_double():
    got = run("""
      h.onclick({detail: 0}); out.keyboard = steps;
      h.onpointerdown({button: 0}); h.onpointerup(); h.onclick({detail: 1});
      out.mouse = steps;
    """)
    assert got["keyboard"] == 1
    assert got["mouse"] == 2


def test_a_right_button_press_does_nothing():
    got = run("h.onpointerdown({button: 2}); advance(5000); out.steps = steps;")
    assert got["steps"] == 0

"""Press-and-hold stepping (ui/holdrepeat.js): one step on the press, a
steady run while held, nothing after release, and playback resumed if it
was playing. Driven in node with fake timers so the schedule is a fact
rather than a feel.

The hold's state lives on the ELEMENT: his live report 2026-08-23 ("it
accidentally triggers the Hold action prematurely, and if I release the
button press, it doesn't STOP") was a press landing in one render's
handlers and the release in the next render's, which had nothing to clear.
"""
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
let steps = 0; let resumed = 0;
const button = {};                       // the element the hold state lives on
const press = { button: 0, currentTarget: button, pointerId: 1 };
const release = { currentTarget: button };
const fresh = (opts) => holdRepeat(() => { steps += 1; }, { timers: fake, ...(opts || {}) });
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
      const h = fresh();
      h.onpointerdown(press); advance(100); h.onpointerup(release);
      advance(5000); out.steps = steps;
    """)
    assert got["steps"] == 1


def test_a_hold_steps_once_then_runs_at_the_interval_until_release():
    got = run("""
      const h = fresh();
      h.onpointerdown(press);
      out.afterPress = steps;
      advance(HOLD_DELAY_MS - 1); out.beforeRun = steps;
      advance(1 + HOLD_INTERVAL_MS * 10); out.afterRun = steps;
      h.onpointerup(release); advance(5000); out.afterRelease = steps;
    """)
    assert got["afterPress"] == 1
    assert got["beforeRun"] == 1
    assert got["afterRun"] == 11
    assert got["afterRelease"] == 11


def test_a_release_through_a_LATER_render_s_handlers_still_stops_the_run():
    """The component re-renders on every step, so the release almost always
    arrives at handlers created after the press. The state is on the
    element, so they find it."""
    got = run("""
      fresh().onpointerdown(press);
      advance(HOLD_DELAY_MS + HOLD_INTERVAL_MS * 3);
      fresh().onpointerup(release);            // a different closure
      advance(5000); out.steps = steps;
    """)
    assert got["steps"] == 4


def test_a_tap_released_through_a_later_render_never_starts_the_run():
    got = run("""
      fresh().onpointerdown(press); advance(50);
      fresh().onpointerup(release);
      advance(5000); out.steps = steps;
    """)
    assert got["steps"] == 1


def test_the_release_resumes_what_the_press_was_told_to():
    got = run("""
      const h = fresh({ onPress: () => () => { resumed += 1; } });
      h.onpointerdown(press); advance(HOLD_DELAY_MS + HOLD_INTERVAL_MS * 2);
      out.duringHold = resumed;
      h.onpointerup(release); out.afterRelease = resumed;
      h.onlostpointercapture(release); out.afterCaptureLoss = resumed;
    """)
    assert got["duringHold"] == 0
    assert got["afterRelease"] == 1
    assert got["afterCaptureLoss"] == 1        # once, not once per event


def test_a_keyboard_activation_steps_once_and_a_mouse_click_does_not_double():
    got = run("""
      const h = fresh();
      h.onclick({detail: 0}); out.keyboard = steps;
      h.onpointerdown(press); h.onpointerup(release); h.onclick({detail: 1});
      out.mouse = steps;
    """)
    assert got["keyboard"] == 1
    assert got["mouse"] == 2


def test_a_right_button_press_does_nothing():
    got = run("fresh().onpointerdown({button: 2, currentTarget: button}); advance(5000); out.steps = steps;")
    assert got["steps"] == 0

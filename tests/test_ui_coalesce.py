"""One star grab causes ONE refetch, not two.

A grab publishes `star_collected` and the `attempt_completed` it produced in
the same server tick. Both are in store.js's REFRESH_ON, so the page fired two
full `/api/session` + `/api/marelo` rounds back to back per grab -- tens of
milliseconds and a wasted round trip on the path the user feels as lag, plus a
real race: two identical fetches in flight together can land out of order and
let the older payload overwrite the newer one.

`ui/coalesce.js` is import-free so node can drive it directly, with the burst
window injected -- the assertions below are about WHEN it runs, which no
render can show and no wall-clock sample would report reliably.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
COALESCE = (REPO / "src" / "sm64_events" / "ui" / "coalesce.js").as_uri()

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH")


SCRIPT = f"""
import {{ coalesce }} from {COALESCE!r};

const flush = () => new Promise((done) => setTimeout(done, 0));
const out = {{}};

// A manual burst window, so "did these join the same run" is decided by the
// test rather than by how a real event loop happened to interleave.
function manual() {{
  const pending = [];
  return {{
    schedule: (fn) => pending.push(fn),
    depth: () => pending.length,
    drain: () => {{ const fns = pending.splice(0); fns.forEach((fn) => fn()); }},
  }};
}}

// A: a burst of events joins ONE run -- the star-grab case.
{{
  const w = manual();
  let runs = 0;
  const request = coalesce(async () => {{ runs += 1; }}, w.schedule);
  request(); request(); request();
  out.burst_scheduled = w.depth();
  w.drain();
  await flush();
  out.burst_runs = runs;
}}

// B: events arriving mid-run queue exactly ONE follow-up, and no two runs are
// ever in flight together (which is what makes an out-of-order land impossible).
{{
  const w = manual();
  let runs = 0, live = 0, mostLive = 0, release = null;
  const request = coalesce(async () => {{
    runs += 1; live += 1; mostLive = Math.max(mostLive, live);
    await new Promise((r) => {{ release = r; }});
    live -= 1;
  }}, w.schedule);
  request();
  w.drain();
  await flush();
  for (let i = 0; i < 10; i += 1) request();
  out.queued_while_running = w.depth();   // still nothing scheduled yet
  release();
  await flush();
  out.followups_scheduled = w.depth();
  w.drain();
  await flush();
  out.inflight_runs = runs;
  out.most_live_at_once = mostLive;
}}

// C: a run that throws must not wedge the next one.
{{
  const w = manual();
  let runs = 0;
  const request = coalesce(async () => {{
    runs += 1;
    if (runs === 1) throw new Error("fetch blew up");
  }}, w.schedule);
  request(); w.drain(); await flush();
  request(); w.drain(); await flush();
  out.after_a_failed_run = runs;
}}

// D: THE CONTROL. With no burst window (schedule runs the trigger straight
// away -- the hidden-document path), a grab's two events cost two runs again.
{{
  let runs = 0;
  const request = coalesce(async () => {{ runs += 1; }}, (fn) => fn());
  request(); request();
  await flush(); await flush();
  out.without_a_burst_window = runs;
}}

console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result() -> dict:
    done = subprocess.run(["node", "--input-type=module", "-"], input=SCRIPT,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_a_burst_of_events_causes_one_run(result):
    assert result["burst_scheduled"] == 1, (
        "three triggers in one burst scheduled more than one run")
    assert result["burst_runs"] == 1, (
        "a star grab publishes two REFRESH_ON events in the same tick; they "
        "must share one refetch")


def test_events_during_a_run_queue_exactly_one_follow_up(result):
    assert result["queued_while_running"] == 0, (
        "a trigger arriving mid-run must not schedule anything until the "
        "run in flight finishes")
    assert result["followups_scheduled"] == 1
    assert result["inflight_runs"] == 2, (
        "ten events during one run must cost one follow-up, not ten")


def test_two_runs_are_never_in_flight_together(result):
    assert result["most_live_at_once"] == 1, (
        "two fetches in flight can land out of order, which is how an older "
        "payload overwrites a newer one")


def test_a_failed_run_does_not_wedge_the_coalescer(result):
    assert result["after_a_failed_run"] == 2, (
        "a rejected run left the coalescer permanently in flight")


def test_without_the_burst_window_a_grab_costs_two_runs(result):
    """Mutation proof, and the hidden-document path's honest cost.

    If this reported 1 the guard above would be green for a reason other than
    the burst window, and deleting the window would cost nothing visible."""
    assert result["without_a_burst_window"] == 2, (
        "the probe is not measuring what it claims -- with no burst window "
        "the two events of one grab must still cost two runs")

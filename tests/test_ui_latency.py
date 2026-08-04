"""The star-to-screen instrument reports ONE trigger per burst, and once.

Live report 2026-08-04: *"It's still slow, and you did not succeed… Do we have
instrumentation for comparing the timegap between detecting the xcam / final
time, and when we actually display it in the frontend?"* We did not, and two
rounds of fixes had been aimed by reading code instead.

Two properties make the number trustworthy, and neither is visible in a render:

* a burst is owned by its FIRST event, because that is the moment the player's
  action became knowable — a grab publishes `star_collected` and
  `attempt_completed` in one server tick and they coalesce into one refresh
  (`ui/coalesce.js`), so charging the paint to the second one would silently
  subtract however long the first had already been waiting;
* marks are consumed ONCE, so a later repaint cannot claim the same event and
  report a latency that includes everything the user did in between.

`ui/latency.js` is import-free so node can drive it, which is the only way to
assert on timing bookkeeping that never reaches the DOM.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LATENCY = (REPO / "src" / "sm64_events" / "ui" / "latency.js").as_uri()

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH")

SCRIPT = f"""
import * as L from {LATENCY!r};

const out = {{}};

// A grab's two events land in one tick; the fetch they share returns later.
L.noteEvent("star_collected", 100, 9000, "T0");
L.noteEvent("attempt_completed", 101, 9000, "T1");
L.noteFetchStart("T2");
L.noteFetchDone("T3");
const first = L.takeMarks();
out.owner_seq = first.ws_seq;              // the FIRST event owns the burst
out.owner_type = first.ws_type;
out.stages = [first.ws_utc, first.fetch_start_utc, first.fetch_done_utc];
out.consumed_once = L.takeMarks() === null;

// A paint with no trigger of ours behind it (a click, a stray re-render) must
// not be attributed to a stale event.
out.unattributed = L.takeMarks() === null;

// A fresh burst after the last one was consumed is measured on its own.
L.resetMarks();
L.noteEvent("star_collected", 200, 9500, "U0");
L.noteFetchStart("U1");
L.noteFetchDone("U2");
out.next_seq = L.takeMarks().ws_seq;

console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result():
    proc = subprocess.run(["node", "--input-type=module", "-e", SCRIPT],
                          capture_output=True, encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_the_first_event_of_a_burst_owns_the_measurement(result):
    assert (result["owner_seq"], result["owner_type"]) \
        == (100, "star_collected")
    assert result["stages"] == ["T0", "T2", "T3"]


def test_marks_are_consumed_once_so_a_repaint_cannot_reuse_them(result):
    assert result["consumed_once"] is True
    assert result["unattributed"] is True


def test_a_later_burst_is_measured_on_its_own(result):
    assert result["next_seq"] == 200

"""ui/setupflow.js — which step the install page shows for a stream of server
steps. His report, 2026-10-17: "Wizard still is super sketchy, I think it's
because it's bouncing visually between so many steps?" The page now shows a
few stages; this drives the machine on a 50 ms clock in node, so each claim
is exact rather than whatever frames a browser happened to catch. The real
modal is driven by tests/test_ui_setup_modal.py.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

FLOW = Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "ui" / "setupflow.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")


def shown(changes, until):
    """`changes` is [(ms, server step)]; returns each change of what the page
    shows as (ms, step, stage, acknowledging)."""
    script = f"""import * as F from {FLOW.as_uri()!r};
    const changes = {json.dumps(changes)};
    const actualAt = t => changes.filter(([at]) => at <= t).at(-1)[1];
    let state = F.shownStart(actualAt(0), 0);
    const out = [[0, state.step, F.stageOf(state.step), false]];
    for (let t = 50; t <= {until}; t += 50) {{
      state = F.shownNext(state, actualAt(t), t);
      const row = [t, state.step, F.stageOf(state.step), !!state.ack];
      if (row.slice(1).join() !== out.at(-1).slice(1).join()) out.push(row);
    }}
    console.log(JSON.stringify({{out, hold: F.HOLD_MS, seen: F.SEEN_MS, ack: F.SUBSTEP_MS}}));"""
    done = subprocess.run(["node", "--input-type=module", "-"], input=script,
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    return [tuple(row) for row in result["out"]], result


def test_a_check_that_flickers_moves_forward_once_and_stays():
    """The server's verify/ready flicker inside "Checking your setup" shows
    Setup checked once; one-poll regressions never take it back."""
    rows, _ = shown([(0, "verify"), (1000, "ready"), (2000, "verify"), (3000, "ready"),
                     (4000, "rom"), (5000, "ready")], until=8000)
    assert rows == [(0, "verify", "check", False), (1000, "ready", "check", False)]


def test_a_step_back_shows_only_once_it_lasts():
    rows, limits = shown([(0, "verify"), (1000, "rom"), (2500, "verify"), (4000, "reopen")], until=8000)
    hold = limits["hold"]
    assert hold <= 2000
    # 1.5 s back at rom is never shown; reopen, lasting, is shown after the hold.
    assert rows == [(0, "verify", "check", False), (4000 + hold, "reopen", "game", False)]


def test_only_a_stage_he_saw_and_acted_on_is_acknowledged():
    _, limits = shown([(0, "rom")], until=0)
    seen, ack = limits["seen"], limits["ack"]
    # Resolved within one poll of appearing: no acknowledgment, straight to the next card.
    quick, _ = shown([(0, "rom"), (1000, "verify")], until=4000)
    assert quick == [(0, "rom", "game", False), (1000, "verify", "check", False)]
    # Read, then acted on: its acknowledgment, then the newest progress.
    read, _ = shown([(0, "reopen"), (1000, "rom"), (seen + 500, "verify"), (seen + 600, "ready")],
                    until=seen + 3000)
    assert read == [(0, "reopen", "game", False), (1000, "rom", "game", False),
                    (seen + 500, "rom", "game", True), (seen + 500 + ack, "ready", "check", False)]
    # A step back during the acknowledgment never returns to the stage it acknowledged.
    back, _ = shown([(0, "install"), (seen, "reopen"), (seen + 100, "install")], until=seen + 1500)
    assert back == [(0, "install", "install", False), (seen, "install", "install", True),
                    (seen + ack, "reopen", "game", False)]
    # A jump past the next stage did not happen in front of him: no "Project64 closed".
    jump, _ = shown([(0, "close"), (seen + 500, "verify")], until=seen + 2000)
    assert jump == [(0, "close", "close", False), (seen + 500, "verify", "check", False)]
    # Waiting stages are never acknowledged: the check stage moves in place.
    check, _ = shown([(0, "verify"), (seen + 500, "ready")], until=seen + 2000)
    assert check == [(0, "verify", "check", False), (seen + 500, "ready", "check", False)]

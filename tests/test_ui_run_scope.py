"""Changing the rank scope mid-run must warn before it throws the run away.

The route IS the rank scope on this app's header card — one control, two
meanings (`.claude/rules/ui-ranks.md`) — so switching it while a run is in
flight would silently re-rate that run against a plan it is not following.

His ruling, 2026-08-03:

    if we trigger a Run, it should automatically change the overall MARELO rank
    mode to whatever route we're running. If we have a run in-progress, we have
    to stop the run before changing ranking scopes... You're allowed to change
    it, just that it will also stop their active run. The dialogue should warn
    them.

`runBlocksScopeChange` is the whole rule and is import-free, so node drives it
directly rather than through a browser. What a render CANNOT check is the case
that matters most — re-picking the route you are already running must not warn —
because nothing visible happens either way.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "ui"
STORE = UI / "store.js"
HEADER = UI / "components" / "header.js"
RUNVIEW = UI / "components" / "runview.js"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from source_scan import strip_comments  # noqa: E402


def _rule_source() -> str:
    """The REAL declaration, lifted out of comment-stripped source.

    `store.js` imports preact hooks at module scope, so node cannot import it
    without the browser's import map — the same reason
    tests/test_cross_language_parity.py extracts rather than imports for every
    Preact-touching module. Extraction, never a restatement: a copy of the rule
    in this file would be a second implementation that passes forever.
    """
    src = strip_comments(STORE.read_text(encoding="utf-8"))
    start = src.index("export function runBlocksScopeChange(")
    depth, i = 0, src.index("{", start)
    while True:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    return src[start:i + 1].replace("export function", "function", 1)


def ask(*calls: str) -> list:
    script = "%s\nconsole.log(JSON.stringify([%s]));" % (
        _rule_source(), ", ".join(calls))
    out = subprocess.run([_node(), "-e", script],
                         capture_output=True, text=True, cwd=UI)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _node() -> str:
    from shutil import which
    node = which("node")
    if node is None:
        pytest.skip("node is not on PATH")
    return node


ACTIVE = '{active: {id: 1, route_id: 4}}'
IDLE = '{active: null}'


def test_no_run_means_no_warning():
    assert ask(f"runBlocksScopeChange({IDLE}, 4, 9)",
               "runBlocksScopeChange(null, 4, 9)") == [False, False]


def test_switching_routes_mid_run_warns():
    assert ask(f"runBlocksScopeChange({ACTIVE}, 4, 9)") == [True]


def test_switching_to_overall_mid_run_warns():
    """`null` is a real pick, not "no pick" — dropping to Overall abandons the
    run exactly as switching to another route does."""
    assert ask(f"runBlocksScopeChange({ACTIVE}, 4, null)") == [True]


def test_repicking_the_route_you_are_already_running_never_warns():
    """The case a render cannot check, and the one that would be most annoying
    to get wrong: a strategy edit, a reconcile, or a stray re-render posts the
    same route id again. Warning there would offer to end a run nobody asked to
    end."""
    assert ask(f"runBlocksScopeChange({ACTIVE}, 4, 4)") == [False]


def test_the_rule_can_still_fail():
    """Every assertion above is satisfied by a function returning False. This
    is the one that proves it returns True somewhere."""
    assert ask(f"runBlocksScopeChange({ACTIVE}, 4, 9)") == [True]


# --- the wiring, comment-immune (source_scan.py) ---------------------------

def test_the_header_asks_before_it_switches():
    """The store returns a sentinel and never asks; the header owns the
    dialog. A `window.confirm` here would block the event loop while the run it
    is asking about carries on, and no rig can see it."""
    src = strip_comments(HEADER.read_text(encoding="utf-8"))
    assert "RUN_ACTIVE" in src and "setPendingScope" in src
    assert "onPickRoute=${pickRouteOrWarn}" in src, (
        "the rank card must go through the guard, not straight to t.pickRoute")
    # Drawn in the shared shell, not by the browser: a native dialog blocks the
    # event loop while the run it is asking about carries on, cannot be styled,
    # and is invisible to the responsive rig. (header.js DOES use
    # window.confirm elsewhere -- deleting a session -- so this is scoped to
    # the warning component rather than to the file.)
    warning = src[src.index("function RunScopeWarning"):src.index("export function Header")]
    assert "Modal" in warning and "confirm" not in warning


def test_saying_yes_ends_the_run_before_it_switches():
    src = strip_comments(HEADER.read_text(encoding="utf-8"))
    assert "await t.endRun();" in src
    assert "t.pickRoute(id, { confirmed: true });" in src


def test_arming_a_run_sets_the_scope_to_that_route():
    """The other half of his ask, and the reason the whole report started: on
    Overall the route never narrows the selector, so the run's own next split is
    not auto-selected."""
    src = strip_comments(RUNVIEW.read_text(encoding="utf-8"))
    assert "t.pickRoute(id, { confirmed: true });" in src, (
        "arming a run must set the rank scope, and confirmed — starting the "
        "run IS the decision, so it must not warn about itself")

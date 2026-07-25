# tests/test_ui_celebrate.py
"""celebrate.js's climb-step effect must key off the celebration's STABLE
identity (its `key`), not the object itself (spec 2026-07-24-marelo-rank-
system fix wave, item 4).

Bug: every `/api/marelo` refetch returns a FRESH object with the same `key`
(REST responses are never memoized). During play, REFRESH_ON events
(star_collected, attempt_completed, strat_set, target_changed) can arrive
faster than the 850ms step timer, so a celebration object identity change
mid-climb restarted the step effect over and over -- stalling the climb and
deferring `finish()` (which only runs once `step` reaches the end) forever.

The sibling reset effect two lines above already depends on
`celebration && celebration.key` for exactly this reason; the climb-step
effect must match it."""
import re
from pathlib import Path

CELEBRATE_JS = (Path(__file__).resolve().parents[1] / "src" / "sm64_events"
                / "ui" / "components" / "celebrate.js")


def test_climb_step_effect_depends_on_the_celebration_key_not_the_object():
    source = CELEBRATE_JS.read_text(encoding="utf-8")
    match = re.search(
        r"return \(\) => clearTimeout\(nextStepTimer\);\s*\}, (\[[^\]]*\])\);",
        source)
    assert match, "the climb-step useEffect not found -- did it move or get restructured?"
    deps = match.group(1)
    assert "celebration && celebration.key" in deps, (
        "the climb-step effect must depend on celebration.key (a stable "
        "primitive), matching the reset effect above it -- a fresh object "
        "identity from every /api/marelo refetch (same key) would otherwise "
        f"restart the step timer mid-climb. Got dependency array: {deps}")

# tests/test_ui_practice_log.py
"""The practice log is the session's history, grouped by what you practiced.

Ordering is pure and is driven under node. The card itself is verified by
RENDERING (tests/test_responsive.py + tools/contact_sheet.py) -- a unit test
cannot tell whether two rank banners crowd each other, and this project has
shipped an invisible feature on unit tests plus `node --check` before.

`orderedSections`'s own declaration is extracted from source rather than
imported. practicelog.js pulls in Preact -- through ranks.js, icons.js and
attemptlog.js -- via the browser's import map (ui/index.html's `"preact":
"/ui/vendor/preact.module.js"`), which plain `node` cannot resolve at all
(`import('preact')` from this directory fails with ERR_MODULE_NOT_FOUND,
verified directly rather than assumed). Every node-driven test in this suite
already targets an import-free module for exactly that reason --
tests/test_cross_language_parity.py's own docstring names the same
constraint for ranks.js/statmenu.js and uses the same fix: extract the ONE
declaration that is actually pure (comments stripped first, so a stale
commented-out version can never be the one picked up) and evaluate it on its
own. `orderedSections` needs nothing from Preact -- it only merges and sorts
plain arrays -- so this is the real function, never a restatement of it.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from source_scan import strip_comments

REPO = Path(__file__).resolve().parent.parent
UI = REPO / "src" / "sm64_events" / "ui"
LOG_JS = UI / "components" / "practicelog.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")


def _ordered_sections_source() -> str:
    source = strip_comments(LOG_JS.read_text(encoding="utf-8"))
    match = re.search(r"^export function orderedSections\(.*?^\}", source,
                       re.S | re.M)
    assert match, "orderedSections not found in practicelog.js — renamed?"
    return match.group(0)


def ordered(view) -> list:
    script = (_ordered_sections_source() + "\n"
              f"console.log(JSON.stringify(orderedSections("
              f"{json.dumps(view)}).map(s => s.id)));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def star(id_, last_activity):
    return {"id": id_, "course_id": 13, "star_id": 1,
            "last_activity": last_activity, "attempts": []}


def segment(id_, last_activity):
    return {"id": id_, "kind": "segment", "segment_id": 12,
            "last_activity": last_activity, "attempts": []}


def test_stars_and_segments_interleave_by_recency():
    """The whole point of the feature: the segment you just finished sits
    above the star you did before it, whatever kind each one is."""
    view = {"stars": [star("a", 100), star("c", 10)],
            "segments": [segment("b", 50)]}
    assert ordered(view) == ["a", "b", "c"]


def test_a_section_with_no_activity_sorts_last_not_first():
    """A target set but never run carries last_activity -1."""
    view = {"stars": [star("fresh", -1), star("played", 5)], "segments": []}
    assert ordered(view) == ["played", "fresh"]


def test_a_missing_segments_key_is_not_a_crash():
    assert ordered({"stars": [star("a", 1)]}) == ["a"]

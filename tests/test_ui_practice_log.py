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
ENTITY_SECTION_JS = UI / "entitysection.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")


def _extract(source: str, name: str) -> str:
    match = re.search(rf"^export function {name}\(.*?^\}}", source,
                       re.S | re.M)
    assert match, f"{name} not found — renamed?"
    return match.group(0)


def _ordered_sections_source() -> str:
    source = strip_comments(LOG_JS.read_text(encoding="utf-8"))
    return _extract(source, "orderedSections")


def ordered(view) -> list:
    script = (_ordered_sections_source() + "\n"
              f"console.log(JSON.stringify(orderedSections("
              f"{json.dumps(view)}).map(s => s.id)));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def star(id_, last_activity, course_id=13, star_id=1):
    return {"id": id_, "course_id": course_id, "star_id": star_id,
            "last_activity": last_activity, "attempts": []}


def segment(id_, last_activity, segment_id=12):
    return {"id": id_, "kind": "segment", "segment_id": segment_id,
            "last_activity": last_activity, "attempts": []}


# ---- topEntityKey / isCardOpen -- the auto-open-newest feature -------------
#
# `topEntityKey` calls `entityKey` (entitysection.js), which practicelog.js
# imports for real -- so the node script needs the REAL `entityKey` too, not a
# restatement of its `star:<course>:<star>` / `segment:<id>` shape. `isSegment`
# and `entityKey` are both plain, import-free declarations in entitysection.js
# (only `displayName`/`segmentFamily` there reach for redsfamily.js), so
# extracting them is the same "real function, comments stripped" technique
# `_ordered_sections_source` above already uses, just from a second file.

def _entity_key_source() -> str:
    source = strip_comments(ENTITY_SECTION_JS.read_text(encoding="utf-8"))
    is_segment = re.search(r"^export const isSegment = .*?;$", source, re.M)
    assert is_segment, "isSegment not found in entitysection.js — renamed?"
    return is_segment.group(0) + "\n" + _extract(source, "entityKey")


def _top_entity_key_source() -> str:
    log_source = strip_comments(LOG_JS.read_text(encoding="utf-8"))
    return "\n".join([_entity_key_source(),
                       _extract(log_source, "orderedSections"),
                       _extract(log_source, "topEntityKey")])


def top_key(view):
    script = (_top_entity_key_source() + "\n"
              f"console.log(JSON.stringify(topEntityKey("
              f"{json.dumps(view)})));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _is_card_open_source() -> str:
    source = strip_comments(LOG_JS.read_text(encoding="utf-8"))
    return _extract(source, "isCardOpen")


def is_open(overrides, top, key):
    script = (_is_card_open_source() + "\n"
              f"console.log(JSON.stringify(isCardOpen("
              f"{json.dumps(overrides)}, {json.dumps(top)}, "
              f"{json.dumps(key)})));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


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


# ---- topEntityKey -----------------------------------------------------------

def test_the_top_key_is_the_most_recently_practiced_entity():
    view = {"stars": [star("a", 100, course_id=2, star_id=4),
                       star("c", 10, course_id=9, star_id=3)],
            "segments": [segment("b", 50, segment_id=6)]}
    assert top_key(view) == "star:2:4"


def test_the_top_key_is_null_with_nothing_practiced():
    """Nothing classified yet -- the unassigned bucket is not an entity and
    is never eligible for the auto-open slot (UnassignedLogCard's own
    comment); `view` itself may not have loaded yet either."""
    assert top_key({"stars": [], "segments": []}) is None
    assert top_key(None) is None


# ---- isCardOpen -- the ONE auto-open slot -----------------------------------

def test_the_top_card_opens_with_no_override():
    assert is_open({}, "star:2:4", "star:2:4") is True


def test_a_non_top_card_stays_closed_with_no_override():
    assert is_open({}, "star:2:4", "star:9:3") is False


def test_nothing_opens_with_no_top_key_at_all():
    assert is_open({}, None, "star:2:4") is False


def test_a_card_the_user_opened_himself_survives_a_different_top():
    """"A card the user opened himself is his. Arriving entries must never
    close it" -- his rule verbatim. The override wins even though a
    DIFFERENT entity now holds the auto-open slot."""
    assert is_open({"star:9:3": "open"}, "star:2:4", "star:9:3") is True


def test_a_card_the_user_closed_himself_stays_closed_even_at_the_top():
    """"A card the user closed himself stays closed... It reopens only when a
    genuinely new entity takes the top slot" -- the override wins even while
    this SAME entity still holds the slot (a mere view refresh must not
    reopen it)."""
    assert is_open({"star:2:4": "closed"}, "star:2:4", "star:2:4") is False


def test_a_displaced_top_card_closes_with_no_override_needed():
    """The half of the feature that needs no state at all: the PREVIOUS top
    card simply stops matching `topKey` the moment a new entity takes the
    slot, so it closes by falling through to the auto-open rule -- exactly
    as if it had never been touched."""
    assert is_open({}, "star:9:3", "star:2:4") is False

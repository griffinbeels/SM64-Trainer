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


def test_an_unassigned_reset_can_never_take_the_top_slot():
    """The unassigned bucket is noise and stays at the bottom, closed.

    Griffin, 2026-08-04: "the unassigned runs card should ALWAYS stay closed,
    unless the user opens it. It should also ALWAYS stay at the bottom of the
    list, even if an unassigned reset is the newest entry. This is because
    that information is noise and should be at the bottom of the screen,
    tucked away."

    The ordering half holds by construction rather than by a rule anyone has
    to remember: `topEntityKey` reads `orderedSections`, which merges the
    view's stars and segments, and the bucket is neither -- it is a flat
    attempt list with no `last_activity` of its own. This pins that, because
    "by construction" stops being true the moment someone teaches
    orderedSections about a third kind.
    """
    view = {"stars": [star("s", 10)], "segments": [],
            "unassigned": [{"id": 99, "journal_id": 9999, "outcome": "reset"}]}
    assert top_key(view) == "star:13:1", (
        "an unassigned attempt, however recent, must not win the auto-open "
        "slot -- it has no card of its own in the recency ordering")


def test_the_unassigned_card_starts_closed():
    """Its own default, and the one half of his rule that needed code.

    Deliberately NOT a check that some option is set to a value -- this is the
    component's shipped behaviour, not a tuning default, so pinning it here
    does not collide with the "no test may assert a shipped default" rule.
    """
    source = strip_comments(LOG_JS.read_text(encoding="utf-8"))
    match = re.search(r"function UnassignedLogCard\([^)]*\)\s*\{\s*"
                      r"const \[open, setOpen\] = useState\((?P<initial>\w+)\)",
                      source)
    assert match, "UnassignedLogCard no longer owns an `open` useState"
    assert match.group("initial") == "false", (
        "the unassigned bucket must start CLOSED -- it is noise tucked away "
        "at the bottom, and only a click of his opens it")


# ---- the log's own row reaches the active-strategy reclassification path ---
#
# `tracking/service.py::set_attempt_strat` already does what Griffin asked for
# (2026-07-24): reclassifying an entity's NEWEST non-cleared attempt also
# moves that entity's ACTIVE strategy, on the theory that "my last run was
# actually strat X" means X is what is being practised now. That rule is
# server-side and pre-dates this round -- nothing here re-implements it. What
# this round changed is where the row doing the reclassifying LIVES: every
# attempt is now inside a `LogCard` (practicelog.js) rather than a per-kind
# section, so the thing worth proving is that the WIRING still reaches the
# server rule, not that the rule itself works.
ATTEMPTLOG_JS = UI / "components" / "attemptlog.js"


def test_the_log_cards_open_attempt_table_carries_its_own_entity():
    """`AttemptRow`'s strategy picker only reclassifies THIS attempt against
    THIS entity's own strategies/identity when it has `sec` -- drop the prop
    and every row falls back to the plain, non-reclassifiable
    `<span>${a.strat_tag || "no strategy"}</span>` branch
    (attemptlog.js::AttemptTable), which would make a log card's own
    top-row-reclassify-the-active-strategy path silently unreachable while
    every other guard in this file stays green (the exact shape a reviewer
    already caught once on this branch, StatChipsRow silently dropped for
    segments)."""
    log_source = strip_comments(LOG_JS.read_text(encoding="utf-8"))
    assert re.search(
        r"<\$\{AttemptTable\}\s+attempts=\$\{sec\.attempts\}\s+rows=\$\{shown\}"
        r"\s+t=\$\{t\}\s*\n\s*focus=\$\{selected \? focus : null\}"
        r"\s+clearFocus=\$\{clearFocus\}\s*\n\s*freshIds=\$\{freshIds\}"
        r"\s+openCompare=\$\{openCompare\}\s+sec=\$\{sec\}", log_source), (
        "LogCard's <AttemptTable> call no longer passes sec=${sec} -- the "
        "attempt rows it renders would lose their entity, and the top row's "
        "own strategy picker could no longer reclassify anything")


def test_the_reclassify_wiring_guard_can_still_fail():
    """Probed in both directions, per the norm in ui-core.md: a comment
    mentioning the shape, or the prop simply missing, must not satisfy the
    positive regex above."""
    comment_only = strip_comments(
        "// <${AttemptTable} attempts=${sec.attempts} rows=${shown} t=${t}\n"
        "// focus=${selected ? focus : null} clearFocus=${clearFocus}\n"
        "// freshIds=${freshIds} openCompare=${openCompare} sec=${sec} />\n")
    dropped_prop = (
        "<${AttemptTable} attempts=${sec.attempts} rows=${shown} t=${t}\n"
        "focus=${selected ? focus : null} clearFocus=${clearFocus}\n"
        "freshIds=${freshIds} openCompare=${openCompare} />\n")
    pattern = (r"<\$\{AttemptTable\}\s+attempts=\$\{sec\.attempts\}\s+rows=\$\{shown\}"
               r"\s+t=\$\{t\}\s*\n\s*focus=\$\{selected \? focus : null\}"
               r"\s+clearFocus=\$\{clearFocus\}\s*\n\s*freshIds=\$\{freshIds\}"
               r"\s+openCompare=\$\{openCompare\}\s+sec=\$\{sec\}")
    assert not re.search(pattern, comment_only)
    assert not re.search(pattern, dropped_prop)


def test_the_attempt_row_still_posts_a_reclassification_to_the_server_rule():
    """The other half of the same chain, in attemptlog.js -- unchanged by
    this round, and pinned here anyway because it is the other link a future
    edit could quietly cut. `POST /api/attempts/{id}/strat` is
    `tracking/service.py::set_attempt_strat`'s own route
    (`server/api.py`); this only proves the CLIENT still calls it from a row
    that has an entity, never the server-side newest-row rule itself."""
    attemptlog_source = strip_comments(ATTEMPTLOG_JS.read_text(encoding="utf-8"))
    assert re.search(
        r'send\("POST", `/api/attempts/\$\{a\.id\}/strat`,\s*'
        r"\{\s*strat_tag:\s*tag\s*\}\)", attemptlog_source), (
        "AttemptRow's strategy picker no longer posts to "
        "/api/attempts/{id}/strat -- reclassifying a row from inside a "
        "LogCard would no longer reach set_attempt_strat's newest-row rule")
    assert re.search(r"\$\{sec\s*\n?\s*\?\s*html`<\$\{StratPicker\}", attemptlog_source), (
        "AttemptRow no longer gates its strategy picker on having a `sec` -- "
        "a row rendered with none would silently fall back to plain, "
        "unreclassifiable text")

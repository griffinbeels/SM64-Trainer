# tests/test_ui_subsections.py
"""Which practice-log card owns which piece.

REWRITTEN 2026-08-08 (round 22). This file used to drive progressive
disclosure -- `familyRoot`/`visibleEntities`/`isExpanded`, the selector's
expand-into-a-family rule -- and that whole model was retired by Griffin's
"complete redesign / upgrade": a subsection is a badge inside its parent's
art now, never a cell, so there is no family to expand and no fold to return
from. What is left of the module is the mapping it always really held, and
these are its tests.

`ui/subsections.js` imports only `entitysection.js` (itself node-driven for
the same reason), so node drives the REAL rule -- a Python reimplementation
would be a second copy of exactly the thing this feature exists to have one
of.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import sm64_events

SUBSECTIONS_JS = Path(sm64_events.__file__).parent / "ui" / "subsections.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")


def star(course, slot, **extra):
    return {"course_id": course, "star_id": slot, "attempts": [], **extra}


def seg(seg_id, parents=(), **extra):
    return {"kind": "segment", "segment_id": seg_id, "course_id": 7,
            "parents": list(parents), "attempts": [], **extra}


def nest(sections, earned_keys=None):
    """`nestSubsections(sections, earned)` as [(key, [child keys])].

    `earned_keys` None means everything earned a card; a list restricts it,
    which is how the interaction with `hasEarnedACard` is exercised without
    importing that (Preact-bound) module.
    """
    earned = ("(() => true)" if earned_keys is None
              else "((sec) => " + json.dumps(list(earned_keys))
                   + ".includes(sec.kind === 'segment'"
                     " ? 'segment:' + sec.segment_id"
                     " : 'star:' + sec.course_id + ':' + sec.star_id))")
    script = (
        f"import {{ nestSubsections }} from {SUBSECTIONS_JS.as_uri()!r};\n"
        f"const key = (s) => s.kind === 'segment' ? 'segment:' + s.segment_id"
        f" : 'star:' + s.course_id + ':' + s.star_id;\n"
        f"const out = nestSubsections({json.dumps(sections)}, {earned});\n"
        "console.log(JSON.stringify(out.map("
        "(g) => [key(g.sec), g.children.map(key)])));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            encoding="utf-8")
    assert result.returncode == 0, result.stderr
    return [(k, tuple(kids)) for k, kids in json.loads(result.stdout)]


# --- the ordinary case ------------------------------------------------------

def test_a_piece_draws_inside_its_parent_and_not_beside_it():
    # "the subsection should appear WITHIN the star's practice log entry as a
    # sub-entry... very very very clear that this subsection was associated
    # with the star it was a subsection for."
    assert nest([star(7, 4), seg(90, ["star:7:4"])]) == [
        ("star:7:4", ("segment:90",))]


def test_a_top_level_segment_is_untouched():
    assert nest([star(7, 4), seg(12)]) == [
        ("star:7:4", ()), ("segment:12", ())]


def test_order_is_the_order_given():
    rows = nest([seg(12), star(7, 4), seg(90, ["star:7:4"])])
    assert [key for key, _ in rows] == ["segment:12", "star:7:4"]


# --- his own LLL shape: one piece, two parents ------------------------------

def test_a_piece_with_two_parents_draws_under_BOTH():
    # Round 20's plural parents, his own case: "Volcano Entry" belongs to both
    # volcano stars. Round 22 drew it under the first only and he rejected
    # that -- "every segment enabled should appear as a subentry in the
    # practice log" -- because a card omitting one of its own pieces is wrong.
    rows = nest([star(7, 4), star(7, 5),
                 seg(90, ["star:7:4", "star:7:5"])])
    assert rows == [("star:7:4", ("segment:90",)),
                    ("star:7:5", ("segment:90",))]


def test_a_piece_drawn_under_its_parents_is_never_ALSO_top_level():
    rows = nest([star(7, 4), star(7, 5),
                 seg(90, ["star:7:4", "star:7:5"])])
    assert "segment:90" not in [key for key, _ in rows]


def test_it_falls_through_to_a_LATER_parent_when_the_first_has_no_card():
    rows = nest([star(7, 5), seg(90, ["star:7:4", "star:7:5"])])
    assert rows == [("star:7:5", ("segment:90",))]


# --- the cases that must NOT nest -------------------------------------------

def test_a_piece_whose_parents_are_all_absent_stays_top_level():
    # Also covers item 5 for free: an `area:`-parented piece names no section
    # at all, so a castle movement "works the same as today, as a standalone
    # top level practice log entry."
    assert nest([seg(90, ["area:6:3"])]) == [("segment:90", ())]


def test_a_disabled_piece_leaves_the_log_entirely():
    # The display half of the dimmed badge: "If the subsection is disabled,
    # then it doesn't appear in the parent star's practice log (which means it
    # will continue to look like how it does today)."
    assert nest([star(7, 4), seg(90, ["star:7:4"], enabled=False)]) == [
        ("star:7:4", ())]


def test_a_piece_can_never_nest_inside_itself():
    assert nest([seg(90, ["segment:90"])]) == [("segment:90", ())]


def test_nesting_is_ONE_level_deep():
    # A piece of a piece draws under the middle one and the middle one still
    # draws at the top -- never a group inside a group, which is what keeps
    # PracticeLog's own renderer from recursing.
    rows = nest([star(7, 4), seg(90, ["star:7:4"]), seg(91, ["segment:90"])])
    assert rows == [("star:7:4", ("segment:90",))]


# --- interaction with hasEarnedACard ---------------------------------------

def test_a_parent_that_earned_nothing_still_gets_a_card_for_its_child():
    # Practising ONLY the piece must not orphan it back to the top level on
    # the very run that proves the association.
    rows = nest([star(7, 4), seg(90, ["star:7:4"])],
                earned_keys=["segment:90"])
    assert rows == [("star:7:4", ("segment:90",))]


def test_a_piece_that_earned_nothing_is_dropped_rather_than_nested():
    rows = nest([star(7, 4), seg(90, ["star:7:4"])],
                earned_keys=["star:7:4"])
    assert rows == [("star:7:4", ())]


def test_a_pair_that_earned_nothing_at_all_leaves_no_card_behind():
    assert nest([star(7, 4), seg(90, ["star:7:4"])], earned_keys=[]) == []

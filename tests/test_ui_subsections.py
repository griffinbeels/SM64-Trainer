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

def test_an_AREA_parented_piece_stays_top_level():
    # Round 22 item 5: an `area:` parent is a PLACE, names no section at all,
    # so a castle movement "works the same as today, as a standalone top level
    # practice log entry."
    assert nest([seg(90, ["area:6:3"])]) == [("segment:90", ())]


def test_a_STAR_parented_piece_with_no_parent_card_is_not_shown_at_all():
    # Round 28, and round 22's "promote it rather than hide it" reversed on his
    # ruling: "this type of segment... should never be a top level entry in the
    # practice log, because it's associated with a specific star... (But in
    # this case, since I didn't select Hot Foot It, it shouldn't appear at all,
    # because we didn't select or grab that star)."
    assert nest([seg(90, ["star:7:4"])]) == []


def test_a_piece_reappears_the_moment_its_parent_earns_a_card():
    rows = nest([star(7, 4), seg(90, ["star:7:4"])])
    assert rows == [("star:7:4", ("segment:90",))]


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
# Round 32 (2026-08-10) stopped `earned` gating NESTING MEMBERSHIP at all --
# "the subsections should always be visible inside of the parent practice
# log... we don't wait for the subsection to trigger for it to have its own
# card... It just starts out empty in a new session." `earned` still gates
# two OTHER questions below: whether a card-less section is promoted to the
# top level at all (round 28's rule, untouched), and -- via the rule right
# above -- whether a parent that earned nothing itself still gets a card
# because it has a nesting child (unaffected in shape, just no longer able
# to be denied a child for the child's OWN lack of history).

def test_a_parent_that_earned_nothing_still_gets_a_card_for_its_child():
    # Practising ONLY the piece must not orphan it back to the top level on
    # the very run that proves the association.
    rows = nest([star(7, 4), seg(90, ["star:7:4"])],
                earned_keys=["segment:90"])
    assert rows == [("star:7:4", ("segment:90",))]


def test_a_piece_that_earned_nothing_still_nests():
    # Before round 32 this piece was dropped from `nested` entirely (the
    # nesting loop's own `earned` check) and fell through to round 28's
    # "no card" rule, same as if it had no parent at all. Now it nests
    # exactly like an earned one -- an unpractised piece is not a hidden
    # piece any more.
    rows = nest([star(7, 4), seg(90, ["star:7:4"])],
                earned_keys=["star:7:4"])
    assert rows == [("star:7:4", ("segment:90",))]


def test_a_pair_that_earned_nothing_at_all_still_nests():
    # Both this star and its piece are pure synthetic input: server-side, a
    # star with no attempts and no target is never `seen` in the first
    # place, and every segment the server publishes is earned by
    # construction (attempts, armed, or being the target) except a piece
    # admitted purely because ITS parent has a card -- and a piece can never
    # itself be a nesting parent (nesting is one level). So this exact
    # "neither earned anything" pair cannot occur for a real parent/child in
    # the shipped app; it exists to pin what the pure function does anyway.
    # Before round 32 this returned `[]` -- an all-unearned pair left no
    # card behind at all. Now the piece nests unconditionally, and the
    # PARENT then earns a card off having a nesting child (the rule above),
    # regardless of whether that child brought any history of its own.
    rows = nest([star(7, 4), seg(90, ["star:7:4"])], earned_keys=[])
    assert rows == [("star:7:4", ("segment:90",))]


# --- the rule the SELECTOR shares with this module --------------------------
# Round 30, 2026-08-09. `isPiece` was extracted so the practice log and the
# quick-select row stop answering "is this a piece" separately -- the star row
# asked `parents.length > 0` over its own level and the castle segment row
# asked whether the parent was offered in the same subarea, so a piece of a
# castle MOVEMENT was a badge on neither and a loose cell instead. Driven here
# in its own right because both callers now depend on it and only one of them
# has a render gate.


def is_piece(row):
    script = (
        f"import {{ isPiece }} from {SUBSECTIONS_JS.as_uri()!r};\n"
        f"console.log(JSON.stringify(isPiece({json.dumps(row)})));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            encoding="utf-8")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def pieces_for(rows, parent_key):
    script = (
        f"import {{ piecesFor }} from {SUBSECTIONS_JS.as_uri()!r};\n"
        f"const out = piecesFor({json.dumps(rows)}, {json.dumps(parent_key)});\n"
        "console.log(JSON.stringify(out.map((r) => r.segment_id)));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            encoding="utf-8")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_a_star_parent_makes_it_a_piece():
    assert is_piece(seg(90, ["star:7:4"])) is True


def test_a_segment_parent_makes_it_a_piece():
    """His BLJs case: the parent is a castle movement, not a star."""
    assert is_piece(seg(92, ["segment:4"])) is True


def test_an_area_parent_does_not():
    """An area is a PLACE -- it names no cell and no card, so such a row is an
    ordinary top-level one (round 22 item 5, unchanged)."""
    assert is_piece(seg(90, ["area:6:2"])) is False


def test_no_parents_at_all_does_not():
    assert is_piece(seg(90)) is False


def test_a_self_reference_is_not_a_parent():
    """The guard exists to stop infinite nesting, never to hide a row."""
    assert is_piece(seg(90, ["segment:90"])) is False


def test_a_selector_row_is_read_the_same_way_as_a_log_section():
    """A `segment_targets` row carries no `kind`, so the self-key branch has
    to read `segment_id` directly -- keying it off `entityKey` alone would
    call every selector row a star and quietly break the self-reference
    guard on exactly the surface this rule was extracted for."""
    assert is_piece({"segment_id": 90, "parents": ["segment:90"]}) is False
    assert is_piece({"segment_id": 90, "parents": ["segment:4"]}) is True


def test_pieces_for_finds_every_row_naming_that_parent():
    """Round 20's plural parents: a piece serving two stars is claimed by
    both, so this is a filter over `parents`, never a first-match lookup."""
    rows = [seg(90, ["star:7:4", "star:7:5"]), seg(91, ["star:7:5"]),
            seg(92, ["segment:4"]), seg(93)]
    assert pieces_for(rows, "star:7:5") == [90, 91]
    assert pieces_for(rows, "segment:4") == [92]
    assert pieces_for(rows, "star:1:0") == []


def test_pieces_for_keeps_a_disabled_piece():
    """The badge is the only control that turns one back on, so filtering a
    disabled piece out here would make the switch one-way."""
    assert pieces_for([seg(90, ["star:7:4"], enabled=False)], "star:7:4") == [90]

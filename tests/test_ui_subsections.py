# tests/test_ui_subsections.py
"""Progressive disclosure: which entities the selector draws.

`ui/subsections.js` is import-free so node can drive the REAL module, the same
arrangement tests/test_cross_language_parity.py uses -- a Python
reimplementation of the rule would be a second copy of exactly the thing this
feature exists to have one of.
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

# One star, one of its subsections, a castle movement, one of ITS subsections,
# and an unrelated top-level segment. Covers both parent kinds at once, which
# is the point of the parents field being one shape. Plural since round 20 --
# [] is top-level, and one piece may list several parents.
CORPUS = [
    {"key": "star:2:1", "parents": []},
    {"key": "segment:90", "parents": ["star:2:1"]},
    {"key": "segment:12", "parents": []},
    {"key": "segment:91", "parents": ["segment:12"]},
    {"key": "segment:13", "parents": []},
]


def call(fn: str, *args):
    script = (f"import {{ {fn} }} from {SUBSECTIONS_JS.as_uri()!r};\n"
              f"console.log(JSON.stringify({fn}("
              + ", ".join(json.dumps(a) for a in args) + ")));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def keys(rows):
    return [r["key"] for r in rows]


# -- the two states -----------------------------------------------------------

def test_nothing_selected_draws_only_top_level_entities():
    assert keys(call("visibleEntities", CORPUS, None)) == [
        "star:2:1", "segment:12", "segment:13"]


def test_a_subsection_is_never_loose_in_the_row():
    """The crowding progressive disclosure exists to solve: a star can own
    many subsections, and the selector's job is that you never hunt."""
    drawn = call("visibleEntities", CORPUS, None)
    assert all(not row["parents"] for row in drawn)


def test_selecting_a_star_draws_it_and_its_subsections_only():
    assert keys(call("visibleEntities", CORPUS, "star:2:1")) == [
        "star:2:1", "segment:90"]


def test_selecting_a_castle_movement_works_identically():
    """The same field carries both parent kinds, so this needs no mechanism
    of its own -- "castle movement sometimes is a specific subsection of
    castle movement rather than movement between courses only" (task 0087)."""
    assert keys(call("visibleEntities", CORPUS, "segment:12")) == [
        "segment:12", "segment:91"]


def test_selecting_something_with_no_subsections_leaves_the_row_alone():
    """No children, no expansion. Hiding the other options is only worth doing
    when there is something to hide them FOR -- and the first version of this
    rule collapsed a whole course's row down to the one ordinary star you had
    just picked, with no gesture anywhere that brought the other six back."""
    assert keys(call("visibleEntities", CORPUS, "segment:13")) == [
        "star:2:1", "segment:12", "segment:13"]


# -- the family, not the selection --------------------------------------------

def test_selecting_a_subsection_keeps_its_parent_and_its_siblings():
    """The dead end this rule exists to avoid, reached by using the feature
    correctly: practice one subsection and the row would collapse to that one
    cell, with no way back to the parent or to the others."""
    siblings = CORPUS + [{"key": "segment:93", "parents": ["star:2:1"]}]
    assert keys(call("visibleEntities", siblings, "segment:90")) == [
        "star:2:1", "segment:90", "segment:93"]


def test_a_selected_subsection_still_reads_as_expanded():
    assert call("isExpanded", CORPUS, "segment:90") is True


def test_the_family_root_of_a_top_level_entity_is_itself():
    assert call("familyRoot", CORPUS, "star:2:1") == "star:2:1"
    assert call("familyRoot", CORPUS, None) is None


def test_the_family_root_of_a_subsection_is_its_parent():
    assert call("familyRoot", CORPUS, "segment:90") == "star:2:1"


def test_an_off_list_key_is_its_own_root():
    """We cannot read a parent off an entity we do not have, and this is what
    keeps the two off-list fallbacks below working."""
    assert call("familyRoot", CORPUS, "star:9:9") == "star:9:9"


# -- one level deep -----------------------------------------------------------

def test_a_subsection_of_a_subsection_is_not_offered():
    """One row cannot show two levels of nesting without becoming the
    scrolling hunt this exists to prevent."""
    nested = CORPUS + [{"key": "segment:92", "parents": ["segment:90"]}]
    assert keys(call("visibleEntities", nested, "star:2:1")) == [
        "star:2:1", "segment:90"]


# -- degenerate inputs --------------------------------------------------------

def test_an_empty_list_draws_nothing():
    assert call("visibleEntities", [], None) == []
    assert call("visibleEntities", [], "star:2:1") == []


def test_a_target_absent_from_the_list_falls_back_to_the_top_level():
    """The target may be practicable somewhere the player is not standing, or
    may have just been deleted. Returning an EMPTY row there would blank the
    selector; falling back is what keeps something on screen."""
    assert keys(call("visibleEntities", CORPUS, "star:9:9")) == [
        "star:2:1", "segment:12", "segment:13"]


def test_an_absent_parent_still_shows_its_children():
    """The reverse case, and the reason the fallback checks BOTH: a target
    off-list whose subsections are here must still expand, or selecting it
    silently collapses the row under the user."""
    orphaned = [{"key": "segment:90", "parents": ["star:2:1"]},
                {"key": "segment:13", "parents": []}]
    assert keys(call("visibleEntities", orphaned, "star:2:1")) == ["segment:90"]


# -- the expanded-state signal ------------------------------------------------

def test_expanded_is_false_when_the_target_has_no_subsections():
    """Selecting something with no subsections must look exactly like the
    plain row -- not like a collapsed row with one item in it."""
    assert call("isExpanded", CORPUS, "segment:13") is False
    assert call("isExpanded", CORPUS, None) is False


def test_expanded_is_true_when_it_does():
    assert call("isExpanded", CORPUS, "star:2:1") is True


def test_subsections_of_returns_children_without_the_parent():
    assert keys(call("subsectionsOf", CORPUS, "star:2:1")) == ["segment:90"]
    assert call("subsectionsOf", CORPUS, "segment:13") == []


# -- plural parents (round 20 item 1) -----------------------------------------
# His example pair: LLL's Hot-Foot-It and Elevator Tour both own "Volcano
# Entry", so the piece appears under EACH star's expansion.

SHARED = [
    {"key": "star:22:0", "parents": []},
    {"key": "star:22:1", "parents": []},
    {"key": "segment:95", "parents": ["star:22:0", "star:22:1"]},
]


def test_a_shared_piece_appears_under_each_parents_expansion():
    assert keys(call("visibleEntities", SHARED, "star:22:0")) == [
        "star:22:0", "segment:95"]
    assert keys(call("visibleEntities", SHARED, "star:22:1")) == [
        "star:22:1", "segment:95"]


def test_selecting_a_shared_piece_keeps_the_family_you_drilled_in_from():
    """preferredRoot breaks the tie: drilling in from the SECOND parent must
    not snap the row to the first one's family."""
    assert call("familyRoot", SHARED, "segment:95", "star:22:1") == "star:22:1"
    assert keys(call("visibleEntities", SHARED, "segment:95",
                     "star:22:1")) == ["star:22:1", "segment:95"]


def test_a_shared_piece_with_no_preference_uses_its_primary_parent():
    assert call("familyRoot", SHARED, "segment:95") == "star:22:0"


def test_a_stale_preference_falls_back_to_the_primary():
    """A preferredRoot that is not one of the piece's parents (the row was
    showing some other family) must not leak in as the root."""
    assert call("familyRoot", SHARED, "segment:95", "segment:13") == "star:22:0"


def test_subsections_of_counts_shared_membership():
    assert keys(call("subsectionsOf", SHARED, "star:22:1")) == ["segment:95"]

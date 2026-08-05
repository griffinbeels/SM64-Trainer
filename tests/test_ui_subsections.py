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
# is the point of the parent field being one shape.
CORPUS = [
    {"key": "star:2:1", "parent": None},
    {"key": "segment:90", "parent": "star:2:1"},
    {"key": "segment:12", "parent": None},
    {"key": "segment:91", "parent": "segment:12"},
    {"key": "segment:13", "parent": None},
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
    assert all(row["parent"] is None for row in drawn)


def test_selecting_a_star_draws_it_and_its_subsections_only():
    assert keys(call("visibleEntities", CORPUS, "star:2:1")) == [
        "star:2:1", "segment:90"]


def test_selecting_a_castle_movement_works_identically():
    """The same field carries both parent kinds, so this needs no mechanism
    of its own -- "castle movement sometimes is a specific subsection of
    castle movement rather than movement between courses only" (task 0087)."""
    assert keys(call("visibleEntities", CORPUS, "segment:12")) == [
        "segment:12", "segment:91"]


def test_selecting_something_with_no_subsections_draws_just_it():
    assert keys(call("visibleEntities", CORPUS, "segment:13")) == ["segment:13"]


# -- one level deep -----------------------------------------------------------

def test_a_subsection_of_a_subsection_is_not_offered():
    """One row cannot show two levels of nesting without becoming the
    scrolling hunt this exists to prevent."""
    nested = CORPUS + [{"key": "segment:92", "parent": "segment:90"}]
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
    orphaned = [{"key": "segment:90", "parent": "star:2:1"},
                {"key": "segment:13", "parent": None}]
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

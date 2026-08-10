"""A Story's `setup` script must not query a GENERIC, shared class name
unscoped -- the mechanism, not just the one fix, for the bug fix round 1 of
Task 4 (spec 2026-08-07-library-page) found.

The Library tab stays mounted with `display:none` once visited (`library.js`'s
own docstring -- the same trick Compare uses) and its own search box reuses
the pre-existing, generic `.library-search` class the Segments editor's own
setup script ALSO queried, unscoped. `PROJECT.stories` share ONE page across a
whole sweep with no reload, so once a sweep pass visited the Library tab, the
Segments-editor story's `document.querySelector('.library-search')` silently
matched the Library tab's own HIDDEN box first (DOM order) instead of the
Segments tab's real one -- typing into a box nobody could see, forever, and
surfacing as `RuntimeError: uilab story 'segments-editor'... scope selector
matched nothing: .segbuilder`, a confusing downstream symptom rather than a
clear one.

This project's rule (CLAUDE.md, the "how do we ENFORCE" line): the deliverable
for a found class of bug is the check that fails, never the principle alone.
`GENERIC_STORY_CLASSES` is the explicit, consciously-extended list -- same
convention as `test_ui_cap_names.py::RAW_TIER_EXPRESSIONS` and
`test_ui_picker_parity.py::DOMAIN_VOCAB_MARKERS`: a future author who gives
two unrelated surfaces the same class name adds a row here, and the guard
catches the NEXT `.library-search`-shaped mistake immediately rather than
after a confusing sweep failure."""
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab_project import STORIES  # noqa: E402

# Class names known to have been reused by more than one UNRELATED surface in
# this app. `.library-search` was the proven case (fix round 1): segments.js's
# own segment-library filter box and librarytarget.js's runner-search box
# shared the name, and only ONE of the two surfaces it could appear on
# unmounts when you leave it. The final whole-branch review (minor finding:
# shared class) gave librarytarget.js's box its own `.library-target-search`
# class instead, so the two can no longer collide -- `.library-search` stays
# in this registry anyway, as a standing warning against giving it a second
# unrelated consumer again.
GENERIC_STORY_CLASSES = (
    "library-search",
)


def _unscoped_generic_queries(setup_script: str, generic_classes) -> list:
    """Every `document.querySelector(All)?` call in `setup_script` whose
    selector's FIRST simple selector is a bare generic class -- i.e. it would
    match the first such element ANYWHERE on the page, not scoped to
    whatever state this Story's own setup just produced."""
    offenders = []
    for match in re.finditer(
            r"document\.querySelector(?:All)?\(\s*['\"]([^'\"]+)['\"]", setup_script):
        selector = match.group(1).strip()
        first_clause = selector.split()[0] if selector else ""
        if first_clause in {f".{cls}" for cls in generic_classes}:
            offenders.append(selector)
    return offenders


def test_no_story_setup_queries_an_unscoped_generic_selector():
    for story in STORIES:
        if not story.setup:
            continue
        offenders = _unscoped_generic_queries(story.setup, GENERIC_STORY_CLASSES)
        assert not offenders, (
            f"Story {story.name!r}'s setup queries {offenders} unscoped -- "
            f"{offenders[0]!r} is in GENERIC_STORY_CLASSES because more than "
            "one unrelated surface uses that class name, and PROJECT.stories "
            "share one page across a whole sweep, so an unscoped query can "
            "silently grab a DIFFERENT (possibly hidden) surface's element "
            "instead of this story's own. Scope it to this story's own root "
            "or state, the way `.segments-page .library-search` does.")


def test_the_guard_can_still_fail():
    # A bare, unscoped query on a generic class is caught.
    assert _unscoped_generic_queries(
        "const x = document.querySelector('.library-search');",
        GENERIC_STORY_CLASSES) == [".library-search"]

    # Scoped to an ancestor -- the actual fix shape -- is not flagged.
    assert _unscoped_generic_queries(
        "const x = document.querySelector('.segments-page .library-search');",
        GENERIC_STORY_CLASSES) == []

    # A class not in the registry is not this guard's business (it may still
    # be a real hazard, but it has not been named yet -- the same "consciously
    # extended list" shape as RAW_TIER_EXPRESSIONS/DOMAIN_VOCAB_MARKERS).
    assert _unscoped_generic_queries(
        "const x = document.querySelector('.some-other-box');",
        GENERIC_STORY_CLASSES) == []

    # querySelectorAll is covered too, not just querySelector.
    assert _unscoped_generic_queries(
        "document.querySelectorAll('.library-search').forEach((el) => {});",
        GENERIC_STORY_CLASSES) == [".library-search"]

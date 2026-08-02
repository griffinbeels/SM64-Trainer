"""`ui/uilog.js` reads the RENDERED page, so its one failure mode is a class
rename — and that failure is SILENT: the log simply goes empty, which is
indistinguishable from "nothing was on screen", the exact answer it exists to
give. That is the worst shape an instrument can fail in, and no unit test of
the observer itself can catch it, because the observer would still be correct
about a page that no longer exists.

So this pins the JOIN instead: every class the reader looks for must actually
be rendered by the components it claims to observe. A rename now fails here
rather than in three weeks, in a log full of empty rows.

Source-scan, like the other tests over these files (none of them is
import-free). `strip_comments` is what stops a class mentioned only in prose
from satisfying the check — the trap `.claude/rules/ui-core.md` names, and one
five guards in this repo have already fallen into.
"""
import re
from pathlib import Path

import pytest

from source_scan import strip_comments

UI = Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "ui"
READER = UI / "uilog.js"
# Where each class the reader looks for is allowed to be rendered. A class
# found in NONE of these is the drift this test exists for.
RENDERERS = (UI / "components" / "stagebanner.js",
             UI / "components" / "practicecell.js",
             UI / "components" / "practice.js")


def _reader_source() -> str:
    source = strip_comments(READER.read_text(encoding="utf-8"))
    match = re.search(r"export function readSelector.*?^}\n.*?"
                      r"export function readTargets.*?^}", source, re.S | re.M)
    assert match, "the two reader functions are not both in uilog.js any more"
    return match.group(0)


def classes_the_reader_needs(source: str) -> set[str]:
    """Every class token the readers depend on: the ones inside querySelector
    strings AND the one asked for by `classList.contains`, which is a separate
    syntax and would otherwise be missed."""
    needed = set()
    for selector in re.findall(r'querySelector(?:All)?\("([^"]+)"\)', source):
        needed.update(re.findall(r"\.([A-Za-z0-9_-]+)", selector))
    needed.update(re.findall(r'classList\.contains\("([A-Za-z0-9_-]+)"\)', source))
    return needed


def _renderer_source() -> str:
    """The observed components' CODE, comments stripped.

    Deliberately a flat token search rather than a parse of `class="…"`: a
    class attribute here is an htm template (`class="starcell ${active ?
    "active-star" : ""}"`), so an attribute-shaped regex stops at the first
    inner quote and silently misses every conditionally-applied class —
    including `active-star`, which is the single most load-bearing token the
    reader needs. The guard's job is to catch a RENAME, and a rename removes
    the token from the file outright."""
    return "\n".join(strip_comments(path.read_text(encoding="utf-8"))
                     for path in RENDERERS)


def test_every_class_the_reader_looks_for_is_actually_rendered():
    needed = classes_the_reader_needs(_reader_source())
    assert needed, "the extractor found no selectors — it has stopped working"
    rendered = _renderer_source()
    missing = sorted(name for name in needed
                     if not re.search(rf"\b{re.escape(name)}\b", rendered))
    assert not missing, (
        f"ui/uilog.js reads classes nothing renders: {missing}. The UI log "
        f"would go silently empty for those — rename them in the reader, or "
        f"add the new renderer to RENDERERS.")


def test_the_reader_covers_both_surfaces_griffin_asked_for():
    """His ask named two things by pointing at them: the star/segment selector
    and the active target card. Losing either half is a quiet regression — the
    log keeps filling, just not with the half you needed."""
    needed = classes_the_reader_needs(_reader_source())
    assert "stagebanner" in needed and "starcell" in needed, "the selector half"
    assert "objective-card" in needed, "the active-target half"
    assert "active-star" in needed, (
        "which cell is HIGHLIGHTED is half of what the reports are about")


@pytest.mark.parametrize("sample,expected", [
    ('querySelector(".stagebanner")', {"stagebanner"}),
    ('querySelectorAll(".objective-name h2")', {"objective-name"}),
    ('classList.contains("active-star")', {"active-star"}),
    ('// querySelector(".only-a-comment")', set()),
])
def test_the_guard_can_still_fail(sample, expected):
    """Probed in both directions, per the norm in ui-core.md: a raw substring
    scan cannot tell code from prose, and five guards in this repo were once
    green because a COMMENT mentioned the thing they were checking for."""
    assert classes_the_reader_needs(strip_comments(sample)) == expected

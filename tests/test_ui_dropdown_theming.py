"""A dropdown's list must stay the browser's to colour.

The popup of a native <select> is drawn by the browser, not by the page, and
Chromium makes two decisions from the page's CSS that are invisible until
someone opens a list (live audit 2026-07-25, both of them wrong at once):

* it themes the popup off the SELECT's own computed background — a transparent
  select gets a WHITE popup;
* it paints the highlighted row light-blue and picks dark text for it, but only
  when the author has not named an option colour. Ours did, so every dropdown
  showed light text on light blue.

Neither shows up in a screenshot of the page, in a render of the component, or
in any behavioural test — the list has to be open, in a browser, for a human to
see it. Hence source guards. Both are expressed as functions of the stylesheet
so `test_the_guards_can_still_fail` can feed them a comment and real code on
every run (tests/source_scan.py explains why that matters).
"""
import re
from pathlib import Path

from source_scan import strip_comments

INDEX_HTML = (Path(__file__).resolve().parent.parent / "src" / "sm64_events"
              / "ui" / "index.html").read_text(encoding="utf-8")

_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
# `select` as an ELEMENT: `.context-select` and `--selector-height` are not it.
_SELECT_ELEMENT = re.compile(r"(?<![\w.\-])select(?![\w\-])")


def _declarations(body: str) -> dict:
    return {part.split(":", 1)[0].strip(): part.split(":", 1)[1].strip()
            for part in body.split(";") if ":" in part}


def option_text_colour(css: str) -> list:
    """Colours the stylesheet forces on <option> — each one breaks a row."""
    forced = []
    for selector, body in _RULE.findall(strip_comments(css)):
        if selector.strip() != "option":
            continue
        declared = _declarations(body)
        forced += [f"{prop}: {value}" for prop, value in declared.items()
                   if prop == "color"]
    return forced


def transparent_selects(css: str) -> list:
    """Select rules that would hand Chromium a white popup."""
    offenders = []
    for selector, body in _RULE.findall(strip_comments(css)):
        if not _SELECT_ELEMENT.search(selector):
            continue
        declared = _declarations(body)
        for prop in ("background", "background-color"):
            if declared.get(prop, "").startswith("transparent"):
                offenders.append(f"{selector.strip()} {{ {prop}: transparent }}")
    return offenders


def test_the_option_row_keeps_its_background_and_not_its_colour():
    rules = [body for selector, body in _RULE.findall(strip_comments(INDEX_HTML))
             if selector.strip() == "option"]
    assert rules, "the `option` rule is gone — the popup goes Chromium grey"
    assert any("background-color" in _declarations(body) for body in rules), (
        "no background-color on <option>: the list stops being navy")
    assert option_text_colour(INDEX_HTML) == [], (
        "an author colour on <option> survives into Chromium's highlighted "
        "row, which it paints light-blue expecting dark text")


def test_no_select_is_transparent():
    # Two selects LOOK transparent: the header cards (hidden with opacity, so
    # the element keeps the shell's dark background) and the sort control
    # (paints its wrapper's own fill). Either one going `transparent` again
    # turns its popup white.
    assert transparent_selects(INDEX_HTML) == []


def test_the_guards_can_still_fail():
    assert option_text_colour("/* option { color: var(--text) } */") == []
    assert option_text_colour("option { background-color: #0d1928; }") == []
    assert option_text_colour("option { color: var(--text); }") == ["color: var(--text)"]

    assert transparent_selects("/* .sort-control select { background: transparent } */") == []
    assert transparent_selects(".context-select { background: transparent; }") == []
    assert transparent_selects("select { background: transparent; }") == [
        "select { background: transparent }"]

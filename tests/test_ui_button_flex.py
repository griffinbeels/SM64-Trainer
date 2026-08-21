# tests/test_ui_button_flex.py
"""Every button flex rule STATES its justify-content.

Chromium gives a <button> a UA default of `justify-content: center` that a
bare `display:flex` silently inherits — so a row-shaped button's contents
drift toward the middle and every cap/icon lands at a different x depending
on its label's length. This shipped TWICE before it became a rule:
`.library-result` (2026-08-10, every row floating mid-card) and
`.std-tier-btn` (2026-08-14, "we need to make sure the symbols are aligned
with each other — right now without alignment, this looks messy").

A comment at each site did not stop the second instance, so this is the
mechanism: any stylesheet rule whose SUBJECT is a class that rides a
<button> in the components and that declares display:flex/inline-flex must
also declare justify-content — naming the intent, whichever it is. The UA
default becomes unwritable rather than discouraged.
"""
import re
from pathlib import Path

from source_scan import strip_comments

UI = Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "ui"


def button_classes(js_sources) -> set:
    """Every class name that ever appears in a <button class="..."> literal.
    Interpolated class fragments contribute their literal words too — a
    partial match errs toward checking more rules, never fewer."""
    found = set()
    for source in js_sources:
        for match in re.finditer(r"<button[^>]*?class=\"([^\"]*)\"", source):
            for cls in re.findall(r"[\w-]+", match.group(1)):
                found.add(cls)
    return found


def undeclared_flex_button_rules(css: str, on_buttons: set) -> list:
    """Rules whose subject is a button class, declaring flex without
    justify-content. The subject is each selector's LAST compound; pseudo
    variants (:hover re-declarations) are exempt — the base rule is the one
    that owes the declaration.

    Comments are stripped before scanning: a rule's own explanatory comment
    may quote the CSS it is overriding (`.library-runner-link` describes the
    global `button` rule's `display: inline-flex` in prose right beside its
    own `display: inline`), and a bare `display:\\s*flex` search cannot tell
    that quote from a real declaration."""
    css = strip_comments(css)
    offenders = []
    for rule in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selector, body = rule.group(1).strip(), rule.group(2)
        if not re.search(r"display\s*:\s*(inline-)?flex", body):
            continue
        if "justify-content" in body:
            continue
        for sel in selector.split(","):
            compound = sel.strip().split()[-1] if sel.strip() else ""
            if ":" in compound.replace("::", ""):
                continue
            if set(re.findall(r"\.([\w-]+)", compound)) & on_buttons:
                offenders.append(compound)
                break
    return offenders


def test_every_button_flex_rule_states_its_justify_content():
    js_sources = [path.read_text(encoding="utf-8")
                  for path in UI.rglob("*.js")]
    css = (UI / "index.html").read_text(encoding="utf-8")
    offenders = undeclared_flex_button_rules(css, button_classes(js_sources))
    assert not offenders, (
        f"button flex rules relying on Chromium's silent centering: "
        f"{offenders} — declare justify-content (center to keep the current "
        "look, flex-start for a row that anchors left); the UA default is "
        "how .library-result and .std-tier-btn both shipped misaligned")


def test_the_guard_can_still_fail():
    """Calibration: a synthetic violation and a synthetic pass, so a regex
    drift that matches nothing cannot rot this guard green forever."""
    buttons = button_classes(['<button class="probe-btn">'])
    assert buttons == {"probe-btn"}
    bad = ".probe-btn{display:flex;gap:4px}"
    good = ".probe-btn{display:flex;justify-content:flex-start}"
    hover = ".probe-btn:hover{display:flex}"
    assert undeclared_flex_button_rules(bad, buttons) == [".probe-btn"]
    assert undeclared_flex_button_rules(good + hover, buttons) == []


def test_a_comment_quoting_flex_is_not_a_declaration():
    """Regression for the false positive this guard shipped with: a rule's
    own comment may quote the CSS it is overriding (`display: inline-flex`)
    right beside a real, different declaration (`display: inline`) — prose,
    not a second rule, and must not be read as one."""
    buttons = button_classes(['<button class="probe-btn">'])
    prose = (".probe-btn{/* overrides the global button's "
              "display: inline-flex */ display: inline;}")
    assert undeclared_flex_button_rules(prose, buttons) == []

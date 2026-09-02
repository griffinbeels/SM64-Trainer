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


def strip_css_comments(css: str) -> str:
    """Comments out, everything else byte-for-byte.

    A raw text scan cannot tell CODE from PROSE, and this guard proved it on
    2026-08-21: a comment inside `.input-bar` explaining that *the shell's own
    button rule* declares `display: inline-flex` made the guard read that rule
    as declaring flex itself, and it reported a rule whose own body says
    `display: block`. The project already carries this lesson for Python
    (`tests/source_scan.py`); CSS needed its own.

    Replacing with a space rather than nothing keeps a comment from fusing the
    tokens on either side of it into one.
    """
    return re.sub(r"/\*.*?\*/", " ", css, flags=re.S)


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
    for rule in re.finditer(r"([^{}]+)\{([^{}]*)\}", strip_css_comments(css)):
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


def test_a_comment_ABOUT_flex_does_not_count_as_declaring_it():
    """Both directions, because this guard has been wrong in one of them.

    A comment explaining that some OTHER rule declares flex is prose, and
    reading it as code reported a rule whose own body says `display: block`
    (2026-08-21). The second half is the calibration: a real declaration
    sitting beside such a comment must still be caught, or the fix would have
    bought silence instead of accuracy.
    """
    buttons = button_classes(['<button class="probe-btn">'])
    prose = ".probe-btn{display:block;/* the UA rule uses display:flex */}"
    assert undeclared_flex_button_rules(prose, buttons) == []
    both = ".probe-btn{/* about display:flex */ display:flex; gap:4px}"
    assert undeclared_flex_button_rules(both, buttons) == [".probe-btn"]


def test_a_comment_quoting_flex_is_not_a_declaration():
    """Regression for the false positive this guard shipped with: a rule's
    own comment may quote the CSS it is overriding (`display: inline-flex`)
    right beside a real, different declaration (`display: inline`) — prose,
    not a second rule, and must not be read as one."""
    buttons = button_classes(['<button class="probe-btn">'])
    prose = (".probe-btn{/* overrides the global button's "
              "display: inline-flex */ display: inline;}")
    assert undeclared_flex_button_rules(prose, buttons) == []


# ---- the same global rule's OTHER half: min-height ----------------------
#
# `justify-content` is not the only thing the global `button` rule imposes.
# It also sets `min-height: 34px; display: inline-flex`, and the usual
# text-reset (`appearance:none; background:none; border:none; padding:0;
# font:inherit`) undoes NEITHER. A <span> that becomes a <button> therefore
# grows its own line box, silently.
#
# Measured 2026-08-20 (ranked-leaderboard, whole-branch review finding H1):
# a runner's name became a button inside the Library's EXISTING ladder list —
# a surface that branch was forbidden to change — and `.library-plain-entry`
# went 24.75px -> 41.75px, `.library-example-meta` 40.38px -> 48.38px. A band
# holds dozens of plain rows, so the ladder grew ~70% taller and nothing
# caught it: the responsive sweep hunts defects rather than growth, and the
# ladder's own geometry test measures with divisions COLLAPSED.

ZERO_PADDING = re.compile(r"padding\s*:\s*0(?:px)?\s*(?:;|$)")


def text_shaped_button_rules(css: str, on_buttons: set) -> list:
    """Rules that strip a button back to inline text without neutralising the
    global rule's `min-height`.

    The discriminator is the full text reset — background AND border AND ALL
    padding gone — because that is what "this should flow as text" looks like
    in declarations. Deliberately NARROW, and the exemptions are the reason:

      * a rule declaring its own `display` has taken control of its box
        (`.std-tier-btn`);
      * a rule keeping its background, border or padding is a chip-shaped
        control that WANTS a button's height (`.version-switch-seg`,
        `.library-mode-seg`);
      * a partial padding reset keeps horizontal padding, so the element is
        still box-shaped (`.candx`, `.vidbtn`).

    Those last two sit just outside this guard and have not been judged by
    rendering. Widening to cover them without judging them first would trade a
    guard that fires reliably for one that gets switched off.
    """
    css = strip_comments(css)
    offenders = []
    for rule in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selector, body = rule.group(1).strip(), rule.group(2)
        if not (re.search(r"background\s*:\s*(none|transparent)", body)
                and re.search(r"border\s*:\s*none", body)
                and ZERO_PADDING.search(body)):
            continue
        if re.search(r"display\s*:", body) or re.search(r"min-height\s*:", body):
            continue
        for sel in selector.split(","):
            compound = sel.strip().split()[-1] if sel.strip() else ""
            if ":" in compound.replace("::", ""):
                continue
            if set(re.findall(r"\.([\w-]+)", compound)) & on_buttons:
                offenders.append(compound)
                break
    return offenders


def test_a_button_reset_to_inline_text_states_its_min_height():
    js_sources = [path.read_text(encoding="utf-8")
                  for path in UI.rglob("*.js")]
    css = (UI / "index.html").read_text(encoding="utf-8")
    offenders = text_shaped_button_rules(css, button_classes(js_sources))
    assert not offenders, (
        f"button rules reset to inline text without neutralising the global "
        f"`min-height: 34px`: {offenders} — declare `min-height: 0` (and a "
        "non-flex `display`) or the element grows its own row; that is how "
        "the Library's ladder rows silently went 24.75px -> 41.75px")


def test_the_min_height_guard_can_still_fail():
    """Calibration in both directions, so neither a regex drift nor an
    over-broad match can rot this one. The failing case is the real shape of
    the 2026-08-20 regression; the passing cases are the three kinds of
    button this guard must leave alone."""
    buttons = button_classes(['<button class="probe-btn">'])
    text_reset = (".probe-btn{appearance:none;background:none;border:none;"
                  "padding:0;font:inherit}")
    assert text_shaped_button_rules(text_reset, buttons) == [".probe-btn"]
    fixed = text_reset[:-1] + ";min-height:0;display:inline}"
    assert text_shaped_button_rules(fixed, buttons) == []
    # a chip-shaped control keeps its border/background — not this guard's business
    chip = (".probe-btn{appearance:none;border:1px solid red;"
            "background:grey;padding:.3rem .8rem;font:inherit}")
    assert text_shaped_button_rules(chip, buttons) == []
    # a rule that declares its own display has taken control of its box
    owns_box = (".probe-btn{display:flex;background:transparent;border:none;"
                "padding:0}")
    assert text_shaped_button_rules(owns_box, buttons) == []

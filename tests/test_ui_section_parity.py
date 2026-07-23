# tests/test_ui_section_parity.py
"""Star and segment practice cards must offer the SAME features.

Why this exists: the two cards are hand-written siblings (StarSection /
SegmentSection in ui/components/practice.js) — deliberately not one
generalized component, because their data differs (IGT vs RTA-only, links,
broken-definition handling).  The cost of that choice is that a feature added
to one silently misses the other: the strategy picker shipped on stars in
v1 and was still missing from segments months later (user-reported
2026-07-23, "the active segment card has no strat dropdown").

This test makes the omission loud.  It compares the set of sub-components
each card renders; any new asymmetry fails until it is either fixed or
recorded in ONLY_IN_* with a reason.  Prop-level differences are out of
scope — this catches the whole-feature-missing class, which is the one that
actually happened.
"""
import re
from pathlib import Path

PRACTICE_JS = (Path(__file__).resolve().parents[1] / "src" / "sm64_events"
               / "ui" / "components" / "practice.js")

# Deliberate, reviewed differences. Empty means "the cards are at parity".
# Adding an entry is a decision: write WHY the other card doesn't want it.
ONLY_IN_STAR: dict[str, str] = {}
ONLY_IN_SEGMENT: dict[str, str] = {}


def _body(source: str, name: str) -> str:
    """The text of a top-level `function <name>(...) { ... }` declaration."""
    match = re.search(rf"^function {name}\(.*?^}}", source, re.S | re.M)
    assert match, f"{name} not found in practice.js — did it get renamed?"
    return match.group(0)


def _components(body: str) -> set[str]:
    """Component names the body renders (htm's `<${Name} ...>` syntax)."""
    return set(re.findall(r"<\$\{(\w+)\}", body))


def test_star_and_segment_cards_render_the_same_components():
    source = PRACTICE_JS.read_text(encoding="utf-8")
    star = _components(_body(source, "StarSection"))
    segment = _components(_body(source, "SegmentSection"))
    assert star, "StarSection renders no components — parser broke"
    missing_from_segment = star - segment - set(ONLY_IN_STAR)
    missing_from_star = segment - star - set(ONLY_IN_SEGMENT)
    assert not missing_from_segment, (
        "SegmentSection is missing components StarSection has: "
        f"{sorted(missing_from_segment)}. Add them to the segment card, or "
        "record the reason in ONLY_IN_STAR.")
    assert not missing_from_star, (
        "StarSection is missing components SegmentSection has: "
        f"{sorted(missing_from_star)}. Add them to the star card, or "
        "record the reason in ONLY_IN_SEGMENT.")


def test_both_cards_offer_a_strategy_picker():
    """The specific regression that motivated this file (2026-07-23)."""
    source = PRACTICE_JS.read_text(encoding="utf-8")
    for name in ("StarSection", "SegmentSection"):
        assert "StratPicker" in _components(_body(source, name)), \
            f"{name} lost its strategy picker"

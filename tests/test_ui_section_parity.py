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
VIEWS_PY = (Path(__file__).resolve().parents[1] / "src" / "sm64_events"
            / "tracking" / "views.py")

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


def test_both_cards_offer_a_failure_compilation():
    """Failure compilation must ship on stars AND segments (spec 2026-07-23)."""
    source = PRACTICE_JS.read_text(encoding="utf-8")
    for name in ("StarSection", "SegmentSection"):
        assert "FailureCompilation" in _components(_body(source, name)), \
            f"{name} is missing the failure-compilation control"


def test_two_rank_banners_are_rendered_for_both_kinds():
    """Rule 11: a feature built for one kind ships for both in the same
    change. Round 2 of the rank-legibility fix (2026-07-25) merged the old
    RankBanner + EntityRankTag pair into ONE component, rendered TWICE with
    different data ("Strategy Rank" graded on the active strategy, "Overall
    Rank" graded on the entity's best-possible ladder) -- deliberately never
    two components that happen to look similar, since a labelled banner next
    to a bare unlabelled chip is exactly the bug this fixed (live report
    2026-07-25). A raw `_components()` set can't tell "one usage" from "two"
    apart (it dedupes by name), so this counts RankBanner occurrences in each
    section's own body instead, the same way the strategy-picker and
    failure-compilation tests above do."""
    source = PRACTICE_JS.read_text(encoding="utf-8")
    for name in ("StarSection", "SegmentSection"):
        body = _body(source, name)
        assert body.count("<${RankBanner}") >= 2, \
            f"{name} does not render both the Strategy Rank and Overall Rank banners"
        assert "Strategy Rank" in body and "Overall Rank" in body, \
            f"{name} is missing one of the two rank banner labels"


def test_both_section_builders_emit_entity_rank():
    source = VIEWS_PY.read_text(encoding="utf-8")
    assert source.count('"entity_rank"') >= 2

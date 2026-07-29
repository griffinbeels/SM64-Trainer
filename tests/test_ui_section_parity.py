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

from source_scan import strip_comments

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
    different data ("Strategy" graded on the active strategy, the ENTITY's
    own label graded on its best-possible ladder) -- deliberately never two
    components that happen to look similar, since a labelled banner next to
    a bare unlabelled chip is exactly the bug this fixed (live report
    2026-07-25). A raw `_components()` set can't tell "one usage" from "two"
    apart (it dedupes by name), so this counts RankBanner occurrences in
    each section's own body instead, the same way the strategy-picker and
    failure-compilation tests above do.

    The entity kicker is per-kind ("Star" / "Segment"), not one shared word,
    and that is what the second assertion pins: `RankBanner` renders on BOTH
    kinds, so a hardcoded "Star" would be a lie on a segment card. Round 4
    (2026-07-25) also dropped the trailing "Rank" from both kickers -- 13
    characters of label did not fit a ~390px banner row, which is what left
    every fixture ellipsized mid-word. Asserting the exact kicker each
    section passes is what stops a future edit from quietly reintroducing
    either fault.

    Round 6 (2026-07-25) made the STRATEGY kicker dynamic: when both
    measures grade identically -- always so for a star whose only strategy
    carries standards -- the entity banner is suppressed and the survivor
    reads "Strategy · Star", because a lone "STRATEGY" banner read as a star
    rank that had failed to load. So that side is pinned as
    `bannerLabel(sec, "<Kind>")`, which carries the per-kind noun into the
    merged case; the entity banner's own kicker stays the literal it always
    was."""
    source = PRACTICE_JS.read_text(encoding="utf-8")
    entity_label = {"StarSection": "Star", "SegmentSection": "Segment"}
    for name in ("StarSection", "SegmentSection"):
        body = _body(source, name)
        assert body.count("<${RankBanner}") >= 2, \
            f"{name} does not render both the strategy and entity rank banners"
        assert f'bannerLabel(sec, "{entity_label[name]}")' in body, \
            (f"{name}'s strategy banner must take its kicker from "
             f'bannerLabel(sec, "{entity_label[name]}") -- a hardcoded '
             '"Strategy" cannot say "Strategy · Segment" on the merged card')
        assert f'label="{entity_label[name]}"' in body, \
            (f"{name}'s entity rank banner must be labelled "
             f"\"{entity_label[name]}\" -- the kicker names the entity this "
             "half grades, and RankBanner renders on both kinds")


def test_both_section_builders_emit_entity_rank():
    source = VIEWS_PY.read_text(encoding="utf-8")
    assert source.count('"entity_rank"') >= 2


def test_both_section_kinds_render_the_shared_stat_chips_row():
    """The Stats menu chooses WHICH stat chips are shown; the chips themselves
    render in the detail drawer.

    Shared as ONE component, not pasted twice: adding a control to two copies
    of markup is precisely the shape that drifts, and rule 11 makes an
    asymmetry between a star and a segment a bug."""
    code = strip_comments(PRACTICE_JS.read_text(encoding="utf-8"))
    assert code.count("<${StatChipsRow}") == 2
    # ...and no card keeps its own hand-rolled copy of the chips loop.
    assert code.count("DUST_STAT_KEYS.has") == 1


def test_every_attempts_tools_row_carries_the_stat_menu_trigger():
    """The Stats TRIGGER moved out of the chips row and into the practice-log
    card's header, left of the sort control (user, 2026-07-28: "For the stats
    button, we should move it to be inside the practice log, to the left of
    the sort filter"). It must appear in every `.attempts-tools` row --
    StarSection, SegmentSection, AND EmptyPractice's "Unassigned attempts"
    card -- from ONE shared component, never pasted: a 1:1 count between the
    toolbar row and the trigger is what a future practice-log card silently
    missing it, or a hand-rolled second copy, would both break.

    This is also what makes the trigger reachable when route focus is on with
    no active target: neither StarSection/SegmentSection's drawer nor
    RouteFocus renders anything then, and EmptyPractice's log card is the only
    surface left -- a trigger missing there would mean the Stats menu again
    has zero access points on the page, the exact gap this move closes."""
    code = strip_comments(PRACTICE_JS.read_text(encoding="utf-8"))
    toolbar_rows = code.count('class="attempts-tools"')
    trigger_uses = code.count("<${StatMenuTrigger}")
    assert toolbar_rows >= 3, (
        f"expected at least 3 practice-log toolbar rows (star/segment/"
        f"unassigned), found {toolbar_rows} -- did one get renamed?")
    assert trigger_uses == toolbar_rows, (
        f"{toolbar_rows} '.attempts-tools' row(s) but {trigger_uses} "
        "StatMenuTrigger use(s) -- every practice-log toolbar must carry "
        "exactly one shared trigger")
    # ...and no card keeps its own hand-rolled copy of the trigger button.
    assert code.count("function StatMenuTrigger") == 1

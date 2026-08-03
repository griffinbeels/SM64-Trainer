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
ENTITYDETAIL_JS = (Path(__file__).resolve().parents[1] / "src" / "sm64_events"
                   / "ui" / "components" / "entitydetail.js")
ATTEMPTLOG_JS = (Path(__file__).resolve().parents[1] / "src" / "sm64_events"
                 / "ui" / "components" / "attemptlog.js")
PRACTICELOG_JS = (Path(__file__).resolve().parents[1] / "src" / "sm64_events"
                  / "ui" / "components" / "practicelog.js")
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


def _exported_body(source: str, name: str) -> str:
    """The text of a top-level `export function <name>(...) { ... }`
    declaration -- entitydetail.js's shared components are exported, unlike
    practice.js's own section builders."""
    match = re.search(rf"^export function {name}\(.*?^}}", source, re.S | re.M)
    assert match, f"{name} not found in entitydetail.js — did it get renamed?"
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


def test_the_page_level_drawer_still_offers_a_failure_compilation():
    """Failure compilation must ship on stars AND segments (spec 2026-07-23).

    2026-08-03 (practice-log-entity-cards, task 4): the detail drawer that
    holds FailureCompilation stopped being two hand-written copies -- both
    StarSection and SegmentSection rendered the SAME shared `EntityDrawer`
    (entitydetail.js), so the parity this test guarded was a structural
    guarantee rather than two things that could quietly drift apart.

    2026-08-03 (task 6): it went one step further -- EntityDrawer stopped
    being called once PER SECTION and became ONE page-level call in
    `Practice`, following whichever entity is in FOCUS (which may be neither
    the star nor the segment card currently active; ui/focustarget.js).
    There is no longer a second call site for a section to individually
    stop calling it, so the property worth asserting is that the one call
    exists and follows the focused entity, plus the unchanged half: the
    shared drawer itself still renders the control."""
    practice = strip_comments(PRACTICE_JS.read_text(encoding="utf-8"))
    assert practice.count("<${EntityDrawer}") == 1, (
        "Practice must render the shared drawer exactly once, at page level "
        "-- a second call site would be the two-copies shape this test "
        "exists to prevent, arriving one level up")
    assert "sec=${focusedSec}" in practice, (
        "the drawer must follow the FOCUSED entity (ui/focustarget.js), not "
        "just the active target -- a browsed entity's Clear-data/standards/"
        "failure-compilation would otherwise be unreachable")
    drawer = _exported_body(ENTITYDETAIL_JS.read_text(encoding="utf-8"), "EntityDrawer")
    assert "FailureCompilation" in drawer, \
        "EntityDrawer is missing the failure-compilation control"


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
    was.

    2026-08-03 (practice-log-entity-cards, task 2): each section's own
    hardcoded `"Star"`/`"Segment"` literal was replaced by
    `entityNoun(sec)` (`ui/entitysection.js`) -- the SAME per-kind noun both
    cards now ask for, rather than two literals that could drift apart if a
    section were ever miscopied. That is a strengthening of this test's
    original concern, not a loosening of it: `entityNoun` is single-sourced
    off `sec.kind`, so a hardcoded lie is no longer even expressible here."""
    source = PRACTICE_JS.read_text(encoding="utf-8")
    for name in ("StarSection", "SegmentSection"):
        body = _body(source, name)
        assert body.count("<${RankBanner}") >= 2, \
            f"{name} does not render both the strategy and entity rank banners"
        assert "bannerLabel(sec, entityNoun(sec))" in body, \
            (f"{name}'s strategy banner must take its kicker from "
             "bannerLabel(sec, entityNoun(sec)) -- a hardcoded "
             '"Strategy" cannot say "Strategy · Segment" on the merged card')
        assert "label=${entityNoun(sec)}" in body, \
            (f"{name}'s entity rank banner must be labelled with "
             "entityNoun(sec) -- the kicker names the entity this half "
             "grades, and RankBanner renders on both kinds")


def test_both_section_builders_emit_entity_rank():
    source = VIEWS_PY.read_text(encoding="utf-8")
    assert source.count('"entity_rank"') >= 2


def test_both_section_kinds_render_the_shared_stat_chips_row():
    """The Stats menu chooses WHICH stat chips are shown; the chips themselves
    render in the detail drawer.

    Shared as ONE component, not pasted twice: adding a control to two copies
    of markup is precisely the shape that drifts, and rule 11 makes an
    asymmetry between a star and a segment a bug.

    2026-08-03 (practice-log-entity-cards, task 4): StatChipsRow moved out of
    practice.js into entitydetail.js, rendered from inside the shared
    EntityDrawer -- which was itself the ONE thing both StarSection and
    SegmentSection called. "Does every card show the chips" was therefore two
    questions: does EntityDrawer reach both cards (2 uses in practice.js),
    and does EntityDrawer itself still render the row exactly once.

    2026-08-03 (task 6): EntityDrawer stopped being called per-section and
    became ONE page-level call following the focused entity, so the first
    question collapses to "does the one call still exist" -- there is no
    second call site left to reach two cards from."""
    practice = strip_comments(PRACTICE_JS.read_text(encoding="utf-8"))
    assert practice.count("<${EntityDrawer}") == 1
    detail = strip_comments(ENTITYDETAIL_JS.read_text(encoding="utf-8"))
    assert detail.count("<${StatChipsRow}") == 1
    # ...and no card keeps its own hand-rolled copy of the chips loop.
    assert detail.count("DUST_STAT_KEYS.has") == 1
    assert "DUST_STAT_KEYS" not in practice


def test_the_practice_logs_one_toolbar_carries_the_stat_menu_trigger():
    """The Stats TRIGGER moved out of the chips row and into the practice-log
    card's header, left of the sort control (user, 2026-07-28: "For the stats
    button, we should move it to be inside the practice log, to the left of
    the sort filter"). Until task 6 it appeared in every `.attempts-tools` row
    -- StarSection, SegmentSection, AND EmptyPractice's "Unassigned attempts"
    card -- from ONE shared component, never pasted: a 1:1 count between the
    toolbar row and the trigger is what a future practice-log card silently
    missing it, or a hand-rolled second copy, would both break.

    2026-08-03 (practice-log-entity-cards, task 6): the practice log itself
    moved out of practice.js whole -- `PracticeLog` (practicelog.js) renders
    ONE heading for the entire log, not one per entity card, which is what
    makes `test_ui_practice_log.py`'s `orderedSections` the single source of
    the list order rather than three sections each carrying their own
    toolbar. So the 1:1 count this test guarded moved location and also
    became a STRICTER guarantee: there is exactly one `.attempts-tools` row on
    the whole page now, one `StatMenuTrigger` use, and practice.js itself
    carries neither -- the log (and its one toolbar) left practice.js
    entirely, rather than one of three copies losing the trigger.

    2026-08-03 (task 5): StatMenuTrigger's own DEFINITION moved out of
    practice.js into attemptlog.js (Step 0 of that task -- the attempt-row
    machinery and the rank-banner helpers move there so practicelog.js can use
    them without practice.js closing an import cycle on itself). That half is
    unaffected by task 6 and re-probed here at its same two addresses:
    practicelog.js must carry ZERO definitions (it only imports the shared
    one), and attemptlog.js must carry exactly ONE."""
    log_source = strip_comments(PRACTICELOG_JS.read_text(encoding="utf-8"))
    toolbar_rows = log_source.count('class="attempts-tools"')
    trigger_uses = log_source.count("<${StatMenuTrigger}")
    assert toolbar_rows == 1, (
        f"expected exactly 1 practice-log toolbar row in practicelog.js, "
        f"found {toolbar_rows} -- did PracticeLog's heading get renamed or "
        "duplicated per card?")
    assert trigger_uses == 1, (
        f"expected exactly 1 StatMenuTrigger use in practicelog.js, found "
        f"{trigger_uses}")
    assert log_source.count("function StatMenuTrigger") == 0, (
        "practicelog.js defines its own StatMenuTrigger -- it should only "
        "import the shared one from attemptlog.js")
    # ...and practice.js itself carries neither the row nor the trigger any
    # more -- the whole log, toolbar included, moved out in task 6.
    practice = strip_comments(PRACTICE_JS.read_text(encoding="utf-8"))
    assert 'class="attempts-tools"' not in practice, (
        "practice.js still has a practice-log toolbar row of its own -- the "
        "log is a page-level surface (practicelog.js) now, not a per-section "
        "card")
    assert "<${StatMenuTrigger}" not in practice, (
        "practice.js still calls StatMenuTrigger directly -- that call moved "
        "to practicelog.js with the rest of the log")
    assert practice.count("function StatMenuTrigger") == 0, (
        "practice.js defines its own StatMenuTrigger -- it should only "
        "import the shared one from attemptlog.js")
    attemptlog = strip_comments(ATTEMPTLOG_JS.read_text(encoding="utf-8"))
    assert attemptlog.count("function StatMenuTrigger") == 1, (
        "attemptlog.js must define StatMenuTrigger exactly once")


def test_the_analysis_card_and_drawer_are_one_component_not_two_copies():
    """They become page-level surfaces that any entity can feed, so a second
    hand-written copy is not just duplication -- it is a copy that the page
    would have no way to point at a browsed entity."""
    detail = strip_comments((ENTITYDETAIL_JS).read_text(encoding="utf-8"))
    for name in ("EntityAnalysis", "EntityDrawer", "wipeSection"):
        assert f"export function {name}" in detail or \
               f"export async function {name}" in detail, name

    practice = strip_comments(PRACTICE_JS.read_text(encoding="utf-8"))
    # Neither section may still build an analysis card or a drawer itself.
    assert "analysis-block" not in practice
    assert "detail-drawer" not in practice
    assert practice.count("<${EntityAnalysis}") == practice.count("<${EntityDrawer}")


def test_the_practice_index_is_gone_and_the_log_replaced_it():
    """The index listed the same set of entities in catalog order. Keeping
    both would put every entity on the page twice."""
    practice = strip_comments(PRACTICE_JS.read_text(encoding="utf-8"))
    assert "practice-index" not in practice


def test_the_active_cards_shrank_to_the_objective_alone():
    """StarSection and SegmentSection used to carry the analysis card, the
    attempts log and the drawer inline; task 6 leaves them holding only the
    objective card. A behavioural check rather than a component-name scan --
    the structural warning this branch has repeated at every step: a source
    count that matches a shared component's call site cannot see a kind-gated
    conditional wrapped around it (a reviewer proved this on task 4's own
    StatChipsRow, silently dropped for segments with every count-based guard
    still green). So this asserts on the SHAPE of what remains -- exactly one
    `<section class="practice-card ...">` per card, wrapped in exactly one
    `.practice-detail-grid` -- rather than counting a component name that
    could be reintroduced behind a per-kind guard with every count unchanged."""
    source = strip_comments(PRACTICE_JS.read_text(encoding="utf-8"))
    for name in ("StarSection", "SegmentSection"):
        body = _body(source, name)
        assert body.count('class="practice-detail-grid') == 1, (
            f"{name} must wrap exactly one grid")
        assert body.count("practice-card") == 1, (
            f"{name} renders more than one practice-card -- the analysis "
            "card, attempts log or drawer regrew inside it")
        assert "attempts-card" not in body, (
            f"{name} still builds its own attempts-log card")
        assert "EntityAnalysis" not in body and "EntityDrawer" not in body, (
            f"{name} still calls the shared analysis card or drawer itself "
            "-- both are page-level surfaces now, following the FOCUSED "
            "entity rather than whichever section is active")

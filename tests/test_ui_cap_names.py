"""Every tier NAME shown to the user must be a cap name, never the raw
scraped tier key -- Task 5, 2026-07-25-mario-cap-rank-icons.

The palette moved to cap colours (Task 1) but the tier KEYS did not --
they are scraped from xcams (tools/scrape_ranks.py) and a rename would just
be reverted. So the tier keyed `Gold` now renders PURPLE (Waluigi's colour)
and `Platinum` renders YELLOW (Wario's) -- any surface still printing the
raw key as visible text is now actively wrong on screen, not merely
inconsistent with a style guide.

RANK_NAMES (the tier keys, in ladder order) is legitimately ITERATED in
several files -- standards.js walks it to build one table row per tier,
stratmodal.js walks it to build the ladder form, celebrate.js slices it to
animate a multi-tier climb. That is fine and stays untouched: none of those
call sites interpolate a bare tier expression directly into a template, they
only use the KEY to drive a loop / index lookup / comparison. What is
forbidden is PRINTING a tier-holding expression -- interpolating it directly
into a template literal or JSX-text/title position without routing it
through capName() first.

Like DOMAIN_VOCAB_MARKERS in test_ui_picker_parity.py, RAW_TIER_EXPRESSIONS
below is the list a future author consciously extends when a new call site
learns to hold a tier value -- not a full JS parse.
"""
import re
from pathlib import Path

from source_scan import strip_comments

UI = Path(__file__).resolve().parent.parent / "src" / "sm64_events" / "ui"
CAPS_FILE = "components/caps.js"

# Every expression this codebase holds a raw tier KEY in, at a call site that
# could plausibly render it. Not a generic JS parse -- an explicit,
# consciously-extended list, same convention as DOMAIN_VOCAB_MARKERS.
RAW_TIER_EXPRESSIONS = (
    "tier", "rank", "shownTier",
    "entity.tier", "entity.next_tier",
    "banner.rank", "banner.next_tier",
    "data.tier", "chip.tier", "step.tier", "band.tier",
    "mark.point.tier", "celebration.to.tier", "celebration.from.tier",
    "view.avg_rank.tier", "videoEdit.rank",
    # M6 (final review, 2026-07-25): these are today only prop handoffs --
    # here.tier (rankpage.js ladder mark), attempt.rank (rankpage.js entity
    # attempts), a.rank/s.rank (practice.js attempt medal and star/segment
    # rank), view.rank (routes.js route step) -- nothing to catch YET, but
    # I2 gave Hat a default title built from exactly this kind of
    # expression, and an unwrapped `${attempt.rank}` in a future tooltip
    # would sail through without this extension. `sec.rank` is different:
    # it is ALREADY handed off bare, as `banner=${sec.rank}` (ranks.js
    # RankBanner) and `sectionRank=${sec.rank}` (standards.js) -- adding it
    # here without also widening _PROP_PREFIXES below turned those two
    # legitimate whole-object handoffs into false positives.
    "here.tier", "attempt.rank", "a.rank", "s.rank", "view.rank", "sec.rank",
    # strategystep.js (Task 6, 2026-07-25-target-picker-strategy-step): the
    # strategy card's own rank, from GET /api/target/strategies' per-strategy
    # `rank`. Already routed through capName() there (a ternary condition,
    # not a bare print) -- added so a future edit that prints it bare cannot
    # sail through unwrapped, same reasoning as the M6 batch above.
    "strat.rank",
    # practicecell.js (mario-cap-rank-icons integration, 2026-07-26): `rank`
    # became {rank, division} once RankIcon replaced Hat there, so the
    # badge's title reads the tier off `rank.rank` -- already wrapped in
    # capName() at that call site, same reasoning as `strat.rank` above.
    "rank.rank",
)

# A tier (or tier-holding object) expression handed to a component as a PROP
# is not printing -- the component takes the raw value and either draws the
# icon itself (`tier=`/`division=`/`rank=` to <Hat>/PracticeCell) or reads
# named sub-fields off it internally, never printing the object whole
# (`banner=` to RankBanner reads banner.rank/banner.next_tier; `sectionRank=`
# to standards.js reads sectionRank.score/sectionRank.basis). That is the
# legitimate unwrapped handoff in both shapes.
#
# `data-tier=` is the same distinction one step further out (2026-08-10): a
# `data-` attribute is a machine hook -- a test selector, a CSS handle -- and
# is never rendered to anyone, so the RAW key is the correct value to put
# there and wrapping it in capName() would make the hook read the display
# vocabulary instead of the ladder's own. Widening the list is a reviewed
# edit, never a way to make a test pass; what keeps this honest is that it
# lengthens the lookbehind only, so every TEXT position in every file stays
# exactly as guarded as it was -- proved by `test_the_guard_still_catches_a_
# real_print` below.
#
# `key=` is the same category as `data-tier=` (2026-08-14, task 0098): a
# Preact reconciliation key is a machine hook, never rendered to anyone, and
# the standards table keys its tier rows on the raw tier so an expanded row's
# identity survives the display vocabulary changing.
_PROP_PREFIXES = ("tier=", "rank=", "division=", "banner=", "sectionRank=",
                  "data-tier=", "key=")


def _bare_interpolation_pattern(expr: str) -> str:
    lookbehinds = "".join(f"(?<!{prefix})" for prefix in _PROP_PREFIXES)
    # The ENTIRE `${...}` content must be just the expression (optionally
    # `.toUpperCase()`-chained, ranks.js's shape) -- nothing else. That is
    # what distinguishes a bare PRINT (`${tier}`) from the same expression
    # used as a ternary CONDITION (`${tier ? ... : ...}`, marelo.js) or
    # passed through a wrapper (`${rankColor(tier)}`, `${capName(tier)}`):
    # both have more content between the braces than the bare expression.
    #
    # `(?!_)` after the close brace excludes hat.js's `wing${tier}_${side}`:
    # that `tier` is an unrelated integer loop counter building a sprite
    # stem (task 2's wing layers), not a tier KEY -- and text meant for a
    # human is never glued directly to an identifier continuation like `_`,
    # only a stem/class-name construction is.
    #
    # `(?!\s+on xcams)` is the ONE deliberate exception (spec decision, Task
    # 5): standards.js/stratmodal.js's xcams bridge names the raw tier key
    # ON PURPOSE, next to its cap name, so a cutoff time can still be
    # cross-referenced against a site that calls these ranks Gold/Silver --
    # `"Waluigi · Gold on xcams"`. That is the raw key surviving BY DESIGN,
    # not the bug this guard exists to catch.
    return (lookbehinds + r"\$\{\s*" + re.escape(expr)
            + r"(?:\.toUpperCase\(\))?\s*\}(?!_)(?!\s+on xcams)")


def raw_tier_print_offenders(source: str) -> list:
    """Tier-holding expressions interpolated bare into a template/JSX
    position -- printed to the user without going through capName() first."""
    code = strip_comments(source)
    return [expr for expr in RAW_TIER_EXPRESSIONS
            if re.search(_bare_interpolation_pattern(expr), code)]


def _js_files():
    return sorted(path for path in UI.rglob("*.js"))


def test_no_file_prints_a_raw_tier_name():
    for path in _js_files():
        relative = path.relative_to(UI).as_posix()
        if relative == CAPS_FILE:
            continue
        offenders = raw_tier_print_offenders(path.read_text(encoding="utf-8"))
        assert not offenders, (
            f"{relative}: interpolates {offenders} directly into a template "
            "-- route it through capName() (the tier palette moved to cap "
            "colours; the raw key no longer matches what's on screen)")


def test_the_guard_can_still_fail():
    # Comment-only: a header/note naming the tier by example must stay green
    # (a raw substring check would trip on this; strip_comments is why it
    # doesn't -- see tests/source_scan.py).
    assert raw_tier_print_offenders(
        "// this component used to print ${entity.tier} directly\n") == []

    # Real code: an unwrapped interpolation is caught.
    assert raw_tier_print_offenders("html`<b>${entity.tier}</b>`") == ["entity.tier"]

    # Wrapped in capName() -- exactly the fix -- must not itself trip the guard.
    assert raw_tier_print_offenders("html`<b>${capName(entity.tier)}</b>`") == []

    # A prop handoff to <Hat> is not printing: Hat draws the icon from the
    # raw key itself.
    assert raw_tier_print_offenders(
        "html`<${Hat} tier=${entity.tier} division=${entity.division} />`") == []

    # A ternary CONDITION on the same expression is not printing either
    # (marelo.js's `${tier ? ... : "Unranked"}`) -- only the bare form is.
    assert raw_tier_print_offenders(
        'html`<b>${tier ? "ranked" : "Unranked"}</b>`') == []

    # Iterating RANK_NAMES to walk the ladder is fine by itself...
    assert raw_tier_print_offenders(
        'RANK_NAMES.filter((rank) => rank !== "Iron").map((rank) => rank)'
    ) == []
    # ...but printing the raw key INSIDE that walk is still the same bug.
    assert raw_tier_print_offenders(
        'RANK_NAMES.map((rank) => html`<td>${rank}</td>`)') == ["rank"]

    # A `data-` attribute is a machine hook, never rendered text, so the RAW
    # key belongs there (2026-08-10, the Library's Overall Rank Standards
    # bands). The exemption is narrow by construction...
    assert raw_tier_print_offenders(
        "html`<div data-tier=${band.tier}>x</div>`") == []
    # ...and lengthening the lookbehind must not have blunted the guard for
    # the same expression in a TEXT position, which is the whole point.
    assert raw_tier_print_offenders("html`<b>${band.tier}</b>`") == ["band.tier"]
    # Nor for a `data-tier` sitting anywhere other than immediately before the
    # interpolation -- an attribute earlier in the tag must not license a
    # print later in it.
    assert raw_tier_print_offenders(
        "html`<div data-tier=${band.tier}><b>${band.tier}</b></div>`") == ["band.tier"]

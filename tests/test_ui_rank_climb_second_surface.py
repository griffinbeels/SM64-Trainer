"""A second surface for the SAME measurement must never replay its climb.

Round 4 of the level-up climb (.claude/rules/ui-climb.md) already tells apart
a fresh page MOUNT (every banner on screen at once -- must snap, or the whole
page would animate on every refresh) from a banner ARRIVING LATE into a card
already up (a strategy just picked -- a rank being earned for the first time,
and exactly what to celebrate). It answers "when did this banner appear" with
a per-lane timestamp (`laneFirstSeen`/`arrivedLateRef`).

What that timestamp could not tell apart: a banner arriving late because ITS
OWN card's disclosure just opened, revealing a measurement some OTHER,
already-mounted surface has been showing the whole time. The active target's
objective card (practice.js) and its practice-log card (practicelog.js)
render the SAME entity's SAME banner through two INDEPENDENT `useRankClimb`
calls sharing one `lane` (the entity key) -- exactly the shape
practice-log-entity-cards' "ranks inside the dropdown" option creates: the log
card's own copy can mount a card-close's worth of seconds after the objective
card's, and lane timing alone reads that as "a rank being earned for the
first time" (Griffin, verbatim: "When we open the dropdown, the animation for
the rank standard shouldn't play from the beginning. It should start from
whatever the user's rank is for that strategy right now").

rankclimb.js's fix is `laneWitnessed`: a high-water mark of the last TARGET
value actually shown for `lane:order:identity`, checked before deciding
`earnedFirstRank`. This file proves it two ways, mounting the REAL
`RankBanner`/`useRankClimb` as two INDEPENDENT component instances -- never a
lookalike -- through raw preact `h`/`render` calls against `/ui/tune.html`'s
already-established import map and stylesheet, the same page (and the same
"drive the real component directly" technique) test_ui_rank_column_climb.py
and test_ui_rank_stacked_climb.py already use for a second render
arrangement. Two independent DOM containers stand in for "the objective card"
and "the practice-log card"; nothing about `RankBanner`/`useRankClimb`
themselves is stubbed.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from ui_fixture import serve_ui  # noqa: E402
from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab import trace  # noqa: E402
from uilab.driver import get_driver  # noqa: E402

# A graded, non-floor rank -- the exact shape ranks.js's RankBanner reads off
# sec.rank/sec.entity_rank in production (rank/division/fill/next_tier/
# next_division/next_gap_cs/mode/basis/reason).
GRADED_BANNER = ("{ rank: 'Gold', division: 'II', fill: 0.4, "
                  "next_tier: 'Gold', next_division: 'I', next_gap_cs: 240, "
                  "mode: 'pb', basis: null, reason: null }")


def mount_script(container_id, lane, order, identity):
    """Mount a REAL RankBanner into a freshly created, freshly appended DOM
    node -- a genuine independent component instance, the same way a second
    surface on the practice page is a genuine independent instance of
    useRankClimb sharing only a `lane` string with the first."""
    return f"""
(async () => {{
  const {{ h, render }} = await import('preact');
  const {{ RankBanner }} = await import('/ui/components/ranks.js');
  const container = document.createElement('div');
  container.id = '{container_id}';
  document.body.appendChild(container);
  render(h(RankBanner, {{
    label: 'Strategy', banner: {GRADED_BANNER}, atFloor: false,
    lane: '{lane}', order: {order}, identity: '{identity}',
    showNext: true, iconSize: 24,
  }}), container);
  return true;
}})()
"""


def bar_selector(container_id):
    return f"#{container_id} .rank-progress-track i"


def name_selector(container_id):
    return f"#{container_id} .rank-banner-name"


@pytest.fixture(scope="module")
def demo():
    with serve_ui() as base:
        with get_driver().launch(headless=True, viewport=(1200, 800)) as page:
            page.goto(f"{base}/ui/tune.html")
            page.wait_for(".rank-banner", timeout_ms=20_000)
            yield page


def test_a_second_surface_for_an_already_shown_rank_snaps(demo):
    """The bug, reproduced live: an already-mounted surface (the objective
    card) has been showing this entity's rank the whole time; a SECOND,
    independent surface for the exact same measurement (lane, order,
    identity) mounts well over a second later (the practice log's own copy,
    revealed by opening its dropdown). Nothing was earned -- it must snap
    straight to the known value, never replay a climb from the floor."""
    identity = "entity-x|strategy|Standard|pb"
    assert demo.evaluate(mount_script("surface-a", "lane-witness-1", 0, identity))
    demo.wait_ms(150)

    # Well past LANE_MOUNT_GRACE_MS (400ms) -- exactly the kind of gap a
    # closed dropdown leaves between the two surfaces' mounts.
    demo.wait_ms(1200)

    result = trace.record(
        demo, watch={"bar": bar_selector("surface-b"), "name": name_selector("surface-b")},
        trigger=lambda pg: pg.evaluate(mount_script("surface-b", "lane-witness-1", 0, identity)),
        ms=9000)
    bar = result.of("bar")
    widths = bar.values("width")
    names = {n for n in result.of("name").values("text") if n}
    assert widths, "the second surface's bar never rendered at all"
    # The FULL swept range, not first-vs-last: useRankClimb's lazy initial
    # state renders the destination for one frame before its effect runs
    # (documented in rankclimb.js -- the same "prints the destination for one
    # frame" quirk marelocelebrate.js's own atTarget logic exists to dodge),
    # so a genuine climb from floor to this exact destination would ALSO
    # start and end on 829.5px, making first-vs-last travel read as zero even
    # while the middle of the trace swept the whole ladder. Only max-min
    # actually asks "did this ever show a DIFFERENT value than the one it
    # started and ended on".
    assert max(widths) - min(widths) < 1.0, (
        f"the second surface's bar MOVED ({min(widths):.1f} .. {max(widths):.1f}px) "
        "-- it replayed a climb for a rank the page already knew")
    assert len(names) == 1, (
        f"the second surface's rank name changed ({sorted(names)}) -- that is "
        "the digit reel playing a climb nobody earned")


def test_a_genuinely_new_measurement_arriving_late_still_climbs(demo):
    """The case the fix must not break: picking a strategy for a star that
    already shows its entity rank makes a STRATEGY banner appear for the
    first time anywhere, seconds after the card mounted. Nothing has
    witnessed THIS lane+order+identity before, so it is a rank being earned
    for the first time -- exactly what round 4 already climbs, and the guard
    above must not silently swallow that on every late arrival."""
    lane = "lane-witness-2"
    assert demo.evaluate(mount_script(
        "surface-c", lane, 1, "entity-y|entity|Standard|pb"))
    demo.wait_ms(1200)

    result = trace.record(
        demo, watch={"bar": bar_selector("surface-d"), "name": name_selector("surface-d")},
        trigger=lambda pg: pg.evaluate(mount_script(
            "surface-d", lane, 0, "entity-y|strategy|Cannonless|pb")),
        ms=9000)
    bar = result.of("bar")
    widths = bar.values("width")
    names = {n for n in result.of("name").values("text") if n}
    assert widths, "the late-arriving banner never rendered at all"
    # max-min, not travel -- see the snap test's own comment: this climb's
    # start and destination happen to be the same drawn width the OTHER
    # order's banner already settled at (both GRADED_BANNER fixtures use the
    # same fill), so first-vs-last alone cannot tell a climb from a snap here.
    assert max(widths) - min(widths) > 5, (
        f"the late-arriving banner's bar never moved ({min(widths):.1f}.."
        f"{max(widths):.1f}px) -- a genuinely first-earned rank must still climb")
    assert len(names) > 1, (
        f"the late-arriving banner's name never changed ({sorted(names)}) -- "
        "it snapped instead of climbing from the floor")

# tests/test_ui_celebrate.py
"""The overall rank-up celebration (2026-07-28). Two hand-rolled scope
overlays (celebrate.js's fill->flip->hold TierRankUp and the DivisionRankUp
top banner) became one -- components/marelocelebrate.js's `MareloCelebration`
-- which flies the REAL route rank card to the centre of the screen and hands
it to ui/rankclimb.js, the same climb engine every rank banner already runs.

The climb-step-effect test that used to live here pinned TierRankUp's own
`useEffect` dependency array; that machinery is deleted along with the rest of
celebrate.js's two overlays, so there is nothing left of it to retarget."""
import re
from pathlib import Path

from source_scan import strip_comments

UI = Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "ui"
CELEBRATE_JS = (UI / "components" / "celebrate.js").read_text(encoding="utf-8")
MARELO_CELEBRATE_JS = (UI / "components" / "marelocelebrate.js").read_text(encoding="utf-8")
INDEX_HTML = (UI / "index.html").read_text(encoding="utf-8")


def test_one_overlay_serves_every_overall_rank_up():
    """Two hand-rolled treatments (TierRankUp's fill->flip->hold and
    DivisionRankUp's top banner) became one on 2026-07-28, at the user's call:
    "Every overall rank-up ... intensity scales with the size of the jump".
    The intensity difference is ONE tunable, not a second component."""
    code = strip_comments(CELEBRATE_JS)
    for gone in ("TierRankUp", "DivisionRankUp", "RankUpOverlay",
                 "flipStep", "rankup-medium"):
        assert gone not in code, gone


def test_the_overlay_performs_the_climb_rather_than_re_animating_it():
    """"the rank at the top of the screen ... should be getting the same type
    of celebration effects as the main rank standards below" (user,
    2026-07-28). Same type means the SAME registry, so the overlay must not
    grow keyframes of its own for a beat ui/celebrations.js already owns --
    it renders the REAL RouteRankCard (components/marelo.js), which is what
    actually calls useRankClimb, rather than re-implementing the climb here."""
    code = strip_comments(MARELO_CELEBRATE_JS)
    assert "RouteRankCard" in code
    assert "@keyframes" not in code
    for reinvented in ("sparkle", "squash", "digitRoll", "wingGrow"):
        assert reinvented not in code, reinvented


def test_the_card_is_parked_at_the_before_rank_first():
    """"I didn't see us animate from BEFORE -> AFTER obviously" (user,
    2026-07-25). The overlay renders celebration.from until it has reached the
    centre, then hands the card celebration.to and lets the climb perform it."""
    code = strip_comments(MARELO_CELEBRATE_JS)
    assert "celebration.from" in code and "celebration.to" in code
    assert "beforeHoldMs" in code


def test_the_header_card_does_not_also_celebrate():
    """One event, one thing celebrating it -- the mistake celebrate.js's own
    header records about the deleted entity toasts. The overlay IS the header
    card's climb, so the card underneath is hidden and snaps."""
    assert "is-celebrating" in strip_comments(MARELO_CELEBRATE_JS)
    assert ".marelo-slot.is-celebrating" in strip_comments(INDEX_HTML)


def test_the_overlay_never_eats_a_click_meant_for_the_game():
    css = strip_comments(INDEX_HTML)
    found = re.search(r"\.marelo-celebrate\s*\{([^}]*)\}", css)
    assert found and "pointer-events: none" in found.group(1)
    card = re.search(r"\.marelo-celebrate-card\s*\{([^}]*)\}", css)
    assert card and "pointer-events: auto" in card.group(1)


# A selector token that IS `.marelo-celebrate-card` or `.marelo-celebrate`
# (optionally with a pseudo-class/combinator tail), never a DIFFERENT class
# that merely starts with the same characters -- `.marelo-celebrate-backdrop`
# is a sibling of the card and is SUPPOSED to carry an opacity rule, so the
# lookahead excludes anything followed by a further word character or hyphen.
CARD_OR_ANCESTOR = re.compile(r"\.marelo-celebrate-card(?![\w-])|\.marelo-celebrate(?![\w-])")


def opacity_rules_on_card_or_ancestor(css: str) -> list[str]:
    """Every CSS rule whose selector touches `.marelo-celebrate-card` or its
    one remaining ancestor below `.app-shell` (`.marelo-celebrate`) and also
    declares `opacity`. A parent's opacity multiplies every child's, which is
    exactly the report-2 bug (2026-07-28): the card sat at effective opacity
    0 on the first frame of every flight because its ANCESTOR was the thing
    fading. `.marelo-celebrate-backdrop` -- the sibling that fades now -- is
    deliberately excluded by the regex above, not by name-listing it here."""
    found = []
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if CARD_OR_ANCESTOR.search(selectors) and re.search(r"\bopacity\s*:", body):
            found.append(selectors.strip())
    return found


def test_the_card_never_has_its_own_opacity_rule():
    """"It should never become invisible at any point, never change the
    opacity here. That breaks the immersion of the effect" (user, 2026-07-28).
    Root cause: `.marelo-celebrate` used to BE the fading backdrop with
    `.marelo-celebrate-card` as its CHILD, so the card's EFFECTIVE opacity was
    always its own (always 1) multiplied by its ancestor's (0 at the start of
    every flight) -- invisible on the first frame, fading in as it travelled,
    fading back out before landing, never reaching 1 even at rest (the tuned
    `backdropOpacity` is 0.8, never 1). The fix is structural: the backdrop
    (`.marelo-celebrate-backdrop`) is a SIBLING of the card now, not an
    ancestor, so there is nowhere left for an opacity rule to hide that would
    reach it. This pins that no rule in the stylesheet does."""
    assert opacity_rules_on_card_or_ancestor(strip_comments(INDEX_HTML)) == []


def test_the_opacity_guard_can_still_fail():
    """Mutation proof, both directions (tests/source_scan.py's own rule: a
    scan that matches nothing is green forever, and a comment mentioning the
    forbidden word must not trip it either)."""
    # The exact bug, reintroduced, on the card itself and on its ancestor:
    assert opacity_rules_on_card_or_ancestor(
        ".marelo-celebrate-card { opacity: 0; }") == [".marelo-celebrate-card"]
    assert opacity_rules_on_card_or_ancestor(
        ".marelo-celebrate { opacity: .5; }") == [".marelo-celebrate"]
    # The SIBLING backdrop is allowed to fade -- it is not an ancestor.
    assert opacity_rules_on_card_or_ancestor(
        ".marelo-celebrate-backdrop.is-lifted { opacity: .55; }") == []
    # A comment mentioning "opacity:" inside the rule body must not trip it --
    # which is exactly why the real test above scans STRIPPED source, not raw
    # (tests/source_scan.py's own trap: five guards were rewritten in one
    # session for reacting to a comment rather than code).
    raw = (".marelo-celebrate-card { /* opacity: intentionally never set */ "
           "transform: none; }")
    assert opacity_rules_on_card_or_ancestor(raw) != [], (
        "the probe sample should trip on RAW text -- if it doesn't, this "
        "probe no longer demonstrates why stripping comments matters")
    assert opacity_rules_on_card_or_ancestor(strip_comments(raw)) == []


def test_the_flight_has_exactly_one_transition_declaration():
    """A rule's `transition` is not additive: a higher-specificity block
    declaring its own replaces the base rule's wholesale, which is what makes
    a motion glide one way and snap the other (2026-07-26). There is no
    prefers-reduced-motion override here to accidentally duplicate it --
    marelocelebrate.js computes --fly-ms as 0 in that case, so the ONE
    declaration already resolves to an instant jump."""
    css = strip_comments(INDEX_HTML)
    rules = re.findall(r"\.marelo-celebrate-card[^{]*\{([^}]*)\}", css)
    assert sum("transition:" in rule for rule in rules) == 1, rules


def test_the_card_wears_the_EARNED_rank_all_the_way_home():
    """Only the outbound flight shows the before-state.

    `atCentre` gated on `"climb" || "hold"` until 2026-07-28, so the frame the
    fly-back began the card reverted to `celebration.from` -- and
    useRankClimb's never-animate-a-regression rule makes that revert INSTANT.
    Measured live: the card climbed to Waluigi 4, then wore Capless 5 for the
    entire trip home. The rank-up appeared to be taken away at exactly the
    moment the user was watching it land, which is the opposite of "I should
    rank up, and then see that new rank settle in the header".

    Asserted on the expression rather than the phase list, because the bug was
    an omission FROM that list -- a test naming the same three phases would
    have been written from the same wrong assumption."""
    code = strip_comments(MARELO_CELEBRATE_JS)
    found = re.search(r"const atCentre = (.*?);", code, re.S)
    assert found, "atCentre was renamed or removed -- re-point this guard"
    expression = found.group(1)
    assert '"out"' in expression, expression
    # An allow-list of phases is exactly how this shipped wrong.
    for earned in ('"climb"', '"hold"', '"back"'):
        assert earned not in expression, (
            f"{expression} enumerates {earned}; gate on the ONE phase that "
            "shows the before-state instead, so a new phase defaults to the "
            "earned rank rather than silently reverting.")


def test_the_flown_card_lands_on_the_real_progress_not_a_full_bar():
    """The bar the celebration ends on IS the bar the header keeps.

    `fill` was hardcoded `atCentre ? 1 : 0` until 2026-07-28, so the flown card
    always finished with a FULL bar and then flew home to a header showing the
    true progress into the new division -- 40% in the demo. "Whatever it ends
    on at the end in the middle is what I should have in the header once it
    settles."

    It also short-circuited the climb engine's own ending: climbplan.js resets
    the bar 1 -> 0 once entering the arrival and sweeps to the destination
    fill, and handing it 1 meant that final sweep had nowhere to go."""
    code = strip_comments(MARELO_CELEBRATE_JS)
    found = re.search(r"fill: (.*?)\s*\}", code, re.S)
    assert found, "the rank object's fill was renamed -- re-point this guard"
    expression = found.group(1)
    assert "toFill" in expression and "fromFill" in expression, expression
    assert re.search(r"\b1\b", expression) is None, (
        f"{expression} still hardcodes a full bar; the destination fill is "
        "marelo.division_progress, which is what the header will show.")


def test_the_scope_overlay_waits_for_the_banner_climbs():
    """Order of operations after a PB: strategy, THEN star, THEN marelo.

    User, 2026-07-29: "Strategy THEN star THEN marelo. Three parts. If
    strategy/star are combined then it's strategy/star THEN marelo. Right now,
    marelo incorrectly displays at the same time."

    The two entity banners already take turns via rankclimb.js's lane
    (practice.js passes `lane` + `order` 0/1, and a combined one-ladder card is
    one banner in that lane). The MARELO overlay passes NO lane -- deliberately,
    it is a different rank, not a third banner in some star's queue -- so it
    started immediately and played over the top.

    Gated on `useClimbsRunning`, NOT `useCelebrating`: a user gesture releases
    the celebration hold while climbs are still running
    (`releaseCelebrationHold`), and gating on that would let the overlay start
    over the top of them, which is the bug."""
    code = strip_comments(MARELO_CELEBRATE_JS)
    header = strip_comments((UI / "components" / "header.js").read_text(encoding="utf-8"))
    assert "useMareloTurn" in code, (
        "the scope overlay must wait its turn -- see ui/mareloturn.js")
    assert "useCelebrating" not in code, (
        "useCelebrating is released by a user gesture while climbs still run; "
        "the turn must depend on whether anything is ANIMATING")
    # BOTH consumers read the SAME hook. Gating only the overlay left the
    # header card free to climb to the new rank behind the banners, which is
    # the half of the report that said "it still animated while the rank
    # standards were animating".
    assert "useMareloTurn" in header, (
        "header.js must show the payload the turn hook allows, or the card "
        "climbs to the new rank while the banners are still animating")
    assert "marelo=${t.marelo}" not in header, (
        "the card must read the HELD payload, not the live one")


def test_the_turn_decision_is_not_written_into_the_hook():
    """The latch and the hold are BEHAVIOUR, and they live in an import-free
    module so tests/test_ui_marelo_turn.py can drive them frame by frame.

    Both bugs this pair has had were single state transitions, and both shipped
    past a source scan exactly like this one -- which is why the real assertions
    are over there and this only keeps the seam open. A hook that grew its own
    opinion about readiness would be untestable again, silently."""
    turn = strip_comments((UI / "mareloturn.js").read_text(encoding="utf-8"))
    assert "advanceTurn" in turn and "mareloturnstate.js" in turn, (
        "the turn's decision belongs in the import-free module, not here")
    assert "state.ready" not in turn, (
        "readiness is decided by advanceTurn; a second opinion here is how the "
        "two consumers start disagreeing about whose turn it is")


def test_the_running_signal_is_not_the_hold_signal():
    """They differ by exactly one thing and it matters here.

    `isCelebrating()` is `liveClimbs.size > 0 && !holdReleased`; `climbsRunning`
    drops the hold term. A guard reading only the export names would pass on a
    future edit that aliased one to the other, so this reads the expressions."""
    climb = strip_comments((UI / "rankclimb.js").read_text(encoding="utf-8"))
    found = re.search(r"export const climbsRunning = \(\) =>(.*?);", climb, re.S)
    assert found, "climbsRunning was renamed -- re-point this guard"
    assert "holdReleased" not in found.group(1), found.group(1)

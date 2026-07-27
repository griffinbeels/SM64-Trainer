"""The Rank tab ladder's "I have made it this far" reading must survive the
palette — for EVERY tier, not just the bright ones.

Live report 2026-07-27: "the bar is incorrectly DIM for the section of the bar
to the left... all of the bars, across all ranks from 0...the users current
rank, should be lit up." The band in question was already filled to 100% (the
reported screenshot's own pixels read #735648 exactly, Capless's tint at full
opacity). What was wrong was that "reached" was encoded ONLY as opacity of the
tier's own colour — a RELATIVE signal, which reads inside the one partially
filled band and nowhere else. A fully reached band has no dim half beside it,
so it is judged against its NEIGHBOURS, in other hues. Off the real render,
the Capless band he had cleared measured 10.77% relative luminance against
5.85% for the brightest band he had not (Vanish) — 1.84x, for the ONE thing
on that bar he had earned.

That is a tuning failure, not a missing rule, so the guard has to be numeric:
it composites the two treatments the CSS actually declares over the ladder's
own backdrop and asserts the dimmest LIT tier still clearly out-brightens the
brightest UNREACHED one. It fails on the CSS as shipped at the time of the
report, and on either half of the fix alone (see MIN_LIT_DIM_RATIO).

The compositing model is validated against the real render, not assumed: with
the sample point set to the bar's vertical midline (alpha .0745 in the gloss
gradient), it predicts Capless lit = rgb(125, 99, 86) and Vanish unreached =
rgb(28, 45, 57), against rgb(124, 97, 84) and rgb(28, 46, 58) measured off a
CDP screenshot of the real Rank tab at 2026-07-27. The ratio below uses the
gloss's AREA-AVERAGED alpha instead, which is what the eye integrates over a
16px bar rather than what any single scanline shows.
"""
import re
from pathlib import Path

from tests.test_ui_caps import _cap_table

INDEX_HTML = (Path(__file__).resolve().parents[1] / "src" / "sm64_events"
              / "ui" / "index.html")

# The dimmest LIT tier must out-brighten the brightest UNREACHED one by at
# least this much. Run against this ladder's own palette, the four combinations
# of the fix's two levers score (each one executed, 2026-07-27, by pointing
# INDEX_HTML at a mutated copy — not estimated):
#   neither (the CSS as reported, base .26 opaque + no gloss) ... 1.53
#   gloss alone ................................................ 2.50
#   dimmed+desaturated base alone .............................. 3.64
#   both (shipped) ............................................. 5.95
# The worst pair is the same in all four: a cleared Toadsworth band read at
# its brown spot colour against an unreached Toad band read at its near-white
# one — the two patterned tiers, whose gradients put the ladder's darkest lit
# pixel next to its brightest unlit one.
# So the floor demands a real separation without naming a mechanism: any
# treatment that gets a cleared band that far clear of an uncleared one
# passes, whether or not it is the gloss.
MIN_LIT_DIM_RATIO = 5.0


def _css_rule(selector: str, optional: bool = False) -> str:
    """The declaration block for one selector in the design-system <style>.
    `optional` is for the lit treatment specifically: "the rule is not there
    at all" is a real state — it is the state the report was made about — and
    has to score as zero lift rather than blow up before the ratio is
    computed, or the guard could never report the number it exists to report."""
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}",
                      INDEX_HTML.read_text(encoding="utf-8"))
    if match is None and optional:
        return ""
    assert match, f"{selector} not found in index.html — did it move or get renamed?"
    return match.group(1)


def _channels(hex_color):
    return [int(hex_color[index:index + 2], 16) for index in (1, 3, 5)]


def _over(top, bottom, alpha):
    """`top` composited onto `bottom` at `alpha`."""
    return [top[index] * alpha + bottom[index] * (1 - alpha) for index in range(3)]


def _saturate(channels, amount):
    """The CSS `saturate()` matrix (filter-effects §8.5), which Chromium
    applies in sRGB for the filter shorthand."""
    red, green, blue = channels
    return [
        (0.213 + 0.787 * amount) * red + (0.715 - 0.715 * amount) * green
        + (0.072 - 0.072 * amount) * blue,
        (0.213 - 0.213 * amount) * red + (0.715 + 0.285 * amount) * green
        + (0.072 - 0.072 * amount) * blue,
        (0.213 - 0.213 * amount) * red + (0.715 - 0.715 * amount) * green
        + (0.072 + 0.928 * amount) * blue,
    ]


def _luminance(channels):
    """WCAG relative luminance, 0-1."""
    def linear(value):
        value = max(0.0, min(1.0, value / 255.0))
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
    return (0.2126 * linear(channels[0]) + 0.7152 * linear(channels[1])
            + 0.0722 * linear(channels[2]))


def _gloss_average_alpha() -> float:
    """Area-average of the white overlay the lit fill wears, read off its own
    gradient stops. Zero when there is no overlay at all — which is the state
    the 2026-07-27 report was made about."""
    rule = _css_rule(".rank-band-fill::after", optional=True)
    stops = re.findall(r"rgba\(255,\s*255,\s*255,\s*([\d.]+)\)\s+([\d.]+)%", rule)
    if not stops:
        return 0.0
    points = sorted((float(position), float(alpha)) for alpha, position in stops)
    assert points[0][0] == 0.0 and points[-1][0] == 100.0, (
        "the gloss gradient must span the whole bar height for this average "
        f"to mean anything — got stops at {[p for p, _ in points]}")
    area = sum((points[index + 1][0] - points[index][0])
               * (points[index + 1][1] + points[index][1]) / 2
               for index in range(len(points) - 1))
    return area / 100.0


def _backdrop():
    """What a half-transparent band sits on: `.rank-ladder`'s own translucent
    plate over the card surface."""
    ladder = _css_rule(".rank-ladder")
    plate = re.search(r"background:\s*rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)", ladder)
    assert plate, ".rank-ladder lost its own background — the model below needs it"
    surface = re.search(r"--surface:\s*(#[0-9a-fA-F]{6})",
                        INDEX_HTML.read_text(encoding="utf-8"))
    assert surface, "--surface not found in :root"
    return _over([int(plate.group(index)) for index in (1, 2, 3)],
                 _channels(surface.group(1)), float(plate.group(4)))


def _tier_tints():
    """Every colour a band can actually paint. The two patterned tiers render
    a gradient between their base and pattern colours (`capGradient`), so both
    ends count — the ladder shows the whole sweep, not the midpoint."""
    tints = {}
    for tier, entry in _cap_table().items():
        colours = [_channels(entry["color"])]
        if entry["pattern_color"]:
            colours.append(_channels(entry["pattern_color"]))
        tints[tier] = colours
    return tints


def _lit_and_unreached():
    base_rule = _css_rule(".rank-band-base")
    opacity = re.search(r"opacity:\s*([\d.]+)", base_rule)
    assert opacity, ".rank-band-base lost its opacity — the dim state is the guard's other half"
    saturation = re.search(r"saturate\(([\d.]+)\)", base_rule)
    amount = float(saturation.group(1)) if saturation else 1.0
    backdrop = _backdrop()
    gloss = _gloss_average_alpha()

    lit, unreached = {}, {}
    for tier, colours in _tier_tints().items():
        lit[tier] = min(_luminance(_over([255, 255, 255], colour, gloss))
                        for colour in colours)
        unreached[tier] = max(
            _luminance(_over(_saturate(colour, amount), backdrop,
                             float(opacity.group(1))))
            for colour in colours)
    return lit, unreached


def test_a_cleared_band_out_brightens_every_band_still_to_climb():
    """The ladder's whole job at rest is "how far have I got", and the answer
    has to survive being read across nine different hues. Capless is the tier
    this fails on first — it is the darkest cap in the registry AND the first
    band anyone clears, so the very first thing a new player earns is the one
    most at risk of looking unearned."""
    lit, unreached = _lit_and_unreached()
    dimmest_lit = min(lit, key=lit.get)
    brightest_unreached = max(unreached, key=unreached.get)
    ratio = lit[dimmest_lit] / unreached[brightest_unreached]
    assert ratio >= MIN_LIT_DIM_RATIO, (
        f"a cleared {dimmest_lit} band is only {ratio:.2f}x as bright as an "
        f"unreached {brightest_unreached} one (floor {MIN_LIT_DIM_RATIO}) — at "
        "1.84x the user reported the cleared band as 'incorrectly DIM'")


def test_the_lit_treatment_does_not_depend_on_the_tier_s_own_colour():
    """The reason the ratio above can hold for Capless at all: the lift is a
    fixed amount of white, not a function of `--band-tint`. A treatment
    derived from the tier's own colour scales with the very thing that made
    the dark tiers unreadable, so it cannot fix them however hard it is tuned."""
    rule = _css_rule(".rank-band-fill::after")
    assert "--band-tint" not in rule, (
        "the lit treatment reads the tier's own tint — that makes 'lit' "
        "relative again, which is the 2026-07-27 bug")
    assert _gloss_average_alpha() > 0, (
        "no tier-independent lift left on the reached fill")

"""ui/components/caps.js is THE tier registry: name, colour, treatment.

Two regressions have real precedent. Colour: Iron shipped at #8a8a8a and read
as a dim Silver at 24px (live report 2026-07-25) -- and the pair that failed
scored 168 on the redmean distance used here, so the floor is set above it.
The check is over EVERY pair, not adjacent ones: `rank-ladder-scale` renders
all nine medals in one 13px row and the chart draws a dot per tier, so any two
can end up side by side. Order: the JS key order IS the ladder, and a reorder
would silently mis-rank every entity.
"""
import re
from math import sqrt
from pathlib import Path

from sm64_events.ranks.classify import RANK_NAMES
from tests.source_scan import strip_comments

CAPS_JS = Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "ui" / "components" / "caps.js"
HAT_JS = CAPS_JS.parent / "hat.js"

# Anything at or below this failed in production; the palette must clear it
# with margin. Raising it is a decision, not a cleanup.
MIN_SEPARATION = 185.0


def _cap_table() -> dict[str, str]:
    """{tier: hex} in declaration order, comments stripped."""
    source = strip_comments(CAPS_JS.read_text(encoding="utf-8"))
    block = re.search(r"export const CAP = \{(.*?)\n\};", source, re.S)
    assert block, "CAP table not found in caps.js -- did it move or get renamed?"
    entries = re.findall(r'(\w+):\s*\{[^}]*?color:\s*"(#[0-9a-fA-F]{6})"', block.group(1), re.S)
    assert entries, "CAP parsed to nothing -- the entry shape changed"
    return dict(entries)


def _channels(hex_color):
    return [int(hex_color[index:index + 2], 16) for index in (1, 3, 5)]


def redmean(first, second):
    """Cheap perceptual distance. Weights green heaviest and red by level,
    which is why it catches two light neutrals that plain RGB distance calls
    far apart."""
    r1, g1, b1 = _channels(first)
    r2, g2, b2 = _channels(second)
    mean_red = (r1 + r2) / 2
    dr, dg, db = r1 - r2, g1 - g2, b1 - b2
    return sqrt((2 + mean_red / 256) * dr * dr + 4 * dg * dg
                + (2 + (255 - mean_red) / 256) * db * db)


def test_registry_covers_every_tier_in_ladder_order():
    assert list(_cap_table()) == list(RANK_NAMES)


def test_every_pair_of_tiers_is_visually_distinct():
    table = _cap_table()
    tiers = list(table)
    for index, first in enumerate(tiers):
        for second in tiers[index + 1:]:
            distance = redmean(table[first], table[second])
            assert distance >= MIN_SEPARATION, (
                f"{first} {table[first]} and {second} {table[second]} are only "
                f"{distance:.0f} apart; the Iron/Silver pair that shipped as a "
                f"bug scored 168")


def test_the_guard_can_still_fail():
    """A guard that cannot fail is not one (tests/source_scan.py)."""
    assert redmean("#8a8a8a", "#c2c2c2") < MIN_SEPARATION   # the shipped bug
    assert redmean("#f5f7f8", "#eeeae4") < MIN_SEPARATION   # white vs off-white
    assert redmean("#e23b3b", "#3dc05c") > MIN_SEPARATION    # red vs green


def test_the_mask_and_the_shade_come_from_one_sprite():
    """Measured 2026-07-25: the tint is exact and backdrop-independent ONLY
    because the masked colour layer and the multiplied greyscale layer read the
    same PNG. Both rules must therefore resolve their art from the SAME custom
    property, so a call site cannot hand them different files."""
    css = strip_comments((CAPS_JS.parents[1] / "index.html").read_text(encoding="utf-8"))
    fill = re.search(r"\.hat \.fill\s*\{(.*?)\}", css, re.S)
    shade = re.search(r"\.hat \.shade\s*\{(.*?)\}", css, re.S)
    assert fill and shade, "the .hat .fill / .hat .shade rules are missing"
    assert "var(--art)" in fill.group(1) and "var(--art)" in shade.group(1), (
        "both layers must take their art from --art; two sources let the "
        "page backdrop leak into the multiply")
    assert "mix-blend-mode: multiply" in shade.group(1)


def test_the_glyph_rule_outranks_the_layer_rule():
    """`.hat i { inset: 0; display: block }` is class+element and beats a bare
    `.glyph` class, which silently parked the numeral outside the cap twice
    during design. The glyph rule needs two classes."""
    css = strip_comments((CAPS_JS.parents[1] / "index.html").read_text(encoding="utf-8"))
    assert ".hat .glyph" in css, "the glyph rule must be .hat .glyph, not .glyph"
    assert re.search(r"\.glyph\s*\{[^}]*inset:\s*auto[^}]*left:", css, re.S), (
        "inset is the shorthand for top/right/bottom/left -- declaring it AFTER "
        "left/top resets them; it must come first")


def test_division_five_wears_no_wings_and_division_one_wears_four():
    source = strip_comments(CAPS_JS.read_text(encoding="utf-8"))
    assert "5 - digit" in source, "wingTiers must map division 5 -> 0 wings"

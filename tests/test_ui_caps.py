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

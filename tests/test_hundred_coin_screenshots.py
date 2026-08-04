"""The bundled 100-coin ladders, checked against the user's own screenshots.

He captured every 100-coin rank table on xcams on 2026-07-30 and asked, when
this was built, that the scrape be confirmed against them rather than trusted:
*"since I gave you the screenshots of all of the rank standards, you should
actually already know each of them. When we scrape, we should confirm that the
scraped results match the screenshots"*.

That is worth a test rather than a one-off look, because it is the ONE check
independent of our own parser. `tools/scrape_ranks.py` and the xcams page read
the same embedded blob, so a bug shared by both — a JP/US swap, a column
misread, an off-by-one exit star — reproduces identically in the scrape and in
any assertion derived from it. The values below were read off the PICTURES.

Two conventions, both deliberate:

* **US-effective seconds**, matching what the seed stores (`parse_standards`).
  The screenshots mostly show JP primary with "(x US)" beside it, but four of
  them are in US mode with "(x JP)" beside instead — the transcription takes
  the US number either way, which is itself a check that the seed did.
* **The EXIT STAR is pinned, not the variant label.** The star is the
  discriminator this whole feature turns on and is read off each screenshot's
  own star list (JRB's "100c + Red Coins on the Ship Afloat" is its 4th star,
  so exit star 3). The label is xcams' display copy and may be restyled.

A ladder absent from a screenshot is simply not listed here: xcams adds
variants (THI's "100c + Rematch with Koopa the Quick" was dimmed — i.e. had no
community times — on 2026-07-30 and has a full ladder today), so this is a
SUBSET check. It catches a value that changed under us, never one that arrived.
"""
import json
from pathlib import Path

import pytest

from sm64_events.ranks.standards import VARIANT_SEP

BUNDLED_SEED = (Path(__file__).resolve().parent.parent / "src" / "sm64_events"
                / "data" / "rank_standards.seed.json")

# Mario -> Bronze; Iron is the unbounded floor and is hatched in every capture.
LADDER_RANKS = ["Mario", "Grandmaster", "Master", "Diamond", "Platinum",
                "Gold", "Silver", "Bronze"]

# {course: {exit_star: {strategy: [8 US seconds, None where the cell is hatched]}}}
SCREENSHOTS = {
    1: {3: {  # BoB — "100c + Find the 8 Red Coins"
        "Standard": [102.20, 105.93, 111.53, 119.43, 127.90, 140.66, 149.90,
                     182.20]}},
    2: {3: {  # WF — "100c + Red Coins on the Floating Isle"
        "Kanno Cycle": [56.06, 56.40, 56.80, 57.46, 58.90, None, None, None],
        "Pro Cycle": [57.70, 58.46, 58.86, 59.53, 60.96, 62.10, None, None],
        "Half Cycle Skip": [60.13, 60.86, 61.70, 62.43, 63.93, 65.10, 66.10,
                            69.60],
        "Half Cycle": [66.03, 66.56, 67.60, 68.33, 69.16, 75.30, 83.43,
                       89.36]}},
    3: {3: {  # JRB — "100c + Red Coins on the Ship Afloat"
        "Standard": [117.96, 119.46, 123.40, 128.23, 134.76, 144.23, 150.46,
                     172.23]}},
    4: {0: {  # CCM — "100c + Slip Slidin' Away"  (captured in US mode)
            "Open": [68.06, 69.00, 70.36, 71.43, 72.66, 74.20, 75.73, 81.10],
            "Standard": [69.13, 70.06, 71.43, 72.50, 73.73, 75.26, 76.80,
                         82.16]},
        2: {  # CCM — "100c + Big Penguin Race"
            "Open": [76.50, 77.23, 78.13, 78.93, 80.16, 81.06, 82.16, 86.00],
            "Atmpas Route": [76.66, 77.33, 78.26, 79.10, 80.40, 81.33, 82.50,
                             86.56],
            "Standard": [77.80, 78.46, 79.40, 80.23, 81.53, 82.46, 83.63,
                         91.70]}},
    5: {3: {  # BBH — "100c + Seek the 8 Red Coins"
        "Standard": [104.06, 106.10, 109.06, 114.23, 119.60, 124.90, 133.20,
                     159.76]}},
    6: {1: {  # HMC — "100c + Elevate for 8 Red Coins"
        "Standard": [119.23, 124.73, 129.73, 141.03, 148.03, 163.66, 174.50,
                     212.40],
        "No BLJ": [140.40, 144.66, 148.53, 157.26, 162.66, 174.76, 183.13,
                   212.40]}},
    7: {4: {  # LLL — "100c + Hot-Foot-it into the Volcano"
        "Lavaboost": [69.16, 70.66, 71.70, 74.10, 76.66, 81.43, 84.70, 96.13],
        "Standard": [69.90, 71.40, 72.43, 74.83, 77.40, 82.16, 85.43, 96.86]}},
    8: {5: {  # SSL — "100c + Pyramid Puzzle"
        "Open": [105.30, 109.63, 115.46, 123.70, 133.63, 143.16, 152.70,
                 186.06],
        "Alt Pillar": [108.26, 112.50, 118.23, 126.30, 136.06, 145.43, 154.76,
                       187.50],
        "Standard": [110.06, 114.30, 120.03, 128.10, 137.86, 147.23, 156.56,
                     189.30]}},
    9: {2: {  # DDD — "100c + Pole-Jumping for Red Coins"
        "Standard": [114.16, 119.70, 126.53, 134.33, 144.23, 154.46, 164.70,
                     200.50],
        "Poles": [160.30, 169.10, 186.70, 195.76, 206.06, 210.53, 239.43,
                  269.80]}},
    10: {4: {  # SL — "100c + Shell Shreddin' for Red Coins"
        "Moneybag Dupe": [73.80, 77.60, 81.26, 89.66, 98.33, 106.66, 115.00,
                          144.13],
        "Standard": [98.96, 102.76, 106.43, 114.83, 123.50, 131.83, 140.16,
                     169.30],
        "Early Spindrifts": [99.00, 102.80, 106.46, 114.86, 123.53, 131.86,
                             140.20, 169.33]}},
    11: {2: {  # WDW — "100c + Secrets in the Shallows and Sky" (US mode)
             "Standard": [72.23, 73.96, 75.86, 78.46, 81.03, 85.26, 88.66,
                          100.53]},
         4: {  # WDW — "100c + Go to Town for Red Coins"
             "Standard": [89.83, 92.33, 94.66, 99.70, 103.90, 108.50, 113.10,
                          129.20]}},
    12: {2: {  # TTM — "100c + Scary 'Shrooms, Red Coins"
        "Standard": [96.23, 98.10, 99.36, 104.33, 112.43, 117.80, 123.16,
                     141.90],
        "Late Red": [96.23, 98.10, 99.36, 104.33, 112.43, 117.80, 123.16,
                     141.90]}},
    13: {4: {  # THI — "100c + Wiggler's Red Coin"
        "16c Start": [85.30, 88.03, 91.13, 98.83, 104.96, 111.86, 118.76,
                      142.93],
        "13c Start + Pole": [89.86, 92.60, 95.70, 103.40, 109.53, 116.43,
                             123.33, 147.50],
        "11c Start + Pole": [91.10, 94.03, 97.33, 105.56, 112.13, 119.53,
                             126.93, 152.80]}},
    14: {3: {  # TTC — "100c + Stomp on the Thwomp"
        "Standard": [58.50, 60.60, 62.73, 66.50, 69.83, 79.96, 95.16, 109.86],
        "Safety Red": [57.83, 59.93, 62.06, 65.83, 69.16, 79.30, 94.50,
                       109.20]}},
    15: {1: {  # RR — "100c + The Big House in the Sky" (the Cutscene tab)
             "Carpetless": [118.83, 121.00, 123.86, 125.86, 130.60, 134.86,
                            139.13, 154.03],
             "Carpetful": [167.26, 169.43, 172.30, 174.30, 179.03, 183.30,
                           187.56, 211.46]},
         5: {  # RR — "100c + Somewhere over the Rainbow" (the Cutscene tab)
             "Standard": [97.63, 99.50, 106.23, 110.96, 116.36, 123.13, 129.90,
                          153.53]}},
}


def _seed_entities():
    return json.loads(BUNDLED_SEED.read_text(encoding="utf-8"))["entities"]


@pytest.mark.parametrize("course", sorted(SCREENSHOTS))
def test_the_scraped_ladders_match_the_screenshots(course):
    entity = _seed_entities()[f"star:{course}:6"]
    variants = entity["exit_variants"]
    for exit_star, strategies in SCREENSHOTS[course].items():
        labels = [label for label, star in variants.items() if star == exit_star]
        assert len(labels) == 1, \
            f"course {course}: exit star {exit_star} has variants {labels}"
        for strategy, expected in strategies.items():
            name = labels[0] + VARIANT_SEP + strategy
            ladder = entity["strategies"].get(name)
            assert ladder is not None, f"missing ladder {name!r}"
            got = [ladder.get(rank) for rank in LADDER_RANKS]
            assert got == expected, f"{name}: {got} != {expected}"


def test_every_screenshot_row_names_a_real_star():
    """The exit star was read off each screenshot's own list of star buttons —
    "100c + Red Coins on the Ship Afloat" being JRB's fourth. This asserts the
    transcription is internally sane (six stars per main course) so a slip in
    the table above cannot pass by naming a star that does not exist."""
    for course, by_exit in SCREENSHOTS.items():
        for exit_star in by_exit:
            assert 0 <= exit_star <= 5, f"course {course}: exit star {exit_star}"


def test_the_screenshots_cover_every_main_course():
    assert sorted(SCREENSHOTS) == list(range(1, 16))

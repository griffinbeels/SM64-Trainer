from sm64_events.ranks.classify import (
    RANK_NAMES, RANK_SCORE, display_cs, rank_for, next_tier, band)

NUTS = {"Mario": 1293, "Grandmaster": 1303, "Master": 1316, "Diamond": 1336,
        "Platinum": 1416, "Gold": 1566, "Silver": 1676}  # centiseconds

def test_rank_order_and_score():
    assert RANK_NAMES[0] == "Mario" and RANK_NAMES[-1] == "Iron"
    assert RANK_SCORE["Mario"] == 9 and RANK_SCORE["Iron"] == 1

def test_display_cs_matches_format_igt():
    # 388 frames -> 12"93 displayed -> 1293 cs
    assert display_cs(388) == 1293
    assert display_cs(1830) == 6100  # 61.00s

def test_rank_for_picks_best_beaten_tier():
    assert rank_for(NUTS, 1290) == "Mario"     # beats Mario threshold
    assert rank_for(NUTS, 1326) == "Diamond"   # 13.26
    assert rank_for(NUTS, 1700) == "Iron"      # slower than Silver -> floor
    assert rank_for({}, 1326) is None          # no standards

def test_next_tier():
    assert next_tier(NUTS, "Diamond") == "Master"
    assert next_tier(NUTS, "Mario") is None
    assert next_tier(NUTS, "Iron") == "Silver"  # easiest defined tier

def test_band_midway():
    b = band(NUTS, 1326)  # Diamond, halfway to Master
    assert b["rank"] == "Diamond" and b["next"] == "Master"
    assert b["gap_cs"] == 10 and abs(b["fill"] - 0.5) < 1e-9

def test_band_top_tier_has_no_bar():
    b = band(NUTS, 1290)
    assert b["rank"] == "Mario" and b["next"] is None and b["fill"] is None

def test_band_floor_empty_bar():
    b = band(NUTS, 1700)
    assert b["rank"] == "Iron" and b["next"] == "Silver"
    assert b["fill"] == 0.0 and b["gap_cs"] == 1700 - 1676

from sm64_events.ranks.classify import (
    RANK_NAMES, RANK_SCORE, IRON_SPAN_MULT, display_cs, rank_for, next_tier,
    band, resolve_cutoff_videos)

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

def test_band_iron_scales_from_notional_start():
    # Iron owns no cutoff, so its bar starts at IRON_SPAN_MULT x the easiest
    # defined tier: a time barely slower than that tier reads nearly full.
    b = band(NUTS, 1700)                        # 0.24s slower than Silver 1676
    assert b["rank"] == "Iron" and b["next"] == "Silver"
    assert b["gap_cs"] == 1700 - 1676
    start = IRON_SPAN_MULT * 1676
    assert abs(b["fill"] - (start - 1700) / (start - 1676)) < 1e-9
    assert b["fill"] > 0.99

def test_band_iron_live_report_reads_near_full():
    # user report 2026-07-25: a 7"53 PB against a 7.43s Bronze showed a flat 0%
    b = band({"Bronze": 743}, 753)
    assert b["rank"] == "Iron" and b["next"] == "Bronze"
    assert round(b["fill"] * 100) == 99

def test_band_iron_slower_than_notional_start_is_zero():
    b = band(NUTS, IRON_SPAN_MULT * 1676 + 1)   # genuinely that slow
    assert b["rank"] == "Iron" and b["fill"] == 0.0

def test_band_iron_degenerate_ladder_is_zero():
    # a zero cutoff leaves the notional start no span -> no progress to show
    assert band({"Bronze": 0}, 500)["fill"] == 0.0

def test_band_empty_ladder():
    assert band({}, 1326) == {"rank": None, "next": None, "gap_cs": None, "fill": None}


def test_resolve_cutoff_videos_bands_by_rank():
    clips = [[1290, "mario"], [1326, "diamond"], [1700, "iron-skip"]]
    out = resolve_cutoff_videos(NUTS, clips)
    assert out == {"Mario": "mario", "Diamond": "diamond"}  # Iron floor never shown


def test_resolve_cutoff_videos_keeps_fastest_per_tier():
    clips = [[1330, "slow-diamond"], [1320, "fast-diamond"]]  # both Diamond band
    assert resolve_cutoff_videos(NUTS, clips)["Diamond"] == "fast-diamond"


def test_resolve_cutoff_videos_override_wins_and_adds_a_tier():
    clips = [[1290, "auto-mario"]]
    out = resolve_cutoff_videos(NUTS, clips, {"Mario": "hand-mario", "Gold": "hand-gold"})
    assert out["Mario"] == "hand-mario"   # manual override beats the auto band pick
    assert out["Gold"] == "hand-gold"     # override adds a tier no clip reaches


def test_resolve_cutoff_videos_empty():
    assert resolve_cutoff_videos(NUTS, []) == {}
    assert resolve_cutoff_videos({}, [[1290, "x"]]) == {}  # no ladder -> no ranks


# -- rank modes (average rank mode spec) ---------------------------------------

def test_rank_modes_registry_complete():
    from sm64_events.ranks.classify import DEFAULT_RANK_MODE, RANK_MODES
    assert DEFAULT_RANK_MODE == "pb" and "pb" in RANK_MODES
    assert list(RANK_MODES) == ["pb", "avg10", "avg50", "best10", "best50",
                                "lifetime"]
    for mode_def in RANK_MODES.values():
        assert set(mode_def) == {"label", "window", "order"}
    assert RANK_MODES["pb"]["order"] is None
    assert RANK_MODES["avg10"] == {"label": "Avg 10", "window": 10,
                                   "order": "recent"}
    assert RANK_MODES["best50"] == {"label": "Best 50", "window": 50,
                                    "order": "top"}
    assert RANK_MODES["lifetime"] == {"label": "Lifetime", "window": None,
                                      "order": "recent"}


def test_average_frames_recent_takes_the_last_window():
    from sm64_events.ranks.classify import average_frames
    # last 2 of [300, 310, 320, 330] are 320+330 -> mean 325
    assert average_frames([300, 310, 320, 330], 2, "recent") == (325, 2)


def test_average_frames_top_takes_the_fastest_window():
    from sm64_events.ranks.classify import average_frames
    # fastest 2 are 300+310 -> mean 305, regardless of position
    assert average_frames([330, 300, 320, 310], 2, "top") == (305, 2)


def test_average_frames_under_window_and_lifetime():
    from sm64_events.ranks.classify import average_frames
    # fewer than window -> mean of what exists (count reports actual)
    assert average_frames([300, 310], 10, "recent") == (305, 2)
    assert average_frames([300, 310], 10, "top") == (305, 2)
    # window None -> every entry (Lifetime)
    assert average_frames([300, 301, 302], None, "recent") == (301, 3)


def test_average_frames_empty_is_none():
    from sm64_events.ranks.classify import average_frames
    assert average_frames([], 10, "recent") is None
    assert average_frames([], None, "recent") is None

import json, importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "scrape_ranks", Path(__file__).resolve().parent.parent / "tools" / "scrape_ranks.py")
scrape = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(scrape)

FIX = Path(__file__).resolve().parent / "fixtures" / "xcams_standards.json"

def test_parse_standards_ssl3_nuts_pless():
    raw = json.loads(FIX.read_text())
    out = scrape.parse_standards(raw)
    nuts = out["7_3"]["Nuts Pless"]
    assert nuts == {"Mario": 12.93, "Grandmaster": 13.03, "Master": 13.16,
                    "Diamond": 13.36, "Platinum": 14.16, "Gold": 15.66, "Silver": 16.76}
    assert "Iron" not in nuts and "Bronze" not in nuts   # floor / skipped tier

def test_parse_standards_excludes_iron_everywhere():
    out = scrape.parse_standards(json.loads(FIX.read_text()))
    for ent in out.values():
        for ladder in ent.values():
            assert "Iron" not in ladder
            assert all(isinstance(v, float) for v in ladder.values())

def test_key_to_entity():
    assert scrape.key_to_entity("7_3") == "star:8:2"      # SSL star 3
    assert scrape.key_to_entity("0_1") == "star:1:0"      # BoB star 1
    assert scrape.key_to_entity("0_100c4") is None        # 100-coin deferred
    assert scrape.key_to_entity("15_pss") == "star:19:0"  # Princess's Secret Slide
    assert scrape.key_to_entity("16_1n") == "segment:5"   # BitDW pipe entry (No Reds)
    assert scrape.key_to_entity("16_2x") == "segment:9"   # Bowser 2 battle
    # Reds IS a trainer entity — the Bowser course's 8-red-coin star (the
    # thing the stage banner targets as "Reds"). Mapping it to None dropped
    # every Bowser reds ladder from the seed (user-reported 2026-07-23).
    assert scrape.key_to_entity("16_1r") == "star:16:0"   # BitDW 8 Red Coins
    assert scrape.key_to_entity("16_2r") == "star:17:0"   # BitFS 8 Red Coins
    assert scrape.key_to_entity("16_3r") == "star:18:0"   # BitS 8 Red Coins

BUNDLED_SEED = (Path(__file__).resolve().parent.parent / "src" / "sm64_events"
                / "data" / "rank_standards.seed.json")

def test_bundled_seed_covers_every_bowser_practice_target():
    """All NINE Bowser targets ship with rank standards.

    The seed is generated, so a hole in key_to_entity is invisible in the
    mapping tests above — only a check on the OUTPUT catches it. Reds was
    missing for months exactly that way (user-reported 2026-07-23: "every
    bowser level is missing them"). Strat names and times change every
    xcams season; "has a ladder with a Mario cutoff" does not."""
    entities = json.loads(BUNDLED_SEED.read_text(encoding="utf-8"))["entities"]
    expected = {"star:16:0": "BitDW Reds", "star:17:0": "BitFS Reds",
                "star:18:0": "BitS Reds",
                "segment:5": "BitDW No Reds", "segment:6": "BitFS No Reds",
                "segment:7": "BitS No Reds",
                "segment:8": "BitDW Battle", "segment:9": "BitFS Battle",
                "segment:10": "BitS Battle"}
    missing = {label for ek, label in expected.items()
               if not (entities.get(ek) or {}).get("strategies")}
    assert not missing, f"Bowser targets with no rank standards: {sorted(missing)}"
    for ek, label in expected.items():
        for strat, ladder in entities[ek]["strategies"].items():
            assert "Mario" in ladder, f"{label} / {strat}: no Mario cutoff"

def test_apply_fixups_rewrites_only_the_exact_published_value():
    """The published value is part of the key so the fixup disarms itself
    once xcams edits that cell (seed-fix discipline: guard on the broken
    value, never blind-overwrite)."""
    key, strat = "16_2r", "No Early Ellies (Star)"
    parsed = {key: {strat: {"Mario": 10.0, "Grandmaster": 10.03,
                            "Diamond": 70.76}}}
    fixed = scrape.apply_fixups(parsed)
    assert fixed[key][strat]["Mario"] == 70.0          # dropped minute restored
    assert fixed[key][strat]["Diamond"] == 70.76       # untouched
    assert parsed[key][strat]["Mario"] == 10.0         # input not mutated
    moved = {key: {strat: {"Mario": 11.5}}}            # upstream changed it
    assert scrape.apply_fixups(moved)[key][strat]["Mario"] == 11.5

def test_suspect_dropped_minute_flags_the_60s_step():
    """Monotonicity can't see this: 10.00 sorts fine before 1:10.76."""
    parsed = {"16_2r": {"NEE": {"Master": 10.06, "Diamond": 70.76,
                                "Platinum": 72.13}}}
    assert scrape.suspect_dropped_minute(parsed) == [("16_2r", "NEE", "Master")]
    ok = {"7_3": {"Nuts Pless": {"Mario": 12.93, "Grandmaster": 13.03}}}
    assert scrape.suspect_dropped_minute(ok) == []

def test_bundled_seed_has_no_dropped_minute_cells():
    """Every shipped ladder is free of the unreachable-top-tier bug — the
    next season's scrape must report a NEW one, not ship it silently."""
    entities = json.loads(BUNDLED_SEED.read_text(encoding="utf-8"))["entities"]
    as_parsed = {ek: ent.get("strategies", {}) for ek, ent in entities.items()}
    assert scrape.suspect_dropped_minute(as_parsed) == []

def test_build_seed_maps_and_adds_segment_defaults():
    parsed = {"7_3": {"Nuts Pless": {"Mario": 12.93}}, "0_100c4": {"x": {"Mario": 1.0}}}
    seed = scrape.build_seed(parsed)
    assert seed["version"] == 4
    assert seed["entities"]["star:8:2"]["clock"] == "igt"
    assert seed["entities"]["star:8:2"]["strategies"]["Nuts Pless"]["Mario"] == 12.93
    assert "star:1:6" not in seed["entities"]               # 100-coin skipped
    assert seed["entities"]["segment:1"]["clock"] == "rta"  # LBLJ default present

def test_extract_standards_blob_picks_the_standards_object():
    js = ("var x=JSON.parse('{\"misc\":1}');"
          "var H=JSON.parse('{\"7_3\":{\"Nuts Pless\":{\"name\":\"Nuts Pless\","
          "\"times\":{\"Mario\":{\"sr\":\"time\",\"time\":{\"time\":1293}}}}}}');")
    blob = scrape.extract_standards_blob(js)
    assert blob["7_3"]["Nuts Pless"]["times"]["Mario"]["time"]["time"] == 1293

def test_extract_standards_blob_skips_viewer_blob_with_list_times():
    js = ("var g=JSON.parse('{\"335\":{\"strat\":{\"stage\":\"x\"},"
          "\"times\":[{\"player\":\"a\",\"ms\":6380}]}}');"
          "var H=JSON.parse('{\"7_3\":{\"Nuts Pless\":{\"name\":\"Nuts Pless\","
          "\"times\":{\"Mario\":{\"sr\":\"time\",\"time\":{\"time\":1293}}}}}}');")
    blob = scrape.extract_standards_blob(js)
    assert "7_3" in blob   # picked the standards blob, not the viewer (list times)


def test_time_to_cs():
    assert scrape._time_to_cs("12.60") == 1260
    assert scrape._time_to_cs("1:20.63") == 8063
    assert scrape._time_to_cs(None) is None
    assert scrape._time_to_cs("-") is None


_CAMS = [{"ext": {"182": {"record": "12.60", "link": "https://youtu.be/A", "ideal": None, "idealLink": None},
                  "9":   {"record": "12.40", "link": None, "ideal": "12.0", "idealLink": "https://youtu.be/IDEAL"}},
          "main": {"5": {"record": "13.00", "link": "https://youtu.be/SLOW", "ideal": None, "idealLink": None}}}]


def test_strat_videos_picks_fastest_record_with_link():
    star = {"jp_set": {"Nuts": {"id_list": [["ext", 182]]},
                        "Multi": {"id_list": [["ext", 182], ["main", 5]]}},
            "us_set": {}}
    out = scrape.strat_videos(star, _CAMS)
    assert out["Nuts"] == "https://youtu.be/A"
    assert out["Multi"] == "https://youtu.be/A"        # 12.60 (A) beats 13.00 (SLOW)


def test_strat_videos_falls_back_to_ideallink_then_any_link():
    star = {"jp_set": {"NoRecLink": {"id_list": [["ext", 9]]}}, "us_set": {}}
    # ext/9 has no record link but has idealLink -> use idealLink
    assert scrape.strat_videos(star, _CAMS)["NoRecLink"] == "https://youtu.be/IDEAL"


def test_strat_videos_falls_back_to_any_link():
    cams = [{"ext": {"7": {"record": "-", "link": "https://youtu.be/ANY", "ideal": None, "idealLink": None}}}]
    star = {"jp_set": {"S": {"id_list": [["ext", 7]]}}, "us_set": {}}
    assert scrape.strat_videos(star, cams)["S"] == "https://youtu.be/ANY"


def test_build_seed_attaches_videos():
    parsed = {"7_3": {"Nuts": {"Mario": 12.6}}}
    catalog = [None]*7 + [{"starList": [{"id": "3", "name": "x",
                          "jp_set": {"Nuts": {"id_list": [["ext", 182]]}}, "us_set": {}}]}]
    seed = scrape.build_seed(parsed, catalog, _CAMS)
    assert seed["entities"]["star:8:2"]["videos"]["Nuts"] == "https://youtu.be/A"


def test_build_seed_without_catalog_omits_videos():
    seed = scrape.build_seed({"7_3": {"Nuts": {"Mario": 12.6}}})
    assert "videos" not in seed["entities"]["star:8:2"]
    assert "clips" not in seed["entities"]["star:8:2"]


def test_strat_clips_returns_all_timed_links_fastest_first():
    star = {"jp_set": {"Multi": {"id_list": [["main", 5], ["ext", 182]]}}, "us_set": {}}
    # main/5 = 13.00 (SLOW), ext/182 = 12.60 (A) -> sorted ascending by record cs
    out = scrape.strat_clips(star, _CAMS)
    assert out["Multi"] == [[1260, "https://youtu.be/A"], [1300, "https://youtu.be/SLOW"]]


def test_strat_clips_excludes_unrecorded_or_linkless():
    # ext/9 has an idealLink but no record-link -> not a usable timed clip
    star = {"jp_set": {"NoRecLink": {"id_list": [["ext", 9]]}}, "us_set": {}}
    assert "NoRecLink" not in scrape.strat_clips(star, _CAMS)


def test_strat_clips_dedupes_by_link_keeping_fastest():
    cams = [{"ext": {"1": {"record": "12.90", "link": "https://youtu.be/DUP"},
                     "2": {"record": "12.40", "link": "https://youtu.be/DUP"}}}]
    star = {"jp_set": {"S": {"id_list": [["ext", 1], ["ext", 2]]}}, "us_set": {}}
    assert scrape.strat_clips(star, cams)["S"] == [[1240, "https://youtu.be/DUP"]]


def test_build_seed_attaches_clips():
    parsed = {"7_3": {"Multi": {"Mario": 12.6}}}
    catalog = [None] * 7 + [{"starList": [{"id": "3", "name": "x",
                            "jp_set": {"Multi": {"id_list": [["main", 5], ["ext", 182]]}},
                            "us_set": {}}]}]
    seed = scrape.build_seed(parsed, catalog, _CAMS)
    assert seed["entities"]["star:8:2"]["clips"]["Multi"] == \
        [[1260, "https://youtu.be/A"], [1300, "https://youtu.be/SLOW"]]


def test_resolve_jp_us():
    assert scrape._resolve_jp_us({"sr": "time", "time": {"time": 4423, "alt": [4546, "us"]}}) == (4423, 4546)
    assert scrape._resolve_jp_us({"sr": "time", "time": {"time": 6913, "alt": [6950, "jp"]}}) == (6950, 6913)  # primary is US
    assert scrape._resolve_jp_us({"sr": "time", "time": {"time": 1293, "alt": None}}) == (1293, 1293)
    assert scrape._resolve_jp_us({"sr": "none"}) is None

_RAW = {"0_1": {"Standard": {"times": {
            "Mario": {"sr": "time", "time": {"time": 4423, "alt": [4546, "us"]}},
            "Iron":  {"sr": "none"}}}},
        "7_3": {"Nuts Pless": {"times": {
            "Mario": {"sr": "time", "time": {"time": 1293, "alt": None}}}}}}

def test_parse_standards_is_us_effective():
    out = scrape.parse_standards(_RAW)
    assert out["0_1"]["Standard"]["Mario"] == 45.46   # US value, not the 44.23 JP primary
    assert out["7_3"]["Nuts Pless"]["Mario"] == 12.93  # no alt -> unchanged
    assert "Iron" not in out["0_1"]["Standard"]

def test_parse_jp_deltas_only_where_differ():
    jp = scrape.parse_jp_deltas(_RAW)
    assert jp["0_1"]["Standard"]["Mario"] == 44.23     # JP value retained
    assert "7_3" not in jp                              # no diff -> no entry

def test_build_seed_attaches_jp_strategies():
    parsed = scrape.parse_standards(_RAW); jp = scrape.parse_jp_deltas(_RAW)
    seed = scrape.build_seed(parsed, jp_deltas=jp)
    assert seed["entities"]["star:1:0"]["strategies"]["Standard"]["Mario"] == 45.46
    assert seed["entities"]["star:1:0"]["jp_strategies"]["Standard"]["Mario"] == 44.23
    assert "jp_strategies" not in seed["entities"]["star:8:2"]

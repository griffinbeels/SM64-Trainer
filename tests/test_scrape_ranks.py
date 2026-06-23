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
    assert scrape.key_to_entity("16_3r") is None          # Reds: no trainer segment

def test_build_seed_maps_and_adds_segment_defaults():
    parsed = {"7_3": {"Nuts Pless": {"Mario": 12.93}}, "0_100c4": {"x": {"Mario": 1.0}}}
    seed = scrape.build_seed(parsed)
    assert seed["version"] == 1
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


def test_build_seed_attaches_videos():
    parsed = {"7_3": {"Nuts": {"Mario": 12.6}}}
    catalog = [None]*7 + [{"starList": [{"id": "3", "name": "x",
                          "jp_set": {"Nuts": {"id_list": [["ext", 182]]}}, "us_set": {}}]}]
    seed = scrape.build_seed(parsed, catalog, _CAMS)
    assert seed["entities"]["star:8:2"]["videos"]["Nuts"] == "https://youtu.be/A"


def test_build_seed_without_catalog_omits_videos():
    seed = scrape.build_seed({"7_3": {"Nuts": {"Mario": 12.6}}})
    assert "videos" not in seed["entities"]["star:8:2"]

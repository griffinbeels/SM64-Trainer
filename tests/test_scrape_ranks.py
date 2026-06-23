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

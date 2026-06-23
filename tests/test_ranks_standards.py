import json
from sm64_events.ranks.standards import RankStandards, entity_key, RANK_COLORS

def _seed(tmp_path):
    p = tmp_path / "seed.json"
    p.write_text(json.dumps({"version": 1, "entities": {
        "star:9:2": {"clock": "igt", "strategies": {
            "Nuts Pless": {"Mario": 12.93, "Master": 13.16, "Diamond": 13.36}}}}}))
    return p

def test_entity_key_and_colors():
    assert entity_key(9, 2) == "star:9:2"
    assert entity_key(None, None, 8) == "segment:8"
    assert RANK_COLORS["Mario"].startswith("#")

def test_first_run_materializes_seed(tmp_path):
    data = tmp_path / "rs.json"
    s = RankStandards(data, seed_path=_seed(tmp_path))
    s.load()
    assert data.exists()                                   # seed copied in
    assert s.ladder_cs("star:9:2", "Nuts Pless")["Mario"] == 1293
    assert s.clock_for("star:9:2") == "igt"

def test_corrupt_file_falls_back_to_seed(tmp_path):
    data = tmp_path / "rs.json"; data.write_text("{bad json")
    s = RankStandards(data, seed_path=_seed(tmp_path)); s.load()
    assert s.strategies("star:9:2") == ["Nuts Pless"]

def test_missing_everything_is_empty(tmp_path):
    s = RankStandards(tmp_path / "rs.json", seed_path=None); s.load()
    assert s.ladders("star:9:2") == {}
    assert s.clock_for("segment:8") == "rta"

def test_crud_round_trip(tmp_path):
    s = RankStandards(tmp_path / "rs.json", seed_path=_seed(tmp_path)); s.load()
    s.set_threshold("star:9:2", "Nuts Pless", "Platinum", 14.16)
    s.create_strategy("star:9:2", "Shell")
    s2 = RankStandards(tmp_path / "rs.json"); s2.load()       # reload from disk
    assert s2.ladder_cs("star:9:2", "Nuts Pless")["Platinum"] == 1416
    assert "Shell" in s2.strategies("star:9:2")
    s2.delete_strategy("star:9:2", "Shell")
    assert "Shell" not in s2.strategies("star:9:2")

def test_reset_entity_restores_seed(tmp_path):
    s = RankStandards(tmp_path / "rs.json", seed_path=_seed(tmp_path)); s.load()
    s.set_threshold("star:9:2", "Nuts Pless", "Mario", 99.0)
    s.reset_entity("star:9:2")
    assert s.ladder_cs("star:9:2", "Nuts Pless")["Mario"] == 1293

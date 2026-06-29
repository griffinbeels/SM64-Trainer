import json
import pytest
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

def test_set_threshold_rejects_iron_and_unknown(tmp_path):
    s = RankStandards(tmp_path / "rs.json"); s.load()
    with pytest.raises(ValueError):
        s.set_threshold("star:9:2", "Nuts Pless", "Iron", 5.0)
    with pytest.raises(ValueError):
        s.set_threshold("star:9:2", "Nuts Pless", "NotARank", 5.0)

def test_videos_accessors(tmp_path):
    import json
    p = tmp_path / "rs.json"
    p.write_text(json.dumps({"version": 1, "entities": {
        "star:8:2": {"clock": "igt", "strategies": {"Nuts": {"Mario": 12.6}},
                     "videos": {"Nuts": "https://youtu.be/A"}}}}))
    s = RankStandards(p); s.load()
    assert s.videos("star:8:2") == {"Nuts": "https://youtu.be/A"}
    assert s.video_for("star:8:2", "Nuts") == "https://youtu.be/A"
    assert s.video_for("star:8:2", "Missing") is None
    assert s.videos("segment:99") == {}        # absent entity -> empty


def test_clips_and_cutoff_videos_accessors(tmp_path):
    import json
    p = tmp_path / "rs.json"
    p.write_text(json.dumps({"version": 3, "entities": {
        "star:8:2": {"clock": "igt",
            "strategies": {"Nuts": {"Mario": 12.93, "Diamond": 13.36}},
            "clips": {"Nuts": [[1290, "mario"], [1326, "diamond"]]}}}}))
    s = RankStandards(p); s.load()
    assert s.clips("star:8:2")["Nuts"] == [[1290, "mario"], [1326, "diamond"]]
    assert s.cutoff_videos("star:8:2")["Nuts"] == {"Mario": "mario", "Diamond": "diamond"}
    assert s.clips("segment:99") == {}                 # absent entity -> empty


def test_set_and_clear_video_override(tmp_path):
    s = RankStandards(tmp_path / "rs.json", seed_path=_seed(tmp_path)); s.load()
    s.set_video("star:9:2", "Nuts Pless", "Gold", "https://youtu.be/gold")
    s2 = RankStandards(tmp_path / "rs.json"); s2.load()                 # reload from disk
    assert s2.user_videos("star:9:2")["Nuts Pless"]["Gold"] == "https://youtu.be/gold"
    assert s2.cutoff_videos("star:9:2")["Nuts Pless"]["Gold"] == "https://youtu.be/gold"
    s2.clear_video("star:9:2", "Nuts Pless", "Gold")
    assert s2.user_videos("star:9:2") == {}            # empties cleaned up


def test_set_video_rejects_iron_and_unknown(tmp_path):
    s = RankStandards(tmp_path / "rs.json"); s.load()
    with pytest.raises(ValueError):
        s.set_video("star:9:2", "Nuts", "Iron", "x")
    with pytest.raises(ValueError):
        s.set_video("star:9:2", "Nuts", "Nope", "x")


def _write(p, data):
    import json; p.write_text(json.dumps(data))


def test_reconcile_preserves_user_videos(tmp_path):
    stored = {"version": 2, "entities": {
        "star:8:2": {"clock": "igt", "strategies": {"Nuts Pless": {"Mario": 44.23}},
                     "user_videos": {"Nuts Pless": {"Gold": "https://youtu.be/mine"}}}}}
    seed = {"version": 3, "entities": {
        "star:8:2": {"clock": "igt", "strategies": {"Nuts Pless": {"Mario": 45.46}},
                     "clips": {"Nuts Pless": [[4500, "auto"]]}}}}
    data = tmp_path / "rs.json"; seedf = tmp_path / "seed.json"
    _write(data, stored); _write(seedf, seed)
    s = RankStandards(data, seed_path=seedf); s.load()
    assert s.ladder_cs("star:8:2", "Nuts Pless")["Mario"] == 4546        # community refreshed
    assert s.clips("star:8:2")["Nuts Pless"] == [[4500, "auto"]]          # new clips pulled in
    assert s.user_videos("star:8:2")["Nuts Pless"]["Gold"] == "https://youtu.be/mine"  # kept

def test_load_reconciles_older_stored_seed_to_newer_bundled(tmp_path):
    # stored: version 1, no videos, old (JP-ish) time + a user-created entity/strat
    stored = {"version": 1, "entities": {
        "star:8:2": {"clock": "igt", "strategies": {"Nuts Pless": {"Mario": 44.23}}},
        "segment:99": {"clock": "rta", "strategies": {"MyStrat": {"Mario": 5.0}}}}}  # user-created entity
    seed = {"version": 2, "entities": {
        "star:8:2": {"clock": "igt",
                     "strategies": {"Nuts Pless": {"Mario": 45.46}},   # US-corrected
                     "videos": {"Nuts Pless": "https://youtu.be/A"},
                     "jp_strategies": {"Nuts Pless": {"Mario": 44.23}}}}}
    data = tmp_path / "rs.json"; seedf = tmp_path / "seed.json"
    _write(data, stored); _write(seedf, seed)
    s = RankStandards(data, seed_path=seedf); s.load()
    # community data refreshed from the newer seed:
    assert s.ladder_cs("star:8:2", "Nuts Pless")["Mario"] == 4546   # US now, not 4423
    assert s.video_for("star:8:2", "Nuts Pless") == "https://youtu.be/A"
    assert s.videos("star:8:2") and s._entity("star:8:2").get("jp_strategies")
    # user-created entity preserved:
    assert s.ladder_cs("segment:99", "MyStrat")["Mario"] == 500
    assert s.to_json()["version"] == 2                              # bumped
    # and persisted to disk:
    s2 = RankStandards(data); s2.load()
    assert s2.video_for("star:8:2", "Nuts Pless") == "https://youtu.be/A"

def test_load_preserves_user_created_strat_on_reconcile(tmp_path):
    stored = {"version": 1, "entities": {
        "star:8:2": {"clock": "igt", "strategies": {
            "Nuts Pless": {"Mario": 44.23}, "MyCustom": {"Mario": 9.9}}}}}  # MyCustom not in seed
    seed = {"version": 2, "entities": {
        "star:8:2": {"clock": "igt", "strategies": {"Nuts Pless": {"Mario": 45.46}}}}}
    data = tmp_path / "rs.json"; seedf = tmp_path / "seed.json"
    _write(data, stored); _write(seedf, seed)
    s = RankStandards(data, seed_path=seedf); s.load()
    assert s.ladder_cs("star:8:2", "Nuts Pless")["Mario"] == 4546   # refreshed
    assert s.ladder_cs("star:8:2", "MyCustom")["Mario"] == 990      # user strat kept

def test_load_no_reconcile_when_version_not_older(tmp_path):
    stored = {"version": 2, "entities": {"star:8:2": {"clock": "igt",
              "strategies": {"Nuts Pless": {"Mario": 12.0}}}}}
    seed = {"version": 2, "entities": {"star:8:2": {"clock": "igt",
            "strategies": {"Nuts Pless": {"Mario": 99.0}}, "videos": {"Nuts Pless": "x"}}}}
    data = tmp_path / "rs.json"; seedf = tmp_path / "seed.json"
    _write(data, stored); _write(seedf, seed)
    s = RankStandards(data, seed_path=seedf); s.load()
    assert s.ladder_cs("star:8:2", "Nuts Pless")["Mario"] == 1200   # stored kept, NOT 99
    assert s.videos("star:8:2") == {}                                # not pulled in

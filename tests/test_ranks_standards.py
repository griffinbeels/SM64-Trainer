import json
from pathlib import Path
import pytest
from sm64_events.ranks.standards import RankStandards, entity_key
from sm64_events.ranks.classify import RANK_NAMES as RANK_NAMES_FOR_GUARD

def _seed(tmp_path):
    p = tmp_path / "seed.json"
    p.write_text(json.dumps({"version": 1, "entities": {
        "star:9:2": {"clock": "igt", "strategies": {
            "Nuts Pless": {"Mario": 12.93, "Master": 13.16, "Diamond": 13.36}}}}}))
    return p

def test_entity_key_and_colors():
    assert entity_key(9, 2) == "star:9:2"
    assert entity_key(None, None, 8) == "segment:8"

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


def test_cutoff_videos_merges_extra_clips_into_the_band_resolution(tmp_path):
    """Task 0098: library clips widen the pool BEFORE banding, so the fastest
    example within a tier's band wins whichever source it came from, and a
    user override still outranks both."""
    import json
    p = tmp_path / "rs.json"
    p.write_text(json.dumps({"version": 3, "entities": {
        "star:8:2": {"clock": "igt",
            "strategies": {"Nuts": {"Mario": 12.93, "Diamond": 13.36}},
            "clips": {"Nuts": [[1326, "diamond-vetted"]]},
            "user_videos": {"Nuts": {"Diamond": "diamond-override"}}}}}))
    s = RankStandards(p); s.load()
    extra = {"Nuts": [[1290, "mario-library"], [1320, "diamond-library"]]}
    resolved = s.cutoff_videos("star:8:2", extra)["Nuts"]
    assert resolved["Mario"] == "mario-library"        # a gap the library fills
    assert resolved["Diamond"] == "diamond-override"   # override still wins
    without_override = RankStandards(p)
    without_override.load()
    without_override.clear_video("star:8:2", "Nuts", "Diamond")
    resolved = without_override.cutoff_videos("star:8:2", extra)["Nuts"]
    assert resolved["Diamond"] == "diamond-library"    # 1320 beats vetted 1326


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

def test_load_preserves_a_user_created_strats_jp_overlay_on_reconcile(tmp_path):
    """A typed JP time (the editor writes jp_strategies) on a USER-CREATED
    strategy survives a seed bump exactly as its US ladder does; on a SEEDED
    strategy it loses to the seed exactly as a typed US time does (whole-
    branch review, 2026-08-15: before this the JP half of a user strategy
    vanished on the next seed while its US half stayed)."""
    stored = {"version": 1, "entities": {
        "star:8:2": {"clock": "igt",
                     "strategies": {"Nuts Pless": {"Mario": 44.23}, "MyCustom": {"Mario": 9.9}},
                     "jp_strategies": {"Nuts Pless": {"Mario": 40.0},      # typed onto a seeded strat
                                       "MyCustom": {"Mario": 8.8}}}}}      # typed onto his own
    seed = {"version": 2, "entities": {
        "star:8:2": {"clock": "igt", "strategies": {"Nuts Pless": {"Mario": 45.46}},
                     "jp_strategies": {"Nuts Pless": {"Mario": 44.23}}}}}
    data = tmp_path / "rs.json"; seedf = tmp_path / "seed.json"
    _write(data, stored); _write(seedf, seed)
    s = RankStandards(data, seed_path=seedf); s.load()
    assert s.jp_deltas("star:8:2", "MyCustom") == {"Mario": 8.8}       # his overlay kept
    assert s.jp_deltas("star:8:2", "Nuts Pless") == {"Mario": 44.23}   # the seed's wins
    assert s.ladder_cs("star:8:2", "MyCustom", "jp")["Mario"] == 880

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


def test_seeded_strategies_lists_seed_strats_only(tmp_path):
    s = RankStandards(tmp_path / "rs.json", seed_path=_seed(tmp_path)); s.load()
    ek = next(iter(s.to_json()["entities"]))
    seed_strats = s.seeded_strategies(ek)
    assert seed_strats == s.strategies(ek)          # fresh install: store == seed
    s.create_strategy(ek, "customx")
    assert "customx" in s.strategies(ek)
    assert "customx" not in s.seeded_strategies(ek)  # custom never seeded


def test_seeded_strategies_without_seed_is_empty(tmp_path):
    s = RankStandards(tmp_path / "rs.json", seed_path=None); s.load()
    assert s.seeded_strategies("star:1:0") == []


# ---- a strategy the seed MOVED to another entity (2026-09-01) ----
#
# Seed v6 split the Princess's Secret Slide ladders: "Under 21" left the
# plain Slide Star (star:19:0) for the Under-21 star (star:19:1). _reconcile
# preserved the old copy as if the user had created it -- absent from the
# seed's entity is all it can see -- and a file already AT v6 never
# reconciles again. Measured on all three live files: every one held the
# stale copy, byte-equal to the ladder pinned in SEED_MOVES.

def _slide_stored(version, extra_19_1=None, stale_ladder=None):
    from sm64_events.ranks.standards import SEED_MOVES
    _new_ek, pinned = SEED_MOVES[("star:19:0", "Under 21")]
    stale = dict(pinned) if stale_ladder is None else stale_ladder
    return {"version": version, "entities": {
        "star:19:0": {"clock": "igt",
                      "strategies": {"Box Star": {"Mario": 12.0}, "Under 21": stale},
                      "jp_strategies": {"Under 21": {"Mario": 20.5}}},
        "star:19:1": {"clock": "igt",
                      "strategies": {"Under 21": dict(pinned), **(extra_19_1 or {})}}}}


def _slide_seed():
    from sm64_events.ranks.standards import SEED_MOVES
    _new_ek, pinned = SEED_MOVES[("star:19:0", "Under 21")]
    return {"version": 6, "entities": {
        "star:19:0": {"clock": "igt", "strategies": {"Box Star": {"Mario": 12.0}}},
        "star:19:1": {"clock": "igt", "strategies": {"Under 21": dict(pinned)}}}}


def test_load_drops_a_strategy_the_seed_moved_to_another_entity(tmp_path):
    """The live shape in this worktree: stored ALREADY at the seed's version,
    so no reconcile runs, and the stale copy has to be caught on a plain
    load -- and written back, so the next load finds a clean file."""
    data = tmp_path / "rs.json"; seedf = tmp_path / "seed.json"
    _write(data, _slide_stored(6)); _write(seedf, _slide_seed())
    s = RankStandards(data, seed_path=seedf); s.load()
    assert s.strategies("star:19:0") == ["Box Star"]
    assert s.jp_deltas("star:19:0", "Under 21") == {}          # the overlay went with it
    assert s.strategies("star:19:1") == ["Under 21"]           # the new home untouched
    s2 = RankStandards(data); s2.load()                        # persisted, not just in memory
    assert s2.strategies("star:19:0") == ["Box Star"]


def test_the_moved_strategy_repair_runs_after_a_reconcile_too(tmp_path):
    """Main's and the installed exe's shape: stored v5, which the v6 seed
    reconciles -- and the reconcile is exactly what preserves the stale copy,
    so the repair must run AFTER it. The user-created "Standard" on the
    new home is his and stays."""
    data = tmp_path / "rs.json"; seedf = tmp_path / "seed.json"
    _write(data, _slide_stored(5, extra_19_1={"Standard": {"Mario": 30.0}}))
    _write(seedf, _slide_seed())
    s = RankStandards(data, seed_path=seedf); s.load()
    assert s.to_json()["version"] == 6
    assert s.strategies("star:19:0") == ["Box Star"]
    assert sorted(s.strategies("star:19:1")) == ["Standard", "Under 21"]


def test_the_repair_keeps_a_moved_strategy_he_edited(tmp_path):
    """The guard is the exact stale value: a ladder he has since typed over
    is his own data, whatever its name, and stays where he put it."""
    data = tmp_path / "rs.json"; seedf = tmp_path / "seed.json"
    _write(data, _slide_stored(6, stale_ladder={"Mario": 19.0})); _write(seedf, _slide_seed())
    s = RankStandards(data, seed_path=seedf); s.load()
    assert sorted(s.strategies("star:19:0")) == ["Box Star", "Under 21"]
    assert s.ladder_cs("star:19:0", "Under 21")["Mario"] == 1900


def test_the_repair_carries_a_hand_attached_video_to_the_new_home(tmp_path):
    stored = _slide_stored(6)
    stored["entities"]["star:19:0"]["user_videos"] = {"Under 21": {"Gold": "https://youtu.be/mine"}}
    data = tmp_path / "rs.json"; seedf = tmp_path / "seed.json"
    _write(data, stored); _write(seedf, _slide_seed())
    s = RankStandards(data, seed_path=seedf); s.load()
    assert s.user_videos("star:19:0") == {}
    assert s.user_videos("star:19:1")["Under 21"]["Gold"] == "https://youtu.be/mine"


def test_a_clean_file_is_not_rewritten_by_the_repair(tmp_path):
    """Idempotent, and quiet: a second load of a repaired file, or a load of
    a file that never held the stale copy, writes nothing."""
    data = tmp_path / "rs.json"; seedf = tmp_path / "seed.json"
    _write(data, _slide_stored(6)); _write(seedf, _slide_seed())
    RankStandards(data, seed_path=seedf).load()                # repairs + writes
    before = data.read_text()
    RankStandards(data, seed_path=seedf).load()                # nothing left to repair
    assert data.read_text() == before
    clean = _slide_seed(); _write(data, clean)
    untouched = data.read_text()
    RankStandards(data, seed_path=seedf).load()
    assert data.read_text() == untouched


def test_every_seed_move_is_true_of_the_bundled_seed():
    """A row outlives its usefulness silently: the bundled seed must file the
    strategy under the NEW home and nowhere under the old one, or the table
    is describing a move the seed never made."""
    import sm64_events
    from sm64_events.ranks.standards import SEED_MOVES
    bundled = Path(sm64_events.__file__).parent / "data" / "rank_standards.seed.json"
    seed = json.loads(bundled.read_text(encoding="utf-8"))["entities"]
    assert SEED_MOVES, "the table exists to hold at least the 2026-08-31 slide split"
    for (old_ek, strat), (new_ek, pinned) in SEED_MOVES.items():
        assert strat in seed[new_ek]["strategies"], (old_ek, strat, new_ek)
        assert strat not in seed[old_ek].get("strategies", {}), (old_ek, strat)
        assert set(pinned) <= set(RANK_NAMES_FOR_GUARD), (old_ek, strat)

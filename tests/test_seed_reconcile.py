"""Editable-defaults seed reconcile (spec 2026-07-23-default-routes-foundation).

Mirrors ranks/standards's reconcile tests: a bundled seed inserts missing
rows, refreshes untouched (seed_dirty=0) seeded rows, and never touches
edited (seed_dirty=1) or user-created rows. The real-bundled-seed test is
the gate proving the shipped defaults.seed.json cannot corrupt a live
install's already-migrated LBLJ/Bowser rows (v5/v6 repairs)."""
import json

from sm64_events.core.paths import bundled_defaults_seed
from sm64_events.storage.db import Database
from sm64_events.tracking.defaults import reconcile_defaults
from sm64_events.tracking.segments import validate_definition

SEED_V1 = {"seed_version": 1,
    "segments": [{"seed_key": "seg:demo", "name": "Demo", "enabled": True,
        "start_triggers": [{"type": "spawned", "level": 16}],
        "end_triggers": [{"type": "level_enter", "to": 6}],
        "waypoints": [], "guards": [], "category": "Tricks"}],
    "routes": [{"seed_key": "route:demo", "name": "Demo Route",
        "category": "Main Categories", "start_condition": {"type": "reset_game"},
        "steps": [{"need": 1, "candidates": [{"type": "segment",
                                              "seed_key": "seg:demo"}]}]}]}


def test_reconcile_inserts_seed_rows(tmp_path):
    db = Database(tmp_path / "t.db")
    reconcile_defaults(db, SEED_V1)
    seg = next(s for s in db.segment_defs() if s["seed_key"] == "seg:demo")
    route = next(r for r in db.routes() if r["seed_key"] == "route:demo")
    # route candidate resolved seed_key -> the new local segment id
    assert route["steps"][0]["candidates"][0]["segment_id"] == seg["id"]
    assert seg["seed_dirty"] == 0
    assert route["category"] == "Main Categories"


def test_reconcile_refreshes_untouched_but_not_dirty(tmp_path):
    db = Database(tmp_path / "t.db")
    reconcile_defaults(db, SEED_V1)
    seg = next(s for s in db.segment_defs() if s["seed_key"] == "seg:demo")
    seed2 = json.loads(json.dumps(SEED_V1)); seed2["seed_version"] = 2
    seed2["segments"][0]["name"] = "Demo v2"
    reconcile_defaults(db, seed2)
    assert next(s for s in db.segment_defs() if s["id"] == seg["id"])["name"] == "Demo v2"
    # now dirty it, bump again -> left alone
    db.update_segment_def(seg["id"], name="Mine"); db.set_seed_dirty("segment_defs", seg["id"], 1)
    seed3 = json.loads(json.dumps(SEED_V1)); seed3["seed_version"] = 3
    seed3["segments"][0]["name"] = "Demo v3"
    reconcile_defaults(db, seed3)
    assert next(s for s in db.segment_defs() if s["id"] == seg["id"])["name"] == "Mine"


def test_reconcile_leaves_user_rows(tmp_path):
    db = Database(tmp_path / "t.db")
    uid = db.insert_segment_def("User", [{"type": "spawned", "level": 16}],
        [{"type": "level_enter", "to": 6}], [], "2026-07-23T00:00:00Z")
    reconcile_defaults(db, SEED_V1)
    assert any(s["id"] == uid and s["seed_key"] is None for s in db.segment_defs())


def test_reconcile_leaves_dirty_route_alone(tmp_path):
    db = Database(tmp_path / "t.db")
    reconcile_defaults(db, SEED_V1)
    route = next(r for r in db.routes() if r["seed_key"] == "route:demo")
    db.update_route(route["id"], name="My Route")
    db.set_seed_dirty("routes", route["id"], 1)
    seed2 = json.loads(json.dumps(SEED_V1)); seed2["seed_version"] = 2
    seed2["routes"][0]["name"] = "Demo Route v2"
    reconcile_defaults(db, seed2)
    assert next(r for r in db.routes() if r["id"] == route["id"])["name"] == "My Route"


def test_resolve_steps_unresolved_key_becomes_negative_one(tmp_path):
    db = Database(tmp_path / "t.db")
    seed = json.loads(json.dumps(SEED_V1))
    seed["routes"][0]["steps"][0]["candidates"][0]["seed_key"] = "seg:missing"
    reconcile_defaults(db, seed)
    route = next(r for r in db.routes() if r["seed_key"] == "route:demo")
    assert route["steps"][0]["candidates"][0]["segment_id"] == -1


# ---------------------------------------------------------------------------
# Real-bundled-seed agreement gate: proves the shipped defaults.seed.json
# reconciles the 10 already-migrated (v5/v6-corrected) segments with ZERO
# behavioral change — only `category` is newly set. If this ever fails, the
# seed JSON has drifted from what a fresh migrated db actually holds, and
# reconcile would silently rewrite a live user's triggers on startup.

def test_real_bundled_seed_does_not_alter_existing_segment_defs(tmp_path):
    db = Database(tmp_path / "t.db")
    before = {s["seed_key"]: s for s in db.segment_defs() if s.get("seed_key")}
    assert len(before) == 10

    seed_path = bundled_defaults_seed()
    assert seed_path is not None, "defaults.seed.json must be bundled"
    seed = json.loads(seed_path.read_text())

    reconcile_defaults(db, seed)

    after = {s["seed_key"]: s for s in db.segment_defs() if s.get("seed_key")}
    assert set(after) == set(before)
    for key, before_row in before.items():
        after_row = after[key]
        assert after_row["name"] == before_row["name"]
        assert after_row["enabled"] == before_row["enabled"]
        assert after_row["start_triggers"] == before_row["start_triggers"]
        assert after_row["end_triggers"] == before_row["end_triggers"]
        assert after_row["waypoints"] == before_row["waypoints"]
        assert after_row["guards"] == before_row["guards"]
        assert after_row["seed_dirty"] == 0
        # category is the ONLY legitimate change a reconcile may introduce
        assert before_row["category"] is None
        assert after_row["category"] is not None
        validate_definition({k: after_row[k] for k in
                             ("name", "start_triggers", "end_triggers", "guards")})

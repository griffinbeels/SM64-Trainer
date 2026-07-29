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


def test_reconcile_skips_a_malformed_row_and_keeps_the_good_ones(tmp_path):
    """One bad seed row must not cost the whole corpus refresh (spec #2 §10)."""
    db = Database(tmp_path / "t.db")
    seed = json.loads(json.dumps(SEED_V1))
    seed["segments"].insert(0, {"seed_key": "seg:bad", "name": "Bad",
                                "start_triggers": [{"type": "nope"}],
                                "end_triggers": [], "waypoints": [],
                                "guards": [], "category": "Tricks"})
    problems = reconcile_defaults(db, seed)
    assert len(problems) == 1 and "seg:bad" in problems[0]
    assert not any(s["seed_key"] == "seg:bad" for s in db.segment_defs())
    assert any(s["seed_key"] == "seg:demo" for s in db.segment_defs())


def test_reconcile_skips_a_row_with_no_seed_key(tmp_path):
    db = Database(tmp_path / "t.db")
    seed = json.loads(json.dumps(SEED_V1))
    seed["routes"].append({"name": "Keyless", "steps": []})
    problems = reconcile_defaults(db, seed)
    assert len(problems) == 1 and "seed_key" in problems[0]
    assert len(db.routes()) == 1


def test_reconcile_skips_a_structurally_wrong_row_shape(tmp_path):
    """A JSON-valid but wrong-shaped seed used to raise KeyError/TypeError out
    of reconcile; it must now be a skipped row, not an aborted refresh."""
    db = Database(tmp_path / "t.db")
    seed = json.loads(json.dumps(SEED_V1))
    seed["segments"].insert(0, "not a dict")
    problems = reconcile_defaults(db, seed)
    assert len(problems) == 1
    assert any(s["seed_key"] == "seg:demo" for s in db.segment_defs())


def test_reconcile_returns_no_problems_for_the_real_bundled_seed(tmp_path):
    """The shipped corpus must be clean by its own validator."""
    db = Database(tmp_path / "t.db")
    seed = json.loads(bundled_defaults_seed().read_bytes().decode("utf-8"))
    assert reconcile_defaults(db, seed) == []


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

    assert reconcile_defaults(db, seed) == []

    after = {s["seed_key"]: s for s in db.segment_defs() if s.get("seed_key")}
    # SUPERSET, not equality (2026-07-24): the seed legitimately INSERTS the
    # corpus's movement segments now, so "no new seeded rows" is no longer the
    # claim. What must still hold — and is the whole point of this gate — is
    # that the ten already-migrated rows come out the other side untouched.
    assert set(before) <= set(after)
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
                             ("name", "start_triggers", "end_triggers",
                              "waypoints", "guards")})


# -- Item 0 fix (spec 2026-07-28-multi-step-segments) landed a match_mode
# column with no seed content behind it yet, so a fresh install used to seed
# all 65 rows strict regardless of the split the plan actually wanted (55
# loose + 10 strict, by INTENT rather than insertion mechanism). Task 19 is
# the later task that puts match_mode on the 55 movements (tools/corpus_vocab
# .py::movement / build_defaults_seed.py::_movement_row) — this test now pins
# the split it actually produces, through the same reconcile path proven
# above.

def test_fresh_install_seeds_the_converted_corpus(tmp_path):
    """The 56 movements now ship match_mode="loose"; the ten legacy tricks
    carry no match_mode key (corpus_legacy.py's _seg() never stamps one) and
    so reconcile still applies the column default, "strict", to them —
    unchanged by this conversion. Task 20's 18 unguarded mechanic rows
    (reds->pipe, 100c->exit) ship explicit match_mode="loose" too, so they
    join the movements on the loose side of the split rather than the
    legacy side their guards=[] shape would otherwise suggest."""
    db = Database(tmp_path / "t.db")
    seed = json.loads(bundled_defaults_seed().read_bytes().decode("utf-8"))
    assert reconcile_defaults(db, seed) == []
    rows = db.segment_defs()
    assert len(rows) == 84
    loose = [r for r in rows if r["match_mode"] == "loose"]
    strict = [r for r in rows if r["match_mode"] == "strict"]
    assert len(loose) == 74 and len(strict) == 10


def test_reconcile_carries_match_mode_on_insert_and_refresh(tmp_path):
    """Sibling of test_reconcile_carries_default_strat_on_insert_and_refresh:
    once a later task puts match_mode on a seed row, reconcile must apply it
    on the fresh INSERT and on every REFRESH of an untouched row — not just
    the insert, or an already-installed row could never pick up a conversion
    from strict to loose."""
    db = Database(tmp_path / "t.db")
    seeded = json.loads(json.dumps(SEED_V1))
    seeded["segments"][0]["match_mode"] = "loose"
    reconcile_defaults(db, seeded)
    seg = next(s for s in db.segment_defs() if s["seed_key"] == "seg:demo")
    assert seg["match_mode"] == "loose"
    # a later seed can convert an untouched row...
    seed2 = json.loads(json.dumps(seeded)); seed2["seed_version"] = 2
    seed2["segments"][0]["match_mode"] = "strict"
    reconcile_defaults(db, seed2)
    assert next(s for s in db.segment_defs()
                if s["id"] == seg["id"])["match_mode"] == "strict"
    # ...but a dirtied row keeps its own
    db.set_seed_dirty("segment_defs", seg["id"], 1)
    seed3 = json.loads(json.dumps(seeded)); seed3["seed_version"] = 3
    seed3["segments"][0]["match_mode"] = "loose"
    reconcile_defaults(db, seed3)
    assert next(s for s in db.segment_defs()
                if s["id"] == seg["id"])["match_mode"] == "strict"


def test_reconcile_carries_default_strat_on_insert_and_refresh(tmp_path):
    """The 55 movements gain "Standard" purely through the reconcile — they are
    seeded and untouched, so no repair migration is needed (spec §5)."""
    db = Database(tmp_path / "t.db")
    seeded = json.loads(json.dumps(SEED_V1))
    seeded["segments"][0]["default_strat"] = "Standard"
    reconcile_defaults(db, seeded)
    seg = next(s for s in db.segment_defs() if s["seed_key"] == "seg:demo")
    assert seg["default_strat"] == "Standard"
    # a later seed can change it on an untouched row...
    seed2 = json.loads(json.dumps(seeded)); seed2["seed_version"] = 2
    seed2["segments"][0]["default_strat"] = "Blindfolded"
    reconcile_defaults(db, seed2)
    assert next(s for s in db.segment_defs()
                if s["id"] == seg["id"])["default_strat"] == "Blindfolded"
    # ...but a dirtied row keeps its own, the known gap in spec §5
    db.set_seed_dirty("segment_defs", seg["id"], 1)
    seed3 = json.loads(json.dumps(seeded)); seed3["seed_version"] = 3
    seed3["segments"][0]["default_strat"] = "Standard"
    reconcile_defaults(db, seed3)
    assert next(s for s in db.segment_defs()
                if s["id"] == seg["id"])["default_strat"] == "Blindfolded"

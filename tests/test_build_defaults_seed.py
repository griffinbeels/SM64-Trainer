"""The generator is how the corpus seed is AUTHORED; the JSON is the artifact
the app reads. These tests pin that the two never disagree (drift guard) and
that the generator reproduces the ten pre-existing seeded defs unchanged — a
live install's LBLJ/Bowser rows must not be rewritten at startup."""
import importlib.util
import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
_spec = importlib.util.spec_from_file_location(
    "build_defaults_seed", TOOLS / "build_defaults_seed.py")
build_seed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_seed)


def test_generated_seed_matches_the_checked_in_file():
    """Drift guard: `uv run python tools/build_defaults_seed.py --check` must
    be clean. If this fails, someone hand-edited the JSON — regenerate."""
    on_disk = build_seed.OUT.read_bytes().decode("utf-8")
    assert build_seed.render(build_seed.build()) == on_disk


def test_generated_seed_is_lf_only():
    """CRLF would churn the whole file in git on every rewrite."""
    assert b"\r\n" not in build_seed.OUT.read_bytes()


def test_legacy_segments_are_carried_forward_verbatim():
    """The ten pre-existing seeded defs keep their exact triggers: reconcile
    overwrites untouched seeded rows, so a drifted trigger here would silently
    rewrite a live user's segments on the next startup."""
    seed = build_seed.build()
    by_key = {s["seed_key"]: s for s in seed["segments"]}
    lblj = by_key["seg:lblj"]
    assert lblj["start_triggers"] == [
        {"type": "level_enter", "to": 6, "from": 16},
        {"type": "attempt_anchor", "level": 6, "area": 1}]
    assert lblj["end_triggers"] == [{"type": "level_enter", "to": 17}]
    assert lblj["guards"] == [] and lblj["waypoints"] == []
    assert lblj["category"] == "Tricks"
    for key in ("seg:mips-clip", "seg:lakitu-skip", "seg:bits-entry",
                "seg:bitdw-pipe", "seg:bitfs-pipe", "seg:bits-pipe",
                "seg:bowser-1", "seg:bowser-2", "seg:bowser-3"):
        assert key in by_key, key
        assert by_key[key]["guards"] == [], f"{key} must stay unguarded"
        assert by_key[key]["waypoints"] == [], key


def test_shipped_seed_has_the_whole_corpus():
    seed = json.loads(build_seed.OUT.read_bytes().decode("utf-8"))
    assert seed["seed_version"] == 2
    assert len(seed["segments"]) == 65          # 10 legacy + 55 movements
    assert len(seed["routes"]) == 48            # 13 main + 35 stage
    assert len({s["seed_key"] for s in seed["segments"]}) == 65
    assert len({r["seed_key"] for r in seed["routes"]}) == 48


def test_shipped_seed_reconciles_into_a_fresh_db_cleanly(tmp_path):
    """End to end: the artifact the app actually reads must apply with zero
    skipped rows and resolve every route candidate to a real segment id."""
    from sm64_events.storage.db import Database
    from sm64_events.tracking.defaults import reconcile_defaults
    db = Database(tmp_path / "t.db")
    seed = json.loads(build_seed.OUT.read_bytes().decode("utf-8"))
    assert reconcile_defaults(db, seed) == []
    assert len(db.segment_defs()) == 65
    routes = db.routes()
    assert len(routes) == 48
    broken = [(r["name"], c) for r in routes for s in r["steps"]
              for c in s["candidates"]
              if c["type"] == "segment" and c["segment_id"] == -1]
    assert broken == []

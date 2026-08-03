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
    # 10 legacy + 56 movements + 3 reds->pipe + 15 hundred-coin exits (Task 20)
    assert len(seed["segments"]) == 84
    assert len(seed["routes"]) == 48            # 13 main + 35 stage
    assert len({s["seed_key"] for s in seed["segments"]}) == 84
    assert len({r["seed_key"] for r in seed["routes"]}) == 48


def test_shipped_seed_reconciles_into_a_fresh_db_cleanly(tmp_path):
    """End to end: the artifact the app actually reads must apply with zero
    skipped rows and resolve every route candidate to a real segment id."""
    from sm64_events.storage.db import Database
    from sm64_events.tracking.defaults import reconcile_defaults
    db = Database(tmp_path / "t.db")
    seed = json.loads(build_seed.OUT.read_bytes().decode("utf-8"))
    assert reconcile_defaults(db, seed) == []
    assert len(db.segment_defs()) == 84
    routes = db.routes()
    assert len(routes) == 48
    broken = [(r["name"], c) for r in routes for s in r["steps"]
              for c in s["candidates"]
              if c["type"] == "segment" and c["segment_id"] == -1]
    assert broken == []


def test_every_movement_defaults_to_STRICT_match_mode():
    """REVERSED 2026-08-02. Every movement shipped "loose" from Task 19 (spec
    ...-multi-step-segments) until Griffin ruled the opposite: *"That is a
    fixed path, and there are no other options. I want it to be very strict...
    There are no deviations that are allowed."*

    Loose was right that the old waypoint-cancellation rules were too eager and
    wrong about what replaces them: a movement's identity IS its route, and
    only a declaration can say so. Still stamped in `_movement_row`, so the 56
    rows cannot disagree with each other."""
    seed = build_seed.build()
    movements = [s for s in seed["segments"] if s["guards"]]
    assert len(movements) == 56
    assert {s["match_mode"] for s in movements} == {"strict"}


def test_every_non_movement_defs_match_mode():
    """The taxonomy match_mode actually takes, after the 2026-07-29 corpus
    reshape (spec 2026-07-28-multi-step-segments, live reports -- two
    rounds the same day) split what used to be one clean "guards=[] ->
    legacy, has match_mode -> mechanic" boundary (Task 20) into four groups
    that no longer align with guards at all:

      * 7 legacy tricks/movements (LBLJ, MIPS Clip, Lakitu Skip, BitS Entry,
        the three Bowser fights) carry NO match_mode key -- unchanged, they
        keep meaning "strict" through reconcile's own column default.
      * 3 legacy Bowser pipe-entry rows (seg:bitdw/bitfs/bits-pipe) now carry
        match_mode="exclusive" -- "the pipe without going for the reds star"
        is what they mean since the reshape (corpus_legacy.py's own comment).
      * 3 reds->pipe mechanics (seg:reds->pipe:*) carry match_mode="strict" --
        a Bowser stage's single collectible star makes the strict
        cancellation rules safe again (corpus_movements.py's own comment).
      * 15 100c->exit mechanics ALSO carry match_mode="strict" (reshaped a
        second time the same day: shipped "loose" first on a since-corrected
        assumption that a main course's other stars would falsely cancel a
        strict def, then found live that loose's transparency to
        level_changed left a segment reading RUNNING after the player left
        the course -- corpus_movements.py's own comment carries the full
        correction).

    So "guards" alone no longer tells any of these groups apart; only
    (guards, waypoints, match_mode, seed_key prefix) together do. This test
    pins the actual counts, not a `not s["guards"]` catch-all, so a future
    row landing in the wrong bucket fails here by COUNT instead of silently
    joining whichever assertion still happens to pass."""
    seed = build_seed.build()
    by_key = {s["seed_key"]: s for s in seed["segments"]}
    legacy_plain = ["seg:lblj", "seg:mips-clip", "seg:lakitu-skip",
                    "seg:bits-entry", "seg:bowser-1", "seg:bowser-2",
                    "seg:bowser-3"]
    legacy_pipe = ["seg:bitdw-pipe", "seg:bitfs-pipe", "seg:bits-pipe"]
    reds_to_pipe = [k for k in by_key if k.startswith("seg:reds->pipe:")]
    hundred_coin = [k for k in by_key if k.startswith("seg:100c->exit:")]
    assert len(legacy_plain) == 7 and len(legacy_pipe) == 3
    assert len(reds_to_pipe) == 3 and len(hundred_coin) == 15
    assert [k for k in legacy_plain if "match_mode" in by_key[k]] == []
    assert {by_key[k]["match_mode"] for k in legacy_pipe} == {"exclusive"}
    assert {by_key[k]["match_mode"] for k in reds_to_pipe} == {"strict"}
    assert {by_key[k]["match_mode"] for k in hundred_coin} == {"strict"}
    # every non-movement def stays unguarded, whichever mode it carries
    assert all(by_key[k]["guards"] == []
               for k in legacy_plain + legacy_pipe + reds_to_pipe + hundred_coin)


def test_every_movement_defaults_to_the_standard_strategy():
    """There is basically one way to do a castle movement, so every movement
    ships with "Standard" already picked (spec 2026-07-24-segment-default-strat).
    Stamped in _movement_row, so the 56 rows cannot disagree with each other.

    The ten hand-written legacy rows are a MIX (untagged-PB fix, live report
    2026-07-31): six resolve to exactly one strategy in the bundled rank
    standards (LBLJ, MIPS Clip, Lakitu Skip, BitS Entry, Bowser 1, Bowser 2)
    and carry "Standard" for the same no-guess reason a movement does. The
    other four have real competing strategies (BitDW/BitFS/BitS Pipe Entry;
    Bowser 3) and carry NO default -- forcing one would be a lie. Task 20's 18
    unguarded mechanic rows (reds->pipe, 100c->exit) also carry no default,
    same precedent."""
    seed = build_seed.build()
    movements = [s for s in seed["segments"] if s["guards"]]
    non_movements = [s for s in seed["segments"] if not s["guards"]]
    assert len(movements) == 56 and len(non_movements) == 28
    assert {s["default_strat"] for s in movements} == {"Standard"}
    unambiguous_legacy = {"seg:lblj", "seg:mips-clip", "seg:lakitu-skip",
                          "seg:bits-entry", "seg:bowser-1", "seg:bowser-2"}
    defaulted = {s["seed_key"] for s in non_movements if s.get("default_strat")}
    assert defaulted == unambiguous_legacy
    assert {s["default_strat"] for s in non_movements
            if s["seed_key"] in unambiguous_legacy} == {"Standard"}
    ambiguous_legacy = {"seg:bitdw-pipe", "seg:bitfs-pipe", "seg:bits-pipe",
                        "seg:bowser-3"}
    assert [s["seed_key"] for s in non_movements
            if s["seed_key"] in ambiguous_legacy and s.get("default_strat")] == []

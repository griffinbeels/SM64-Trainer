"""tools/corpus_from_db.py: a live segment_defs row -> pasteable
corpus_movements.py source. Round-tripped through eval(), not string
equality, so formatting changes can't silently break the promise that the
printed source reproduces the stored clauses -- see the module docstring for
why (three traps: enter_area's hardcoded CASTLE, named area constants, and a
from_subarea/to_subarea constraint corpus_vocab can't express).
"""
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

_spec = importlib.util.spec_from_file_location(
    "corpus_from_db", TOOLS / "corpus_from_db.py")
corpus_from_db = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(corpus_from_db)

_seed_spec = importlib.util.spec_from_file_location(
    "build_defaults_seed", TOOLS / "build_defaults_seed.py")
build_defaults_seed = importlib.util.module_from_spec(_seed_spec)
_seed_spec.loader.exec_module(build_defaults_seed)

import corpus_vocab  # noqa: E402  (already on sys.path via corpus_from_db)

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "tracker.db"


def corpus_namespace() -> dict:
    """Everything a printed movement(...) line can reference by name --
    the clause helpers plus the LOBBY/UPSTAIRS/BASEMENT constants (trap b).
    Pulled from the real module rather than re-listed here, so this can never
    drift from what corpus_movements.py itself imports."""
    return {name: getattr(corpus_vocab, name) for name in dir(corpus_vocab)
            if not name.startswith("_")}


def _as_segment_shape(movement_dict: dict) -> dict:
    """movement() returns the COMPACT {seed_key,name,start,via,end} shape --
    the same expansion build_defaults_seed.py::_movement_row applies (start/
    end wrapped into single-clause lists, via -> one-clause-per-waypoint) is
    what turns that into the segment_defs row shape a live definition
    actually has. Reusing the real function rather than re-deriving the
    wrapping convention here, so the two can never quietly disagree."""
    return build_defaults_seed._movement_row(movement_dict)


# --- Step 1 (brief-mandated) -------------------------------------------------

def test_a_live_definition_prints_as_pasteable_corpus_source():
    src = corpus_from_db.segment_row_source({
        "name": "Bowser 1 → WF", "seed_key": "seg:bowser1->wf",
        "match_mode": "loose",
        "start_triggers": [{"type": "level_exit", "from": 30}],
        "end_triggers": [{"type": "level_enter", "to": 24}],
        "waypoints": [], "guards": [], "category": "Castle Movement"})
    assert src == ('movement("seg:bowser1->wf", "Bowser 1 → WF",\n'
                   '         exit_level(30), enter_level(24)),')


def test_the_printed_source_round_trips_through_the_corpus_builder():
    # movement() itself returns the COMPACT {seed_key,name,start,via,end}
    # shape -- _as_segment_shape applies the same expansion
    # build_defaults_seed.py::_movement_row does, to compare against the
    # segment_defs row shape a live definition actually has.
    row = {"name": "WF → SSL", "seed_key": "seg:wf->ssl",
           "start_triggers": [{"type": "level_exit", "from": 24}],
           "end_triggers": [{"type": "level_enter", "to": 8}],
           "waypoints": [[{"type": "area_enter", "level": 6, "area": 3}]],
           "guards": [{"type": "in_active_route"}], "category": "Castle Movement",
           "match_mode": "strict"}
    ns = corpus_namespace()
    produced = eval(corpus_from_db.segment_row_source(row).rstrip(","), ns)
    expanded = _as_segment_shape(produced)
    assert expanded["start_triggers"] == row["start_triggers"]
    assert expanded["end_triggers"] == row["end_triggers"]
    assert expanded["waypoints"] == row["waypoints"]
    assert produced["name"] == row["name"]
    # match_mode is NOT part of movement()'s shape at all (see the module
    # docstring's "WHAT segment_row_source DOES NOT CARRY") -- every real
    # seeded row in this db is "strict", the only value reconcile ever
    # assigns a corpus_movements.py row, so nothing is lost by this row.


def test_via_waypoint_prints_a_second_line_with_named_constants():
    """Trap (b): a real seeded row's BASEMENT waypoint must print the NAME,
    not the raw area int 3 -- a correct-but-numeric answer looks nothing like
    the file it's meant to be pasted into."""
    row = {"name": "WF → SSL", "seed_key": "seg:wf->ssl",
           "start_triggers": [{"type": "level_exit", "from": 24}],
           "end_triggers": [{"type": "level_enter", "to": 8}],
           "waypoints": [[{"type": "area_enter", "level": 6, "area": 3}]]}
    src = corpus_from_db.segment_row_source(row)
    assert src == ('movement("seg:wf->ssl", "WF → SSL",\n'
                   '         exit_level(24), enter_level(8),\n'
                   '         via=[enter_area(BASEMENT)]),')


def test_area_enter_clause_round_trips_as_an_end_trigger():
    """Coverage for an area_enter clause OUTSIDE a waypoint too."""
    row = {"name": "BoB → Basement", "seed_key": "seg:bob->basement",
           "start_triggers": [{"type": "level_exit", "from": 9}],
           "end_triggers": [{"type": "area_enter", "level": 6, "area": 3}],
           "waypoints": []}
    ns = corpus_namespace()
    src = corpus_from_db.segment_row_source(row)
    assert "enter_area(BASEMENT)" in src
    produced = eval(src.rstrip(","), ns)
    assert _as_segment_shape(produced)["end_triggers"] == row["end_triggers"]


# --- Trap (a): enter_area's hardcoded CASTLE --------------------------------

def test_area_enter_on_a_non_castle_level_is_refused_not_mis_filed():
    """A course has its own subareas (CCM's slide is area 2) --
    enter_area() always hardcodes CASTLE, so a clause naming a different
    level must refuse rather than silently emit an enter_area() call that
    round-trips to the WRONG level."""
    row = {"name": "CCM Slide entry", "seed_key": "seg:ccm-slide",
           "start_triggers": [{"type": "level_enter", "to": 5}],
           "end_triggers": [{"type": "area_enter", "level": 5, "area": 2}],
           "waypoints": []}
    with pytest.raises(corpus_from_db.NotExpressible, match="not the castle"):
        corpus_from_db.segment_row_source(row)


# --- Trap (c): from_subarea/to_subarea ---------------------------------------

def test_a_subarea_pinned_level_exit_is_refused_not_silently_loosened():
    """exit_level()/enter_level() have no subarea parameter at all -- found
    against the live dev db (an edited MIPS Clip carrying to_subarea=3).
    Printing exit_level(7, to=6) for it would match MORE than the live
    definition does, so this must refuse."""
    row = {"name": "MIPS Clip", "seed_key": "seg:mips-clip",
           "start_triggers": [{"type": "level_exit", "from": 7, "to": 6,
                               "to_subarea": 3, "from_subarea": None}],
           "end_triggers": [{"type": "level_enter", "to": 23}],
           "waypoints": []}
    with pytest.raises(corpus_from_db.NotExpressible, match="subarea"):
        corpus_from_db.segment_row_source(row)


def test_a_null_subarea_key_is_the_same_as_absent():
    row = {"name": "Lakitu Skip", "seed_key": "seg:lakitu-skip",
           "start_triggers": [{"type": "spawned", "level": 16}],
           "end_triggers": [{"type": "level_enter", "to": 6, "from": 16,
                             "to_subarea": None, "from_subarea": None}],
           "waypoints": []}
    src = corpus_from_db.segment_row_source(row)   # must not raise
    assert "enter_level(6, frm=16)" in src


# --- reset_game: no helper, handled explicitly ------------------------------

def test_reset_game_start_is_refused_with_its_own_reason_not_a_crash():
    row = {"name": "Whole run", "seed_key": "seg:whole-run",
           "start_triggers": [{"type": "reset_game"}],
           "end_triggers": [{"type": "level_enter", "to": 9}],
           "waypoints": []}
    with pytest.raises(corpus_from_db.NotExpressible, match="reset_game"):
        corpus_from_db.segment_row_source(row)


# --- multi-clause start/end: the corpus_legacy.py _seg() shape --------------

def test_a_definition_with_two_start_triggers_is_refused():
    """movement() takes exactly one start clause and one end clause --
    LBLJ's shipped shape (level_enter + attempt_anchor) is corpus_legacy.py's
    _seg(), not movement(), and must be named as such rather than silently
    dropping the second trigger."""
    row = {"name": "LBLJ", "seed_key": "seg:lblj",
           "start_triggers": [{"type": "level_enter", "to": 6, "from": 16},
                              {"type": "attempt_anchor", "level": 6, "area": 1}],
           "end_triggers": [{"type": "level_enter", "to": 17}],
           "waypoints": []}
    with pytest.raises(corpus_from_db.NotExpressible, match="_seg\\(\\)"):
        corpus_from_db.segment_row_source(row)


def test_an_any_of_waypoint_is_refused():
    """`via` is documented as a FLAT list -- no corpus movement needs an
    any-of waypoint. A stored waypoint with more than one clause has no
    movement() equivalent."""
    row = {"name": "Hypothetical", "seed_key": "seg:hyp",
           "start_triggers": [{"type": "level_exit", "from": 9}],
           "end_triggers": [{"type": "level_enter", "to": 24}],
           "waypoints": [[{"type": "level_enter", "to": 6},
                          {"type": "level_enter", "to": 16}]]}
    with pytest.raises(corpus_from_db.NotExpressible, match="any-of"):
        corpus_from_db.segment_row_source(row)


# --- a brand-new, never-seeded definition (no seed_key yet) -----------------

def test_a_definition_with_no_seed_key_gets_one_generated():
    row = {"name": "BLJs", "seed_key": None,
           "start_triggers": [{"type": "area_enter", "level": 6, "area": 2}],
           "end_triggers": [{"type": "level_enter", "to": 21}],
           "waypoints": []}
    src = corpus_from_db.segment_row_source(row)
    assert src.startswith('movement("seg:bljs", "BLJs",')
    produced = eval(src.rstrip(","), corpus_namespace())
    assert _as_segment_shape(produced)["start_triggers"] == row["start_triggers"]


# --- CLI: id lookup and --all-dirty -----------------------------------------

def _make_test_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE segment_defs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
          start_triggers TEXT NOT NULL, end_triggers TEXT NOT NULL,
          waypoints TEXT NOT NULL DEFAULT '[]', guards TEXT NOT NULL DEFAULT '[]',
          category TEXT, seed_key TEXT, seed_dirty INTEGER NOT NULL DEFAULT 0,
          default_strat TEXT, match_mode TEXT NOT NULL DEFAULT 'strict',
          created_utc TEXT NOT NULL
        )""")
    conn.execute(
        "INSERT INTO segment_defs (name, start_triggers, end_triggers, "
        "seed_key, seed_dirty, created_utc) VALUES (?,?,?,?,?,?)",
        ("Bowser 1 → WF", json.dumps([{"type": "level_exit", "from": 30}]),
         json.dumps([{"type": "level_enter", "to": 24}]),
         "seg:bowser1->wf", 0, "2026-01-01T00:00:00Z"))
    conn.execute(
        "INSERT INTO segment_defs (name, start_triggers, end_triggers, "
        "seed_key, seed_dirty, created_utc) VALUES (?,?,?,?,?,?)",
        ("LBLJ (edited)",
         json.dumps([{"type": "level_enter", "to": 6, "from": 16}]),
         json.dumps([{"type": "level_enter", "to": 17}]),
         "seg:lblj", 1, "2026-01-01T00:00:00Z"))
    conn.commit()
    conn.close()


def test_cli_prints_one_definition_by_id(tmp_path, capsys):
    db_path = tmp_path / "t.db"
    _make_test_db(db_path)
    rc = corpus_from_db.main(["1", "--db", str(db_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert 'movement("seg:bowser1->wf"' in out


def test_cli_all_dirty_prints_only_seed_dirty_rows(capsys, tmp_path):
    db_path = tmp_path / "t.db"
    _make_test_db(db_path)
    rc = corpus_from_db.main(["--all-dirty", "--db", str(db_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "seg:bowser1->wf" not in out    # seed_dirty=0, not printed
    assert 'movement("seg:lblj"' in out    # seed_dirty=1, printed


def test_cli_requires_exactly_one_of_id_or_all_dirty(tmp_path):
    db_path = tmp_path / "t.db"
    _make_test_db(db_path)
    with pytest.raises(SystemExit):
        corpus_from_db.main(["--db", str(db_path)])
    with pytest.raises(SystemExit):
        corpus_from_db.main(["1", "--all-dirty", "--db", str(db_path)])


# --- against the real dev db (gitignored, machine-local) --------------------

def test_every_real_seeded_definition_round_trips_or_is_explicitly_refused():
    """Not a shipped-default content test (data/tracker.db is gitignored,
    machine-local scratch state, not the corpus) -- the invariant checked is
    "segment_row_source never succeeds with the WRONG answer", which holds
    whether the db has 0 rows or 65. Skips cleanly on a fresh clone/CI where
    the file doesn't exist."""
    if not DB_PATH.exists():
        pytest.skip("data/tracker.db not present (machine-local, gitignored)")
    uri = DB_PATH.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM segment_defs WHERE seed_key IS NOT NULL ORDER BY id"
    ).fetchall()
    conn.close()
    assert rows, "expected the shipped corpus to be reconciled into this db"

    ns = corpus_namespace()
    refused, round_tripped = [], []
    for r in rows:
        defn = {"id": r["id"], "name": r["name"], "seed_key": r["seed_key"],
                "start_triggers": json.loads(r["start_triggers"]),
                "end_triggers": json.loads(r["end_triggers"]),
                "waypoints": json.loads(r["waypoints"])}
        try:
            src = corpus_from_db.segment_row_source(defn)
        except corpus_from_db.NotExpressible as exc:
            refused.append((defn["seed_key"], str(exc)))
            continue
        produced = _as_segment_shape(eval(src.rstrip(","), dict(ns)))
        assert produced["start_triggers"] == defn["start_triggers"], defn["seed_key"]
        assert produced["end_triggers"] == defn["end_triggers"], defn["seed_key"]
        assert produced["waypoints"] == defn["waypoints"], defn["seed_key"]
        round_tripped.append(defn["seed_key"])

    print(f"\n{len(round_tripped)}/{len(rows)} round-tripped; "
         f"{len(refused)} refused:")
    for key, reason in refused:
        print(f"  {key}: {reason}")

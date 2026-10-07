"""Filtering and bulk selection run the shipping JS, independent of race limits."""
import json
from pathlib import Path
import subprocess

from sm64_events.core.childproc import quiet_spawn_kwargs

JS = (Path(__file__).resolve().parents[1] / "src/sm64_events/ui/ghostlibrary.js").as_uri()
ROWS = [dict(source="local", attempt_id=7, frames=120, author="You", kind="star", entity_key="2-1",
             target_name="Wild Blue", strat="Standard", practice=False, outcome="success", available=True,
             recorded_utc="2026-09-30T12:00:00+00:00"),
        dict(source="import", import_id="a" * 64, frames=100, author="Other Player", kind="star", entity_key="2-1",
             target_name="Wild Blue", strat="Cannon", practice=False, outcome="success", available=True,
             recorded_utc="2026-09-29T12:00:00+00:00"),
        dict(source="import", import_id="b" * 64, frames=10, author="Practice Player", kind="star", entity_key="2-1",
             target_name="Wild Blue", strat="Standard", practice=True, outcome="success", available=True,
             recorded_utc="2026-09-30T14:00:00+00:00")]


def run(expression):
    program = f"import {{libraryRows, selectionOf, raceChoice, bestGroups, overallSelection, importSelection, openingTarget, resolveShortcut}} from {JS!r};\nconst rows={json.dumps(ROWS)};\nconsole.log(JSON.stringify({expression}));"
    result = subprocess.run(["node", "--input-type=module", "-"], input=program, capture_output=True,
                            text=True, encoding="utf-8", timeout=30, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_library_filters_practice_by_default_and_sorts_actual_time_and_creator():
    rows = run('libraryRows(rows,{query:"",source:"all",targetOnly:false,practice:false,sort:"fastest"},null)')
    assert [row["author"] for row in rows] == ["Other Player", "You"]
    rows = run('libraryRows(rows,{query:"cannon",source:"all",targetOnly:false,practice:false,sort:"fastest"},null)')
    assert [row["author"] for row in rows] == ["Other Player"]
    rows = run('libraryRows(rows,{query:"",source:"all",targetOnly:false,practice:true,sort:"recent"},null)')
    assert [row["author"] for row in rows] == ["Practice Player", "You", "Other Player"]


def test_bulk_export_selection_and_race_selection_preserve_distinct_id_namespaces():
    assert run('selectionOf(rows)') == {"attempts": [7], "imports": ["a" * 64, "b" * 64]}
    assert run('raceChoice(rows,{kind:"star",entity_key:"2-1"},false)') == {
        "pb": False, "attempts": [7], "imports": ["a" * 64, "b" * 64]}
    assert run('raceChoice(rows,{kind:"star",entity_key:"2-2"},false)') == {"pb": False, "attempts": [], "imports": []}


def test_opening_section_is_independent_of_live_target_and_local_browse_is_fixed():
    assert run('openingTarget("star:2:1",{kind:"segment",entity_key:"6"})') == {"kind": "star", "entity_key": "2-1"}
    assert run('openingTarget("segment:6",null)') == {"kind": "segment", "entity_key": "6"}
    assert run('libraryRows(rows,{query:"",source:"all",target:{kind:"star",entity_key:"2-2"},practice:true,sort:"fastest"},null)') == []


def test_unavailable_overall_best_never_selects_a_slower_strategy():
    assert run('''[...overallSelection([
      {...rows[0],frames:90,available:false,clock:"igt",game_version:"us"},
      {...rows[1],frames:100,clock:"igt",game_version:"us"}
    ],{kind:"star",entity_key:"2-1"})]''') == []


def test_each_timer_and_version_has_an_independent_default_and_ties_choose_available():
    result = run('''[...overallSelection([
      {...rows[0],frames:90,available:false,clock:"igt",game_version:"us"},
      {...rows[1],frames:90,clock:"igt",game_version:"us"},
      {...rows[2],frames:100,clock:"rta",game_version:"jp"}
    ],{kind:"star",entity_key:"2-1"})]''')
    assert result == ["a" * 64, "b" * 64]


def test_import_modes_distinguish_unknown_comparison_from_no_personal_best():
    expression = '''[...importSelection([
      {id:"fast",importable:true,duplicate:false,comparison:"faster"},
      {id:"slow",importable:true,duplicate:false,comparison:"not_faster"},
      {id:"new",importable:true,duplicate:false,comparison:"no_best"},
      {id:"unknown",importable:true,duplicate:false,comparison:"unknown"},
      {id:"duplicate",importable:true,duplicate:true,comparison:"faster"}
    ],%s,%s)]'''
    assert run(expression % ('"all"', 'false')) == ["fast", "slow", "new", "unknown"]
    assert run(expression % ('"faster"', 'false')) == ["fast"]
    assert run(expression % ('"faster"', 'true')) == ["fast", "new"]


def test_saved_search_resolves_fresh_recordings_and_excludes_unavailable_winners():
    assert run('resolveShortcut({ghosts:rows,bests:[{...rows[0],available:false}]},{query:"",source:"local",target:null,practice:false,sort:"fastest",bests:true})') == []
    assert [row["attempt_id"] for row in run('resolveShortcut({ghosts:rows},{query:"",source:"local",target:null,practice:false,sort:"fastest",bests:false})')] == [7]

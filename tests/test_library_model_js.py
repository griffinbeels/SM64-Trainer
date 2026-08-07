# tests/test_library_model_js.py
"""Node-drives `ui/components/librarymodel.js` directly -- the Library page's
pure rules, kept import-free so this file can pin them with no browser and no
Preact. Same idiom as test_cross_language_parity.py's `run_node` (read that
file's header before touching this one): the script is piped over STDIN, not
`-e` -- `-e` truncates on Windows the moment the script carries a quote the
shell would otherwise have to escape, which every JSON.stringify(...) call
here does. `encoding="utf-8"` is load-bearing on this machine (the Windows
ANSI codepage cannot even encode a script with a non-ASCII glyph, and it
silently mangles anything it CAN encode instead of raising).

`MODEL.as_uri()` (a `file://` URL), not a plain Windows path: an ESM `import`
of a bare "C:/..." path 404s -- verified empirically against this exact node
build, and it's the same fix test_cross_language_parity.py already needed for
every one of its own imports.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

MODEL = (Path(__file__).resolve().parent.parent / "src" / "sm64_events"
         / "ui" / "components" / "librarymodel.js")

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")


def run_js(expr: str):
    script = (f"import * as m from {MODEL.as_uri()!r};\n"
              f"console.log(JSON.stringify({expr}));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_sections_run_beginner_to_expert_with_no_ladder_last():
    approaches = [{"name": "fast", "ladder": {"Mario": 10.0}},
                  {"name": "none"},
                  {"name": "slow", "ladder": {"Mario": 20.0}}]
    assert [a["name"] for a in run_js(f"m.sectionOrder({json.dumps(approaches)})")] \
        == ["slow", "fast", "none"]


def test_auto_expand_prefers_the_selected_strategy_else_first():
    ordered = [{"name": "A"}, {"name": "B", "matched_strategy": "Skyjump"}]
    assert run_js(f"m.autoExpandName({json.dumps(ordered)}, 'Skyjump')") == "B"
    assert run_js(f"m.autoExpandName({json.dumps(ordered)}, null)") == "A"


def test_auto_expand_also_matches_a_variant_qualified_name():
    # Task 1's stamp (commit eb9a92e): the SAME (entity, strategy) pair can be
    # stamped on two sibling targets sharing an entity, and the stamped name
    # may be variant-qualified ("100c + Slide . Open") rather than a bare
    # strategy name. autoExpandName must match on the approach's OWN `name`
    # too, not only `matched_strategy` -- a caller may be resolving against
    # either vocabulary depending on where the selected strat came from.
    ordered = [{"name": "100c + Slide . Open"}, {"name": "B"}]
    assert run_js(
        f"m.autoExpandName({json.dumps(ordered)}, '100c + Slide . Open')") \
        == "100c + Slide . Open"


def test_bands_run_slowest_first_and_sort_slowest_within():
    ladder = {"Bronze": 14.0, "Mario": 12.0}
    entries = [{"runner": "a", "time_cs": 1500}, {"runner": "b", "time_cs": 1390},
               {"runner": "c", "time_cs": 1180}, {"runner": "d", "time_cs": 1395}]
    bands = run_js(f"m.bandsOf({json.dumps(ladder)}, {json.dumps(entries)})")
    assert [b["tier"] for b in bands] == ["Below Bronze", "Bronze", "Mario"]
    assert [e["runner"] for e in bands[1]["entries"]] == ["d", "b"]  # slowest first


def test_band_for_agrees_with_a_hand_walked_non_monotonic_ladder():
    # bandFor's own comment claims it applies classify.rank_for's rule
    # ("the fastest tier a time still beats"), but the two walk the ladder in
    # OPPOSITE directions -- rank_for hardest-first-return-first, bandFor
    # easiest-first-overwrite-last (equivalent, since the hardest tier a time
    # beats is necessarily the LAST one an easy-to-hard scan finds satisfied).
    # A deliberately non-monotonic ladder (Silver's own cutoff looser than
    # Bronze's) is the case that would catch either scan getting this wrong,
    # unlike an ordinary ladder where "first" and "last" trivially coincide.
    ladder = {"Bronze": 20.0, "Silver": 25.0, "Mario": 5.0}
    assert run_js(f"m.bandFor({json.dumps(ladder)}, 1000)") == "Silver"
    assert run_js(f"m.bandFor({json.dumps(ladder)}, 300)") == "Mario"
    assert run_js(f"m.bandFor({json.dumps(ladder)}, 3000)") == "Below Bronze"


def test_grid_shapes():
    assert [run_js(f"m.gridShape({n})") for n in (1, 2, 3, 5, 9)] == [
        {"rows": 1, "cols": 1}, {"rows": 1, "cols": 2}, {"rows": 2, "cols": 2},
        {"rows": 2, "cols": 3}, {"rows": 3, "cols": 3}]


def test_youtube_helpers_and_search():
    assert run_js("m.youtubeId('https://youtu.be/abc123XYZ_-')") == "abc123XYZ_-"
    assert run_js("m.youtubeId('https://www.youtube.com/watch?v=Qabc123XYZ0&t=91s')") == "Qabc123XYZ0"
    assert run_js("m.youtubeId('https://clips.twitch.tv/x')") is None
    assert run_js("m.youtubeThumb('https://clips.twitch.tv/x')") is None
    assert run_js("m.youtubeThumb('https://youtu.be/abc123XYZ_-')") == \
        "https://i.ytimg.com/vi/abc123XYZ_-/hqdefault.jpg"
    assert run_js("m.youtubeEmbed('https://clips.twitch.tv/x', null)") is None
    assert run_js("m.matchesRunner({runner: 'Kally'}, 'kal')") is True
    assert run_js("m.matchesRunner({runner: 'Kally'}, 'zzz')") is False
    assert run_js("m.matchesRunner({runner: 'Kally'}, '')") is True


def test_last_practiced_takes_the_newest_attempt():
    # views.py::build_session_view ships two SEPARATE top-level arrays --
    # `stars` and `segments`, never one merged `sections` list (confirmed
    # against the real view builder and the JS consumers that already read
    # it this way: focustarget.js, compare.js, practicelog.js). A star
    # section also carries no `kind` key at all (views.py's own comment: the
    # UI branches on `kind` being undefined for stars), which this fixture
    # matches. And attempt recency compares `journal_id`, never the raw
    # `id` -- a reattributed 100-coin attempt keeps a segment-namespace id
    # around 7.5e11 that would outrank every native id forever
    # (tracking-storage.md's "Attempt ordering must use journal_id" law;
    # ui/focustarget.js::newestJournalId is the same rule already shipped).
    view = {"stars": [
        {"course_id": 2, "star_id": 3,
         "attempts": [{"journal_id": 5}, {"journal_id": 9}]}],
        "segments": [
        {"kind": "segment", "segment_id": 4,
         "attempts": [{"journal_id": 7}]}]}
    assert run_js(f"m.lastPracticed({json.dumps(view)})") == "star:2:3"


def test_last_practiced_prefers_the_segment_when_it_is_newest():
    view = {"stars": [
        {"course_id": 2, "star_id": 3, "attempts": [{"journal_id": 5}]}],
        "segments": [
        {"kind": "segment", "segment_id": 4,
         "attempts": [{"journal_id": 11}]}]}
    assert run_js(f"m.lastPracticed({json.dumps(view)})") == "segment:4"


def test_last_practiced_is_null_with_no_attempts_anywhere():
    view = {"stars": [{"course_id": 2, "star_id": 3, "attempts": []}],
            "segments": []}
    assert run_js(f"m.lastPracticed({json.dumps(view)})") is None


def test_tray_to_import_carries_the_trim_as_frames():
    # TASK 6: entity_key rides on the item itself now (task-6-caveats.md
    # point 6), not a second argument -- see test_tray_to_import_reads_the_
    # items_own_entity_key below for the case that argument existing used to
    # hide (a caller passing a DIFFERENT entity than the item's own).
    item = {"runner": "Kally", "time_cs": 4380, "video": "https://youtu.be/z",
            "trim": {"start_s": 12, "end_s": 19.5}, "entity_key": "star:1:0"}
    out = run_js(f"m.trayToImport({json.dumps(item)})")
    assert out["body"]["source_kind"] == "youtube"
    assert out["body"]["entity_key"] == "star:1:0"
    assert out["body"]["source_ref"] == "https://youtu.be/z"
    assert out["body"]["strat"] == "Standard"
    # TASK 5 RULING (task-5-caveats.md point 2): `name` is a pre-filled
    # default for compare.js's editable "name this comparison" field, not a
    # fixed label -- pinned to fmtSeconds (the SAME notation the Library card
    # this item came from already showed for time_cs) rather than the raw
    # `.toFixed(2)` it shipped with, so the number carries over unchanged.
    assert out["body"]["name"] == 'Kally 43"80', out["body"]["name"]
    # in/out_frame are GAME frames on Usamune's 30fps clock (storage/db.py's
    # own comment: "in/out_frame are non-destructive sync bounds in GAME
    # frames"; tracking/comparisons.py::master_seek_time divides by the same
    # 30 default) -- trim seconds x 30, matching GAME_FPS.
    assert out["edit"] == {"in_frame": 360, "out_frame": 585}


def test_tray_to_import_edit_is_null_when_untrimmed():
    item = {"runner": "Kally", "time_cs": 4380, "video": "https://youtu.be/z",
            "entity_key": "star:1:0"}
    out = run_js(f"m.trayToImport({json.dumps(item)})")
    assert out["edit"] is None


def test_tray_to_import_reads_the_items_own_entity_key():
    """A tray can hold items gathered from more than one entity (Task 5 fix
    round 1) -- this is the case a second `entityKey` PARAMETER could get
    wrong (a caller handing in whatever entity the user happens to be
    standing on, rather than the one the item actually came from), which is
    exactly why that parameter is gone."""
    item = {"runner": "Kally", "time_cs": 4380, "video": "https://youtu.be/z",
            "entity_key": "star:2:5"}
    out = run_js(f"m.trayToImport({json.dumps(item)})")
    assert out["body"]["entity_key"] == "star:2:5"

"""The Library search RULE, driven under node.

`ui/librarysearch.js` is import-free for exactly this reason: what a search
MEANS is proved here, without a browser, and the component only has to draw
the answer. The render half -- that the box exists, that typing swaps the grid
for the results, that a result opens its target -- is
tests/test_ui_library_search.py.

Round 12, his call at capture: a result row is always a TARGET, matched on its
own name AND on its approaches' names.
"""
import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MODULE = (REPO / "src" / "sm64_events" / "ui" / "librarysearch.js").as_uri()


def run(expression: str):
    """Evaluate one expression against the real module and return its JSON."""
    script = (
        f"import('{MODULE}').then((m) => {{"
        f"  const out = {expression};"
        f"  process.stdout.write(JSON.stringify(out));"
        f"}}).catch((err) => {{ console.error(err); process.exit(1); }});"
    )
    done = subprocess.run(["node", "--input-type=module", "-e", script],
                          capture_output=True, encoding="utf-8", cwd=REPO)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


INDEX = json.dumps({
    "groups": [
        {"group": "14. Tick Tock Clock", "targets": [
            {"index": 1, "label": "Stop Watch, Get Wet", "entity_key": "star:14:2",
             "entries": 9, "approach_names": ["Pole jump", "Slide kick"]},
            {"index": 2, "label": "Roll Into the Cage", "entity_key": "star:14:5",
             "entries": 1, "approach_names": []},
        ]},
        {"group": "Castle Movements (Lobby)", "targets": [
            {"index": 3, "label": "BitDW Entry", "entity_key": None,
             "entries": 4, "approach_names": ["LBLJ (Lobby)", "Backwards long jump"]},
        ]},
    ],
})


def search(query: str):
    return run(f"m.searchTargets({INDEX}, {json.dumps(query)})")


def test_an_empty_query_finds_nothing_at_all():
    """The caller reads [] as "draw the grid instead" -- so an empty box must
    not be a search that happens to match everything."""
    assert search("") == []
    assert search("   ") == []


def test_a_target_matches_on_its_own_label():
    hits = search("roll into")
    assert [hit["target"]["index"] for hit in hits] == [2]
    assert hits[0]["matched"] is None


def test_a_target_matches_on_its_GROUP_name():
    """He thinks of a star as "TTC -- Stop Watch", and the row says exactly
    that, so the visible name has to be the thing that matched."""
    assert {hit["target"]["index"] for hit in search("tick tock")} == {1, 2}


def test_a_target_matches_on_an_APPROACH_name_and_says_which():
    """His call: typing "LBLJ" finds the target that documents it."""
    hits = search("lblj")
    assert len(hits) == 1
    assert hits[0]["target"]["label"] == "BitDW Entry"
    assert hits[0]["matched"] == "LBLJ (Lobby)"


def test_a_label_match_reports_no_approach():
    """Saying "matched approach: X" under a name that already contains the
    query reads as the search having found something else."""
    assert search("stop watch")[0]["matched"] is None


def test_every_word_must_appear_and_order_does_not_matter():
    assert [hit["target"]["index"] for hit in search("cage ttc")] == []
    assert [hit["target"]["index"] for hit in search("cage tock")] == [2]
    assert search("stop watch banana") == []


def test_punctuation_and_case_fold_on_both_sides():
    """"Bob-omb" must match "bob omb": a runner types the words, not the
    dashes."""
    assert run("m.fold('Tick Tock Clock — Stop Watch, Get Wet')") == \
        "tick tock clock stop watch get wet"
    assert [hit["target"]["index"] for hit in search("STOP-WATCH")] == [1]


def test_a_half_typed_word_still_narrows():
    """"query in real time" only means anything if a prefix matches -- the box
    is searched on every keystroke, mid-word."""
    assert [hit["target"]["index"] for hit in search("sto")] == [1]


def test_the_sub_line_says_why_the_row_is_here():
    hits = search("lblj")
    assert run(f"m.resultSub({json.dumps(hits[0])})") == "matched: LBLJ (Lobby)"
    label_hit = search("roll into")[0]
    assert run(f"m.resultSub({json.dumps(label_hit)})") == "1 entry"
    plural = search("stop watch")[0]
    assert run(f"m.resultSub({json.dumps(plural)})") == "9 entries"


def test_a_missing_approach_names_field_is_not_a_crash():
    """An index served by an older build carries counts and no names. The
    search must degrade to label-only rather than throw inside a render."""
    older = json.dumps({"groups": [{"group": "G", "targets": [
        {"index": 7, "label": "Stop Watch", "entity_key": None, "entries": 2}]}]})
    hits = run(f"m.searchTargets({older}, 'stop')")
    assert [hit["target"]["index"] for hit in hits] == [7]
    assert run(f"m.searchTargets({older}, 'lblj')") == []

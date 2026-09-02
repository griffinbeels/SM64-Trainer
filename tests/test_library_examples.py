"""Library entries as example clips for the rank-standards table (task 0098).

The doors under test are the SAME doors that give a strategy its ladder, so a
strategy's example videos always come from the row that grades it: an explicit
adoption, then an unadopted approach on the target's own entity under its
matched (vetted) name or its own, and NEVER an unadopted subsection — a piece's
time banded against the whole target's ladder would file every clip several
tiers too fast."""
from sm64_events.library.audit import row_key
from sm64_events.library.examples import example_clips, sheet_best

NO_JP = lambda entity, strat: False


def _entry(cs, video=None, version=None):
    return {"runner": "r", "time_cs": cs, "video": video, "version": version}


def _payload():
    def item(name, entries, matched=None):
        row = {"ids": ["1"], "name": name, "best_cs": 100, "best_runner": "M",
               "times": {}, "ideal_cs": None, "fill_rate": 0.2,
               "entries": entries}
        if matched:
            row["matched_strategy"] = matched
        return row
    return {"schema_version": 2, "sheet_revision": "x", "fetched_at": "x",
            "runners": [], "ladder_model": {}, "targets": [
                {"entity_key": "star:2:4", "group": None, "section": "WF",
                 "label": "Caged Island", "version": None, "miss_reason": None,
                 "approaches": [
                     item("TJ Owlless", [_entry(1180, "https://v/fast"),
                                         _entry(1290, "https://v/slow"),
                                         _entry(1200)],
                          matched="TJ Owlless"),
                     item("Own Name Way", [_entry(1400, "https://v/own")]),
                 ],
                 "subsections": [
                     item("Whomp text Xcam", [_entry(300, "https://v/piece")]),
                 ]},
                {"entity_key": None, "group": "Castle Movements (Lobby)",
                 "section": "★ BoB", "label": "Lobby door (L) - BoB door",
                 "version": None, "miss_reason": "castle_movement",
                 "approaches": [
                     item("Lobby door (L) - BoB door",
                          [_entry(276, "https://v/door")]),
                 ],
                 "subsections": []}]}


def _key(payload, target_index, name):
    target = payload["targets"][target_index]
    item = next(row for kind in ("approaches", "subsections")
                for row in target[kind] if row["name"] == name)
    return row_key(target, name, item["ids"])


def test_an_unadopted_approach_files_under_its_matched_or_own_name():
    clips = example_clips(_payload(), {}, "star:2:4", NO_JP)
    # matched_strategy wins where stamped; entries without a video drop.
    assert clips["TJ Owlless"] == [[1180, "https://v/fast"],
                                   [1290, "https://v/slow"]]
    assert clips["Own Name Way"] == [[1400, "https://v/own"]]


def test_an_unadopted_subsection_never_reaches_the_targets_entity():
    clips = example_clips(_payload(), {}, "star:2:4", NO_JP)
    assert "https://v/piece" not in str(clips)


def test_an_adopted_row_files_under_the_users_entity_as_its_strategy_name():
    payload = _payload()
    rows = {_key(payload, 1, "Lobby door (L) - BoB door"): "segment:42",
            _key(payload, 0, "Whomp text Xcam"): "segment:7"}
    # The approach is named after its target, so it lands as "Standard" —
    # adoptions.strategy_name's own rule; a subsection always does.
    assert example_clips(payload, rows, "segment:42", NO_JP) == {
        "Standard": [[276, "https://v/door"]]}
    assert example_clips(payload, rows, "segment:7", NO_JP) == {
        "Standard": [[300, "https://v/piece"]]}


def test_an_adopted_row_stops_contributing_to_its_targets_entity():
    payload = _payload()
    rows = {_key(payload, 0, "Own Name Way"): "segment:9"}
    clips = example_clips(payload, rows, "star:2:4", NO_JP)
    assert "Own Name Way" not in clips


def test_jp_entries_drop_only_where_a_jp_difference_is_annotated():
    payload = _payload()
    payload["targets"][0]["approaches"][0]["entries"].append(
        _entry(1100, "https://v/jp", version="JP"))
    combined = example_clips(payload, {}, "star:2:4", NO_JP)
    assert [1100, "https://v/jp"] in combined["TJ Owlless"]
    annotated = example_clips(
        payload, {}, "star:2:4",
        lambda entity, strat: strat == "TJ Owlless")
    assert [1100, "https://v/jp"] not in annotated["TJ Owlless"]
    # the other strategies on the same entity keep their entries
    assert annotated["Own Name Way"] == [[1400, "https://v/own"]]


# --- Sheet Best: the fastest recorded time, not the best FILMED one --------

def test_sheet_best_takes_the_fastest_entry_even_with_no_video():
    """The row answers "what IS the fastest recorded time", so a videoless
    faster run beats a slower filmed one and `video` is simply None. That is
    the one way it differs from example_clips, which needs a URL to be worth
    anything."""
    payload = _payload()
    payload["targets"][0]["approaches"][0]["entries"].append(_entry(1050))
    best = sheet_best(payload, {}, "star:2:4", NO_JP)
    assert best["TJ Owlless"] == {"time_cs": 1050, "runner": "r", "video": None}
    # ... and it still carries the video when the fastest run has one.
    assert best["Own Name Way"] == {"time_cs": 1400, "runner": "r",
                                    "video": "https://v/own"}


def test_sheet_best_resolves_through_the_same_three_doors_as_the_clips():
    """Same walk, so a strategy's Sheet Best and its example videos can never
    come from different rows: adoption wins, an unadopted approach files under
    its matched or own name, and an unadopted subsection never reaches its
    target's entity (its time measures a PIECE)."""
    payload = _payload()
    rows = {_key(payload, 1, "Lobby door (L) - BoB door"): "segment:42"}
    assert sheet_best(payload, rows, "segment:42", NO_JP)["Standard"][
        "time_cs"] == 276
    unadopted = sheet_best(payload, {}, "star:2:4", NO_JP)
    assert set(unadopted) == {"TJ Owlless", "Own Name Way"}   # no subsection
    assert 300 not in [b["time_cs"] for b in unadopted.values()]


def test_sheet_best_drops_a_jp_run_only_where_a_jp_difference_is_annotated():
    """The same JP rule the clips apply, which matters more here: an annotated
    JP ladder means the JP times grade elsewhere, so letting one set the row
    would print an unreachable target under a US ladder."""
    payload = _payload()
    payload["targets"][0]["approaches"][0]["entries"].append(
        _entry(900, version="JP"))
    assert sheet_best(payload, {}, "star:2:4", NO_JP)["TJ Owlless"][
        "time_cs"] == 900
    split = sheet_best(payload, {}, "star:2:4",
                       lambda entity, strat: strat == "TJ Owlless")
    assert split["TJ Owlless"]["time_cs"] == 1180
    assert split["Own Name Way"]["time_cs"] == 1400   # unaffected


def test_sheet_best_is_absent_for_a_strategy_with_no_sheet_row():
    """An empty cell, never a guess: the table draws a dash where the sheet
    has nothing to say about that strategy."""
    assert sheet_best(_payload(), {}, "star:99:9", NO_JP) == {}

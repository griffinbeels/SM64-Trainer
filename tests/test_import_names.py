"""Reading a typed block into targets and times.

The format is the community's own vocabulary, so most of these are really
assertions about what a runner would actually write down.
"""
import gzip
import json
from pathlib import Path

import sm64_events
from sm64_events.tracking.import_names import (Catalog, normalise, parse_block,
                                               parse_time_cs, resolve_target,
                                               segment_catalog, sheet_catalog,
                                               star_catalog)


def catalog():
    return star_catalog()


def sheet_payload():
    seed = (Path(sm64_events.__file__).parent / "data"
            / "sheet_library.seed.json.gz")
    return json.loads(gzip.decompress(seed.read_bytes()).decode("utf-8"))


# -- times -------------------------------------------------------------------

def test_every_shape_a_runner_writes_a_time_in():
    assert parse_time_cs("0:23.57") == 2357
    assert parse_time_cs("23.57") == 2357
    assert parse_time_cs("1:19.33") == 7933
    assert parse_time_cs("1'19\"33") == 7933
    assert parse_time_cs("8.86") == 886
    assert parse_time_cs("23") == 2300


def test_a_single_decimal_is_TENTHS_not_hundredths():
    """`.3` written by hand means three tenths. Reading it as three
    hundredths would silently credit a time 0.27s faster."""
    assert parse_time_cs("1:19.3") == 7930


def test_what_is_not_a_time():
    assert parse_time_cs("") is None
    assert parse_time_cs("Blast Away the Wall") is None
    assert parse_time_cs("2:75.00") is None      # 75 seconds past a minute
    assert parse_time_cs("0") is None            # a bare zero is not a run


# -- names -------------------------------------------------------------------

def test_apostrophes_and_case_do_not_matter():
    assert normalise("Whomp's Fortress") == normalise("WHOMPS  fortress")
    assert normalise("Whomp’s Fortress") == normalise("whomps fortress")


def test_a_star_resolves_by_its_own_name():
    assert resolve_target("Blast Away the Wall", catalog()) == "star:2:5"
    assert resolve_target("chip off whomps block", catalog()) == "star:2:0"


def test_the_shorthand_every_runner_types():
    """Players count stars from ONE; we store them from zero."""
    assert resolve_target("BoB 1", catalog()) == "star:1:0"
    assert resolve_target("WF 6", catalog()) == "star:2:5"
    assert resolve_target("Whomp's Fortress 5", catalog()) == "star:2:4"


def test_the_hundred_coin_star_in_the_forms_people_write_it():
    for text in ("WF 100", "WF 100c", "Whomp's Fortress 100 coins"):
        assert resolve_target(text, catalog()) == "star:2:6", text


def test_an_unknown_name_resolves_to_nothing_rather_than_guessing():
    assert resolve_target("Sneaky Chungus Skip", catalog()) is None
    assert resolve_target("", catalog()) is None


def test_a_real_star_name_cannot_be_redirected_by_a_later_source():
    """Precedence is add-order, and the game's own names go in first."""
    built = star_catalog()
    built.add_target("Blast Away the Wall", "star:9:9")
    assert resolve_target("Blast Away the Wall", built) == "star:2:5"


def test_the_sheets_own_labels_resolve_too():
    """A block copied straight out of the Ultimate Sheet must land with no
    editing — its labels ARE the community's names for these runs."""
    built = sheet_catalog(sheet_payload(), star_catalog())
    assert resolve_target("Big Bob-omb on the Summit", built) == "star:1:0"


def test_a_local_segment_resolves_by_name():
    """A segment id from the SHEET is meaningless here; one from this
    database is exactly what it says."""
    built = segment_catalog([{"id": 12, "name": "LBLJ"}], star_catalog())
    assert resolve_target("lblj", built) == "segment:12"


# -- whole blocks ------------------------------------------------------------

def test_a_block_of_the_three_separators_people_actually_paste():
    text = (
        "BoB 1\t0:23.57\n"
        "WF 6, 8.86, LJ\n"
        "Blast Away the Wall   10.03   Texture\n")
    candidates, unresolved = parse_block(text, catalog())
    assert unresolved == []
    assert [(c.entity_key, c.time_cs, c.strat_tag) for c in candidates] == [
        ("star:1:0", 2357, ""),
        ("star:2:5", 886, "LJ"),
        ("star:2:5", 1003, "Texture")]


def test_one_line_written_with_single_spaces_still_splits():
    candidates, unresolved = parse_block("Blast Away the Wall 8.86", catalog())
    assert unresolved == []
    assert candidates[0].entity_key == "star:2:5"
    assert candidates[0].time_cs == 886


def test_a_name_ending_in_a_number_does_not_lose_its_slot():
    """`BoB 1 0:23.57` has TWO numeric-looking tails; the LAST parseable one
    is the time, or the shorthand eats its own slot."""
    candidates, _ = parse_block("BoB 1 0:23.57", catalog())
    assert candidates[0].entity_key == "star:1:0"
    assert candidates[0].time_cs == 2357


def test_blank_lines_and_comments_are_punctuation_not_data():
    text = "# my golds\n\nBoB 1\t23.57\n\n   \n"
    candidates, unresolved = parse_block(text, catalog())
    assert len(candidates) == 1
    assert unresolved == []


def test_every_line_it_cannot_use_comes_back_with_its_reason():
    """A resolver that drops what it did not understand reports a clean
    import of half the data, and the missing half is invisible."""
    text = ("BoB 1\t23.57\n"
            "Sneaky Chungus Skip\t12.00\n"
            "WF 6\tnot a time\n"
            "\t45.00\n")
    candidates, unresolved = parse_block(text, catalog())
    assert len(candidates) == 1
    assert [(u.line, u.reason) for u in unresolved] == [
        (2, "unknown_target"), (3, "no_time"), (4, "no_target")]
    assert unresolved[0].text == "Sneaky Chungus Skip\t12.00".replace("\t", "\t")


def test_a_known_strategy_keeps_the_spelling_the_standards_use():
    built = Catalog()
    star_catalog(built)
    built.add_strategy("star:2:5", "Longjump")
    candidates, _ = parse_block("WF 6\t8.86\tLONGJUMP", built)
    assert candidates[0].strat_tag == "Longjump"


def test_a_strategy_nobody_has_heard_of_still_lands():
    """A strategy the player invented is a real strategy; refusing it would
    make the import lossy over exactly the times they care most about."""
    candidates, _ = parse_block("WF 6\t8.86\tMy Weird Setup", catalog())
    assert candidates[0].strat_tag == "My Weird Setup"


def test_the_timer_mode_is_the_callers_to_decide():
    """Stars are IGT and segments are RTA; the resolver does not own that
    rule, it asks."""
    built = segment_catalog([{"id": 3, "name": "LBLJ"}], star_catalog())
    candidates, _ = parse_block(
        "LBLJ\t12.00\nBoB 1\t23.57", built,
        timer_mode_for=lambda key: "rta" if key.startswith("segment:") else "igt")
    assert [(c.entity_key, c.timer_mode) for c in candidates] == [
        ("segment:3", "rta"), ("star:1:0", "igt")]

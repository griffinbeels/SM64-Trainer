"""Reading a LiveSplit splits file for its golds."""
import pytest

from sm64_events.tracking.import_names import segment_catalog, star_catalog
from sm64_events.tracking.livesplit import (candidates_for, parse_time_cs,
                                            read_golds)

SPLITS = """<?xml version="1.0" encoding="UTF-8"?>
<Run version="1.7.0">
  <GameName>Super Mario 64</GameName>
  <CategoryName>16 Star</CategoryName>
  <Segments>
    <Segment>
      <Name>LBLJ</Name>
      <BestSegmentTime><RealTime>00:00:12.3400000</RealTime></BestSegmentTime>
    </Segment>
    <Segment>
      <Name>Bowser 1 -&gt; WF</Name>
      <BestSegmentTime><RealTime>00:01:06.8299999</RealTime></BestSegmentTime>
    </Segment>
    <Segment>
      <Name>Never finished this one</Name>
      <BestSegmentTime />
    </Segment>
    <Segment>
      <Name>BoB 1</Name>
      <BestSegmentTime><RealTime>00:00:23.5700000</RealTime></BestSegmentTime>
    </Segment>
    <Segment>
      <Name>Some split nobody here has</Name>
      <BestSegmentTime><RealTime>00:00:09.0000000</RealTime></BestSegmentTime>
    </Segment>
  </Segments>
</Run>
"""

MINE = [{"id": 3, "name": "LBLJ"}, {"id": 7, "name": "Bowser 1 → WF"}]


def catalog():
    return segment_catalog(MINE, star_catalog())


# -- times -------------------------------------------------------------------

def test_livesplits_float_artefacts_round_to_the_time_actually_run():
    """A real 1:06.83 is on disk as `00:01:06.8299999`. TRUNCATING gives
    1:06.82 — a centisecond FASTER than the run, which is the one direction
    an import must never move a number."""
    assert parse_time_cs("00:00:23.5700000") == 2357
    assert parse_time_cs("00:01:06.8299999") == 6683
    assert parse_time_cs("00:00:12.3400000") == 1234


def test_the_shapes_a_hand_edited_file_carries():
    assert parse_time_cs("12.34") == 1234
    assert parse_time_cs("01:02:03.5") == 372350
    assert parse_time_cs("") is None
    assert parse_time_cs("not a time") is None


# -- golds -------------------------------------------------------------------

def test_every_split_with_a_best_time_in_file_order():
    golds = read_golds(SPLITS)
    assert [g.name for g in golds] == [
        "LBLJ", "Bowser 1 -> WF", "BoB 1", "Some split nobody here has"]
    assert golds[0].time_cs == 1234


def test_an_ascii_arrow_matches_a_segment_named_with_a_real_one():
    """LiveSplit files write `Bowser 1 -> WF`; our own corpus writes
    `Bowser 1 → WF`. Neither should have to change for the other."""
    from sm64_events.tracking.import_names import resolve_target
    assert resolve_target("Bowser 1 -> WF", catalog()) == "segment:7"
    assert resolve_target("Bowser 1 → WF", catalog()) == "segment:7"


def test_a_split_nobody_has_finished_is_absent_not_zero():
    """A zero would land as an impossibly fast time."""
    assert not any(g.name.startswith("Never finished") for g in read_golds(SPLITS))


def test_game_time_is_read_but_named_as_such():
    """A LiveSplit game-time column is only filled by an auto-splitter and
    measures a different clock again — read it, but say which it was."""
    data = SPLITS.replace(
        "<RealTime>00:00:12.3400000</RealTime>",
        "<GameTime>00:00:12.3400000</GameTime>")
    gold = next(g for g in read_golds(data) if g.name == "LBLJ")
    assert gold.clock == "game"


def test_real_time_wins_when_a_split_carries_both():
    data = SPLITS.replace(
        "<RealTime>00:00:12.3400000</RealTime>",
        "<RealTime>00:00:12.3400000</RealTime>"
        "<GameTime>00:00:99.0000000</GameTime>")
    gold = next(g for g in read_golds(data) if g.name == "LBLJ")
    assert gold.clock == "real"
    assert gold.time_cs == 1234


def test_a_file_that_is_not_xml_says_so_rather_than_importing_nothing():
    """"Nothing landed" and "that file is not a splits file" look identical
    from the outside, and only one is worth acting on."""
    import xml.etree.ElementTree as ElementTree
    with pytest.raises(ElementTree.ParseError):
        read_golds("this is a screenshot, not a splits file")


def test_an_external_entity_cannot_read_a_local_file():
    """MEASURED, not guarded: CPython's ElementTree does not resolve external
    entities at all and raises the moment one is REFERENCED. A hand-rolled
    hook on top of that would be a guard nobody can demonstrate."""
    import xml.etree.ElementTree as ElementTree
    hostile = ('<?xml version="1.0"?><!DOCTYPE Run [<!ENTITY x SYSTEM '
               '"file:///c:/Windows/win.ini">]>'
               "<Run><Segments><Segment><Name>&x;</Name>"
               "<BestSegmentTime><RealTime>00:00:01.0</RealTime>"
               "</BestSegmentTime></Segment></Segments></Run>")
    with pytest.raises(ElementTree.ParseError, match="undefined entity"):
        read_golds(hostile)


# -- candidates --------------------------------------------------------------

def test_golds_land_on_YOUR_segments_by_name_on_the_rta_clock():
    candidates, _ = candidates_for(SPLITS, catalog())
    assert [(c.entity_key, c.time_cs, c.timer_mode) for c in candidates] == [
        ("segment:3", 1234, "rta"), ("segment:7", 6683, "rta")]


def test_a_split_naming_a_STAR_is_reported_not_landed():
    """A gold is a real-time stretch of the run; a star's bests are Usamune
    IGT. Filing one against the other reads as a wildly good time."""
    _, unresolved = candidates_for(SPLITS, catalog())
    assert ("BoB 1", "not_a_segment") in [(u.text, u.reason) for u in unresolved]


def test_a_split_nobody_here_has_built_is_reported():
    """A file of thirty splits that lands four times owes the player
    twenty-six answers."""
    _, unresolved = candidates_for(SPLITS, catalog())
    assert ("Some split nobody here has", "unknown_target") in [
        (u.text, u.reason) for u in unresolved]


def test_a_strategy_can_be_named_for_the_whole_file():
    candidates, _ = candidates_for(SPLITS, catalog(), strategy="Standard")
    assert {c.strat_tag for c in candidates} == {"Standard"}

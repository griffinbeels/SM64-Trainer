"""Reading one runner's sheet column as importable times.

Every count here was measured against the bundled snapshot on 2026-08-20
(revision 2026-08-10T15:14:43). A re-scrape moves them; re-measure rather than
loosening the assertion, because a fixture that stops naming a real number
stops being evidence.
"""
import gzip
import json
from pathlib import Path

import sm64_events
from sm64_events.library.import_runner import candidates_for


def payload():
    seed = (Path(sm64_events.__file__).parent / "data"
            / "sheet_library.seed.json.gz")
    return json.loads(gzip.decompress(seed.read_bytes()).decode("utf-8"))


def test_dentoriousred_maps_to_fourteen_times_over_ten_stars():
    candidates, rejected = candidates_for(payload(), "DentoriousRed")
    assert len(candidates) == 14
    assert len({c.entity_key for c in candidates}) == 10
    # ONE ROW PER DROPPED ENTRY, named so he can review it -- a tally by kind
    # hid exactly the rows he wants to see (round 3, 2026-08-23). The text
    # names the target, the approach where it differs from the target, and
    # the sheet's own time.
    assert rejected == [
        {"reason": "segments",
         "text": "Bowser in the Fire Sea Course — No pole glitch — 0'39\"43"},
        {"reason": "segments",
         "text": "Bowser in the Sky Course — 0'48\"00"},
        {"reason": "no_entity",
         "text": "CCM wooden door - Enter BitDW (LBLJ) — 0'09\"23"},
    ]


def test_a_segment_row_drops_unless_the_caller_can_vouch_for_the_id():
    """Six of the snapshot's targets map to a segment, and a bare segment id
    is LOCAL to each database -- landing one blind would attribute a time to
    whatever that id happens to name here. With no resolver the reader drops
    them, named."""
    candidates, rejected = candidates_for(payload(), "DentoriousRed")
    assert sum(1 for row in rejected if row["reason"] == "segments") == 2
    assert not any(c.entity_key.startswith("segment:") for c in candidates)


def _seeded(local_ids):
    """A placer the way the router builds one, Bowser half only: the sheet's
    segment key -> THIS database's id for the same seeded movement, on the
    segment's clock, the sheet's own strategy."""
    from sm64_events.library.mapping import segment_seed_key

    def place(target, _item, kind):
        if kind != "approach":
            return None
        local = local_ids.get(segment_seed_key(target.get("entity_key") or ""))
        return (f"segment:{local}", "rta", None) if local is not None else None
    return place


def test_the_bowser_rows_land_on_the_seeded_movement_by_seed_key():
    """His correction, 2026-08-23: "Bowser in the Fire Sea Course ... are just
    the No Reds options for each bowser course. Bowser in the Dark World
    Battle == Bowser 1 ... These should also be allowed to be imported."
    The sheet's `segment:6` means the BitFS pipe entry; the placer says
    which id THAT is here -- 60 in this fixture, not 6 -- and the time lands
    on it, RTA, with the vetted strategy the adopt layer already paired."""
    place = _seeded({"seg:bitfs-pipe": 60, "seg:bits-pipe": 70})
    candidates, rejected = candidates_for(payload(), "DentoriousRed", place=place)
    bowser = [c for c in candidates if c.entity_key.startswith("segment:")]
    assert {(c.entity_key, c.strat_tag, c.time_cs, c.timer_mode) for c in bowser} == {
        ("segment:60", "Zero Cycle", 3943, "rta"),
        # 2026-09-02: a row named after its TARGET is that thing's STANDARD
        # strategy, not a strategy called after the course -- his rule, and
        # the same answer `adoptions.strategy_name` already gave every LINKED
        # row. Only the seed-key branch had kept its own.
        ("segment:70", "Standard", 4800, "rta"),
    }
    assert not any(row["reason"] == "segments" for row in rejected)
    assert len(candidates) == 16


def test_a_bowser_row_whose_movement_this_database_lacks_still_drops():
    """A placer that cannot place the key (he deleted Bowser 2, say) leaves
    the row in the list rather than landing it anywhere else."""
    place = _seeded({"seg:bitfs-pipe": 60})
    candidates, rejected = candidates_for(payload(), "DentoriousRed", place=place)
    assert [row["text"] for row in rejected if row["reason"] == "segments"] == [
        "Bowser in the Sky Course — 0'48\"00"]
    assert sum(1 for c in candidates if c.entity_key.startswith("segment:")) == 1


def test_a_linked_subsection_lands_on_its_segment_as_standard():
    """His question, 2026-08-23: "If an entry is a subsection AND we've
    successfully linked an actual subsection segment that we've recorded to
    that library entry, then when we import, it should import correctly. Is
    this the case?" It was not; now the placer answers for a piece with a
    link, and the piece lands on that segment under the strategy the link
    names (a subsection's community timing is its piece's Standard)."""
    def place(target, item, kind):
        if kind == "subsection" and item["name"] == "Volcano entry":
            return ("segment:42", "rta", "Standard")
        return None
    candidates, rejected = candidates_for(payload(), "GTM", place=place)
    pieces = [c for c in candidates if c.entity_key == "segment:42"]
    assert [(c.strat_tag, c.time_cs, c.timer_mode) for c in pieces] == [
        ("Standard", 806, "rta")]
    # The UNLINKED piece of the same target still drops, named.
    assert [row["text"] for row in rejected if row["reason"] == "subsections"] == [
        "Hot-Foot-It into the Volcano — Inside the volcano — 0'08\"53"]


def test_a_placed_castle_movement_lands_under_the_sheets_own_approach_name():
    """A movement the placer can put somewhere (a link, or the name-match an
    entity-less target gets unasked) lands there; with no strategy from the
    placer, the sheet's own approach name is the strategy."""
    def place(target, item, kind):
        if kind == "approach" and target["label"] == "Lakitu skip":
            return ("segment:3", "rta", None)
        return None
    candidates, rejected = candidates_for(payload(), "GTM", place=place)
    lakitu = [c for c in candidates if c.entity_key == "segment:3"]
    assert [(c.strat_tag, c.time_cs, c.timer_mode) for c in lakitu] == [
        ("JD -> Speedkick ending", 553, "rta")]
    assert not any(row["text"].startswith("Lakitu skip") for row in rejected)
    assert sum(1 for row in rejected if row["reason"] == "no_entity") == 22


def test_every_bowser_target_on_the_sheet_has_a_seed_key():
    """All six segment-mapped targets resolve to one of the seeded Bowser
    movements -- a seventh would be a mapping change that owes a row here."""
    from sm64_events.library.mapping import segment_seed_key
    keys = {t["entity_key"] for t in payload()["targets"]
            if (t.get("entity_key") or "").startswith("segment:")}
    assert len(keys) == 6
    assert {segment_seed_key(key) for key in keys} == {
        "seg:bitdw-pipe", "seg:bitfs-pipe", "seg:bits-pipe",
        "seg:bowser-1", "seg:bowser-2", "seg:bowser-3"}
    assert segment_seed_key("segment:999") is None
    assert segment_seed_key("star:1:0") is None


def test_a_jp_target_and_a_subsection_piece_are_named_in_their_rows():
    """The sheet opens a separate target per ROM version for some stars and
    movements, so without the version two dropped rows read as one; and a
    subsection row is only reviewable if it says which PIECE it timed."""
    _candidates, rejected = candidates_for(payload(), "GTM")
    texts = [row["text"] for row in rejected]
    assert "HMC door - Enter DDD (☆15 MIPS Clip) (JP) — 0'23\"00" in texts
    assert "Hot-Foot-It into the Volcano — Volcano entry — 0'08\"06" in texts


def test_every_candidate_names_a_star_a_strategy_and_the_igt_clock():
    candidates, _ = candidates_for(payload(), "DentoriousRed")
    assert all(c.entity_key.startswith("star:") for c in candidates)
    assert all(c.strat_tag for c in candidates)
    assert all(c.time_cs > 0 for c in candidates)
    assert all(c.timer_mode == "igt" for c in candidates)


def test_the_jp_rows_carry_their_version():
    """A JP time is genuinely faster than the same star on US, so graded
    against a US ladder it reads as superhuman. The version has to ride with
    the time."""
    candidates, _ = candidates_for(payload(), "DentoriousRed")
    assert sum(1 for c in candidates if c.game_version == "jp") == 2


def test_a_matched_strategy_wins_over_the_sheet_approach_name():
    candidates, _ = candidates_for(payload(), "DentoriousRed")
    blast = {c.strat_tag for c in candidates if c.entity_key == "star:2:5"}
    assert blast == {"Texture", "OG", "LJ"}


def test_a_subsection_never_becomes_a_personal_best():
    """A subsection times a stretch INSIDE a target. Imported as a best it
    would publish a 15.90s way of doing a 43s star."""
    runner = "Raisn"          # 93 subsection rows on the snapshot
    candidates, rejected = candidates_for(payload(), runner)
    assert any(row["reason"] == "subsections" for row in rejected)
    every_approach_name = {
        approach["name"]
        for target in payload()["targets"]
        for approach in target["approaches"]}
    subsection_names = {
        piece["name"]
        for target in payload()["targets"]
        for piece in target["subsections"]} - every_approach_name
    assert not ({c.strat_tag for c in candidates} & subsection_names)


def test_an_unknown_runner_yields_nothing_rather_than_raising():
    candidates, rejected = candidates_for(payload(), "NobodyAtAll")
    assert candidates == []
    assert rejected == []


def test_an_empty_payload_is_not_an_error():
    assert candidates_for({}, "DentoriousRed") == ([], [])

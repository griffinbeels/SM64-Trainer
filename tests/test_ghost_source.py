"""Does this star or segment have a ghost, and where is its footage?

The ghost is the SAVED recording of the attempt the active template was marked
from (his ruling, 2026-09-18: "yes"). Everything here is the executable form
of that sentence, plus the rule that every unavailable case says why in one
sentence a player can read.
"""
import pytest

from sm64_events.ghost.source import (
    REASONS, GhostSource, from_marked_attempt, resolve_ghost)
from sm64_events.inputs.templates import TemplateStore
from sm64_events.replay.service import ReplayService
from sm64_events.storage.db import Database

from tests.ghost_fixture import FakeReplay, a_document, a_view


@pytest.fixture
def templates(tmp_path) -> TemplateStore:
    db = Database(tmp_path / "t.db")
    return TemplateStore(db._conn, db._lock)


def save(templates, **overrides):
    fields = dict(kind="star", entity_key="24-1", strat_tag="10 coin",
                  name="the good one", origin="attempt:7",
                  document=a_document())
    fields.update(overrides)
    return templates.save(**fields)


def ghost(templates, replay, kind="star", entity_key="24-1", strat="10 coin",
          **kwargs) -> GhostSource:
    return resolve_ghost(kind, entity_key, strat, templates=templates,
                         replay=replay, **kwargs)


def a_ready_ghost(templates, attempt_id=7, **overrides):
    template = save(templates, origin=f"attempt:{attempt_id}", **overrides)
    replay = FakeReplay(saved=[attempt_id],
                        views={attempt_id: a_view(attempt_id)})
    return template, replay


# -- the available case ------------------------------------------------------

def test_a_marked_attempt_with_saved_frame_mapped_footage_is_a_ghost(templates):
    template, replay = a_ready_ghost(templates)
    answer = ghost(templates, replay)
    assert answer.available is True
    assert answer.reason is None
    assert answer.template_id == template.id
    assert answer.attempt_id == 7
    assert answer.media_url == "/api/replay/saved/7"
    assert answer.exactness == "frame_map"


def test_the_clip_view_is_passed_through_untouched(templates):
    """Its one owner is `replay/service.py`. Re-deriving any of it here would
    be the second path the single-source rule forbids."""
    _, replay = a_ready_ghost(templates)
    served = replay._views[7]
    answer = ghost(templates, replay)
    assert answer.view == served
    assert answer.payload()["view"] is served


def test_a_segment_answers_the_same_way(templates):
    save(templates, kind="segment", entity_key="12", strat_tag=None,
         origin="attempt:9", document=a_document(target="segment 12"))
    replay = FakeReplay(saved=[9], views={9: a_view(9)})
    answer = ghost(templates, replay, kind="segment", entity_key="12",
                   strat=None)
    assert (answer.available, answer.attempt_id) == (True, 9)


def test_a_strategy_with_no_template_of_its_own_falls_back(templates):
    """The store's existing fallback: a template recorded before he named a
    strategy still describes the same movement."""
    save(templates, strat_tag=None, origin="attempt:7")
    replay = FakeReplay(saved=[7], views={7: a_view(7)})
    assert ghost(templates, replay, strat="a new idea").available is True


def test_a_strategys_own_template_wins_over_the_shared_one(templates):
    save(templates, strat_tag=None, origin="attempt:7")
    save(templates, strat_tag="10 coin", origin="attempt:8")
    replay = FakeReplay(saved=[7, 8], views={7: a_view(7), 8: a_view(8)})
    assert ghost(templates, replay, strat="10 coin").attempt_id == 8


# -- every unavailable case, each with its own reason ------------------------

def test_nothing_set_up_yet_is_no_template(templates):
    answer = ghost(templates, FakeReplay())
    assert (answer.available, answer.reason) == (False, "no_template")
    assert answer.template_id is None and answer.view is None


@pytest.mark.parametrize("origin", ["import:someone else's run", "authored"])
def test_a_template_that_never_came_from_a_run_here_has_no_footage(
        templates, origin):
    template = save(templates, origin=origin)
    answer = ghost(templates, FakeReplay())
    assert answer.reason == "template_has_no_attempt"
    assert answer.template_id == template.id
    assert answer.attempt_id is None


def test_an_unsaved_recording_says_so_and_never_cuts_a_new_clip(templates):
    """The ring is not a ghost source: footage that was never saved is gone
    after the session, so the switch must read unavailable rather than make
    the poll loop extract a clip on a practice-page render."""
    template = save(templates, origin="attempt:7")
    replay = FakeReplay(saved=[], views={7: a_view(7)})
    answer = ghost(templates, replay)
    assert answer.reason == "recording_not_saved"
    assert (answer.template_id, answer.attempt_id) == (template.id, 7)
    assert replay.viewed == []


def test_footage_the_capture_layer_never_stamped_has_no_frame_map(templates):
    save(templates, origin="attempt:7")
    replay = FakeReplay(saved=[7], views={7: a_view(7, frame_map=None)})
    answer = ghost(templates, replay)
    assert answer.reason == "recording_has_no_frame_map"
    assert answer.attempt_id == 7
    assert answer.media_url is None


def test_a_saved_file_whose_run_left_the_history_reads_as_unsaved(templates):
    """An orphan file with no attempt row cannot be lined up against a clock;
    it is the same answer for the player as never having saved it."""
    save(templates, origin="attempt:7")
    answer = ghost(templates, FakeReplay(saved=[7], views={}))
    assert answer.reason == "recording_not_saved"


def test_a_database_that_went_away_is_not_an_answer(templates):
    class Broken(FakeReplay):
        def view(self, attempt_id):
            raise RuntimeError("database unavailable")

    save(templates, origin="attempt:7")
    with pytest.raises(RuntimeError):
        ghost(templates, Broken(saved=[7]))


# -- the sentences themselves ------------------------------------------------

def test_every_unavailable_case_carries_a_readable_sentence(templates):
    seen = set()
    for reason, text in REASONS.items():
        seen.add(reason)
        assert text.endswith(".") and len(text.split()) >= 8, reason
        assert text[0].isupper(), reason
    assert seen == {"no_template", "template_has_no_attempt",
                    "recording_not_saved", "recording_has_no_frame_map"}


@pytest.mark.parametrize("jargon", [
    "frame map", "frame_map", "template store", "attempt_id", "API",
    "sidecar", "capture layer", "null", "None", "payload", "endpoint"])
def test_no_reason_names_a_technique(jargon):
    for reason, text in REASONS.items():
        assert jargon.lower() not in text.lower(), f"{reason}: {text}"


def test_a_ready_ghost_has_no_reason_text_to_show(templates):
    _, replay = a_ready_ghost(templates)
    assert ghost(templates, replay).reason_text is None


# -- the shape the follower and the practice card consume --------------------

def test_the_payload_carries_exactly_the_agreed_keys(templates):
    _, replay = a_ready_ghost(templates)
    assert set(ghost(templates, replay).payload()) == {
        "available", "reason", "reason_text", "template_id", "attempt_id",
        "media_url", "view", "exactness"}


def test_an_unavailable_payload_fills_every_key(templates):
    payload = ghost(templates, FakeReplay()).payload()
    assert payload["available"] is False
    assert payload["reason"] == "no_template"
    assert isinstance(payload["reason_text"], str)
    assert [payload[key] for key in
            ("template_id", "attempt_id", "media_url", "view", "exactness")
            ] == [None] * 5


# -- the seam the other two providers arrive through -------------------------

def test_a_second_provider_can_answer_for_an_origin_this_one_declines(
        templates):
    """Imported replays and downloaded footage are rows to add here, not a
    second copy of this resolution."""
    def from_an_imported_replay(template, *, replay):
        if not template.origin.startswith("import:"):
            return None
        return GhostSource(available=True, template_id=template.id,
                           media_url="/api/inputs/templates/1/replay",
                           view=a_view(0), exactness="frame_map")

    save(templates, origin="import:a friend's run")
    answer = ghost(templates, FakeReplay(),
                   providers=(from_marked_attempt, from_an_imported_replay))
    assert answer.available is True
    assert answer.media_url == "/api/inputs/templates/1/replay"


def test_an_unknown_kind_is_refused_rather_than_answered(templates):
    with pytest.raises(ValueError):
        ghost(templates, FakeReplay(), kind="course")


def test_the_replay_doors_this_reads_still_exist():
    """The fakes above stand in for ReplayService; a rename there must fail
    here rather than leave the ghost quietly unavailable forever."""
    for door in ("find_saved", "view"):
        assert callable(getattr(ReplayService, door))

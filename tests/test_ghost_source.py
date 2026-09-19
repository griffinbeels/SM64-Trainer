"""Does this star or segment have a ghost, and where is its footage?

The ghost is the SAVED recording of the attempt the active template was marked
from (his ruling, 2026-09-18: "yes"). Everything here is the executable form
of that sentence, plus the rule that every unavailable case says why in one
sentence a player can read.
"""
import pytest

from sm64_events.ghost.source import (
    ATTEMPT_FRAMES, PICTURE_TIMER, REASONS, GhostSource, from_marked_attempt,
    resolve_ghost, span_of)
from sm64_events.inputs.templates import TemplateStore
from sm64_events.replay.service import ReplayService
from sm64_events.storage.db import Database
from sm64_events.tracking.projection import Attempt

from tests.ghost_fixture import (
    FakeAttempt, FakeReplay, a_document, a_real_clip_view, attempts_of, a_view)


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


# -- his own clip, and the facts this file reads out of it -------------------
#
# `tests/fixtures/ghost_span_clip.json` holds the two per-picture arrays of the
# saved clip behind his star 6-0 template, verbatim. Read straight out of them:
LEAD_IN_PICTURES = 45      # 0..44, the previous run held through the reload
FIRST_OF_THE_RUN = 45      # game frame 308680, the first picture timed 3
RUN_START_FRAME = 308680
LAST_OF_THE_RUN = 626      # game frame 309261, the first picture timed 584
RUN_END_FRAME = 309261
RECORDED_FRAMES = 584      # the run's own length, where its timer stops
CLIP_PICTURES = 642        # so fifteen pictures follow the star


def real_clip(templates, attempt_id=9, **attempt_fields):
    """His clip, the template that was marked from it, and its attempt row."""
    save(templates, origin=f"attempt:{attempt_id}")
    replay = FakeReplay(saved=[attempt_id],
                        views={attempt_id: a_real_clip_view(attempt_id)})
    row = FakeAttempt(id=attempt_id, **attempt_fields)
    return replay, attempts_of(row)


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
        "media_url", "view", "exactness", "span"}


def test_an_unavailable_payload_fills_every_key(templates):
    payload = ghost(templates, FakeReplay()).payload()
    assert payload["available"] is False
    assert payload["reason"] == "no_template"
    assert isinstance(payload["reason_text"], str)
    assert [payload[key] for key in
            ("template_id", "attempt_id", "media_url", "view", "exactness",
             "span")] == [None] * 6


# -- the span: exactly the attempt, whatever the recorder was set to ---------

@pytest.mark.parametrize("anchor_frame", [308677, 308678, 308679, 308680])
def test_his_own_clip_is_clamped_to_the_run_inside_it(templates, anchor_frame):
    """His ruling: "clamped to the EXACT START FRAME of the attempt, and the
    exact ending frame of the star grab... regardless of the user's recording
    settings."

    The clip's timer reads 3 on picture 45, so the reset landed three game
    frames earlier; the anchor is swept across all four candidates because the
    ANSWER must not depend on which of them the row recorded.
    """
    replay, attempts = real_clip(
        templates, anchor_frame=anchor_frame,
        rta_frames=RUN_END_FRAME - anchor_frame, igt_frames=RECORDED_FRAMES)
    span = ghost(templates, replay, attempts=attempts).span
    assert (span.first_picture, span.last_picture) == (FIRST_OF_THE_RUN,
                                                       LAST_OF_THE_RUN)
    assert (span.start_frame, span.end_frame) == (anchor_frame, RUN_END_FRAME)
    assert span.basis == ATTEMPT_FRAMES


def test_the_lead_in_from_the_previous_run_is_left_out(templates):
    """Every one of the 45 pictures before the run carries the PREVIOUS run's
    final timer, held through the reset's reload — the shape that made the
    follower refuse his footage outright."""
    view = a_real_clip_view()
    before = [t for t in view["picture_igt"][:LEAD_IN_PICTURES]
              if t is not None]
    assert set(before) == {3029} and len(before) == 42
    replay, attempts = real_clip(templates, anchor_frame=308677,
                                 rta_frames=584, igt_frames=RECORDED_FRAMES)
    assert ghost(templates, replay, attempts=attempts
                 ).span.first_picture == FIRST_OF_THE_RUN


def test_the_footage_after_the_star_is_left_out(templates):
    """The recorder kept rolling: the last fifteen pictures sit frozen at the
    run's own final time, which is where the recorded time stops."""
    view = a_real_clip_view()
    assert len(view["frame_map"]) == CLIP_PICTURES
    after = view["picture_igt"][LAST_OF_THE_RUN + 1:]
    assert after and set(after) == {RECORDED_FRAMES}
    replay, attempts = real_clip(templates, anchor_frame=308677,
                                 rta_frames=584, igt_frames=RECORDED_FRAMES)
    assert ghost(templates, replay, attempts=attempts
                 ).span.last_picture == LAST_OF_THE_RUN


def test_the_first_picture_of_the_run_is_the_one_the_timer_restarts_on(
        templates):
    view = a_real_clip_view()
    assert view["picture_igt"][FIRST_OF_THE_RUN] == 3
    assert view["frame_map"][FIRST_OF_THE_RUN] == RUN_START_FRAME
    assert view["frame_map"][LAST_OF_THE_RUN] == RUN_END_FRAME
    assert view["picture_igt"][LAST_OF_THE_RUN] == RECORDED_FRAMES


def test_unstamped_pictures_and_repeated_frames_move_neither_end():
    """Two pictures can show one game frame, and a picture can carry no stamp
    at all. The first end takes the EARLIER of a pair and the last end the
    LATER, so neither drops a frame the player actually played."""
    view = {"frame_map": [100, None, 100, 101, 101, None, 102, 102, 103],
            "picture_igt": [0, None, 0, 1, 1, None, 2, 2, 3]}
    span = span_of(view, FakeAttempt(anchor_frame=101, rta_frames=1))
    assert (span.first_picture, span.last_picture) == (3, 7)
    assert (span.start_frame, span.end_frame) == (101, 102)


def test_a_row_with_no_frames_of_its_own_falls_back_to_the_clip_timer(
        templates):
    """An imported time, or any row closed with no anchor, still gets a span —
    read off the timer, and SAYING it was read off the timer."""
    replay, attempts = real_clip(templates, igt_frames=RECORDED_FRAMES)
    span = ghost(templates, replay, attempts=attempts).span
    assert span.basis == PICTURE_TIMER
    assert (span.first_picture, span.last_picture) == (FIRST_OF_THE_RUN,
                                                       LAST_OF_THE_RUN)
    assert (span.start_frame, span.end_frame) == (RUN_START_FRAME,
                                                  RUN_END_FRAME)


def test_the_timer_fallback_takes_the_LAST_restart_not_the_first():
    """A clip can hold more than one reset. The run being shown is the one
    the clip ends on."""
    view = {"frame_map": [10, 11, 12, 13, 14, 15, 16],
            "picture_igt": [900, 1, 2, 800, 1, 2, 3]}
    span = span_of(view, FakeAttempt(igt_frames=3))
    assert (span.first_picture, span.last_picture) == (4, 6)


def test_a_clip_that_opens_at_zero_has_no_drop_to_find():
    """His one clip that happened to carry no pre-roll is still a run."""
    view = {"frame_map": [10, 11, 12], "picture_igt": [1, 2, 3]}
    span = span_of(view, FakeAttempt(igt_frames=3))
    assert (span.first_picture, span.last_picture) == (0, 2)
    assert span.basis == PICTURE_TIMER


def test_a_grab_timed_row_also_names_the_frame_of_the_touch():
    """The ghost ends where the recorded time stops. A row whose time was
    taken AT the touch says so, so the choice can be overruled without the
    server being asked again."""
    view = {"frame_map": [10, 11, 12], "picture_igt": [1, 2, 3]}
    span = span_of(view, FakeAttempt(anchor_frame=10, rta_frames=2,
                                     timed_at="grab"))
    assert span.grab_frame == 12 and span.end_frame == 12


def test_an_xcam_timed_row_does_not_claim_to_know_the_touch():
    """`timed_at` is the only record of the moment, and the touch frame is
    journaled on the grab event rather than copied onto the row."""
    view = {"frame_map": [10, 11, 12], "picture_igt": [1, 2, 3]}
    assert span_of(view, FakeAttempt(anchor_frame=10, rta_frames=2,
                                     timed_at="xcam")).grab_frame is None


@pytest.mark.parametrize("view,attempt", [
    ({"frame_map": None, "picture_igt": None}, FakeAttempt(anchor_frame=10,
                                                           rta_frames=2)),
    ({"frame_map": [10, 11], "picture_igt": [99, 100]}, None),
    ({"frame_map": [10, 11], "picture_igt": [99, 100]},
     FakeAttempt(igt_frames=5)),
    ({"frame_map": [10, 11], "picture_igt": [1, 2]},
     FakeAttempt(anchor_frame=900, rta_frames=10)),
])
def test_a_span_nothing_can_find_is_null_rather_than_a_guess(view, attempt):
    assert span_of(view, attempt) is None


def test_a_ghost_with_no_span_is_still_available(templates):
    """The page has its own rule to fall back on; refusing the footage because
    the clamp is unknown would hide a run he can watch."""
    template, replay = a_ready_ghost(templates)
    answer = ghost(templates, replay)
    assert answer.available is True and answer.span is None
    assert answer.payload()["span"] is None


def test_the_span_never_rewrites_the_view(templates):
    """Domain contract 6: the view has one owner. The span is a sibling."""
    replay, attempts = real_clip(templates, anchor_frame=308677,
                                 rta_frames=584, igt_frames=RECORDED_FRAMES)
    served = replay._views[9]
    answer = ghost(templates, replay, attempts=attempts)
    assert answer.view is served
    assert answer.view["frame_map"] == a_real_clip_view()["frame_map"]
    assert "span" not in answer.view


def test_a_segment_is_clamped_to_its_own_frames_too(templates):
    save(templates, kind="segment", entity_key="12", strat_tag=None,
         origin="attempt:9", document=a_document(target="segment 12"))
    replay = FakeReplay(saved=[9], views={9: a_real_clip_view(9)})
    attempts = attempts_of(FakeAttempt(id=9, anchor_frame=308677,
                                       rta_frames=584, igt_frames=584))
    span = ghost(templates, replay, kind="segment", entity_key="12",
                 strat=None, attempts=attempts).span
    assert (span.first_picture, span.last_picture) == (FIRST_OF_THE_RUN,
                                                       LAST_OF_THE_RUN)
    assert span.basis == ATTEMPT_FRAMES


def test_a_missing_attempt_row_costs_the_clamp_and_nothing_else(templates):
    save(templates, origin="attempt:7")
    replay = FakeReplay(saved=[7], views={7: a_real_clip_view(7)})
    answer = ghost(templates, replay, attempts=attempts_of())
    assert answer.available is True and answer.span is None


def test_the_attempt_fields_the_span_reads_still_exist():
    """`FakeAttempt` stands in for the projector's row; a rename there must
    fail here rather than silently drop every ghost back to the timer."""
    for field in ("anchor_frame", "rta_frames", "igt_frames", "timed_at"):
        assert field in Attempt.__dataclass_fields__, field


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

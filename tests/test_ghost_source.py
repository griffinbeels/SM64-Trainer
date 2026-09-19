"""Does this star or segment have a ghost, and where is its footage?

The ghost is the SAVED recording of the attempt the active template was marked
from (his ruling, 2026-09-18: "yes"). Everything here is the executable form
of that sentence, plus the rule that every unavailable case says why in one
sentence a player can read.
"""
import pytest

from sm64_events.detectors.star_grab import _PendingGrab
from sm64_events.ghost.source import (
    ATTEMPT_FRAMES, GRAB_EVENT, GRAB_EVENT_TYPE, PICTURE_TIMER, REASONS,
    GhostSource, from_marked_attempt, resolve_ghost, span_of)
from sm64_events.inputs.templates import TemplateStore
from sm64_events.replay.service import ReplayService
from sm64_events.storage.db import Database
from sm64_events.tracking.projection import BOUNDARY_EVENT_TYPES, Attempt

from tests.ghost_fixture import (
    FakeAttempt, FakeEvent, FakeReplay, a_document, a_real_clip_attempt,
    a_real_clip_journal, a_real_clip_view, attempts_of, a_view, events_of)


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
# `tests/fixtures/ghost_span_clip.json` holds his Swimming Beast in the Cavern
# run: the clip's two per-picture arrays, the attempt row, and the grab that
# closed it, all verbatim (the file says where each came from). Read out of it:
LEAD_IN_PICTURES = 45      # 0..44, the previous run held through the reload
FIRST_OF_THE_RUN = 45      # game frame 308680, the first picture timed 3
ANCHOR_FRAME = 308679      # the reset, three frames before that picture
RUN_START_FRAME = 308680
TOUCH_FRAME = 309215       # the grab event's own `grab_frame`
TOUCH_PICTURE = 580        # where the ghost now ends
TIMED_FRAME = 309219       # the landing, four frames later, where the time is
TIMED_PICTURE = 584        # where the ghost used to end
RECORDED_FRAMES = 543      # the row's igt_frames -- NOT the clip's final 584,
                           # which is where Usamune's counter came to rest on
                           # the star-select screen, 41 frames on
CLIP_PICTURES = 642        # so 61 pictures follow the touch


def real_clip(templates, attempt_id=9, with_grab=True, **attempt_fields):
    """His clip, the template marked from it, its attempt row and its grab."""
    save(templates, origin=f"attempt:{attempt_id}")
    replay = FakeReplay(saved=[attempt_id],
                        views={attempt_id: a_real_clip_view(attempt_id)})
    row = a_real_clip_attempt(attempt_id, **attempt_fields)
    return replay, {"attempts": attempts_of(row),
                    "events": a_real_clip_journal() if with_grab else None}


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

def test_his_own_clip_is_clamped_to_the_run_inside_it(templates):
    """His ruling: "clamped to the EXACT START FRAME of the attempt, and the
    exact ending frame of the star grab... regardless of the user's recording
    settings," and then "End at the touch. No reason to keep it after that."

    Every number here is the journal's own: the reset landed on 308679 and the
    grab that closed the attempt records the touch at 309215.
    """
    replay, doors = real_clip(templates)
    span = ghost(templates, replay, **doors).span
    assert (span.first_picture, span.last_picture) == (FIRST_OF_THE_RUN,
                                                       TOUCH_PICTURE)
    assert (span.start_frame, span.end_frame) == (ANCHOR_FRAME, TOUCH_FRAME)
    assert span.grab_frame == TOUCH_FRAME
    assert span.basis == f"{ATTEMPT_FRAMES}+{GRAB_EVENT}"


def test_the_ghost_now_stops_four_pictures_earlier_than_the_recorded_time(
        templates):
    """The time is taken at the landing, four game frames after the touch on
    this run, and the ghost used to run that far. The landing is kept beside
    the end so the two can still be reconciled."""
    replay, doors = real_clip(templates)
    span = ghost(templates, replay, **doors).span
    assert span.timed_frame == TIMED_FRAME
    assert TIMED_FRAME - TOUCH_FRAME == 4
    assert TIMED_PICTURE - span.last_picture == 4


def test_the_lead_in_from_the_previous_run_is_left_out(templates):
    """Every one of the 45 pictures before the run carries the PREVIOUS run's
    final timer, held through the reset's reload — the shape that made the
    follower refuse his footage outright."""
    view = a_real_clip_view()
    before = [t for t in view["picture_igt"][:LEAD_IN_PICTURES]
              if t is not None]
    assert set(before) == {3029} and len(before) == 42
    replay, doors = real_clip(templates)
    assert ghost(templates, replay, **doors).span.first_picture == (
        FIRST_OF_THE_RUN)


def test_the_footage_after_the_touch_is_left_out(templates):
    """The recorder kept rolling to the star-select screen: 61 pictures follow
    the touch, and Usamune's counter runs 41 frames past the recorded time
    before coming to rest — so the clip's final 584 is not this run's length."""
    view = a_real_clip_view()
    assert len(view["frame_map"]) == CLIP_PICTURES
    assert view["picture_igt"][-1] == 584 and RECORDED_FRAMES == 543
    replay, doors = real_clip(templates)
    span = ghost(templates, replay, **doors).span
    assert span.last_picture == TOUCH_PICTURE
    assert CLIP_PICTURES - 1 - span.last_picture == 61


def test_the_first_picture_of_the_run_is_the_one_the_timer_restarts_on():
    view = a_real_clip_view()
    assert view["picture_igt"][FIRST_OF_THE_RUN] == 3
    assert view["frame_map"][FIRST_OF_THE_RUN] == RUN_START_FRAME
    assert view["frame_map"][TOUCH_PICTURE] == TOUCH_FRAME
    assert view["frame_map"][TIMED_PICTURE] == TIMED_FRAME


def test_a_star_whose_touch_is_not_journalled_ends_at_the_landing(templates):
    """A grab written before that field existed, or a boot with no database to
    ask: the ghost ends where the time was taken, and the basis says so."""
    replay, doors = real_clip(templates, with_grab=False)
    span = ghost(templates, replay, **doors).span
    assert (span.end_frame, span.timed_frame) == (TIMED_FRAME, TIMED_FRAME)
    assert (span.last_picture, span.grab_frame) == (TIMED_PICTURE, None)
    assert span.basis == ATTEMPT_FRAMES


def test_a_grab_from_another_star_inside_the_span_does_not_answer(templates):
    """`events_between` is a wall-clock slice, so a grab that is not this
    attempt's can land in it. Matched on the row's own star."""
    replay, doors = real_clip(templates)
    doors["events"] = a_real_clip_journal(star_id=4, grab_frame=308900)
    span = ghost(templates, replay, **doors).span
    assert span.end_frame == TIMED_FRAME and span.basis == ATTEMPT_FRAMES


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
    read off the timer, and SAYING it was read off the timer. It lands one
    picture past the landing, because the recorded time is the counter plus
    the display tick while the picture carries the counter itself."""
    replay, doors = real_clip(templates, anchor_frame=None, rta_frames=None)
    span = ghost(templates, replay, **doors).span
    assert span.basis == PICTURE_TIMER
    assert (span.first_picture, span.last_picture) == (FIRST_OF_THE_RUN,
                                                       TIMED_PICTURE + 1)
    assert span.start_frame == RUN_START_FRAME
    assert span.end_frame == span.timed_frame == TIMED_FRAME + 1


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


def test_a_grab_timed_row_needs_no_journal_to_find_its_touch():
    """The backstop case where no star dance ever ran: the landing IS the
    touch, and it comes off the row, so the basis says the row."""
    view = {"frame_map": [10, 11, 12], "picture_igt": [1, 2, 3]}
    span = span_of(view, FakeAttempt(anchor_frame=10, rta_frames=2,
                                     timed_at="grab"))
    assert (span.grab_frame, span.end_frame, span.timed_frame) == (12, 12, 12)
    assert span.basis == ATTEMPT_FRAMES


def test_a_touch_outside_the_attempt_is_refused_rather_than_used():
    """A grab frame below the anchor or above the landing cannot be this run's,
    so the landing stands."""
    view = {"frame_map": [10, 11, 12], "picture_igt": [1, 2, 3]}
    for grab in (9, 13):
        span = span_of(view, FakeAttempt(anchor_frame=10, rta_frames=2),
                       a_real_clip_journal(grab_frame=grab, course_id=None,
                                           star_id=None))
        assert (span.end_frame, span.grab_frame) == (12, None)
        assert span.basis == ATTEMPT_FRAMES


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
    replay, doors = real_clip(templates)
    served = replay._views[9]
    answer = ghost(templates, replay, **doors)
    assert answer.view is served
    assert answer.view["frame_map"] == a_real_clip_view()["frame_map"]
    assert "span" not in answer.view


def test_a_segment_keeps_its_own_end_because_it_has_no_touch(templates):
    """Only a star is touched. A segment ends where its own frames say, even
    with a grab sitting inside its wall-clock span."""
    save(templates, kind="segment", entity_key="12", strat_tag=None,
         origin="attempt:9", document=a_document(target="segment 12"))
    replay = FakeReplay(saved=[9], views={9: a_real_clip_view(9)})
    attempts = attempts_of(a_real_clip_attempt(9, segment_id=12,
                                               course_id=None, star_id=None))
    span = ghost(templates, replay, kind="segment", entity_key="12",
                 strat=None, attempts=attempts,
                 events=a_real_clip_journal()).span
    assert (span.first_picture, span.last_picture) == (FIRST_OF_THE_RUN,
                                                       TIMED_PICTURE)
    assert (span.end_frame, span.grab_frame) == (TIMED_FRAME, None)
    assert span.basis == ATTEMPT_FRAMES


def test_a_missing_attempt_row_costs_the_clamp_and_nothing_else(templates):
    save(templates, origin="attempt:7")
    replay = FakeReplay(saved=[7], views={7: a_real_clip_view(7)})
    answer = ghost(templates, replay, attempts=attempts_of())
    assert answer.available is True and answer.span is None


def test_only_the_grab_event_is_asked_for_the_touch(templates):
    """Other rows land in the same wall-clock slice; none of them is a touch."""
    replay, doors = real_clip(templates)
    doors["events"] = events_of(
        FakeEvent("practice_reset", 308679, {"mario_acted": True}),
        FakeEvent("warp_entered", 308900, {"grab_frame": 308900}))
    span = ghost(templates, replay, **doors).span
    assert span.end_frame == TIMED_FRAME and span.basis == ATTEMPT_FRAMES


def test_the_attempt_fields_the_span_reads_still_exist():
    """`FakeAttempt` stands in for the projector's row; a rename there must
    fail here rather than silently drop every ghost back to the timer."""
    for field in ("anchor_frame", "rta_frames", "igt_frames", "timed_at",
                  "course_id", "star_id", "segment_id", "started_utc",
                  "ended_utc"):
        assert field in Attempt.__dataclass_fields__, field


def test_the_grab_this_reads_is_the_one_the_detector_writes():
    """The touch is a payload key on one event type. A rename of either must
    fail here rather than send every ghost quietly back to the landing."""
    assert GRAB_EVENT_TYPE in BOUNDARY_EVENT_TYPES
    assert "grab_frame" in _PendingGrab.__dataclass_fields__


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

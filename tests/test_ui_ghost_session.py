"""The Ghost Overlay control's rules, driven through node.

`ui/ghostsession.js` is import-free so these run without a browser. What is
worth pinning here is not the arithmetic -- it is the three states the switch
has to tell apart, because two of them look identical on screen if the rules
are wrong:

  * the session is answering about ANOTHER entity (a target change the server
    has not caught up with yet) -- the control must not borrow that answer;
  * the entity genuinely has no footage -- the control is off and says why;
  * the entity has footage -- the control works.

The middle one is the state Griffin asked for ("disabled / not selectable if
the user hasn't set up a template for the star/segment yet"), and the first is
what stops it being drawn about the wrong star for a frame.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
GHOST_JS = (REPO / "src" / "sm64_events" / "ui" / "ghostsession.js").as_uri()

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")


def run_node(names: str, body: str):
    script = (f"import {{ {names} }} from {GHOST_JS!r};\n"
              f"console.log(JSON.stringify({body}));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def controls(session, entity_key="star:8:1"):
    return run_node("ghostControls",
                    f"ghostControls({json.dumps(session)}, "
                    f"{json.dumps(entity_key)})")


def test_pose_only_template_offers_mario_even_when_whole_video_is_unavailable():
    result = controls(session(source={"available": False, "native_available": True,
                                      "whole_available": False, "native_reason_text": None}))
    assert result["nativeViews"]["mario"] and not result["nativeViews"]["whole"]
    assert result["marioBoxes"]["available"]


def test_native_preparation_is_visible_while_switch_stays_usable():
    result = controls(session(enabled=True, view="mario", source={"native_available": True},
                              window={"backend": "native", "reason": "Preparing Mario..."}))
    assert result["canToggle"] and result["reasonText"] == "Preparing Mario..."


def session(**over):
    body = {"enabled": False, "opacity": 0.6, "nudge_frames": 0,
            "tint": {"enabled": True, "hue_degrees": 120},
            "view": "whole",
            "target": {"kind": "star", "entity_key": "8-1", "strat": None},
            "source": {"available": True, "reason": None, "reason_text": None,
                       "template_id": 4, "attempt_id": 90,
                       "exactness": "frame_map",
                       "mario_boxes_available": False,
                       "mario_boxes_reason": "no_box_list",
                       "mario_boxes_reason_text":
                           "This run has not had Mario found in it yet, so "
                           "only the whole picture can be shown."},
            "window": {"running": False, "visible": False}}
    for key, value in over.items():
        if key in ("source", "target", "window") and isinstance(value, dict):
            body[key] = {**(body[key] or {}), **value} if body[key] else value
        else:
            body[key] = value
    return body


# --- the three states -------------------------------------------------------

def test_a_session_about_another_entity_is_not_an_answer_about_this_one():
    """The one rule a render cannot easily show and a wrong reading of which
    paints another star's refusal under this star's name."""
    answer = controls(session(), entity_key="star:22:0")
    assert answer["known"] is False
    assert answer["canToggle"] is False
    # It still says something -- a disabled control with no reason is the
    # dead button this project has shipped once already.
    assert answer["reasonText"]
    assert "Checking" in answer["reasonText"]


def test_no_session_yet_reads_as_still_looking_not_as_refused():
    answer = controls(None)
    assert (answer["known"], answer["canToggle"]) == (False, False)
    assert answer["reasonText"]


def test_an_entity_with_no_ghost_is_disabled_and_carries_the_servers_sentence():
    """His words: 'disabled / not selectable if the user hasn't set up a
    template for the star/segment yet' -- plus the acceptance rule that a
    control which cannot run shows a visible reason."""
    sentence = ("You have not set a template for this yet, so there is no "
                "run to play alongside you.")
    answer = controls(session(source={"available": False,
                                      "reason": "no_template",
                                      "reason_text": sentence}))
    assert answer["known"] is True
    assert answer["available"] is False
    assert answer["canToggle"] is False
    assert answer["reasonText"] == sentence
    assert answer["showsTuning"] is False


def test_an_entity_with_a_ghost_can_be_switched_and_carries_no_reason():
    answer = controls(session())
    assert (answer["known"], answer["available"], answer["canToggle"]) == (
        True, True, True)
    assert answer["reasonText"] is None
    assert answer["enabled"] is False
    assert answer["showsTuning"] is False


def test_the_tuning_controls_appear_only_while_it_is_on():
    answer = controls(session(enabled=True))
    assert answer["enabled"] is True
    assert answer["showsTuning"] is True


def test_enabled_can_never_outlive_its_footage():
    """`enabled` persists across restarts, so it legitimately arrives true on
    a target with nothing to play. Drawing it ON would promise a ghost that
    cannot appear."""
    answer = controls(session(enabled=True,
                              source={"available": False,
                                      "reason_text": "no footage"}))
    assert answer["enabled"] is False
    assert answer["showsTuning"] is False


def test_an_unavailable_source_with_no_sentence_still_says_something():
    """A reason_text the server forgot must not become a silent dead
    control."""
    answer = controls(session(source={"available": False,
                                      "reason": "no_template",
                                      "reason_text": None}))
    assert answer["canToggle"] is False
    assert answer["reasonText"]


# --- the two spellings of an entity -----------------------------------------

@pytest.mark.parametrize("target,expected", [
    ({"kind": "star", "entity_key": "8-1"}, "star:8:1"),
    ({"kind": "star", "entity_key": "star:8:1"}, "star:8:1"),
    ({"kind": "segment", "entity_key": "12"}, "segment:12"),
    ({"kind": "segment", "entity_key": "segment:12"}, "segment:12"),
    (None, None),
    ({"kind": "star"}, None),
])
def test_a_session_target_reads_as_a_practice_card_key(target, expected):
    """A template is filed under ("star", "8-1") and a card under "star:8:1".
    One translation, here, so no reader invents a second."""
    assert run_node("ghostTargetKey",
                    f"ghostTargetKey({json.dumps(target)})") == expected


def test_segments_get_the_switch_too():
    """Rule 11: stars and segments are both practiced things. A segment
    session drives the same control, through the same rules."""
    answer = controls(session(target={"kind": "segment", "entity_key": "12",
                                      "strat": "Standard"}),
                      entity_key="segment:12")
    assert (answer["known"], answer["canToggle"]) == (True, True)


# --- values -----------------------------------------------------------------

@pytest.mark.parametrize("given,expected", [
    (0.6, 0.6), (0, 0.15), (-3, 0.15), (2, 1),
])
def test_opacity_is_clamped_into_a_range_that_can_be_seen(given, expected):
    assert run_node("clampOpacity", f"clampOpacity({json.dumps(given)})") \
        == pytest.approx(expected)


@pytest.mark.parametrize("given", [None, "", "x"])
def test_an_opacity_nobody_stated_is_unknown_rather_than_fully_visible(given):
    """A value the server never sent must not come back as a number.

    It used to answer `OPACITY_MAX`, so a payload that had simply not said yet
    put "100%" under the slider -- a figure nobody chose, sitting one small
    drag from being written back as though he had chosen it. His own stored
    ghost visibility is 1.0 while the server's default has always been 0.5.
    """
    assert run_node("clampOpacity", f"clampOpacity({json.dumps(given)})") is None


def test_the_control_claims_no_visibility_before_the_server_states_one():
    """Both doors: the third state while the first read is in flight, and a
    payload that answers about this entity without saying how solid to draw."""
    assert controls(None)["opacity"] is None
    unstated = session()
    del unstated["opacity"]
    assert controls(unstated)["known"] is True
    assert controls(unstated)["opacity"] is None


@pytest.mark.parametrize("given,expected", [
    (0, 0), (4, 4), (-4, -4), (2.4, 2), (9999, 300), (-9999, -300),
    (None, 0), ("x", 0),
])
def test_a_nudge_is_whole_game_frames_inside_ten_seconds(given, expected):
    assert run_node("clampNudge", f"clampNudge({json.dumps(given)})") == expected


@pytest.mark.parametrize("frames,expected", [
    (0, "with you"), (1, "1 frame ahead"), (4, "4 frames ahead"),
    (-1, "1 frame behind"), (-4, "4 frames behind"),
])
def test_the_timing_readout_is_a_sentence_not_a_signed_number(frames, expected):
    """The acceptance rule about user-facing wording: say what it does. A
    reader should not have to know which direction positive means."""
    assert run_node("nudgeText", f"nudgeText({frames})") == expected


def test_the_opacity_readout_is_a_percentage():
    assert run_node("opacityText", "opacityText(0.62)") == "62%"
    # And it does not invent one for a value nobody has stated.
    assert run_node("opacityText", "opacityText(null)") == "—"


# --- the colour treatment ---------------------------------------------------

@pytest.mark.parametrize("given,expected", [
    (120, 120), (0, 0), (359, 359), (400, 359), (-30, 0), (12.6, 13),
    (None, 0), ("x", 0),
])
def test_a_colour_is_a_whole_turn_of_the_wheel(given, expected):
    assert run_node("clampHue", f"clampHue({json.dumps(given)})") == expected


def test_the_control_claims_no_colour_before_the_server_states_one():
    """Unknown is null, never "off" -- the same rule the opacity follows, and
    here the wrong guess is worse: the shipped treatment is ON, so drawing it
    off would show a plain ghost and tell him he had already turned it off."""
    assert controls(None)["tint"] is None
    unstated = session()
    del unstated["tint"]
    assert controls(unstated)["known"] is True
    assert controls(unstated)["tint"] is None
    assert controls(session())["tint"] == {"enabled": True, "hue": 120}


def test_the_colour_readout_names_the_shift():
    assert run_node("hueText", "hueText(120)") == "+120°"
    assert run_node("hueText", "hueText(0)") == "+0°"


@pytest.mark.parametrize("current,draft,expected", [
    # Nothing stated and nothing touched: still unknown.
    (None, None, None),
    # The switch alone must not throw away the angle he chose...
    ({"enabled": True, "hue": 200}, {"enabled": False},
     {"enabled": False, "hue": 200}),
    # ...and the angle alone must not switch the treatment off.
    ({"enabled": True, "hue": 200}, {"hue_degrees": 40},
     {"enabled": True, "hue": 40}),
    # `false` is a value, not an absence: the question is whether the key is
    # there, never whether it is truthy.
    ({"enabled": True, "hue": 200}, {"enabled": False, "hue_degrees": 0},
     {"enabled": False, "hue": 0}),
    ({"enabled": False, "hue": 200}, None, {"enabled": False, "hue": 200}),
])
def test_the_two_halves_of_the_colour_merge_rather_than_replace(
        current, draft, expected):
    """They are written by DIFFERENT gestures -- the switch on the press, the
    angle on a trailing edge -- so either can be in flight alone. Replacing
    the whole object is how one of them silently reverts the other."""
    assert run_node("shownTint", f"shownTint({json.dumps(current)}, "
                                 f"{json.dumps(draft)})") == expected


# --- the broadcast ----------------------------------------------------------

def test_the_newest_ghost_change_is_picked_out_of_the_raw_feed():
    """The store's feed is newest-first and carries every event; the control
    refreshes on ghost_changed and ignores the rest."""
    feed = [{"type": "attempt_completed", "payload": {}},
            {"type": "ghost_changed", "payload": {"enabled": True}},
            {"type": "ghost_changed", "payload": {"enabled": False}}]
    assert run_node("newestGhostSession",
                    f"newestGhostSession({json.dumps(feed)})") \
        == {"enabled": True}
    assert run_node("newestGhostSession", "newestGhostSession([])") is None
    assert run_node("newestGhostSession", "newestGhostSession(undefined)") is None


# --- what the ghost shows ---------------------------------------------------

def test_the_control_claims_no_view_before_the_server_states_one():
    """Unknown is null, the same rule the colour and the visibility follow: a
    control that guesses is one small gesture from writing its guess back."""
    assert controls(None)["view"] is None
    unstated = session()
    del unstated["view"]
    assert controls(unstated)["known"] is True
    assert controls(unstated)["view"] is None
    assert controls(session())["view"] == "whole"
    assert controls(session(view="mario"))["view"] == "mario"


def test_a_view_this_build_cannot_draw_reads_as_unknown():
    """A payload from another build, or a typo on the wire. Drawing the
    control on a value nothing renders is how a select goes blank and looks
    unset (the same trap a filtered dropdown already paid for)."""
    assert controls(session(view="rotoscope"))["view"] is None


def test_the_card_is_told_whether_this_ghost_can_be_drawn_as_mario():
    """One bit and one sentence. The boxes themselves are hundreds of rows and
    never ride `ghost_changed`; what the control needs is whether it may be
    used and, if not, the server's own words for why."""
    without = controls(session())["marioBoxes"]
    assert without["available"] is False
    assert "Mario" in without["reasonText"]

    with_boxes = controls(session(source={"mario_boxes_available": True,
                                          "mario_boxes_reason": None,
                                          "mario_boxes_reason_text": None}))
    assert with_boxes["marioBoxes"] == {"available": True, "reasonText": None}


@pytest.mark.parametrize("current,draft,expected", [
    (None, None, None),
    ("whole", None, "whole"),
    ("whole", "mario", "mario"),      # his choice wins while it is in flight
    ("mario", "whole", "whole"),
    (None, "mario", "mario"),
    ("whole", "rotoscope", "whole"),  # a draft nothing draws is not a choice
])
def test_the_view_control_shows_his_choice_before_the_server_agrees(
        current, draft, expected):
    assert run_node("shownView", f"shownView({json.dumps(current)}, "
                                 f"{json.dumps(draft)})") == expected


def test_the_two_views_are_named_for_what_they_draw():
    """His standing rule for anything a user reads: say what it does. Neither
    label names a rectangle, a model or a box."""
    labels = run_node("GHOST_VIEWS, VIEW_LABELS",
                      "GHOST_VIEWS.map((name) => VIEW_LABELS[name])")
    assert labels == ["Whole picture", "Only Mario"]


# --- choosing a ghost independently of the input-comparison template --------


def recording_options(selection, view="mario", draft=None):
    return run_node("ghostRecordingOptions",
                    f"ghostRecordingOptions({json.dumps(selection)}, "
                    f"{json.dumps(view)}, {json.dumps(draft or {})})")


def saved_candidate(template_id, *, native=True, whole=True):
    return {"template_id": template_id, "name": "Caged Island",
            "attempt_id": 1200 + template_id, "strat": "TJ Owlless",
            "native_available": native, "whole_available": whole,
            "native_reason_text": None if native else "No saved Mario poses",
            "whole_reason_text": None if whole else "No saved video"}


def test_ghost_picker_never_borrows_candidates_from_another_target():
    data = session(selection={"template_id": 4,
                              "candidates": [saved_candidate(4)]})
    assert controls(data, entity_key="star:22:0")["selection"] is None
    assert controls(data)["selection"]["template_id"] == 4
    assert recording_options(None) is None


@pytest.mark.parametrize("view,disabled", [("mario", [True, False]),
                                            ("whole", [False, True])])
def test_picker_qualifies_each_recording_for_the_selected_show_mode(view, disabled):
    answer = recording_options({"template_id": None, "candidates": [
        saved_candidate(4, native=False), saved_candidate(5, whole=False),
    ]}, view)
    assert answer["options"][0] == {
        "value": "", "label": "Use input template", "disabled": False}
    assert [option["disabled"] for option in answer["options"][1:]] == disabled
    refused = next(option for option in answer["options"] if option["disabled"])
    assert ("No saved Mario poses" if view == "mario" else "No saved video") in refused["label"]
    assert answer["views"] == {"mario": True, "whole": True}


def test_picker_distinguishes_same_named_recordings_and_keeps_chosen_unusable_one():
    answer = recording_options({"template_id": 4, "candidates": [
        saved_candidate(4, native=False), saved_candidate(5),
    ]})
    assert answer["value"] == "4"
    assert answer["options"][1]["disabled"] is True
    assert "Attempt #1204" in answer["options"][1]["label"]
    assert "Attempt #1205" in answer["options"][2]["label"]
    assert "TJ Owlless" in answer["options"][2]["label"]


def test_deleted_selection_is_named_instead_of_substituting_a_valid_ghost():
    answer = recording_options({"template_id": 9, "candidates": [saved_candidate(4)]})
    assert answer["value"] == "9"
    missing = next(option for option in answer["options"] if option["value"] == "9")
    assert missing["disabled"] and "unavailable" in missing["label"]
    assert "no longer saved" in answer["reasonText"]


def test_returning_to_input_template_is_an_explicit_optimistic_null_choice():
    selection = {"template_id": 4, "candidates": [saved_candidate(4), saved_candidate(5)]}
    assert recording_options(selection, draft={"template_id": None})["value"] == ""
    assert recording_options(selection, draft={"template_id": 5})["value"] == "5"
    assert recording_options(selection)["value"] == "4"


def test_picker_survives_a_disabled_overlay_and_explains_empty_choices():
    selection = {"template_id": None, "candidates": []}
    data = session(enabled=False, source={"available": False}, selection=selection)
    model = controls(data)
    assert not model["canToggle"]
    answer = recording_options(model["selection"])
    assert "Save a recorded attempt" in answer["reasonText"]
    assert answer["options"] == [
        {"value": "", "label": "Use input template", "disabled": False}]

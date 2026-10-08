"""The Ghost Overlay control's rules, driven through node.

`ui/ghostsession.js` is import-free so these run without a browser. What is
worth pinning here is not the arithmetic -- it is the three states the switch
has to tell apart, because two of them look identical on screen if the rules
are wrong:

  * the session is answering about ANOTHER entity (a target change the server
    has not caught up with yet) -- the control must not borrow that answer;
  * the entity has no recorded poses -- the control is off and says why;
  * the entity has recorded poses -- the control works.

The middle one is the state Griffin asked for ("disabled / not selectable if
the user hasn't set up a template for the star/segment yet"), and the first is
what stops it being drawn about the wrong star for a frame.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sm64_events.ghost.settings import limits as published_limits

REPO = Path(__file__).resolve().parent.parent
#: The ranges the server publishes: the card must read these, never its own.
LIMITS = published_limits()
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


def test_global_appearance_without_target_does_not_enable_a_practice_card():
    body = session(target=None)
    appearance = run_node("ghostAppearanceControls",
                          f"ghostAppearanceControls({json.dumps(body)})")
    assert appearance["opacity"] == body["opacity"]
    assert appearance["tint"]["hue"] == body["tint"]["hue_degrees"]
    assert controls(body)["known"] is False


def test_modal_appearance_draft_keeps_nested_gestures_until_done():
    draft = run_node("mergeGhostAppearance", """(() => {
        let value = mergeGhostAppearance({}, {tint: {hue_degrees: 180}});
        value = mergeGhostAppearance(value, {tint: {enabled: false}});
        value = mergeGhostAppearance(value, {proximity: {extra_distance: 300}});
        return mergeGhostAppearance(value, pending => ({proximity: {enabled: false}}));
    })()""")
    assert draft == {"tint": {"hue_degrees": 180, "enabled": False},
                     "proximity": {"extra_distance": 300, "enabled": False}}


def test_pose_only_ghost_is_available_without_video_metadata():
    result = controls(session(source={"available": True}))
    assert result["available"] and result["canToggle"]


def test_native_preparation_is_visible_while_switch_stays_usable():
    result = controls(session(enabled=True, source={"available": True},
                              window={"backend": "native", "reason": "Preparing Mario..."}))
    assert result["canToggle"] and result["reasonText"] == "Preparing Mario..."


def session(**over):
    body = {"enabled": False, "opacity": 0.6, "nudge_frames": 0, "limits": LIMITS,
            "tint": {"enabled": True, "hue_degrees": 120},
            "target": {"kind": "star", "entity_key": "8-1", "strat": None},
            "source": {"available": True, "reason": None, "reason_text": None,
                       "attempt_id": 90, "ghosts": []},
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


def test_enabled_can_never_outlive_its_recorded_poses():
    """`enabled` persists across restarts, so it legitimately arrives true on
    a target with nothing to play. Drawing it ON would promise a ghost that
    cannot appear."""
    answer = controls(session(enabled=True,
                              source={"available": False,
                                      "reason_text": "no recorded poses"}))
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
    (0.6, 0.6), (0, LIMITS["opacity"]["min"]), (-3, LIMITS["opacity"]["min"]),
    (2, LIMITS["opacity"]["max"]),
])
def test_opacity_is_clamped_into_the_published_range(given, expected):
    assert run_node("clampOpacity", f"clampOpacity({json.dumps(given)}, {json.dumps(LIMITS['opacity'])})") \
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


NUDGE_LIMIT = LIMITS["nudge_frames"]["limit"]


@pytest.mark.parametrize("given,expected", [
    (0, 0), (4, 4), (-4, -4), (2.4, 2), (9999, NUDGE_LIMIT), (-9999, -NUDGE_LIMIT),
    (None, 0), ("x", 0),
])
def test_a_nudge_is_whole_game_frames_inside_the_published_limit(given, expected):
    assert run_node("clampNudge", f"clampNudge({json.dumps(given)}, {NUDGE_LIMIT})") == expected


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
    (120, 120), (0, 0), (359, 359), (400, LIMITS["tint"]["max"]), (-30, LIMITS["tint"]["min"]),
    (12.6, 13), (None, 0), ("x", 0),
])
def test_a_colour_is_a_whole_turn_of_the_wheel(given, expected):
    assert run_node("clampHue", f"clampHue({json.dumps(given)}, {json.dumps(LIMITS['tint'])})") == expected


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

def test_the_on_off_settings_are_unknown_until_stated_never_off():
    """The shadow and the fade-in-front switches: a bool the server stated,
    otherwise null, so an unknown switch is drawn unknown rather than off."""
    assert controls(None)["switches"] == {}
    fade = lambda body: controls(body)["switches"]["occlusion_fade"]  # noqa: E731
    assert fade(session()) is None  # a payload that never said
    assert fade(session(occlusion_fade=True)) is True
    assert fade(session(occlusion_fade=False)) is False
    for garbage in ("yes", 1, None):
        assert fade(session(occlusion_fade=garbage)) is None
    assert controls(session(shadow=False))["switches"]["shadow"] is False
    # A server that publishes no limits lists no switches: none are drawn.
    assert controls(session(limits=None))["switches"] == {}


def test_the_path_is_unknown_until_stated_and_held_inside_the_published_ranges():
    """The path's choice and its four numbers: what the server stated, each
    number clamped into the range it publishes, and null while it has not
    said -- an unknown thumb is never parked at a default."""
    ranges = LIMITS["path_tuning"]
    assert controls(None)["pathTuning"] is None and controls(None)["pathSource"] is None
    assert controls(session())["pathTuning"] is None  # a payload that never said
    assert controls(session(path_source="average"))["pathSource"] == "average"
    assert controls(session(path_source="middle"))["pathSource"] is None
    stated = {"ahead_s": 99, "width": -5, "opacity": None, "spacing_s": 0.2}
    shown = controls(session(path_tuning=stated))["pathTuning"]
    assert shown == {"ahead_s": ranges["ahead_s"]["max"], "width": ranges["width"]["min"],
                     "opacity": None, "spacing_s": 0.2}
    # His in-flight drag lays over what the server said, one number at a time.
    merged = run_node("ghostPathTuning", f"ghostPathTuning({json.dumps(stated)}, "
                      f"{json.dumps(LIMITS)}, {{width: 60}})")
    assert merged["width"] == 60 and merged["spacing_s"] == 0.2
    assert controls(session(path_tuning=stated, limits=None))["pathTuning"] is None


@pytest.mark.parametrize("name,value,expected", [
    ("ahead_s", 3, "3.0 s"), ("ahead_s", 0.5, "0.5 s"), ("width", 40, "40 units"),
    ("opacity", 0.85, "85%"), ("spacing_s", 0.1, "0.10 s"), ("width", None, "—"),
])
def test_each_path_readout_states_its_unit_at_its_sliders_precision(name, value, expected):
    shown = run_node("pathTuningText", f"pathTuningText({json.dumps(name)}, {json.dumps(value)}, "
                     f"{json.dumps(LIMITS['path_tuning'][name])})")
    assert shown == expected


# --- choosing a ghost independently of the input-comparison template --------


def recording_options(selection, draft=None, strat=None):
    return run_node("ghostRecordingOptions",
                    f"ghostRecordingOptions({json.dumps(selection)}, "
                    f"{json.dumps(draft or {})}, {json.dumps(strat)})")


def saved_candidate(attempt_id, *, native=True, frames=None, strat="TJ Owlless"):
    return {"attempt_id": attempt_id, "frames": 534 + attempt_id if frames is None else frames,
            "strat": strat,
            "native_available": native,
            "native_reason_text": None if native else "No saved Mario poses"}


def ghosts(pb=True, *attempts):
    return {"pb": pb, "attempts": list(attempts)}


def test_imported_ghost_credit_and_choice_stay_separate_from_local_attempt_ids():
    identity = "ab" * 32
    selection = {"ghosts": {**ghosts(True, 4), "imports": [identity]}, "pb": PB,
                 "candidates": [saved_candidate(4),
                                {**saved_candidate(None, frames=300), "import_id": identity,
                                 "author": "Other player"}],
                 "playing": [{"key": identity, "hue_degrees": 257}]}
    answer = recording_options(selection)
    foreign = answer["items"][-1]
    assert foreign["key"] == identity and foreign["checked"] and foreign["hue"] == 257
    assert foreign["label"] == '10"00 · Other player · TJ Owlless'
    body = json.dumps(selection)
    toggle = lambda key: run_node("ghostRecordingOptions", f"ghostRecordingOptions({body}).toggle({json.dumps(key)})")
    assert toggle("pb") == {"pb": False, "attempts": [4], "imports": [identity]}
    assert toggle(4) == {"pb": True, "attempts": [], "imports": [identity]}
    assert toggle(identity) == {"pb": True, "attempts": [4], "imports": []}


def test_a_choice_waits_for_both_the_state_and_published_options():
    assert run_node("ghostOption", f"ghostOption('input_mode', 'values', {json.dumps(LIMITS)})") == "values"
    assert run_node("ghostOption", "ghostOption('input_mode', 'stick', {})") is None
    assert run_node("ghostOption", f"ghostOption('input_mode', null, {json.dumps(LIMITS)})") is None
    assert run_node("ghostOption", f"ghostOption('input_mode', 'combined', {json.dumps(LIMITS)})") is None


def test_imported_practice_ghost_is_hidden_until_explicitly_selected():
    identity = "ab" * 32
    candidate = {**saved_candidate(None, frames=300), "import_id": identity,
                 "author": "Other player", "practice": True}
    selection = {"ghosts": ghosts(), "pb": PB, "candidates": [candidate]}
    assert [item["key"] for item in recording_options(selection)["items"]] == ["pb"]
    selection["ghosts"]["imports"] = [identity]
    selected = recording_options(selection)["items"][-1]
    assert selected["key"] == identity and selected["checked"] and not selected["disabled"]


def ticks(answer):
    return [(item["key"], item["checked"], item["disabled"]) for item in answer["items"]]


PB = {"attempt_id": 1906, "frames": 534, "native_available": True,
      "native_reason_text": None}


def test_ghost_picker_never_borrows_candidates_from_another_target():
    data = session(selection={"ghosts": ghosts(True, 4), "candidates": [saved_candidate(4)]})
    assert controls(data, entity_key="star:22:0")["selection"] is None
    assert controls(data)["selection"]["ghosts"] == ghosts(True, 4)
    assert recording_options(None) is None


def test_each_recording_is_qualified_for_native_playback_and_the_pb_box_leads():
    answer = recording_options({"ghosts": ghosts(), "candidates": [
        saved_candidate(4, native=False), saved_candidate(5),
    ]})
    assert answer["items"][0]["key"] == "pb" and answer["items"][0]["checked"]
    assert "next PB" in answer["items"][0]["reason"]
    assert [item["disabled"] for item in answer["items"][1:]] == [True, False]
    assert answer["items"][1]["reason"] == "No saved Mario poses"


def test_a_ticked_unplayable_recording_can_still_be_unticked():
    answer = recording_options({"ghosts": ghosts(True, 4), "candidates": [
        saved_candidate(4, native=False), saved_candidate(5)]}, None, "TJ Owlless")
    assert ticks(answer) == [("pb", True, False), (4, True, False), (5, False, False)]
    assert answer["items"][1]["label"] == '17"93' and answer["items"][2]["label"] == '17"96'


def test_a_deleted_tick_stays_listed_until_unticked():
    answer = recording_options({"ghosts": ghosts(False, 9), "candidates": [saved_candidate(4)]})
    assert ticks(answer)[-1] == (9, True, False)
    assert "no longer saved" in answer["reasonText"]


def test_the_optimistic_draft_is_the_whole_set():
    selection = {"ghosts": ghosts(True, 4), "candidates": [saved_candidate(4), saved_candidate(5)]}
    assert recording_options(selection, draft={"ghosts": ghosts(False, 5)})["choice"] == ghosts(False, 5)
    assert recording_options(selection)["choice"] == ghosts(True, 4)


def test_nothing_ticked_says_so():
    selection = {"ghosts": ghosts(False), "candidates": [saved_candidate(4)]}
    data = session(enabled=False, source={"available": False}, selection=selection)
    assert "Tick a ghost" in recording_options(controls(data)["selection"])["reasonText"]
    both = {"ghosts": ghosts(True, 4), "pb": PB, "candidates": [saved_candidate(4)]}
    assert recording_options(both)["reasonText"] is None


def test_runs_are_named_by_their_time_and_other_strategies_only_when_different():
    answer = recording_options({"ghosts": ghosts(True, 1923), "pb": PB, "candidates": [
        saved_candidate(1923, frames=538), saved_candidate(1950, frames=560, strat="Sideflip")]},
        None, "TJ Owlless")
    assert [item["label"] for item in answer["items"]] == ['Your PB · 17"80', '17"93', '18"66 · Sideflip']
    minute = recording_options({"ghosts": ghosts(), "pb": {**PB, "frames": 1830}, "candidates": []})
    assert minute["items"][0]["label"] == "Your PB · 1'01\"00"


def test_his_pb_leads_with_its_time_and_each_playing_ghost_wears_its_colour():
    selection = {"ghosts": ghosts(True, 4), "pb": PB, "candidates": [saved_candidate(4), saved_candidate(5)],
                 "playing": [{"key": "pb", "attempt_id": 1906, "hue_degrees": 120},
                             {"key": 4, "attempt_id": 4, "hue_degrees": 257}]}
    answer = recording_options(selection)
    assert answer["items"][0]["label"] == 'Your PB · 17"80'
    assert [item["hue"] for item in answer["items"]] == [120, 257, None]


def test_a_run_with_missing_positions_says_where_the_ghost_will_blink():
    selection = {"ghosts": ghosts(True, 4), "pb": {**PB, "blinks": 1},
                 "candidates": [{**saved_candidate(4), "blinks": 12}]}
    mario = recording_options(selection)
    assert [item["reason"] for item in mario["items"]] == [
        "blinks on 1 frame with no recorded position", "blinks on 12 frames with no recorded position"]


OLDER = "This Mario recording uses an older pose format. Record a new attempt to use it."


def older_candidate(attempt_id, reason="older_pose_format", text=OLDER):
    return {**saved_candidate(attempt_id, native=False),
            "native_reason": reason, "native_reason_text": text}


def test_runs_that_can_never_play_as_mario_fold_into_one_quiet_line():
    """His picker, 2026-09-23: a column of disabled boxes each saying "older
    pose format" is noise in ghosts. They fold into one line; a ticked
    one stays a box so it can be unticked."""
    selection = {"ghosts": ghosts(True, 3), "pb": PB, "candidates": [
        saved_candidate(1), older_candidate(2), older_candidate(3),
        older_candidate(4, "no_native_poses", "Mario poses were not recorded."),
        saved_candidate(5, native=False)]}
    mario = recording_options(selection)
    assert [item["key"] for item in mario["items"]] == ["pb", 1, 3, 5]
    assert mario["folded"]["count"] == 2
    assert mario["folded"]["label"] == "2 older runs can't play as ghosts"
    assert OLDER in mario["folded"]["reason"] and "not recorded" in mario["folded"]["reason"]

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


def session(**over):
    body = {"enabled": False, "opacity": 0.6, "nudge_frames": 0,
            "target": {"kind": "star", "entity_key": "8-1", "strat": None},
            "source": {"available": True, "reason": None, "reason_text": None,
                       "template_id": 4, "attempt_id": 90,
                       "exactness": "frame_map"},
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

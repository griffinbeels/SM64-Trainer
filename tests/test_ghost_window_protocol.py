"""The control line the trainer writes and the overlay child reads.

The child is a process the player never sees, so a line it cannot understand
must never end it: the ghost would vanish mid-attempt with nothing on screen
to say why. Every case here is "the child stays up and records what it
ignored".
"""
import json

import pytest

from sm64_events.ghost.window.protocol import (
    ALLOWED_URL_SCHEMES,
    alpha_byte,
    encode_control_line,
    encode_event,
    parse_control_line,
)

GHOST_URL = "http://127.0.0.1:8065/ui/ghost/index.html?kind=star&entity_key=bob-1"


def test_one_line_can_carry_every_field():
    msg = parse_control_line(
        json.dumps({"url": GHOST_URL, "opacity": 0.4, "visible": True}))
    assert msg.url == GHOST_URL
    assert msg.opacity == pytest.approx(0.4)
    assert msg.visible is True
    assert msg.quit is False
    assert msg.rejected == ()


def test_quit_is_its_own_field():
    msg = parse_control_line('{"quit": true}')
    assert msg.quit is True
    assert msg.opacity is None and msg.url is None and msg.visible is None


@pytest.mark.parametrize("line", ["", "   ", "\n", "\t\n"])
def test_blank_lines_say_nothing(line):
    assert parse_control_line(line) is None


@pytest.mark.parametrize("line", [
    "not json at all",
    "{",
    '["a", "list"]',
    '"a bare string"',
    "42",
])
def test_an_unreadable_line_is_reported_not_raised(line):
    msg = parse_control_line(line)
    assert msg is not None
    assert msg.rejected, "a line we could not read must say so"
    assert msg.quit is False and msg.opacity is None and msg.visible is None


def test_unknown_fields_are_ignored_without_rejecting_the_line():
    """The trainer's newer half may learn to send a field this build predates;
    that must not cost the fields this build does understand."""
    msg = parse_control_line('{"opacity": 0.25, "nudge_frames": 3}')
    assert msg.opacity == pytest.approx(0.25)
    assert msg.rejected == ()


@pytest.mark.parametrize("sent,kept", [(-2.0, 0.0), (0.0, 0.0), (1.0, 1.0), (7.5, 1.0)])
def test_opacity_is_clamped_rather_than_refused(sent, kept):
    msg = parse_control_line(json.dumps({"opacity": sent}))
    assert msg.opacity == pytest.approx(kept)


@pytest.mark.parametrize("bad", ["0.5", None, True, float("nan"), float("inf")])
def test_opacity_that_is_not_a_finite_number_is_rejected(bad):
    msg = parse_control_line(json.dumps({"opacity": bad}))
    assert msg.opacity is None
    assert any("opacity" in reason for reason in msg.rejected)


@pytest.mark.parametrize("bad", [1, 0, "true", "yes", None])
def test_visible_must_be_a_real_boolean(bad):
    msg = parse_control_line(json.dumps({"visible": bad}))
    assert msg.visible is None
    assert any("visible" in reason for reason in msg.rejected)


@pytest.mark.parametrize("scheme", ALLOWED_URL_SCHEMES)
def test_the_schemes_a_ghost_page_can_live_at(scheme):
    msg = parse_control_line(json.dumps({"url": f"{scheme}://host/ghost.html"}))
    assert msg.url == f"{scheme}://host/ghost.html"


@pytest.mark.parametrize("bad", [
    "javascript:alert(1)",
    "data:text/html,<h1>hi</h1>",
    "",
    "ui/ghost/index.html",
    17,
])
def test_a_url_the_overlay_will_not_load(bad):
    """The child runs with the trainer's own privileges and the window has no
    chrome to show where it went: only the page schemes the trainer serves."""
    msg = parse_control_line(json.dumps({"url": bad}))
    assert msg.url is None
    assert any("url" in reason for reason in msg.rejected)


def test_a_rejected_field_does_not_cost_the_good_ones():
    msg = parse_control_line('{"opacity": "half", "visible": true}')
    assert msg.visible is True
    assert msg.opacity is None
    assert msg.rejected


@pytest.mark.parametrize("opacity,byte", [
    (0.0, 0), (1.0, 255), (0.5, 128), (0.25, 64), (-1.0, 0), (2.0, 255),
])
def test_opacity_becomes_a_whole_window_alpha_byte(opacity, byte):
    assert alpha_byte(opacity) == byte


def test_the_parent_and_the_child_spell_a_line_the_same_way():
    """One encoder, one parser: the controller never hand-rolls the JSON."""
    line = encode_control_line(url=GHOST_URL, opacity=0.6, visible=False)
    assert line.endswith("\n")
    msg = parse_control_line(line)
    assert msg.url == GHOST_URL
    assert msg.opacity == pytest.approx(0.6)
    assert msg.visible is False
    assert msg.rejected == ()


def test_an_event_line_names_itself():
    line = encode_event("applied", opacity=0.6)
    assert line.endswith("\n")
    payload = json.loads(line)
    assert payload["event"] == "applied"
    assert payload["opacity"] == pytest.approx(0.6)

"""The supported minimum width is ONE number, and the app must actually hold it.

A layout gate that stops measuring below 850px while the app happily opens at
480px does not narrow the supported range — it hides defects inside it. So the
floor the sweep uses and the floor the shipped window enforces are checked
against each other here, and the window's three enforcement points are checked
individually: `min_size` constrains dragging but not the size a window is
CREATED at, and neither constrains a geometry file saved before the rule
existed.

Needs no browser, so it runs everywhere.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from find_uilab import find_uilab  # noqa: E402

# The two window tests below need no uilab at all, so this is a per-test skip
# rather than a module-level one — a stranger with no uilab checkout still gets
# the enforcement half, which is the half that ships.
_MISSING = find_uilab()


def _project():
    if _MISSING:
        pytest.skip(_MISSING)
    from uilab_project import PROJECT
    return PROJECT


def _window_module():
    """Import desktop.window without needing pywebview installed."""
    pytest.importorskip("webview", reason="pywebview is a desktop-only extra")
    from sm64_events.desktop import window
    return window


def test_the_sweep_floor_is_the_window_floor():
    project = _project()
    window = _window_module()
    assert project.min_viewport_width == window.MIN_WINDOW_WIDTH, (
        "the layout sweep and the shipped window disagree about the narrowest "
        "supported width. Whichever is higher, the gap between them is a range "
        "the app can be used at and nothing measures.")


def test_the_window_cannot_be_dragged_narrower(monkeypatch):
    window = _window_module()
    captured = {}

    class FakeWindow:
        class events:
            resized = moved = closed = property(lambda self: None)

    def fake_create_window(*args, **kwargs):
        captured.update(kwargs)
        raise SystemExit          # stop before the event wiring

    monkeypatch.setattr(window.webview, "create_window", fake_create_window)
    with pytest.raises(SystemExit):
        window.create(lambda: None)
    assert captured["min_size"][0] == window.MIN_WINDOW_WIDTH


def test_a_first_run_does_not_open_below_the_minimum():
    """The default was 480px — narrower than the floor it is supposed to hold."""
    window = _window_module()
    assert window._DEFAULT["w"] >= window.MIN_WINDOW_WIDTH


def test_a_geometry_saved_before_the_rule_is_raised_to_it(monkeypatch, tmp_path):
    """Every window saved before 2026-07-29 was saved from a 480px default, and
    `min_size` does not touch a restored size — without the clamp those installs
    reopen at 480px forever."""
    window = _window_module()
    state = tmp_path / "window.json"
    state.write_text('{"w": 480, "h": 900, "x": 10, "y": 10}')
    monkeypatch.setattr(window, "window_state_path", lambda: state)
    assert window._load_geometry()["w"] == window.MIN_WINDOW_WIDTH


def test_a_geometry_above_the_minimum_is_left_alone(monkeypatch, tmp_path):
    """The clamp raises, and only raises — the user's own wider window stays."""
    window = _window_module()
    state = tmp_path / "window.json"
    state.write_text('{"w": 1600, "h": 900, "x": 10, "y": 10}')
    monkeypatch.setattr(window, "window_state_path", lambda: state)
    assert window._load_geometry()["w"] == 1600


def test_the_matrix_actually_measures_the_floor():
    """Declaring a minimum is worthless if no probe point sits ON it — that is
    the width most likely to break, and the one the user will run."""
    project = _project()
    from uilab.sweep import derived_matrix
    widths = {view.width for view in derived_matrix(project)}
    assert project.min_viewport_width in widths
    assert min(widths) >= project.min_viewport_width

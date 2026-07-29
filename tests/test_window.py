"""Regression tests for desktop window geometry guards.

Covers the fix for: window persisting minimized/off-screen coordinates and
reopening off-screen after a restart.

- _save_geometry skips the Windows -32000 minimized sentinel (any coord <= -30000)
- _save_geometry skips implausibly small sizes (w or h < 100)
- _load_geometry resets off-screen x/y to None but keeps w/h
- _load_geometry returns _DEFAULT on missing or corrupt file

Monkeypatches sm64_events.desktop.window.window_state_path so no file is
written to the real worktree. Does NOT call webview.start() or window.run().
"""
import json

import pytest

from sm64_events.desktop.window import (
    _DEFAULT, MIN_WINDOW_WIDTH, _load_geometry, _save_geometry)

# Widths here are all ABOVE the supported minimum on purpose. Each of these
# tests is about position handling, not about width, and they were written with
# literals like 480 and 600 -- below the floor added 2026-07-29, so the clamp
# in _load_geometry rewrote the very field they were asserting and six of them
# went red for a reason unrelated to what they check. The clamp's own behaviour
# is covered separately, in tests/test_min_supported_width.py.
WIDE = MIN_WINDOW_WIDTH + 150


class _FakeWin:
    def __init__(self, w, h, x, y):
        self.width = w
        self.height = h
        self.x = x
        self.y = y


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _patch_path(monkeypatch, tmp_path):
    """Make window_state_path() point at a temp file and return that Path."""
    p = tmp_path / "window.json"
    monkeypatch.setattr(
        "sm64_events.desktop.window.window_state_path", lambda: p
    )
    return p


# ---------------------------------------------------------------------------
# _save_geometry
# ---------------------------------------------------------------------------

def test_save_skips_minimized_sentinel_x(monkeypatch, tmp_path):
    """x at the Windows minimized sentinel (-32000 <= -30000 threshold) → no write."""
    p = _patch_path(monkeypatch, tmp_path)
    win = _FakeWin(w=480, h=900, x=-32000, y=100)
    _save_geometry(win)
    assert not p.exists(), "file must NOT be written when x is the minimized sentinel"


def test_save_skips_minimized_sentinel_y(monkeypatch, tmp_path):
    """y at the Windows minimized sentinel → no write."""
    p = _patch_path(monkeypatch, tmp_path)
    win = _FakeWin(w=480, h=900, x=100, y=-32000)
    _save_geometry(win)
    assert not p.exists(), "file must NOT be written when y is the minimized sentinel"


def test_save_skips_both_sentinel(monkeypatch, tmp_path):
    """Both x and y at -32000 (typical minimized state on Windows) → no write."""
    p = _patch_path(monkeypatch, tmp_path)
    win = _FakeWin(w=480, h=900, x=-32000, y=-32000)
    _save_geometry(win)
    assert not p.exists()


def test_save_skips_sentinel_boundary(monkeypatch, tmp_path):
    """Any coord exactly at -30000 (the threshold) is rejected."""
    p = _patch_path(monkeypatch, tmp_path)
    win = _FakeWin(w=480, h=900, x=-30000, y=100)
    _save_geometry(win)
    assert not p.exists()


def test_save_skips_implausibly_small_width(monkeypatch, tmp_path):
    """Width < 100 → no write (window not yet laid out)."""
    p = _patch_path(monkeypatch, tmp_path)
    win = _FakeWin(w=99, h=900, x=100, y=100)
    _save_geometry(win)
    assert not p.exists()


def test_save_skips_implausibly_small_height(monkeypatch, tmp_path):
    """Height < 100 → no write."""
    p = _patch_path(monkeypatch, tmp_path)
    win = _FakeWin(w=480, h=99, x=100, y=100)
    _save_geometry(win)
    assert not p.exists()


def test_save_writes_valid_geometry(monkeypatch, tmp_path):
    """Normal on-screen window → file written with correct w/h/x/y."""
    p = _patch_path(monkeypatch, tmp_path)
    win = _FakeWin(w=480, h=900, x=200, y=50)
    _save_geometry(win)
    assert p.exists(), "file must be written for valid geometry"
    state = json.loads(p.read_text())
    assert state == {"w": 480, "h": 900, "x": 200, "y": 50}


def test_save_does_not_overwrite_when_minimized(monkeypatch, tmp_path):
    """Pre-existing valid save is left untouched when a minimized save is skipped."""
    p = _patch_path(monkeypatch, tmp_path)
    # Write a valid state first.
    p.write_text(json.dumps({"w": 480, "h": 900, "x": 200, "y": 50}))
    # Now a minimized event must not overwrite it.
    win = _FakeWin(w=480, h=900, x=-32000, y=-32000)
    _save_geometry(win)
    state = json.loads(p.read_text())
    assert state["x"] == 200, "previous valid x must be preserved"
    assert state["y"] == 50, "previous valid y must be preserved"


# ---------------------------------------------------------------------------
# _load_geometry
# ---------------------------------------------------------------------------

def test_load_missing_file_returns_default(monkeypatch, tmp_path):
    """No file → returns _DEFAULT. Compared against _DEFAULT itself, not a copy
    of its values: the default is a preference someone may retune, and a test
    naming its contents turns that into a red build."""
    _patch_path(monkeypatch, tmp_path)
    assert _load_geometry() == _DEFAULT


def test_load_corrupt_file_returns_default(monkeypatch, tmp_path):
    """Corrupt JSON → returns defaults without crashing."""
    p = _patch_path(monkeypatch, tmp_path)
    p.write_text("not json {{{")
    assert _load_geometry() == _DEFAULT


def test_load_offscreen_x_resets_position_keeps_size(monkeypatch, tmp_path):
    """Saved x at sentinel → x and y reset to None; w/h kept."""
    p = _patch_path(monkeypatch, tmp_path)
    p.write_text(json.dumps({"w": WIDE, "h": 1000, "x": -32000, "y": -32000}))
    g = _load_geometry()
    assert g["x"] is None, "off-screen x must be reset to None"
    assert g["y"] is None, "off-screen y must be reset to None"
    assert g["w"] == WIDE, "width must be preserved"
    assert g["h"] == 1000, "height must be preserved"


def test_load_offscreen_y_resets_position_keeps_size(monkeypatch, tmp_path):
    """Only y at sentinel → both x and y reset to None; w/h kept."""
    p = _patch_path(monkeypatch, tmp_path)
    p.write_text(json.dumps({"w": WIDE, "h": 900, "x": 100, "y": -32000}))
    g = _load_geometry()
    assert g["x"] is None
    assert g["y"] is None
    assert g["w"] == WIDE
    assert g["h"] == 900


def test_load_valid_geometry_unchanged(monkeypatch, tmp_path):
    """Valid on-screen geometry is returned as-is."""
    p = _patch_path(monkeypatch, tmp_path)
    p.write_text(json.dumps({"w": WIDE, "h": 800, "x": 150, "y": 30}))
    g = _load_geometry()
    assert g == {"w": WIDE, "h": 800, "x": 150, "y": 30}


def test_load_partial_save_fills_defaults(monkeypatch, tmp_path):
    """File with only w/h (no x/y) gets x=None, y=None from _DEFAULT."""
    p = _patch_path(monkeypatch, tmp_path)
    p.write_text(json.dumps({"w": WIDE, "h": 1080}))
    g = _load_geometry()
    assert g["w"] == WIDE
    assert g["h"] == 1080
    assert g["x"] is None
    assert g["y"] is None

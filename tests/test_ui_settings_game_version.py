"""The Game version control in the Settings drawer (header.js).

Task 8, feature/game-version. `GET|PUT /api/mode` (docs/api.md "Game
version") answers `{mode, version, effective, unsupported}` -- `version` is
"auto"/"jp"/"us", `effective` is what grading actually resolves to, and
`unsupported` marks the emulator path with an explicit JP (no verified JP
addresses -- detection stays US while grading uses JP standards).

The field is extracted from `feature/console-support`'s own "Console"
section (Tracking mode + N64 setup) with one correction the brief carried:
the option reads "JP", never "Japan". It applies LIVE -- no restart is ever
required, so no note anywhere in the drawer may say so.

`tools/ui_fixture.py::serve_ui` always points `/api/mode`'s persistence at a
scratch tempdir (the compare-cache scratch dir's own `tracker_mode.json`),
so the PUTs this file drives never touch a real data directory."""
import json
import shutil
import sys
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH")

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from ui_fixture import serve_ui  # noqa: E402
from uilab import driver  # noqa: E402

OPEN_SETTINGS = "document.querySelector('.settings-link').click()"

# One reader, reused by every test: the section is found by its OWN heading
# text rather than by position, so a sibling task reordering another section
# cannot silently redirect this file at the wrong node.
READ_GAME_SECTION = """
  (() => {
    const sections = Array.from(
      document.querySelectorAll('.settings-drawer .settings-section'));
    const names = sections.map((s) => s.querySelector('h3').textContent);
    const gameIndex = names.indexOf('Game');
    const section = gameIndex >= 0 ? sections[gameIndex] : null;
    const select = section ? section.querySelector('select') : null;
    return {
      names,
      gameIndex,
      trainerIndex: names.indexOf('Trainer'),
      value: select ? select.value : null,
      disabled: select ? select.disabled : null,
      options: select
        ? Array.from(select.options).map((option) => option.textContent)
        : [],
      // Round 24 draws the region as a FLAG inside this sentence, so the
      // word lives in the image's `alt` (what a screen reader reads)
      // rather than in a text node. Substitute it back before reading,
      // so these assertions still describe the sentence a person hears.
      notes: section
        ? Array.from(section.querySelectorAll('.settings-note'))
            .map((p) => {
              const clone = p.cloneNode(true);
              clone.querySelectorAll('img.region-flag').forEach((img) =>
                img.replaceWith(document.createTextNode(img.alt)));
              return clone.textContent.replace(/\\s+/g, ' ').trim();
            })
        : [],
      drawerText: document.querySelector('.settings-drawer').textContent,
    };
  })()
"""


def _wait_for_game_state(page, predicate_js, timeout_ms=10000):
    """Poll `READ_GAME_SECTION` in ONE atomic script until `predicate_js`
    (a JS expression over the local `state`) is true, or the timeout lapses.

    Never reads in the same tick a click/dispatch fired in -- Preact commits
    the fetched `/api/mode` response after the microtask queue drains, so a
    same-call read would catch the pre-fetch (disabled, "auto") state, and a
    same-call read right after a change-dispatch would catch the optimistic
    DOM value the browser sets on the <select> before the PUT round-trips
    (ui-core.md's "handed a stale closure" trap, same shape one tick later)."""
    return page.evaluate(f"""
      (async () => {{
        const deadline = Date.now() + {timeout_ms};
        let state = {READ_GAME_SECTION};
        while (Date.now() < deadline) {{
          state = {READ_GAME_SECTION};
          if ({predicate_js}) return state;
          await new Promise((resolve) => setTimeout(resolve, 50));
        }}
        return state;
      }})()
    """)


def _pick_version(page, version):
    """Set the select's value and dispatch `change`, the way a real user's
    pick reaches a controlled <select> -- never call `putGameVersion`
    directly, which would prove nothing about the wiring."""
    page.evaluate(f"""
      (() => {{
        const sections = Array.from(
          document.querySelectorAll('.settings-drawer .settings-section'));
        const section = sections.find(
          (s) => s.querySelector('h3').textContent === 'Game');
        const select = section.querySelector('select');
        select.value = {version!r};
        select.dispatchEvent(new Event('change', {{bubbles: true}}));
      }})()
    """)


def _get_mode(base):
    with urllib.request.urlopen(f"{base}/api/mode", timeout=10) as response:
        return json.loads(response.read())


@pytest.fixture(scope="module")
def game_version_server():
    with serve_ui() as base:
        yield base


@pytest.fixture
def page(game_version_server):
    """A FRESH page per test (test_ui_library_target.py's own pattern) --
    the setting itself lives server-side, so a page reload always reflects
    whatever the previous test left behind, and a shared page would also
    leak a typed value or an open dialog into whatever runs after it."""
    with driver.get_driver().launch(headless=True) as opened:
        opened.goto(f"{game_version_server}/ui/index.html")
        opened.wait_for(".log-list-card")
        opened.evaluate(OPEN_SETTINGS)
        opened.wait_for(".settings-drawer .settings-section select",
                         timeout_ms=10000)
        yield opened


def test_game_section_sits_right_after_trainer_and_reads_auto_on_first_open(
        page, game_version_server):
    # The server starts with no persisted tracker_mode.json, so the default
    # ModeConfig (auto/emu) is what a first-ever open must show -- confirmed
    # against the server directly, not assumed.
    assert _get_mode(game_version_server)["version"] == "auto"

    state = _wait_for_game_state(page, "state.disabled === false")
    assert state["gameIndex"] >= 0, state["names"]
    assert state["gameIndex"] == state["trainerIndex"] + 1, state["names"]
    assert state["options"] == ["Auto-detect", "JP", "US"], state["options"]
    assert state["value"] == "auto", state
    assert state["notes"] == [
        "Graded on US standards. Auto-detect is US on the emulator.",
    ], state["notes"]
    assert not any("restart" in note.lower() for note in state["notes"]), \
        state["notes"]


def test_choosing_a_version_applies_live_and_ends_back_on_auto(
        page, game_version_server):
    # jp -> the unsupported note appears (no verified JP addresses on the
    # emulator path) and the server actually flips.
    _pick_version(page, "jp")
    state = _wait_for_game_state(
        page, "state.value === 'jp' "
              "&& state.notes.some((n) => n.includes('verified JP addresses'))")
    assert state["value"] == "jp", state
    assert any(note.startswith("Graded on JP standards")
               for note in state["notes"]), state["notes"]
    assert any("verified JP addresses" in note for note in state["notes"]), \
        state["notes"]
    server_state = _get_mode(game_version_server)
    assert server_state["version"] == "jp", server_state
    assert server_state["effective"] == "jp", server_state
    assert server_state["unsupported"] is True, server_state

    # us -> back to a supported explicit choice, unsupported note gone.
    _pick_version(page, "us")
    state = _wait_for_game_state(
        page, "state.value === 'us' "
              "&& !state.notes.some((n) => n.includes('verified JP addresses'))"
              "&& !state.notes.some((n) => n.includes('Auto-detect is US'))")
    assert state["value"] == "us", state
    # An explicit choice earns no Auto-detect explainer (review nit, 2026-08-15).
    assert state["notes"] == ["Graded on US standards."], state["notes"]
    server_state = _get_mode(game_version_server)
    assert server_state["version"] == "us", server_state
    assert server_state["unsupported"] is False, server_state

    # auto -> US again (the only ROM the emulator path supports), and the
    # server agrees -- leaves the fixture on auto for whatever runs next.
    _pick_version(page, "auto")
    # Wait for the NOTE, not just the select: the native select shows the
    # pick synchronously while the note follows the PUT's response.
    state = _wait_for_game_state(
        page, "state.value === 'auto' "
              "&& state.notes.some((n) => n.includes('Auto-detect is US'))")
    assert state["value"] == "auto", state
    assert state["notes"] == [
        "Graded on US standards. Auto-detect is US on the emulator.",
    ], state["notes"]
    server_state = _get_mode(game_version_server)
    assert server_state["version"] == "auto", server_state
    assert server_state["effective"] == "us", server_state


def test_the_word_japan_never_appears_in_the_drawer(page):
    state = _wait_for_game_state(page, "state.disabled === false")
    assert "japan" not in state["drawerText"].lower(), state["drawerText"]

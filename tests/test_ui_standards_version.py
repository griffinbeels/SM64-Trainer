# tests/test_ui_standards_version.py — the Rank standards panel's JP/US switch.
"""Task 6, spec 2026-08-15-game-version-design.

Griffin's ruling: a JP/US switch sits on the standards panel toolbar, right
after the Community defaults button — JP LEFT, US RIGHT, default = the
version he is graded on — and it is VISUAL ONLY: "whatever their version is
in the settings is the one we actually grade against." Task 0056 grows the
editor with a per-strategy "JP differs" checkbox, off by default (assume US
and JP times match); ticking it opens a second, JP-labelled TimeFields for
that strategy alone.

Driven in a real browser, per this project's own rule: a version-resolved
fetch and a controlled checkbox are both invisible to `node --check` and a
plain unit test — only a render proves the switch actually repaints the
table, and only a driven commit proves the editor actually writes through
the JP endpoint rather than just drawing a second box.

Fixture star: `ui_fixture.py`'s default seeding (star:2:4, "Fall onto the
Caged Island") ships five bundled strategies, two of which (derived from the
API rather than hard-coded — the sheet content is not this test's to assume)
already carry an annotated JP ladder that differs from their US one.
"""
import json
import sys
import tempfile
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from ui_fixture import FIXTURE_COURSE, FIXTURE_STAR, serve_ui  # noqa: E402
from uilab import driver  # noqa: E402

ENTITY = f"star:{FIXTURE_COURSE}:{FIXTURE_STAR}"

OPEN_IT = """
  (() => {
    const card = Array.from(document.querySelectorAll('.log-card'))
      .find((c) => c.querySelector('.standards-toggle'));
    if (!card) return false;
    card.querySelector('.standards-toggle').click();
    return true;
  })()
"""

SETTLE = "new Promise(r => setTimeout(r, 1200))"


def _read_switch_js():
    return """
      (() => {
        const tools = document.querySelector('.stdpanel .stdtools');
        if (!tools) return null;
        const switches = tools.querySelectorAll('.version-switch');
        const sw = switches[0] || null;
        // Round 24 draws each segment as its country's FLAG, so the
        // region reads off the image rather than off the text node --
        // `alt` is the same word, and is what a screen reader gets.
        const segs = sw ? Array.from(sw.querySelectorAll('.version-switch-seg'))
          .map((b) => ({
            text: (b.querySelector('img.region-flag') || {}).alt
                  || b.textContent.trim(),
            pressed: b.getAttribute('aria-pressed') }))
          : null;
        const note = sw ? sw.querySelector('.version-switch-note') : null;
        const commDefaults = Array.from(tools.children)
          .find((el) => (el.textContent || '').includes('Community defaults'));
        return {
          count: switches.length,
          segs,
          noteText: note ? note.textContent.trim() : null,
          nextSiblingIsSwitch: commDefaults
            ? !!(commDefaults.nextElementSibling
                 && commDefaults.nextElementSibling.classList.contains('version-switch'))
            : null,
        };
      })()
    """


def _click_segment_js(text):
    return f"""
      (() => {{
        const sw = document.querySelector('.stdpanel .stdtools .version-switch');
        const btn = Array.from(sw.querySelectorAll('.version-switch-seg'))
          .find((b) => b.getAttribute('aria-label') === {text!r});
        btn.click();
        return true;
      }})()
    """


def _cell_text_js(strat, rank):
    """Locate a table cell by the STRATEGY's own header text and the RANK's
    own tier-name text — never by position, since `slowestFirst` reorders
    columns and this table's row order is independent of ROW_ORDER's own
    layout in the payload. Works in both view and edit mode: the strategy
    name is always the header `<th>`'s FIRST child (a bare text node or the
    fastest-video `<a>`), before the edit-only delete button/JP-toggle."""
    return f"""
      (() => {{
        const table = document.querySelector('.stdpanel .stdtable');
        if (!table) return null;
        const headerRow = table.querySelector('thead tr:last-child');
        const ths = Array.from(headerRow.children);
        const colIndex = ths.findIndex((th) => th.childNodes[0]
          && th.childNodes[0].textContent.trim() === {strat!r});
        if (colIndex < 0) return null;
        const rows = Array.from(table.querySelectorAll('tbody > tr'));
        const row = rows.find((tr) => {{
          const nameEl = tr.querySelector('.std-tier-name');
          return nameEl && nameEl.textContent.trim() === {rank!r};
        }});
        if (!row) return null;
        const cell = row.children[colIndex];
        return cell ? cell.textContent.trim() : null;
      }})()
    """


def _cell_element_js(strat, rank, var_name="cell"):
    """Same lookup as `_cell_text_js`, but assigns the live element to
    `var_name` for a caller that needs to inspect or act on it further."""
    return f"""
      const __{var_name}_table = document.querySelector('.stdpanel .stdtable');
      const __{var_name}_ths = Array.from(
        __{var_name}_table.querySelector('thead tr:last-child').children);
      const __{var_name}_col = __{var_name}_ths.findIndex((th) => th.childNodes[0]
        && th.childNodes[0].textContent.trim() === {strat!r});
      const __{var_name}_rows = Array.from(
        __{var_name}_table.querySelectorAll('tbody > tr'));
      const __{var_name}_row = __{var_name}_rows.find((tr) => {{
        const nameEl = tr.querySelector('.std-tier-name');
        return nameEl && nameEl.textContent.trim() === {rank!r};
      }});
      const {var_name} = __{var_name}_row.children[__{var_name}_col];
    """


def _put_mode(base, version):
    req = urllib.request.Request(
        f"{base}/api/mode", method="PUT",
        data=json.dumps({"version": version}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as response:
        return json.load(response)


def _get_standards(base, version=None):
    qs = f"&version={version}" if version else ""
    with urllib.request.urlopen(
            f"{base}/api/ranks/standards?entity={ENTITY}{qs}") as response:
        return json.load(response)


@pytest.fixture(scope="module")
def server():
    with serve_ui(Path(tempfile.mkdtemp()) / "std_version.db") as base:
        yield base


@pytest.fixture(scope="module")
def fixture_data(server):
    """One GET, read once for the whole module — the strategies to exercise
    are DERIVED from it (a strategy whose JP ladder actually differs, and one
    that does not) rather than guessed at, per this project's own rule about
    hand-picked examples satisfying an assertion through the wrong path."""
    data = _get_standards(server)
    jp_strat = data["jp_strategies"][0]
    plain_strat = next(
        s for s in data["strategies"] if s not in data["jp_strategies"])
    return {"jp_strat": jp_strat, "plain_strat": plain_strat}


@pytest.fixture
def opened_page(server):
    """A FRESH page per test, opened straight onto the fixture star's own
    standards panel — sharing one across the module is exactly the flakiness
    this project has already paid for once (test_ui_library_target.py's own
    fixture docstring)."""
    with driver.get_driver().launch(headless=True, viewport=(1500, 1100)) as page:
        page.goto(server)
        page.wait_for(".log-card")
        opened = page.evaluate(OPEN_IT)
        assert opened, "no card carries a .standards-toggle to open"
        page.wait_for(".stdpanel .stdtable", timeout_ms=15000)
        page.evaluate(SETTLE)
        yield page


# ---- toolbar: one switch, right after Community defaults, default = grading

def test_the_toolbar_has_exactly_one_switch_after_community_defaults(opened_page):
    state = opened_page.evaluate(_read_switch_js())
    assert state is not None, "no .stdtools toolbar rendered"
    assert state["count"] == 1, f"expected exactly one .version-switch: {state}"
    assert state["nextSiblingIsSwitch"] is True, (
        f"the switch is not the element immediately after Community defaults: {state}")


def test_segments_read_jp_left_us_right(opened_page):
    state = opened_page.evaluate(_read_switch_js())
    texts = [seg["text"] for seg in state["segs"]]
    assert texts == ["JP", "US"], f"segment order/text wrong: {texts}"


def test_default_pressed_segment_is_the_grading_version_with_no_note(opened_page):
    state = opened_page.evaluate(_read_switch_js())
    pressed = [seg["text"] for seg in state["segs"] if seg["pressed"] == "true"]
    assert pressed == ["US"], (
        f"a fresh fixture grades on US, so US should be pressed: {state}")
    assert state["noteText"] is None, (
        f"shown == graded should carry no note: {state}")


# ---- clicking the switch: refetches, repaints, notes the mismatch

def test_clicking_jp_refetches_and_repaints_a_different_time(opened_page, fixture_data):
    strat = fixture_data["jp_strat"]
    us_value = opened_page.evaluate(_cell_text_js(strat, "Mario"))
    assert us_value and us_value != "—", f"no US time rendered for {strat!r} Mario"

    opened_page.evaluate(_click_segment_js("JP"))
    opened_page.evaluate(SETTLE)

    state = opened_page.evaluate(_read_switch_js())
    pressed = [seg["text"] for seg in state["segs"] if seg["pressed"] == "true"]
    assert pressed == ["JP"], f"JP click did not press JP: {state}"
    assert state["noteText"] == "Viewing JP standards · you are graded on US", (
        f"wrong/missing note while viewing JP: {state}")

    jp_value = opened_page.evaluate(_cell_text_js(strat, "Mario"))
    assert jp_value and jp_value != "—", f"no JP time rendered for {strat!r} Mario"
    assert jp_value != us_value, (
        f"{strat!r}'s Mario cell did not change between US and JP: "
        f"us={us_value!r} jp={jp_value!r}")

    opened_page.evaluate(_click_segment_js("US"))
    opened_page.evaluate(SETTLE)
    state_back = opened_page.evaluate(_read_switch_js())
    assert state_back["noteText"] is None, (
        f"the note should clear once the shown version matches grading again: {state_back}")
    us_value_again = opened_page.evaluate(_cell_text_js(strat, "Mario"))
    assert us_value_again == us_value, (
        "clicking back to US did not restore the original US time: "
        f"{us_value_again!r} != {us_value!r}")


# ---- default follows the GRADING version, not a hard-coded one

def test_default_follows_the_grading_version():
    with serve_ui(Path(tempfile.mkdtemp()) / "std_version_jp.db") as base:
        put_result = _put_mode(base, "jp")
        assert put_result["effective"] == "jp", put_result
        try:
            with driver.get_driver().launch(headless=True,
                                            viewport=(1500, 1100)) as page:
                page.goto(base)
                page.wait_for(".log-card")
                opened = page.evaluate(OPEN_IT)
                assert opened, "no card carries a .standards-toggle to open"
                page.wait_for(".stdpanel .stdtable", timeout_ms=15000)
                page.evaluate(SETTLE)
                state = page.evaluate(_read_switch_js())
                pressed = [seg["text"] for seg in state["segs"] if seg["pressed"] == "true"]
                assert pressed == ["JP"], (
                    f"grading on JP should default the switch to JP: {state}")
                assert state["noteText"] is None, (
                    f"shown == graded (both JP) should carry no note: {state}")
        finally:
            _put_mode(base, "us")


def test_an_open_panel_follows_a_live_setting_change(fixture_data):
    """Flip the Game version SETTING while the panel is already open and
    untouched: the view refetches (game_version_changed), the card passes
    the new grading version down, and the panel refetches on its own -- the
    pressed segment moves to JP and a JP-differing cell repaints. No reload,
    no click on the switch."""
    with serve_ui(Path(tempfile.mkdtemp()) / "std_version_live.db") as base:
        strat = fixture_data["jp_strat"]
        us_ladder = _get_standards(base)["strategies"][strat]
        jp_ladder = _get_standards(base, "jp")["strategies"][strat]
        rank = next(r for r in us_ladder if jp_ladder.get(r) != us_ladder[r])
        try:
            with driver.get_driver().launch(headless=True,
                                            viewport=(1500, 1100)) as page:
                page.goto(base)
                page.wait_for(".log-card")
                assert page.evaluate(OPEN_IT)
                page.wait_for(".stdpanel .stdtable", timeout_ms=15000)
                page.evaluate(SETTLE)
                before = page.evaluate(_cell_text_js(strat, rank))
                _put_mode(base, "jp")
                page.wait_for(
                    ".stdpanel .stdtools .version-switch-seg:first-child[aria-pressed='true']",
                    timeout_ms=15000)
                page.evaluate(SETTLE)
                after = page.evaluate(_cell_text_js(strat, rank))
                assert after != before, (strat, rank, before, after)
                state = page.evaluate(_read_switch_js())
                assert state["noteText"] is None, state   # shown == graded again
        finally:
            _put_mode(base, "us")


# ---- edit mode: a per-strategy JP toggle, and the editor writes JP times

def test_every_strategy_column_has_a_jp_toggle_in_edit_mode(opened_page, fixture_data, server):
    n_toggles = opened_page.evaluate("""
      Array.from(document.querySelectorAll('.stdtable thead tr:last-child th'))
        .filter((th) => th.querySelector('.std-jp-toggle')).length
    """)
    assert n_toggles == 0, (
        "the JP toggle should only render in EDIT mode, before Edit is clicked")

    clicked = opened_page.evaluate("""
      (() => {
        const btn = Array.from(document.querySelectorAll('.stdpanel .stdtools button'))
          .find((b) => (b.textContent || '').includes('Edit'));
        if (!btn) return false;
        btn.click();
        return true;
      })()
    """)
    assert clicked, "no Edit button found in the toolbar"
    opened_page.evaluate(SETTLE)

    n_strats = opened_page.evaluate("""
      Array.from(document.querySelectorAll('.stdtable thead tr:last-child th')).length - 1
    """)
    n_toggles_editing = opened_page.evaluate("""
      Array.from(document.querySelectorAll('.stdtable thead tr:last-child th'))
        .filter((th) => th.querySelector('.std-jp-toggle')).length
    """)
    assert n_toggles_editing == n_strats, (
        f"expected one .std-jp-toggle per strategy column: "
        f"{n_toggles_editing} toggles for {n_strats} strategies")

    # The editor reads explicit per-version ladders, so entering it neither
    # forces nor freezes the switch: flip to JP mid-edit and a JP-flagged
    # strategy's US field still shows its US time (whole-branch review
    # 2026-08-15 -- an inert switch was a dead control with no explanation).
    jp_strat = fixture_data["jp_strat"]
    us_time = _get_standards(server)["strategies"][jp_strat]["Mario"]
    opened_page.evaluate(_click_segment_js("JP"))
    opened_page.wait_for(".stdpanel .version-switch-note", timeout_ms=10000)
    opened_page.evaluate(SETTLE)
    state = opened_page.evaluate(_read_switch_js())
    pressed = [seg["text"] for seg in state["segs"] if seg["pressed"] == "true"]
    assert pressed == ["JP"], f"the switch must stay live while editing: {state}"
    us_field = opened_page.evaluate(f"""
      (() => {{
        const th = Array.from(document.querySelectorAll('.stdtable thead tr:last-child th'))
          .findIndex((h) => (h.textContent || '').trim().startsWith({jp_strat!r}));
        const rows = Array.from(document.querySelectorAll('.stdtable tbody tr'));
        const row = rows.find((r) => r.querySelector('.std-tier-name')
          && r.querySelector('.std-tier-name').textContent.trim() === 'Mario');
        const cell = row.children[th];
        const us = cell.querySelector('.stdcell-version .stdcell-version-label');
        const fields = Array.from(cell.querySelectorAll('.stdcell-version'))[0]
          .querySelectorAll('input');
        return {{ label: us && us.textContent.trim(),
                  digits: Array.from(fields).map((f) => f.value).join(':') }};
      }})()
    """)
    assert us_field["label"] == "US", us_field
    seconds = float(us_time)
    mins, rem = divmod(seconds, 60)
    secs = int(rem)
    cs = int(round((rem - secs) * 100))
    shown_minutes, shown_seconds, shown_cs = us_field["digits"].split(":")
    assert (int(shown_minutes or 0), int(shown_seconds), int(shown_cs)) == (
        int(mins), secs, cs), (us_field, us_time)
    opened_page.evaluate(_click_segment_js("US"))
    opened_page.evaluate(SETTLE)


def test_ticking_jp_differs_opens_a_second_field_and_commits_through_the_jp_endpoint(
        opened_page, fixture_data, server):
    strat = fixture_data["plain_strat"]
    rank = "Mario"

    # Enter edit mode.
    opened_page.evaluate("""
      (() => {
        const btn = Array.from(document.querySelectorAll('.stdpanel .stdtools button'))
          .find((b) => (b.textContent || '').includes('Edit'));
        btn.click();
      })()
    """)
    opened_page.evaluate(SETTLE)

    # Before ticking: a plain strategy renders ONE .stdcell, no stacked pair.
    before = opened_page.evaluate(f"""
      (() => {{
        {_cell_element_js(strat, rank)}
        return {{
          versions: cell.querySelectorAll('.stdcell-versions').length,
          fields: cell.querySelectorAll('.timefields').length,
        }};
      }})()
    """)
    assert before == {"versions": 0, "fields": 1}, (
        f"a strategy with no JP times should show one time field: {before}")

    # Tick "JP differs".
    opened_page.evaluate(f"""
      (() => {{
        const th = Array.from(document.querySelectorAll('.stdtable thead tr:last-child th'))
          .find((el) => el.childNodes[0] && el.childNodes[0].textContent.trim() === {strat!r});
        const cb = th.querySelector('.std-jp-toggle input');
        cb.checked = true;
        cb.dispatchEvent(new Event('change', {{ bubbles: true }}));
      }})()
    """)
    opened_page.evaluate(SETTLE)

    after_tick = opened_page.evaluate(f"""
      (() => {{
        {_cell_element_js(strat, rank)}
        const versions = Array.from(cell.querySelectorAll('.stdcell-version'))
          .map((v) => v.querySelector('.stdcell-version-label').textContent.trim());
        return {{ versionCount: versions.length, labels: versions }};
      }})()
    """)
    assert after_tick == {"versionCount": 2, "labels": ["US", "JP"]}, (
        f"ticking JP differs should stack a labelled US then JP field: {after_tick}")

    # Commit a JP time through the second field.
    opened_page.evaluate(f"""
      (() => {{
        {_cell_element_js(strat, rank)}
        const versions = Array.from(cell.querySelectorAll('.stdcell-version'));
        const jpVersion = versions.find(
          (v) => v.querySelector('.stdcell-version-label').textContent.trim() === 'JP');
        const inputs = Array.from(jpVersion.querySelectorAll('input.timefield'));
        const setVal = (el, val) => {{
          const proto = Object.getPrototypeOf(el);
          const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
          setter.call(el, val);
          el.dispatchEvent(new Event('input', {{ bubbles: true }}));
        }};
        setVal(inputs[0], '0');
        setVal(inputs[1], '15');
        setVal(inputs[2], '50');
        inputs[2].dispatchEvent(new Event('blur', {{ bubbles: true }}));
      }})()
    """)
    opened_page.evaluate("new Promise(r => setTimeout(r, 1500))")

    try:
        data = _get_standards(server, version="jp")
        assert strat in data["jp_strategies"], (
            f"{strat!r} should now carry an annotated JP ladder: {data['jp_strategies']}")
        assert data["strategies"][strat][rank] == pytest.approx(15.5), (
            f"the committed JP Mario time should be 15.50s: {data['strategies'][strat]}")
    finally:
        # Untick with window.confirm stubbed true — this IS the cleanup: it
        # clears the JP overlay through the same DELETE the brief specifies,
        # leaving the store exactly as this test found it.
        opened_page.evaluate("window.confirm = () => true;")
        opened_page.evaluate(f"""
          (() => {{
            const th = Array.from(document.querySelectorAll('.stdtable thead tr:last-child th'))
              .find((el) => el.childNodes[0] && el.childNodes[0].textContent.trim() === {strat!r});
            const cb = th.querySelector('.std-jp-toggle input');
            cb.checked = false;
            cb.dispatchEvent(new Event('change', {{ bubbles: true }}));
          }})()
        """)
        opened_page.evaluate("new Promise(r => setTimeout(r, 1200))")

    cleared = opened_page.evaluate(f"""
      (() => {{
        {_cell_element_js(strat, rank)}
        return cell.querySelectorAll('.stdcell-versions').length;
      }})()
    """)
    assert cleared == 0, "unticking should collapse the cell back to one field"

    final = _get_standards(server, version="jp")
    assert strat not in final["jp_strategies"], (
        f"unticking (confirmed) should clear the server-side JP overlay: "
        f"{final['jp_strategies']}")

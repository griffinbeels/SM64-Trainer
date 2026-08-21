"""The New-strategy modal's "US and JP timed differently?" toggle (task 0056).

Griffin, confirmed 2026-08-15: "by default, we assume the times are the same,
and the user can select if JP is different, which then enables the column
entry." Off means one universal time; on adds a SECOND time column, labelled
"US - Time (Seconds)" then "JP - Time (Seconds)" per his spec.

Render-driven, not a source scan: the thing that matters is what actually
paints when the checkbox is ticked (a real second `TimeFields` per row, a
relabelled header row) and that the JP time actually reaches the store through
the server's existing `?version=jp` door -- neither is visible to
`node --check` or a text search (`.claude/rules/ui-core.md`'s own rule: unit
tests + node --check once shipped an invisible feature).

The entity used throughout is the fixture's own active target,
`star:{FIXTURE_COURSE}:{FIXTURE_STAR}` -- the same star
`tests/test_standards_live_in_the_cards.py` and the hundred-coin render tests
already lean on, seeded with FIVE strategies so a newly created one lands
beside real neighbours rather than alone.
"""
import json
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request
import uuid
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

from ui_fixture import FIXTURE_COURSE, FIXTURE_STAR, serve_ui  # noqa: E402
from uilab import driver  # noqa: E402

ENTITY = f"star:{FIXTURE_COURSE}:{FIXTURE_STAR}"


# ---- small HTTP helpers (verifying the WRITE reached the store, not just
# that the DOM looks right -- a render check alone cannot prove the ?version=jp
# PUT actually fired) ----

def _get(base: str, path: str) -> dict:
    with urllib.request.urlopen(f"{base}{path}", timeout=10) as response:
        return json.loads(response.read())


def _request(base: str, method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = urllib.request.Request(
        f"{base}{path}", data=data, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=10) as response:
        body = response.read()
        return json.loads(body) if body else {}


# ---- driving the modal open ----

OPEN_PANEL = """
  (() => { const b = document.querySelector(".standards-toggle");
           if (b && b.getAttribute("aria-expanded") !== "true") b.click(); })()
"""

CLICK_EDIT = """
  (() => { const b = Array.from(document.querySelectorAll(".stdtools button"))
             .find((x) => x.textContent.includes("Edit"));
           if (b) b.click(); return !!b; })()
"""

CLICK_ADD_STRATEGY = """
  (() => { const b = Array.from(document.querySelectorAll(".stdtools button"))
             .find((x) => x.textContent.includes("Strategy"));
           if (b) b.click(); return !!b; })()
"""


def _open_new_strategy_modal(page) -> None:
    page.evaluate(OPEN_PANEL)
    page.wait_for(".stdtable", timeout_ms=10000)
    edited = page.evaluate(CLICK_EDIT)
    assert edited, "no Edit button in .stdtools"
    # The "+ Strategy" button only exists after `editing` commits -- a
    # separate round trip, not the same one that clicked Edit
    # (`.claude/rules/ui-core.md`: Preact commits after the tick).
    page.wait_ms(150)
    opened = page.evaluate(CLICK_ADD_STRATEGY)
    assert opened, "no + Strategy button after entering edit mode"
    page.wait_for(".modal-body .strategy-ladder", timeout_ms=10000)


# ---- reading and driving the modal's own controls ----

READ_LADDER = """
  (() => {
    const ladder = document.querySelector(".modal-body .strategy-ladder");
    const row = Array.from(document.querySelectorAll(".modal-body .strategy-include-field"))
      .find((r) => (r.textContent || "").includes("US and JP timed differently"));
    const checkbox = row ? row.querySelector('input[type="checkbox"]') : null;
    const labels = ladder
      ? Array.from(ladder.querySelectorAll(".strategy-ladder-labels > span"))
          .map((s) => s.textContent.trim())
      : [];
    const rows = ladder ? Array.from(ladder.querySelectorAll(".strategy-rank-row")) : [];
    return {
      hasToggle: !!checkbox,
      checked: checkbox ? checkbox.checked : null,
      hasJpClass: ladder ? ladder.classList.contains("has-jp") : null,
      labels,
      timefieldsPerRow: rows.map((r) => r.querySelectorAll(".timefields").length),
    };
  })()
"""


def _read_ladder(page) -> dict:
    return page.evaluate(READ_LADDER)


def _set_jp_toggle(page, checked: bool) -> None:
    script = f"""
      (() => {{
        const row = Array.from(document.querySelectorAll(".modal-body .strategy-include-field"))
          .find((r) => (r.textContent || "").includes("US and JP timed differently"));
        const box = row ? row.querySelector('input[type="checkbox"]') : null;
        if (!box) return "no JP toggle checkbox";
        box.checked = {json.dumps(checked)};
        box.dispatchEvent(new Event("change", {{bubbles: true}}));
        return "ok";
      }})()
    """
    result = page.evaluate(script)
    assert result == "ok", result
    page.wait_ms(150)


def _set_name(page, name: str) -> None:
    script = f"""
      (() => {{
        const input = document.querySelector(".modal-body .stratname");
        input.value = {json.dumps(name)};
        input.dispatchEvent(new Event("input", {{bubbles: true}}));
      }})()
    """
    page.evaluate(script)


def _fill_mario_time(page, which: str, seconds: str, centis: str) -> None:
    """`which` is "us" (the always-present first TimeFields on the Mario row)
    or "jp" (the second one, only present once the toggle is on)."""
    index = 0 if which == "us" else 1
    script = f"""
      (() => {{
        const rows = Array.from(document.querySelectorAll(".modal-body .strategy-rank-row"));
        const row = rows.find((r) =>
          (r.querySelector(".strategy-rank-name") || {{}}).textContent?.trim() === "Mario");
        if (!row) return "no Mario row";
        const fields = row.querySelectorAll(".timefields");
        const field = fields[{index}];
        if (!field) return "no timefields at index {index}";
        const boxes = field.querySelectorAll("input");
        const set = (el, v) => {{
          el.value = v;
          el.dispatchEvent(new Event("input", {{bubbles: true}}));
          el.dispatchEvent(new Event("blur", {{bubbles: true}}));
        }};
        set(boxes[0], "");
        set(boxes[1], {json.dumps(seconds)});
        set(boxes[2], {json.dumps(centis)});
        return "ok";
      }})()
    """
    result = page.evaluate(script)
    assert result == "ok", result
    page.wait_ms(150)


def _click_save(page) -> None:
    script = """
      (() => {
        const b = Array.from(document.querySelectorAll(".modal-actions button"))
          .find((x) => x.textContent.includes("Save strategy"));
        if (!b) return "no Save strategy button";
        b.click();
        return "ok";
      })()
    """
    result = page.evaluate(script)
    assert result == "ok", result


def _wait_for_modal_to_close(page) -> None:
    """Closing IS the completion signal, not a cosmetic afterthought: `save()`
    calls `onSaved` -- which is what makes the caller unmount the modal -- only
    after every awaited PUT in the try block has resolved without throwing. A
    fixed sleep would either measure the loading state or pad every run."""
    for _ in range(30):
        if page.count(".modal-body") == 0:
            return
        page.wait_ms(200)
    error = page.evaluate(
        '(() => { const e = document.querySelector(".modal-error"); '
        "return e ? e.textContent : null; })()")
    raise AssertionError(f"the modal never closed after Save; error={error!r}")


# ---- fixtures ----

@pytest.fixture(scope="module")
def jp_server():
    with tempfile.TemporaryDirectory() as scratch:
        # Scratch standards store: this file CREATES a strategy and writes a
        # JP overlay on it, and its own cleanup runs only when the test gets
        # that far. Pointing the store at scratch makes the isolation
        # structural rather than conditional (2026-08-21 -- see
        # test_fixture_reaches_the_real_page.py's scratch-store guard for what
        # one unrestored edit cost).
        with serve_ui(Path(scratch) / "stratmodal_jp.db",
                      standards_path=Path(scratch) / "standards.json") as base:
            yield base


@pytest.fixture
def modal_page(jp_server):
    """A fresh page and a freshly opened modal per test -- the toggle tests
    tick and untick it, and the round-trip test types a name and saves; none
    of that should leak into a sibling test."""
    with driver.get_driver().launch(headless=True, viewport=(1500, 1100)) as page:
        page.goto(f"{jp_server}/ui/index.html")
        page.wait_for(".log-card", timeout_ms=20000)
        _open_new_strategy_modal(page)
        yield page


# ---- tests ----

def test_off_by_default_is_one_universal_time(modal_page):
    state = _read_ladder(modal_page)
    assert state["hasToggle"], "no 'US and JP timed differently' checkbox"
    assert state["checked"] is False, state
    assert state["hasJpClass"] is False, state
    assert state["labels"] == ["Rank", "Time", "Example video"], state
    assert state["timefieldsPerRow"], "no ladder rows rendered"
    assert all(n == 1 for n in state["timefieldsPerRow"]), (
        f"a row carries more than one time editor while the toggle is off: {state}")


def test_ticking_adds_the_jp_column_and_unticking_reverts_it(modal_page):
    _set_jp_toggle(modal_page, True)
    ticked = _read_ladder(modal_page)
    assert ticked["checked"] is True, ticked
    assert ticked["hasJpClass"] is True, ticked
    assert ticked["labels"] == [
        "Rank", "US · Time (seconds)", "JP · Time (seconds)", "Example video",
    ], ticked
    assert ticked["timefieldsPerRow"], "no ladder rows rendered"
    assert all(n == 2 for n in ticked["timefieldsPerRow"]), (
        f"a row does not carry a second (JP) time editor while ticked: {ticked}")

    _set_jp_toggle(modal_page, False)
    reverted = _read_ladder(modal_page)
    assert reverted["checked"] is False, reverted
    assert reverted["hasJpClass"] is False, reverted
    assert reverted["labels"] == ["Rank", "Time", "Example video"], reverted
    assert all(n == 1 for n in reverted["timefieldsPerRow"]), (
        f"unticking left a second time editor behind: {reverted}")


def test_a_new_strategy_can_carry_a_us_and_a_jp_time_per_rank(modal_page, jp_server):
    strat_name = f"JP Probe {uuid.uuid4().hex[:8]}"
    entity_q = urllib.parse.quote(ENTITY, safe="")
    strat_q = urllib.parse.quote(strat_name, safe="")

    _set_name(modal_page, strat_name)
    _set_jp_toggle(modal_page, True)
    _fill_mario_time(modal_page, "us", "30", "00")
    _fill_mario_time(modal_page, "jp", "29", "00")
    _click_save(modal_page)
    _wait_for_modal_to_close(modal_page)

    try:
        us = _get(jp_server, f"/api/ranks/standards?entity={entity_q}")
        assert strat_name in us["strategies"], (
            f"{strat_name!r} never reached the store: {sorted(us['strategies'])}")
        assert abs(us["strategies"][strat_name]["Mario"] - 30) < 0.01, (
            us["strategies"][strat_name])
        assert strat_name in us["jp_strategies"], (
            f"{strat_name!r} has a JP time but is not listed as jp_strategies: "
            f"{us['jp_strategies']}")

        jp = _get(jp_server, f"/api/ranks/standards?entity={entity_q}&version=jp")
        assert abs(jp["strategies"][strat_name]["Mario"] - 29) < 0.01, (
            jp["strategies"][strat_name])
    finally:
        # This fixture's standards file lives under the worktree's own
        # data/rank_standards.json and persists across runs -- clean up the
        # strategy this test minted. TWO calls, not one: `?purge=true` only
        # pops the base ladder (ranks/standards.py::delete_strategy never
        # touches the entity's `jp_strategies` dict), so a JP-carrying custom
        # strategy purged without the JP-specific DELETE first leaves an
        # orphaned overlay behind forever -- caught by inspecting the real
        # store after this test's first run.
        _request(jp_server, "DELETE", f"/api/ranks/standards/{entity_q}/{strat_q}/jp")
        _request(jp_server, "DELETE",
                 f"/api/ranks/standards/{entity_q}/{strat_q}?purge=true")

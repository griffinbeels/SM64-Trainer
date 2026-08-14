"""The whole chain, against the REAL rendered app: DOM reader -> POST ->
endpoint -> file.

`tests/test_uilog.py` proves the store and `tests/test_ui_log_selectors.py`
proves the reader's selectors still name classes that exist. Neither can prove
the thing that actually matters — that loading the practice page produces
observations — and this project has shipped an invisible feature on exactly
that combination before (unit tests plus `node --check`, `.claude/rules/
ui-core.md`). An instrument that records nothing is worse than none, because
its silence reads as "nothing was on screen".
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from ui_fixture import serve_ui  # noqa: E402

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab import driver  # noqa: E402

from sm64_events.core import uilog  # noqa: E402

SETTLE = "new Promise(r => setTimeout(r, 2500))"

# Reaching the recorder is the setup's whole job: it is a modal on another tab,
# so no plain page load ever renders it. Same walk `tools/uilab_project.py`'s
# recorder stories take, kept short here because this test needs the list
# PAINTED and nothing else.
_OPEN_RECORDER = """
(async () => {
  const waitFor = async (test, ms = 4000) => {
    const until = Date.now() + ms;
    while (Date.now() < until) {
      if (test()) return true;
      await new Promise((r) => setTimeout(r, 50));
    }
    return false;
  };
  const seg = document.querySelector('button.nav-item[title="Segments"]');
  if (seg) { seg.click(); await waitFor(() => !!document.querySelector('.segments-page')); }
  // Scoped: the hidden Library page carries its own record doors (task 0096).
  const open = Array.from(document.querySelectorAll('.segments-page button'))
    .find((b) => b.textContent.includes('Record a segment'));
  if (open) { open.click(); await waitFor(() => !!document.querySelector('.record-picks')); }
  await waitFor(() => !!document.querySelector('.record-row-wrap'));
})()
"""


def test_loading_the_practice_page_records_what_it_painted(tmp_path, monkeypatch):
    # Redirected so a test run never appends to the log a live session is
    # writing — the fixture serves in-process, so patching the module
    # attribute reaches the endpoint.
    log = tmp_path / "ui_log.jsonl"
    monkeypatch.setattr(uilog, "log_path", lambda: log)

    with serve_ui(tmp_path / "uilog-render.db") as base:
        with driver.get_driver().launch(headless=True) as page:
            page.goto(base)
            page.evaluate(SETTLE)

    entries = uilog.read(log)
    assert entries, (
        "the practice page rendered and recorded NOTHING. The observer is in "
        "components/practice.js (useUiLog); a silent log is the failure this "
        "whole module exists to prevent.")

    surfaces = {entry["surface"] for entry in entries}
    assert surfaces == {"selector", "target", "log"}, (
        f"expected all three halves he asked for, got {sorted(surfaces)}")
    # The log half must carry ROWS, not merely exist. Its first version queried
    # the attempt tables inside `.objective-card`, where there are none — the
    # log is its own section two cards below — so it returned a well-formed
    # record with an empty row list on every render, posted once at mount, and
    # never again. Nothing errored and every class it names really is rendered,
    # so the selector guard stayed green; only a real page with real attempts
    # can tell the difference (2026-08-04, found by the end-to-end report
    # joining zero grabs after a session of play).
    logs = [entry for entry in entries if entry["surface"] == "log"]
    assert any(log.get("rows") for entry in logs for log in entry["logs"]), (
        "the log reader found no attempt rows on a page the fixture seeded "
        "attempts into — it is reading the wrong element and will report "
        "silence for every latency question asked of it")

    selector = [entry for entry in entries if entry["surface"] == "selector"][-1]
    names = [cell["name"] for cell in selector["cells"]]
    assert names, "the selector was recorded with no cells at all"
    # The fixture seeds a real star target, so the row it paints must contain
    # the star it seeded and one cell must be the highlighted one.
    assert any(cell["active"] for cell in selector["cells"]), (
        "no cell recorded as active, though the fixture sets a target — "
        "`active-star` is how a report says WHICH thing was selected")

    target = [entry for entry in entries if entry["surface"] == "target"][-1]
    assert target["cards"], "no objective card recorded"
    assert any(card["name"] for card in target["cards"]), (
        "an objective card was recorded with no name — the reader found the "
        "card but not its heading")
    # Every record must be joinable with the journal, which is the entire
    # point of the channel.
    assert all(entry.get("server_utc") for entry in entries)
    assert all("frame" in entry for entry in entries)


def test_a_page_that_paints_the_same_thing_twice_records_it_once(
        tmp_path, monkeypatch):
    """Deduping is on the RENDERED snapshot, and the view refetches on every
    WebSocket event — without it a quiet minute would bury the three frames
    that matter under thousands of identical rows."""
    log = tmp_path / "ui_log.jsonl"
    monkeypatch.setattr(uilog, "log_path", lambda: log)

    with serve_ui(tmp_path / "uilog-dedupe.db") as base:
        with driver.get_driver().launch(headless=True) as page:
            page.goto(base)
            page.evaluate(SETTLE)
            # Force a burst of re-renders with nothing visibly changing.
            page.evaluate(
                "(async () => { for (let i = 0; i < 20; i++) "
                "await fetch('/api/session'); })()")
            page.evaluate(SETTLE)

    per_surface = {}
    for entry in uilog.read(log):
        per_surface[entry["surface"]] = per_surface.get(entry["surface"], 0) + 1
    assert per_surface, "nothing recorded at all"
    # A few is fine (the page genuinely settles through a state or two on
    # load); one per refetch is not.
    for surface, count in per_surface.items():
        assert count <= 6, (
            f"{surface} recorded {count} times for a page that never changed "
            f"— the snapshot dedupe is not holding")


def test_opening_the_recorder_records_the_rows_it_painted(tmp_path, monkeypatch):
    """The surface every latency report about the recorder is about, and the
    one nothing observed until 2026-08-06.

    His report: *"I've clearly grabbed this star, but I don't see the event in
    the recorder"* — and the row did arrive, late. The server was measured
    innocent end to end for that exact grab (journal id 2313: 18 frames from
    touching the star to the x-cam, `published_after` 0), so the question is
    entirely about what the LIST held and when, which nothing recorded.

    THE PRACTICE PAGE'S OBSERVER CANNOT COVER IT: `app.js` renders one tab at
    a time, so while the recorder is open the practice page is unmounted and
    its effect does not run at all. Hence the recorder's own call, its own
    root, and this test — a reader wired to a root that never exists would be
    silent in exactly the way this whole module exists to prevent.
    """
    log = tmp_path / "ui_log.jsonl"
    monkeypatch.setattr(uilog, "log_path", lambda: log)

    with serve_ui(tmp_path / "uilog-recorder.db") as base:
        with driver.get_driver().launch(headless=True) as page:
            page.goto(base)
            page.evaluate(SETTLE)
            page.evaluate(_OPEN_RECORDER)
            page.evaluate(SETTLE)

    entries = [e for e in uilog.read(log) if e["surface"] == "recorder"]
    assert entries, (
        "the recorder was open and recorded NOTHING. Its observer is in "
        "components/segmenttimeline.js (useUiLog on its own root); a silent "
        "log reads as 'nothing was on screen', which is the one answer it "
        "exists to give.")
    assert any(entry.get("rows") for entry in entries), (
        "the recorder reader found no rows on a page the fixture seeded "
        "events into — it is reading the wrong element and would report "
        "silence for every latency question asked of it")
    labelled = [row for entry in entries for row in entry["rows"] if row["label"]]
    assert labelled, "rows were recorded with no labels, which name nothing"

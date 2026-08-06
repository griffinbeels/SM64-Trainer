"""Naming a landmark from the recorder, driven in a real browser.

This is a CONTRACT test rather than a defect probe, for the reason
`.claude/rules/ui-core.md` gives: no sweep can answer "does typing a name here
change what every other row says". Three properties, and each was a real
decision rather than an implementation detail:

  * the pencil exists ONLY on a row whose moment named a placed landmark — a
    level change has nothing to name, and an object the GAME made mid-play
    (Mario, a star popping out) shares one key with every other of its kind, so
    a name typed on it would land on all of them at once;
  * a name applies BACKWARDS. Nothing stores the name a row was drawn with, so
    every row that landmark ever appeared in re-labels at once. That is the
    whole reason the label is resolved server-side at fetch time;
  * the catalogue OUTLIVES the row. His ask, 2026-08-05: *"if we already know
    that a specific door is the door to HMC, we don't ever need to redefine
    that"* — so the name is readable back from the API, not just on screen.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab.driver import get_driver  # noqa: E402
from ui_fixture import FIXTURE_SEGMENT, serve_ui_live  # noqa: E402

# The two doors ui_fixture publishes: the first named by the catalogue, the
# second left alone so one page carries both states.
NAMED = "Open the HMC Door in Castle Inside"
UNNAMED = "Open a door in Castle Inside"

_OPEN_THE_RECORDER = """
(async () => {
  const waitFor = async (test, ms = 4000) => {
    const until = Date.now() + ms;
    while (Date.now() < until) {
      if (test()) return true;
      await new Promise((r) => setTimeout(r, 20));
    }
    return false;
  };
  const seg = document.querySelector('button.nav-item[title="Segments"]');
  if (seg && seg.getAttribute('aria-current') !== 'page') {
    seg.click();
    await waitFor(() => !!document.querySelector('.segments-page'));
  }
  const open = Array.from(document.querySelectorAll('button'))
    .find((b) => b.textContent.includes('Record a segment'));
  open.click();
  return await waitFor(() => !!document.querySelector('.record-picks'));
})()
"""

# Which rows carry a rename control, by their own label — the shape the
# assertions are about, rather than a count that says nothing about which.
_RENAMEABLE_ROWS = """
Array.from(document.querySelectorAll('.record-row-wrap')).map((wrap) => [
  wrap.querySelector('.record-row').textContent.trim(),
  !!wrap.querySelector('.record-rename'),
])
"""

# Types into the unnamed door's input and commits with Enter, the way he would.
_NAME_THE_SECOND_DOOR = """
(async () => {
  const waitFor = async (test, ms = 4000) => {
    const until = Date.now() + ms;
    while (Date.now() < until) {
      if (test()) return true;
      await new Promise((r) => setTimeout(r, 20));
    }
    return false;
  };
  const wrap = Array.from(document.querySelectorAll('.record-row-wrap'))
    .find((w) => w.querySelector('.record-row').textContent.includes(%r));
  if (!wrap) return 'no unnamed door row';
  wrap.querySelector('.record-rename').click();
  const ok = await waitFor(() => !!wrap.querySelector('.record-rename-input'));
  if (!ok) return 'the input never appeared';
  const input = wrap.querySelector('.record-rename-input');
  input.value = %r;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  // EVERY card's rows, not `querySelector`'s first one: the recorder draws one
  // card per place, so a single-card read is an instrument that cannot see the
  // row it is about — it reported a working rename as a failure once already.
  const relabelled = await waitFor(() => Array.from(
    document.querySelectorAll('.record-row')).some(
      (row) => row.textContent.includes(%r)));
  return relabelled ? 'ok' : 'the rows never re-labelled';
})()
"""


def test_naming_a_door_relabels_it_and_lands_in_the_catalogue():
    # `arm_segment` is what runs the fixture's own arm/close block, where the
    # two door moments are published — the SAME story the recorder contact
    # sheet renders, so the screenshot and this test look at one page.
    fixture = serve_ui_live(arm_segment=FIXTURE_SEGMENT)
    with fixture as (base, _service), get_driver().launch() as page:
        page.goto(f"{base}/ui/index.html")
        # `.log-list-card`, not `.objective-card`: the Active Target card was
        # deleted on main (spec practice-log-entity-cards) and the practice
        # page never renders that class any more, so waiting on it times out
        # on a page that is fully drawn.
        page.wait_for(".log-list-card")
        assert page.evaluate(_OPEN_THE_RECORDER) is True, (
            "the recorder never opened — nothing below measures anything")

        rows = page.evaluate(_RENAMEABLE_ROWS)
        by_label = {label: renameable for label, renameable in rows}
        assert any(NAMED in label for label in by_label), (
            f"the catalogue's own name never reached the page. Rows: {list(by_label)}")

        # ONLY a PLACED landmark offers one. A level change names no object at
        # all; the textbox row names one the GAME made mid-play, which shares
        # its key with every other of its kind, so a name typed there would
        # land on all of them at once.
        assert any("Trigger a textbox" in label for label in by_label), (
            "the fixture no longer carries a runtime-spawned landmark, so the "
            "rule that refuses to name one has nothing to be tested against")
        for label, renameable in rows:
            expected = "Open " in label
            assert renameable is expected, (
                f"{label!r} {'lacks' if expected else 'has'} a rename control")

        verdict = page.evaluate(
            _NAME_THE_SECOND_DOOR % (UNNAMED, "Moat Door", "Open the Moat Door"))
        assert verdict == "ok", verdict

        # It OUTLIVES the row: the catalogue is what the next session reads.
        names = page.evaluate(
            "(async () => (await (await fetch('/api/landmarks')).json()).names)()")
        assert names["6:3:800ebc8c:717,-1177,-869"] == "Moat Door"

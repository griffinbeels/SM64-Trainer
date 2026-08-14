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
  // Scoped: the hidden Library page carries its own record doors (task 0096).
  const open = Array.from(document.querySelectorAll('.segments-page button'))
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

        # ONLY a NAMEABLE landmark offers one, and since round 9 item 7 that
        # is nearly everything: a POLE has no spawn point but never moves, so
        # it is keyed by where it stands and can carry a name of its own ("we
        # should be able to rename ANYTHING"). What is still refused is the
        # textbox row's object, which has NEITHER coordinate and so shares one
        # key with every other of its kind in that area. A level change names
        # no object at all.
        assert any("Trigger a textbox" in label for label in by_label), (
            "the fixture no longer carries a landmark with neither "
            "coordinate, so the rule that refuses to name one has nothing to "
            "be tested against")
        # "a pole", not "a tree": this fixture deliberately skips
        # reconcile_defaults, so the shipped kind catalogue is absent and the
        # row falls back to the MOMENTS registry's own generic label.
        assert any("Grab a pole" in label for label in by_label), (
            "the fixture no longer carries a scriptless STATIC landmark, so "
            f"the rule that DOES name one is untested. Rows: {list(by_label)}")
        for label, renameable in rows:
            expected = "Open " in label or "Grab " in label
            assert renameable is expected, (
                f"{label!r} {'lacks' if expected else 'has'} a rename control")

        verdict = page.evaluate(
            _NAME_THE_SECOND_DOOR % (UNNAMED, "Moat Door", "Open the Moat Door"))
        assert verdict == "ok", verdict

        # It OUTLIVES the row: the catalogue is what the next session reads.
        names = page.evaluate(
            "(async () => (await (await fetch('/api/landmarks')).json()).names)()")
        assert names["6:3:800ebc8c:717,-1177,-869"] == "Moat Door"


_START_TYPING = """
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
  if (!wrap) return 'no row to rename';
  wrap.querySelector('.record-rename').click();
  const ok = await waitFor(() => !!wrap.querySelector('.record-rename-input'));
  if (!ok) return 'the input never appeared';
  const input = wrap.querySelector('.record-rename-input');
  input.value = 'Volcano P';
  input.dispatchEvent(new Event('input', { bubbles: true }));
  return 'typing';
})()
"""


def test_typing_a_name_survives_a_live_event(capsys):
    """Round 9 item 2: "my text suddenly gets deleted, and I find myself
    racing against some mysterious timer". The timer is the next game event:
    the live tail appends a row, the recorder re-renders, and an input whose
    value is drawn from the stored name gets diffed back to it — empty, for
    the unnamed row he is mid-typing on. While the game runs that is every
    few seconds, hence the race. The draft must belong to the control while
    he types."""
    import asyncio
    import datetime as dt
    import threading

    from sm64_events.core.events import Event

    with serve_ui_live(arm_segment=FIXTURE_SEGMENT) as (base, service), \
            get_driver().launch() as page:
        page.goto(f"{base}/ui/index.html")
        page.wait_for(".log-list-card")
        assert page.evaluate(_OPEN_THE_RECORDER) is True
        assert page.evaluate(_START_TYPING % UNNAMED) == "typing"

        async def go():
            await service.publish(Event(
                type="level_changed", frame=90210,
                timestamp_utc=dt.datetime.now(dt.timezone.utc),
                payload={"from": 6, "to": 9}))
        published = threading.Thread(target=lambda: asyncio.run(go()))
        published.start()
        published.join(timeout=10)

        page.wait_ms(500)   # long enough for the tail append + repaint
        held = page.evaluate(
            "document.querySelector('.record-rename-input')"
            " && document.querySelector('.record-rename-input').value")
        assert held == "Volcano P", (
            f"the repaint ate his draft (input now holds {held!r}) — the "
            "mysterious timer he reported racing against")


_CANCEL_VIA_X = """
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
    .find((w) => w.querySelector('.record-rename'));
  wrap.querySelector('.record-rename').click();
  if (!await waitFor(() => !!wrap.querySelector('.record-rename-input')))
    return 'the input never appeared';
  const input = wrap.querySelector('.record-rename-input');
  input.value = 'SHOULD NOT PERSIST';
  input.dispatchEvent(new Event('input', { bubbles: true }));
  const cancel = wrap.querySelector('.record-rename-cancel');
  if (!cancel) return 'no visible cancel control';
  cancel.click();
  if (!await waitFor(() => !wrap.querySelector('.record-rename-input')))
    return 'the input never closed';
  const names = await (await fetch('/api/landmarks')).json();
  if (JSON.stringify(names).includes('SHOULD NOT PERSIST'))
    return 'cancel WROTE the draft';
  return 'ok';
})()
"""


def test_the_visible_cancel_discards_the_draft_without_writing():
    """Round 12 item 5: "we should be able to cancel the name change easily
    (there's no clear way to do this)". Escape always cancelled, but a key
    nobody can see is not an affordance — the ✕ must exist, close the input,
    and write NOTHING (its mousedown is prevented so the input's own
    blur-commit cannot fire first, which is the one ordering that would turn
    a cancel into a save)."""
    fixture = serve_ui_live(arm_segment=FIXTURE_SEGMENT)
    with fixture as (base, _service), get_driver().launch() as page:
        page.goto(f"{base}/ui/index.html")
        page.wait_for(".log-list-card")
        assert page.evaluate(_OPEN_THE_RECORDER) is True
        assert page.evaluate(_CANCEL_VIA_X) == "ok"

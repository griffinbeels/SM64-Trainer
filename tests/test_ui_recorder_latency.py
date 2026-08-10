"""How long a real game event takes to reach the recorder's list.

**Latency IS the deliverable here**, in his own words, which is why this is a
measurement and not an assertion that a websocket handler exists:

    "the purpose is to be able to show exactly what's happening in the game,
     so the user can understand very clearly what actions they're actually
     tracking and when they're happening. It's imperative that this timing is
     quick and snappy, so that we can get a 1:1 feeling of exactly WHAT I DID
     resulting in what events in the editor."

and the standing ruling behind it: **a wait he can SEE is a defect** — "if I
can see the timer on my screen, we should be able to detect it as well".

WHAT IS MEASURED, exactly, so the number is not read as more than it is: from
`TrackerService.publish` returning on the server to the row being PAINTED in
the browser. That covers the journal write, the broadcast, the tail fetch
(`GET /api/segments/timeline?after_id=`) and Preact's render — every stage this
surface owns. It does NOT cover the poller's own read of emulator memory,
which happens upstream of publish and is the same for every event in the app.

THE CLOCK IS THE PAGE'S OWN. A Python polling loop over CDP would charge its
own round trips to the feature: each `evaluate` is a request/response, so the
first read after the row lands can be tens of milliseconds late and the number
would measure the instrument. Instead the page stamps `Date.now()` from a
`requestAnimationFrame` loop the moment the row appears, and Python compares it
against the `time.time()` it took immediately before publishing — same machine,
same wall clock, no skew to correct for.

THE BUDGET IS A CEILING, NOT A TARGET. It fails a REGRESSION (a fetch moved
back behind a coalescer, a subscription lost) rather than certifying a feel;
the actual measured number is printed on every run, because that is the thing
worth reading and a pass/fail hides it.
"""
import asyncio
import datetime as dt
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab.driver import get_driver  # noqa: E402
from ui_fixture import serve_ui_live  # noqa: E402

from sm64_events.core.events import Event  # noqa: E402

# A row landing later than this is a live report waiting to happen. Chosen as
# roughly one tenth of the 2.9 s gap he reported as unacceptable on 2026-08-05,
# and comfortably above what a localhost round trip plus a render costs.
LATENCY_CEILING_MS = 600

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

# Arms the page's own stopwatch BEFORE the event is published: a rAF loop that
# stamps the instant a row bearing `needle` is on screen. Reading the DOM (not
# the store) deliberately, for the same reason ui/uilog.js does — the question
# is when he could SEE it, and a model-based mark answers when we believed it.
_ARM_STOPWATCH = """
window.__rowMark = null;
(function loop() {
  const found = Array.from(document.querySelectorAll('.record-row'))
    .some((row) => row.textContent.includes(%r));
  if (found) { window.__rowMark = Date.now(); return; }
  requestAnimationFrame(loop);
})();
"""


def test_a_published_event_reaches_the_recorder_within_the_budget(capsys):
    # Jolly Roger Bay -> Castle Inside: a level edge the default `view=steps`
    # timeline lists, and one no other fixture row already carries, so the
    # stopwatch's needle can only match the row this test causes. `12` is a
    # LEVEL id, not a course id -- the two numbering systems agree only by
    # coincidence (eventlabel.py's ID-SPACE TRAP), and course 3 renders as the
    # unnamed "Level 3", which is a needle that matches nothing.
    needle = "Jolly Roger Bay"
    with serve_ui_live() as (base, service), get_driver().launch() as page:
        page.goto(f"{base}/ui/index.html")
        # `.log-list-card`, not `.objective-card`: the Active Target card was
        # deleted on main (spec practice-log-entity-cards) and the practice
        # page never renders that class any more, so waiting on it times out
        # on a page that is fully drawn.
        page.wait_for(".log-list-card")
        assert page.evaluate(_OPEN_THE_RECORDER) is True, (
            "the recorder never opened — this measures nothing until it does")
        assert needle not in (page.evaluate(
            "document.querySelector('.record-rows').textContent") or ""), (
            "the fixture already lists this row, so the stopwatch would stop "
            "on someone else's event and report a latency of zero")
        page.evaluate(_ARM_STOPWATCH % needle)

        # In a WORKER THREAD, because the browser driver's sync API owns an
        # event loop on this one and `asyncio.run` refuses to nest. Same
        # `asyncio.run(service.publish(...))`-from-outside-the-server pattern
        # `ui_fixture._arm_segment` already uses, moved off the main thread.
        async def go():
            await service.publish(Event(
                type="level_changed", frame=90210,
                timestamp_utc=dt.datetime.now(dt.timezone.utc),
                payload={"from": 12, "to": 6}))

        published = threading.Thread(target=lambda: asyncio.run(go()))
        started = time.time() * 1000
        published.start()
        published.join(timeout=10)

        deadline = time.monotonic() + 10
        mark = None
        while mark is None and time.monotonic() < deadline:
            mark = page.evaluate("window.__rowMark")
            if mark is None:
                page.wait_ms(20)
        assert mark is not None, (
            "the row never arrived. The recorder holds no websocket "
            "subscription of its own — it tails on `t.feed` growing (store.js), "
            "so a store that stopped feeding it breaks the whole live half")
        latency_ms = mark - started
        with capsys.disabled():
            print(f"\n  recorder latency: publish -> painted row "
                  f"= {latency_ms:.0f} ms (ceiling {LATENCY_CEILING_MS} ms)")
        assert latency_ms < LATENCY_CEILING_MS, (
            f"a row took {latency_ms:.0f} ms to appear; anything he can "
            "perceive is a defect, not a cost")


def test_a_row_the_server_takes_back_disappears_from_the_list():
    """His report, twice: *"I'm still getting the 'moved to another part'
    event before the 'Started' event"* — with the stray row carrying the
    LOAD's own time, so it is the load's own settling rather than a move he
    made.

    The endpoint is right about it and the CLIENT was keeping the old answer.
    `eventlabel.level_entry_rows` settles a load's area edges RETROACTIVELY:
    poll while the load is in flight and the row honestly reads "Moved to
    another part of …", poll again once the spawn lands and that row is gone
    and "Started …" is there instead. An append-only tail — which is what a
    live feed naturally is — can never take the first answer back.

    Drives exactly that sequence against the real endpoint: publish the level
    edge and its area edges, let the recorder poll, then publish the spawn."""
    with serve_ui_live() as (base, service), get_driver().launch() as page:
        page.goto(f"{base}/ui/index.html")
        page.wait_for(".log-list-card")
        assert page.evaluate(_OPEN_THE_RECORDER) is True

        def publish(*events):
            async def go():
                for event in events:
                    await service.publish(event)
            thread = threading.Thread(target=lambda: asyncio.run(go()))
            thread.start()
            thread.join(timeout=10)

        now = dt.datetime.now(dt.timezone.utc)
        # The load, WITHOUT its spawn: the last area edge is promoted, since
        # nothing better has arrived to speak for the load yet.
        publish(
            Event(type="level_changed", frame=90000, timestamp_utc=now,
                  payload={"from": 6, "to": 24}),
            Event(type="area_changed", frame=90000, timestamp_utc=now,
                  payload={"level": 24, "from": 1, "to": 1}),
            Event(type="area_changed", frame=90000, timestamp_utc=now,
                  payload={"level": 24, "from": 1, "to": 2}),
        )
        stray = "Moved to another part of Whomp's Fortress"
        appeared = page.evaluate(
            "(async () => {"
            "  const until = Date.now() + 4000;"
            "  while (Date.now() < until) {"
            "    if (document.querySelector('.record-rows')"
            f"        .textContent.includes({stray!r})) return true;"
            "    await new Promise(r => setTimeout(r, 50));"
            "  } return false; })()")
        assert appeared is True, (
            "the mid-load row never rendered, so this measures nothing — the "
            "endpoint is supposed to promote it until the spawn arrives")

        # The spawn: the endpoint now settles BOTH area edges and names the
        # arrival instead. The list must follow.
        publish(Event(type="spawned", frame=90030, timestamp_utc=now,
                      payload={"level": 24, "kind": "spawn"}))
        gone = page.evaluate(
            "(async () => {"
            "  const until = Date.now() + 4000;"
            "  while (Date.now() < until) {"
            "    const text = document.querySelector('.record-rows').textContent;"
            f"    if (!text.includes({stray!r})"
            "        && text.includes('Started Whomp\\'s Fortress')) return true;"
            "    await new Promise(r => setTimeout(r, 50));"
            "  } return false; })()")
        assert gone is True, (
            "the load's own area edge is still on screen after its arrival "
            "landed — the tail appended it once and can never take it back, "
            "which is the row he reported twice")


def test_a_burst_of_events_still_paints_the_first_row(capsys):
    """A star is never ONE broadcast — it is a burst (star_collected,
    attempt_completed, rank traffic), and his grab painted 4.7 s late, in the
    same paint as his RESET ("i usually have to reset the stage entirely for
    it to update", round 8 item 3, diagnosed against his own ui_log). The
    mechanism: the tail fetch lived inside an effect keyed on the feed's
    newest seq, so each burst message re-ran the effect and CLEANED UP the
    instance whose fetch was in flight — every response discarded as stale,
    and the retry ran inside the dead closure so its response was discarded
    too. The single-event test above cannot see any of that, which is why it
    measured 29 ms while he waited seconds.

    Two conditions make the failure reproducible rather than a race:
    `pad_journal` gives the endpoint a LIVE-sized journal walk (his poll cost
    ~70 ms at 23k rows), and the trailing broadcasts are spaced ~35 ms apart
    so each one lands while the previous tail fetch is genuinely in flight.
    Fails by construction against the effect-closure tail this branch shipped
    with; the ceiling is the same regression bar as the lone-event test."""
    needle = "Jolly Roger Bay"
    with serve_ui_live(pad_journal=40_000) as (base, service), \
            get_driver().launch() as page:
        page.goto(f"{base}/ui/index.html")
        page.wait_for(".log-list-card")
        assert page.evaluate(_OPEN_THE_RECORDER) is True, (
            "the recorder never opened — this measures nothing until it does")
        assert needle not in (page.evaluate(
            "document.querySelector('.record-rows').textContent") or ""), (
            "the fixture already lists this row, so the stopwatch would stop "
            "on someone else's event and report a latency of zero")
        page.evaluate(_ARM_STOPWATCH % needle)

        async def go():
            await service.publish(Event(
                type="level_changed", frame=90210,
                timestamp_utc=dt.datetime.now(dt.timezone.utc),
                payload={"from": 12, "to": 6}))
            # THE BURST: four trailing broadcasts, none of which produces a
            # row containing the needle (Castle <-> CCM edges). 35 ms apart —
            # far enough that the browser flushes each as its own effect run
            # (same-frame messages coalesce into one), close enough that each
            # lands inside the previous tail fetch's flight window.
            for trailing, (source, dest) in enumerate(
                    ((6, 5), (5, 6), (6, 5), (5, 6))):
                await asyncio.sleep(0.035)
                await service.publish(Event(
                    type="level_changed", frame=90211 + trailing,
                    timestamp_utc=dt.datetime.now(dt.timezone.utc),
                    payload={"from": source, "to": dest}))

        published = threading.Thread(target=lambda: asyncio.run(go()))
        started = time.time() * 1000
        published.start()
        published.join(timeout=10)

        deadline = time.monotonic() + 10
        mark = None
        while mark is None and time.monotonic() < deadline:
            mark = page.evaluate("window.__rowMark")
            if mark is None:
                page.wait_ms(20)
        assert mark is not None, (
            "the burst's first row never painted: every tail fetch the burst "
            "triggered was discarded as stale — the effect-lifecycle bug this "
            "test exists to keep dead")
        latency_ms = mark - started
        with capsys.disabled():
            print(f"\n  recorder burst latency: publish -> painted row "
                  f"= {latency_ms:.0f} ms (ceiling {LATENCY_CEILING_MS} ms)")
        assert latency_ms < LATENCY_CEILING_MS, (
            f"the burst's first row took {latency_ms:.0f} ms to appear; the "
            "whole point of the tail machine is that a burst must not delay "
            "what it announces")

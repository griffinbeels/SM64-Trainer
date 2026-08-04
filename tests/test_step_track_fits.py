"""The practice card's step track, rendered: does it show the whole route?

Two different claims, and only the first has teeth today.

1. THE TRACK MATCHES THE SERVER. `armed_detail.steps` is the route and
   `armed_detail.progress` is the cursor; a track that drew one fewer chip, or
   marked the wrong one live, would look entirely plausible and be wrong — the
   exact failure mode of the bug this feature came from (a card confidently
   showing a step the player had passed). So the rendered chips are compared
   against the running server's own answer, end to end.

2. IT STAYS ON ONE LINE. `.objective-card` is a FIXED-HEIGHT `overflow:
   hidden` box, so a second line here does not grow the card — it pushes the
   card's own contents out under the clip, the trap `.claude/rules/ui-core.md`
   names and the reason a layout probe cannot see it. Stated honestly: this
   has HEADROOM right now, measured rather than assumed — the corpus's longest
   route needs 386px and the row gives 614px at the 850px floor. It is here to
   fail when the corpus grows a longer route or the card gets narrower, and it
   has not been seen to fail. Do not read it as a tight fit.

Both measure the REAL chips: the widest-route check clones the shipped
`.step-chip` and swaps only its label and state class, so the font, gaps,
separator and marker slot are the ones that ship. A probe building its own
markup would be measuring a second implementation.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from sm64_events.core.paths import bundled_defaults_seed  # noqa: E402
from sm64_events.tracking.segments import (SegmentDef,     # noqa: E402
                                           card_step_labels)
from uilab.driver import get_driver                        # noqa: E402
from uilab_project import PROJECT, _EXPAND_ALL             # noqa: E402

WIDTHS = (1500, 1200, 900, 850)

# What the page actually DREW for the armed segment.
DRAWN = """
(() => {
  const track = document.querySelector(".step-track");
  if (!track) return null;
  return JSON.stringify({
    names: Array.from(track.querySelectorAll(".step-name"))
      .map((el) => el.textContent.trim()),
    states: Array.from(track.children)
      .map((el) => el.className.replace("step-chip ", "")),
    counter: document.querySelector(".seg-waiting-step").textContent
      .replace(/\\s+/g, " ").trim(),
  });
})()
"""

# Clone the page's own chip; one row per candidate route with the geometry
# that answers the question. `lines == 1` is the claim.
MEASURE = """
((routes) => {
  const track = document.querySelector(".step-track");
  if (!track) return null;
  const proto = track.querySelector(".step-chip");
  if (!proto) return null;
  const counter = document.querySelector(".seg-waiting-step");
  const original = track.innerHTML, originalCount = counter.textContent;
  const out = [];
  for (const route of routes) {
    track.innerHTML = "";
    route.forEach((label, index) => {
      const chip = proto.cloneNode(true);
      chip.className = "step-chip " + (index === 0 ? "done"
        : index === 1 ? "now" : "ahead");
      chip.querySelector(".step-name").textContent = label;
      track.appendChild(chip);
    });
    counter.textContent = "Step " + route.length + " of " + route.length;
    const tops = Array.from(track.children)
      .map((el) => Math.round(el.getBoundingClientRect().top));
    out.push({route, lines: new Set(tops).size,
              scroll: track.scrollWidth, client: track.clientWidth,
              clipped: Array.from(track.querySelectorAll(".step-name"))
                .some((el) => el.scrollWidth > el.clientWidth + 1)});
  }
  track.innerHTML = original;
  counter.textContent = originalCount;
  return JSON.stringify(out);
})(%s)
"""


def seeded_routes() -> list[list[str]]:
    seed = json.loads(bundled_defaults_seed().read_text(encoding="utf-8"))
    routes = []
    for row in seed["segments"]:
        definition = SegmentDef(
            id=0, name=row["name"], enabled=True,
            start_triggers=row["start_triggers"],
            end_triggers=row["end_triggers"],
            waypoints=row.get("waypoints") or [],
            guards=row.get("guards") or [],
            match_mode=row.get("match_mode", "strict"))
        routes.append(card_step_labels(definition))
    return routes


@pytest.fixture(scope="module")
def rendered():
    routes = seeded_routes()
    assert len(routes) > 50, f"only {len(routes)} routes — corpus not loaded"
    assert max(len(route) for route in routes) >= 4, (
        "the corpus's longest route is under four steps — either the seed did "
        "not load or card_step_labels stopped expanding waypoints, and the "
        "geometry check below would then be measuring a case that cannot fail")
    geometry, drawn, served = {}, None, None
    with PROJECT.open() as url, get_driver().launch() as page:
        page.goto(url)
        page.wait_for(PROJECT.ready_selector)
        # Asked through the PAGE's own origin rather than a hand-built URL:
        # `PROJECT.open()` yields the page address, not the API root, and a
        # guessed base is how a gate ends up 404ing against a healthy server.
        served = json.loads(page.evaluate("""
          fetch('/api/session?clock=igt').then(r => r.json()).then(view =>
            JSON.stringify((view.segments.find(s => s.armed_detail) || {})
              .armed_detail || null))
        """))
        for width in WIDTHS:
            page.set_viewport(width, 2800)
            page.wait_ms(400)
            page.evaluate(_EXPAND_ALL)
            page.wait_ms(450)
            if drawn is None:
                raw = page.evaluate(DRAWN)
                assert raw, "no step track on the page — the fixture arms a " \
                            "segment, so this means the row stopped rendering"
                drawn = json.loads(raw)
            raw = page.evaluate(MEASURE % json.dumps(routes))
            assert raw, f"no step track on the page at {width}px"
            geometry[width] = json.loads(raw)
    return {"geometry": geometry, "drawn": drawn, "served": served}


def test_the_track_draws_exactly_the_route_the_server_named(rendered):
    """One chip per step, in order, with the labels the server sent. A track
    one chip short reads as a complete route and is a lie about what is left
    to do."""
    served, drawn = rendered["served"], rendered["drawn"]
    assert served is not None, "the fixture armed no segment"
    assert drawn["names"] == served["steps"]
    assert len(drawn["names"]) == served["total"] + 1


def test_the_live_chip_is_the_step_the_server_says_you_are_on(rendered):
    """Every chip before the cursor reads done, the cursor reads now, the rest
    ahead — and there is exactly ONE now. Getting this off by one is invisible
    on a two-step route and wrong on every four-step one."""
    served, drawn = rendered["served"], rendered["drawn"]
    expected = ["done"] * served["progress"] + ["now"] \
        + ["ahead"] * (len(served["steps"]) - served["progress"] - 1)
    assert drawn["states"] == expected
    assert drawn["counter"].startswith(
        f"Step {served['progress'] + 1} of {served['total'] + 1}")


@pytest.mark.parametrize("width", WIDTHS)
def test_every_seeded_route_stays_on_one_line(rendered, width):
    wrapped = [row for row in rendered["geometry"][width] if row["lines"] > 1]
    assert wrapped == [], (
        f"at {width}px these routes wrap to {wrapped[0]['lines']} lines inside "
        f"a fixed-height card, which clips a sibling rather than showing "
        f"anything: {[row['route'] for row in wrapped]}. Shorten a label in "
        f"`addresses.node_short_label` — never let this row grow.")


@pytest.mark.parametrize("width", WIDTHS)
def test_no_seeded_route_has_to_ellipsise_a_place_name(rendered, width):
    """The chips CAN shrink (`min-width: 0` plus per-name ellipsis) and that is
    the safety net, not the normal case. A truncated place name is unreadable —
    "Upstai…" is not a place — so the shipped corpus must never reach it."""
    clipped = [row["route"] for row in rendered["geometry"][width]
               if row["clipped"]]
    assert clipped == [], (
        f"at {width}px these routes truncate a place name: {clipped}")


def test_the_gate_can_still_fail(rendered):
    """The two geometry assertions above pass on an empty list, which a probe
    that measured nothing would also produce. This is what proves real chips
    were laid out."""
    rows = rendered["geometry"][max(WIDTHS)]
    assert rows, "no routes measured at all"
    assert any(row["scroll"] > 100 for row in rows), (
        f"suspiciously narrow tracks — nothing was really rendered: {rows[:3]}")

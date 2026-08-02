"""The "X.XXs to rank up" line may only CHANGE while it is invisible.

    "on the actual practice page, it abruptly cuts in sometimes... It seems
     like *sometimes* it displays with a fade, whereas other times it abruptly
     cuts in? It feels like it gets stuck sometimes? It should ALWAYS fade."
    (user, 2026-08-01)

    "the fade is a cut when I don't actually rank up. So we need to make sure
     that even if we don't rank up, the text should still be fading out / in.
     All cases" (2026-08-01)

Every regression in this line has been a single FRAME, and every one of them
survived review because both end states look perfect -- which is exactly the
class of defect a screenshot cannot see and an assertion about the DOM cannot
express. Measured, the three that shipped were:

  * the second banner in a lane read opacity 0 while it waited its turn, so it
    went 1.000 -> 0.000 in one frame, sat blank for three seconds, then
    0.000 -> 0.999 in one frame when its own approach opened;
  * a climb that gains NO RANK is a plan of one bar step with no approach to
    fade out across and no arrival to fade back in, so the line simply grew
    its "· 0.15s to rank up" suffix at full opacity;
  * the render between a new payload landing and the effect starting the climb
    still holds the old climb state, and printed the DESTINATION's sentence
    for one frame at opacity 1.0 before the fade-out began.

So this drives the real component at its own frame rate and asks the two
questions the reports were actually about: does the opacity ever CUT, and does
the wording ever change while anyone can read it.

It runs against /ui/tune.html -- the climb inspector -- because that page is
the only place a climb can be TRIGGERED on demand, and it renders the shipped
`RankBanner` in the shipped stylesheet with the same lane and order practice.js
gives it. The empty fixture database is deliberate here and not the silent
degradation `.claude/rules/ui-core.md` warns about: this page reads no server
data at all.
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

from uilab import trace  # noqa: E402
from uilab.driver import get_driver  # noqa: E402

BANNERS = {
    "strategy": ".rank-slot .rank-banner:nth-child(1) .rank-banner-next",
    "star": ".rank-slot .rank-banner:nth-child(2) .rank-banner-next",
}

# A CUT, not a fast fade. Deliberately loose: how QUICKLY the line fades is a
# tuned number (the bar sweep's own floor), and no test here may pin a shipped
# default -- see .claude/rules/ui-climb.md. The defect being caught is a
# single-frame 1.0 -> 0.0, and nothing legitimate comes close to half of that.
CUT = 0.5

# The line is unreadable below this, so its wording may change here and only
# here. Not zero: the exchange pivot lands a fraction of a frame either side.
INVISIBLE = 0.05

# Total time the line spends PARTLY visible across a climb -- out plus in. A
# perception floor, deliberately far under the shipped value, so a tuning round
# has room and only a snap is caught. Taking 0.10s off a PB moves the bar ~2%
# of a division, and the fade used to inherit that: 100ms each way, reported as
# "the animation was still super snappy" (user, 2026-08-01).
READABLE_MS = 250

PLAY = ("[...document.querySelectorAll('button')]"
        ".find(b => b.textContent.includes('Play')).click()")

# Point the destination picker at the START rank -- a better time inside the
# rank you already hold, which moves the bar and nothing else. Division first
# and tier second, in two separate ticks: each select's handler closes over the
# OTHER value as it was at render time, so both in one tick sends the stale
# partner straight back.
AIM_AT_START = """
(() => {
  const boxes = document.querySelectorAll('.tune-pickers')[0]
    .querySelectorAll(':scope > div');
  const select = boxes[1].querySelectorAll('select')[WHICH];
  const start = boxes[0].querySelectorAll('select')[WHICH];
  select.value = start.value;
  select.dispatchEvent(new Event('change', {bubbles: true}));
  return true;
})()
"""

SHOWING = "document.querySelector('.tune-showing').textContent.trim()"


def line_frames(track):
    """[(t, opacity, text)] for one banner's next-step line."""
    return [(row["t"], row.get("opacity"), row.get("text"))
            for row in track.frames if row.get("opacity") is not None]


def check(name, rows):
    """Every failure names the frame it happened on -- a report that says only
    'the line cut' is a log, and a log is not a diagnosis."""
    travel = sum(abs(b[1] - a[1]) for a, b in zip(rows, rows[1:]))
    assert travel > 0.5, (
        f"{name}: the line never moved ({len(rows)} frames) -- the climb did "
        "not play, so this measured nothing")
    for (t0, v0, s0), (t1, v1, s1) in zip(rows, rows[1:]):
        assert abs(v1 - v0) <= CUT, (
            f"{name}: the line CUT at {t1:.0f}ms -- opacity {v0:.3f} -> "
            f"{v1:.3f} in one frame. It must always fade.")
        if s0 != s1:
            # The OUTGOING sentence must have reached zero before it was
            # replaced. The incoming one is then free to rise on the very next
            # frame -- that is the fade-in, not an overlap. Checking the frame
            # AFTER instead would only be measuring the frame rate.
            assert v0 <= INVISIBLE, (
                f"{name}: the wording changed at {t1:.0f}ms while it was still "
                f"READABLE (opacity {v0:.3f} -> {v1:.3f}): {s0!r} -> {s1!r}")
    assert rows[-1][1] > 0.9, (
        f"{name}: the line never came back (ended at {rows[-1][1]:.3f})")
    fading = [t for t, v, _ in rows if INVISIBLE < v < 1 - INVISIBLE]
    assert fading and (fading[-1] - fading[0]) >= READABLE_MS, (
        f"{name}: the fade lasted {fading[-1] - fading[0]:.0f}ms in total -- "
        "that reads as a snap, not a fade")


def play(page, ms):
    return trace.record(page, watch=BANNERS, properties=["--climb-reveal"],
                        trigger=lambda pg: pg.evaluate(PLAY), ms=ms)


@pytest.fixture(scope="module")
def demo():
    with serve_ui() as base:
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/tune.html")
            page.wait_for(".rank-banner", timeout_ms=20_000)
            yield page


def test_the_line_fades_on_a_rank_up(demo):
    """The inspector's own default climb: Capless V -> Waluigi IV, both
    banners, the second of which waits its turn in the lane."""
    demo.goto(demo.evaluate("location.href"))
    demo.wait_for(".rank-banner", timeout_ms=20_000)
    result = play(demo, 9000)
    for name in BANNERS:
        check(name, line_frames(result.of(name)))


def test_the_line_fades_when_no_rank_is_gained(demo):
    """The common case, and the one the demo could not reach until 2026-08-01:
    a better time inside the rank you already hold."""
    demo.goto(demo.evaluate("location.href"))
    demo.wait_for(".rank-banner", timeout_ms=20_000)
    demo.evaluate(AIM_AT_START.replace("WHICH", "1"))
    demo.wait_ms(150)
    demo.evaluate(AIM_AT_START.replace("WHICH", "0"))
    demo.wait_ms(150)
    showing = demo.evaluate(SHOWING)
    start, _, dest = str(showing).partition("→")
    assert start.strip() == dest.strip(), (
        f"the destination did not take: showing {showing!r}")
    result = play(demo, 4000)
    for name in BANNERS:
        check(name, line_frames(result.of(name)))

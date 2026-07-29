"""Re-measure `--objective-card-narrow` against a POPULATED Active Target card.

The card is a hard fixed height on purpose (the user streams it in OBS and a
card that reflowed mid-run would shift their capture), so the value has to come
from what the content actually needs. Two ways that has gone wrong, both paid
for, both guarded here:

  * measuring ONE card -- the Practice page renders an `.objective-card` per
    index item as well as the active-target one, and the first is usually the
    shorter. Take the max over all of them.
  * measuring the EMPTY card -- without a stage and a target the card renders
    "Nothing to practice here", which is 39px shorter than the real thing. The
    288px that shipped until 2026-07-29 was measured that way.
  * measuring only a STAR -- spec 2026-07-28-multi-step-segments put a third
    grid row (`.seg-waiting`) inside this same card, rendered only while a
    SEGMENT is armed (`sec.armed_detail` non-null). A star can never reach
    that state, so ARM_SEGMENT below arms a real segment definition
    alongside the star target (`ui_fixture.py`'s `_arm_segment`) and this
    script takes the max across BOTH cards, same as it already does across
    every card on the page.

Uses `serve_ui()`'s own defaults, so it measures exactly the card the layout
gate measures. Those defaults seed a star with FIVE strategies precisely so
BOTH rank banners render -- the tallest thing the card ever holds.

Run:  uv run python tools/measure_objective_card.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from find_uilab import find_uilab                     # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    raise SystemExit(_MISSING)

from ui_fixture import FIXTURE_SEGMENT, serve_ui      # noqa: E402
from uilab.driver import get_driver                   # noqa: E402
from uilab_project import PROJECT                     # noqa: E402

# BitFS Pipe Entry (segment id 6) -- one of the ten legacy tricks the schema
# migration itself inserts, so it exists even in the dev db snapshot
# regardless of whatever the defaults corpus currently holds. Not LBLJ
# (id 1): see `ui_fixture.FIXTURE_SEGMENT`'s own comment -- LBLJ has exactly
# one bundled strategy, so its ladder IS its best ladder and the card drew a
# single combined rank banner instead of two. The STAGE/TARGET constants
# that sat here are gone: main made serve_ui() seed a deterministic
# practice state of its own, so naming them again would be a second copy.
ARM_SEGMENT = FIXTURE_SEGMENT

_ALL_WIDTHS = (320, 340, 360, 400, 430, 480, 520, 560, 600, 640, 680, 700,
               720, 740, 759, 761, 790, 820,
               # The supported floor and one pixel above it. Added 2026-07-29:
               # the list jumped 820 -> 900 and so skipped the narrowest width
               # the app actually claims, which is where a fixed-height card is
               # likeliest to clip. The sweep's own extra_viewports pin the same
               # two.
               850, 851,
               900, 1100, 1101, 1400)

# Filtered by the project's OWN supported floor, not a literal repeated here.
# Main made 850px the minimum supported width (uilab_project.min_viewport_width)
# and dropped everything below it from the sweep; this tool kept measuring
# 320-790 and, once the fixture armed a TWO-banner segment, duly reported
# "15 widths clip, declare 360px" for a band the product no longer claims.
# Acting on that would have grown a fixed-height card by 13px to satisfy
# widths nothing supports -- and a tool whose failures must be ignored stops
# being read at all. If the floor moves, this moves with it.
WIDTHS = tuple(w for w in _ALL_WIDTHS if w >= PROJECT.min_viewport_width)

# `scrollHeight - clientHeight` is exactly the rule uilab's clipping probe
# applies, and using anything else here means the tool and the gate can
# disagree about whether the card fits. Both are content+padding boxes; the
# `height` property is a BORDER box (`* { box-sizing: border-box }`), so the
# value to declare is the current one plus that overflow.
TALLEST = r"""
(() => {
  let worst = null;
  for (const card of document.querySelectorAll(".objective-card")) {
    const style = getComputedStyle(card);
    if (style.display === "none") continue;
    const over = card.scrollHeight - card.clientHeight;
    if (!worst || over > worst.over)
      worst = {over, content: card.scrollHeight, box: card.clientHeight,
               declared: parseFloat(style.height)};
  }
  return worst && JSON.stringify(worst);
})()
"""

# ARM_SEGMENT's card sits inside a closed `<details class="practice-index-
# item">` (ui/components/practice.js) -- a closed <details>'s content computes
# `display: none`, which TALLEST above already skips, so an unopened one would
# silently drop straight out of the "worst" calculation instead of erroring.
EXPAND_INDEX = """
document.querySelectorAll('details.practice-index-item:not([open])')
  .forEach((d) => { d.open = true; });
"""


def main() -> int:
    shortfalls: list[tuple[int, int, int]] = []
    with serve_ui(arm_segment=ARM_SEGMENT) as base, \
            get_driver().launch() as page:
        page.goto(f"{base}/ui/index.html")
        page.wait_for(".objective-card")
        page.evaluate(EXPAND_INDEX)
        # This tool's whole claim is "max across BOTH cards", and TALLEST
        # skips display:none -- so if the armed segment ever stops producing
        # `armed_detail`, the segment card silently drops out and it prints
        # "Every card fits" having measured only the star. A constant in
        # index.html now rests on this run, so a false pass is worse than a
        # crash. Refuse loudly, the same posture _seed_target's post() takes.
        # (Delta review, finding 3.)
        # `return`, not a bare expression: evaluate() wraps its argument as a
        # function BODY, so a bare one yields None and this check would crash
        # on int(None) instead of reporting what it measured. (Same trap
        # tests/test_fixture_reaches_the_real_page.py::count documents — it
        # caught me twice in one sitting, which is why both now say why.)
        reached = int(page.evaluate(
            "return document.querySelectorAll("
            "'.objective-card .seg-waiting').length"))
        if reached < 1:
            raise SystemExit(
                "the armed-segment card never rendered (.seg-waiting absent) "
                "-- this run would have measured the STAR card alone and "
                "reported a clean result for a state it never reached. Check "
                f"ui_fixture.py's FIXTURE_SEGMENT ({ARM_SEGMENT}) still arms, "
                "and that EXPAND_INDEX still opens the practice index.")
        for width in WIDTHS:
            page.set_viewport(width, 1000)
            page.wait_ms(420)
            raw = page.evaluate(TALLEST)
            if not raw:
                print(f"{width:>5}px  no visible card")
                continue
            import json
            worst = json.loads(raw)
            over = worst["over"]
            want = worst["declared"] + over
            flag = f"  <-- clips {over}px, declare {want:g}px" if over > 1 else ""
            print(f"{width:>5}px  content {worst['content']:>4}  "
                  f"fits {worst['box']:>4}  declared {worst['declared']:g}px{flag}")
            if over > 1:
                shortfalls.append((width, want, over))

    if not shortfalls:
        print("\nEvery card fits its declared height.")
        return 0
    needed = max(want for _, want, _ in shortfalls)
    print(f"\n{len(shortfalls)} widths clip. Declare {needed:g}px in "
          f"ui/index.html and re-run.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

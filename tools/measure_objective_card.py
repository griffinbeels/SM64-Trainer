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

from ui_fixture import serve_ui                       # noqa: E402
from uilab.driver import get_driver                   # noqa: E402

# LBLJ (segment id 1) -- one of the ten legacy tricks the schema migration
# itself inserts, so it exists even in the dev db snapshot regardless of
# whatever the defaults corpus currently holds. The STAGE/TARGET constants
# that sat here are gone: main made serve_ui() seed a deterministic
# practice state of its own, so naming them again would be a second copy.
ARM_SEGMENT = 1

WIDTHS = (320, 340, 360, 400, 430, 480, 520, 560, 600, 640, 680, 700,
          720, 740, 759, 761, 790, 820, 900, 1100, 1101, 1400)

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

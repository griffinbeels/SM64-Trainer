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

# Shifting Sand Land (course 8, level 8), star 0 -- the card in the 2026-07-28
# and -29 reports, and the one whose ranks the dev db already holds.
STAGE = (8, 8)
TARGET = (8, 0)

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


def main() -> int:
    shortfalls: list[tuple[int, int, int]] = []
    with serve_ui(from_dev_db=True, stage=STAGE, target=TARGET) as base, \
            get_driver().launch() as page:
        page.goto(f"{base}/ui/index.html")
        page.wait_for(".objective-card")
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

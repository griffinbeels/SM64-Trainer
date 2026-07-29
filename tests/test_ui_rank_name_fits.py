"""Every rank name fits under the route rank card's icon, uncut.

The card puts the rank name in a fixed-width column under the icon, and that
column is capped so it cannot eat the progress bar beside it. "Short by
construction" was the comment on that cap and it was wrong: at 16px/700 the
widest name in the registry -- "Toadsworth 1" -- measures 102px against a 78px
cap, so it shipped as "Toadsw..." in the flown card AND in the resting header
(user, 2026-07-28: "looks like we maybe need to decrease sizing of text here
so that Toadsworth can fit appropriately. Need to make sure each rank name can
fit").

Why this is a RENDER test and not a source scan: the answer depends on the
font the browser actually resolves, the weight, and the letter shapes -- none
of which a regex over the stylesheet can see. It measures the real element's
own computed font with the real stylesheet applied, then asks the browser to
measure every name in the real registry against the real cap.

The nine cap names are a fixed set that changes only when someone edits
caps.js::CAP, which is exactly when this should fail. It reads them from the
registry rather than restating them -- a restated list is a second copy, and
a new tier would not be in it.
"""
import contextlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH")

# uilab replaced tools/cdp.py (deleted on main when the layout rig was
# extracted). Its Page protocol offers the verbs this test needs, and resolving
# uilab BY PATH is what stops `uv sync` pruning the gate into a silent skip --
# the reasoning is in tools/find_uilab.py, which is the ONE door for it.
from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab import driver  # noqa: E402


@contextlib.contextmanager
def chrome_session(url: str):
    """tools/cdp.py's old shape, over uilab's driver."""
    with driver.get_driver().launch(headless=True) as page:
        page.goto(url)
        yield page


def _cap_names() -> list[str]:
    """Every tier name + a division digit, straight from caps.js."""
    caps = REPO / "src" / "sm64_events" / "ui" / "components" / "caps.js"
    script = (f"import {{ CAP, capName }} from {caps.as_uri()!r};\n"
              "console.log(JSON.stringify(Object.keys(CAP).map(capName)));")
    done = subprocess.run(["node", "--input-type=module", "-"], input=script,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


MEASURE = """(() => {
  const b = document.querySelector('.marelo-bar-icon-col > b');
  if (!b) return JSON.stringify({error: 'no rank name element on the card'});
  const cs = getComputedStyle(b);
  const ctx = document.createElement('canvas').getContext('2d');
  ctx.font = `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
  const cap = parseFloat(cs.maxWidth);
  const widths = {};
  for (const name of NAMES) widths[name] = Math.ceil(ctx.measureText(name).width);
  return JSON.stringify({cap, fontSize: cs.fontSize, widths});
})()"""


def test_every_rank_name_fits_the_card_without_ellipsis():
    from ui_fixture import serve_ui                       # noqa: E402

    # A division digit is always appended, and "Unranked" is the sentinel the
    # card shows before a rank exists -- both are real strings this element
    # renders, so both are measured.
    names = [f"{name} 1" for name in _cap_names()] + ["Unranked"]
    assert "Toadsworth 1" in names, (
        "the tier that caused this test is missing from the registry read -- "
        "check caps.js::CAP rather than editing this assertion")

    with serve_ui() as base, chrome_session(f"{base}/ui/index.html") as page:
        page.set_viewport(1500, 950)
        page.evaluate("new Promise(r => setTimeout(r, 1200))")
        measured = json.loads(page.evaluate(
            f"(NAMES => {MEASURE})({json.dumps(names)})"))

    assert "error" not in measured, measured["error"]
    over = {name: width for name, width in measured["widths"].items()
            if width > measured["cap"]}
    assert not over, (
        f"at {measured['fontSize']} these rank names exceed the "
        f"{measured['cap']}px column and would ellipsise: {over}. Either drop "
        "the font size or widen the column -- but every pixel of that column "
        "comes off the progress bar beside it, so prefer the font size.")

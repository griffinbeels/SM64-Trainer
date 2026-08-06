"""ui/nextstep.js -- the "time to go" wording for RankBanner's tightened
`nextStepMode`s ("always"/"compact"), pulled out of ranks.js specifically so
it is import-free and this suite can drive it directly under node, the same
reason climbplan.js/climbcurve.js/caps.js are import-free.

Spec practice-log-entity-cards, round 3. Griffin: "I think we tighten up the
wording, and no need to tell them what they're ranking up to (e.g., no need
to say 'Waluigi 4 -> Waluigi 3' -- people will understand, no need). What we
DO need is an indicator of the timesave needed to move on to the next
level." -- so this module's whole job is: never print a destination rank,
always print (or withhold) the timesave.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / "src" / "sm64_events" / "ui" / "nextstep.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")


def run(mode, next_label, gap):
    script = (
        f"import {{ tightenedNextStepText }} from {MODULE.as_uri()!r};\n"
        f"console.log(JSON.stringify(tightenedNextStepText("
        f"{json.dumps(mode)}, {json.dumps(next_label)}, {json.dumps(gap)})));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_always_reads_the_number_with_no_destination_rank():
    assert run("always", "Waluigi 3", "0.22") == "0.22s to go"


def test_compact_drops_even_the_to_go_suffix():
    assert run("compact", "Waluigi 3", "0.22") == "0.22s"


def test_neither_mode_ever_prints_the_destination_rank():
    """The whole point of the feature: the caller's rank name never appears
    in the returned text, for any real (label, gap) pair."""
    for mode in ("always", "compact"):
        for label in ("Waluigi 3", "Mario 1", "Toadsworth II"):
            text = run(mode, label, "1.00")
            assert label not in text, f"{mode} leaked the destination rank: {text!r}"


def test_top_rank_is_the_shared_sentinel():
    assert run("always", None, None) == "top rank"
    assert run("compact", None, None) == "top rank"


def test_a_withheld_gap_prints_nothing_rather_than_a_stale_number():
    """Mid-climb the server's next_gap_cs describes the FINAL rank only, so
    ranks.js withholds it (gap=null) while a label is still present. This
    must never fall back to "top rank" -- that would assert something false
    (there genuinely is a next step, it just is not settled yet)."""
    assert run("always", "Waluigi 3", None) == ""
    assert run("compact", "Waluigi 3", None) == ""


def test_the_wording_never_contains_an_arrow_or_bold_markup():
    """A regression that accidentally routed "always"/"compact" back through
    the classic arrow-and-bold sentence would still LOOK like a timesave at
    a glance; this is what actually distinguishes the tightened wording."""
    text = run("always", "Waluigi 3", "0.22")
    assert "→" not in text
    assert "<" not in text

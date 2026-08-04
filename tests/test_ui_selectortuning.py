"""ui/selectortuning.js — the registry the selector-exchange inspector is
generated from. The same two failure modes climbtuning.js and marelotuning.js
have, and neither is caught by anything else: a row nobody reads is a slider
that does nothing, and a module reading a key the registry lacks gets
`undefined`, i.e. a NaN timer — here, a row of cards stuck invisible.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from source_scan import code_only

REPO = Path(__file__).resolve().parents[1]
UI = REPO / "src" / "sm64_events" / "ui"
TUNING_JS = UI / "selectortuning.js"
# ui/exchange.js is the only reader: every number here is a duration the state
# machine hands to a timer or a transition. cellrow.js resolves the ACTIVE
# tuning and passes it in, which is the wiring layer's job and deliberately not
# a second reader (a consumer that reads the active slot itself cannot be driven
# by the inspector).
READERS = (UI / "exchange.js",)

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                               reason="node not on PATH")


def run_node(imports: str, body: str, module: Path = TUNING_JS):
    script = f"import {{ {imports} }} from {module.as_uri()!r};\n{body}"
    done = subprocess.run(["node", "--input-type=module", "-"],
                          input=script, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def reader_source() -> str:
    """Comment-free: a prose mention of a key would otherwise read as a real
    read (tests/source_scan.py — the false positive this repo has been bitten
    by five times)."""
    return "\n".join(code_only(path) for path in READERS)


def test_every_tunable_is_actually_read():
    keys = run_node("SELECTOR_DEFAULTS",
                    "console.log(JSON.stringify(Object.keys(SELECTOR_DEFAULTS)))")
    source = reader_source()
    unread = [key for key in keys
              if not re.search(rf"\.{re.escape(key)}\b", source)]
    assert not unread, (
        f"{unread} appear in the inspector as sliders nothing reads — either "
        f"wire them up or delete the rows")


def test_no_reader_asks_for_a_key_the_registry_lacks():
    keys = set(run_node("SELECTOR_DEFAULTS",
                        "console.log(JSON.stringify(Object.keys(SELECTOR_DEFAULTS)))"))
    asked = set(re.findall(r"\btuning\.(\w+)", reader_source()))
    assert asked <= keys, (
        f"{sorted(asked - keys)} would resolve to undefined — a NaN duration, "
        f"i.e. a row that never comes back")


def test_an_unknown_stored_value_is_dropped_rather_than_carried():
    """The inspector persists a draft to localStorage, so a browser that touched
    the page before a row was renamed keeps the dead key forever. Carried
    through, it reaches SAVE and fails a legitimate write with an error naming a
    tunable he has never heard of (marelotuning.js, 2026-07-28)."""
    assert run_node(
        "withSelectorDefaults, SELECTOR_DEFAULTS",
        """const merged = withSelectorDefaults({outMs: 42, gonePhaseMs: 9});
        console.log(JSON.stringify([merged.outMs, "gonePhaseMs" in merged,
                                    Object.keys(merged).length
                                      === Object.keys(SELECTOR_DEFAULTS).length]));
        """) == [42, False, True]


def test_the_shipped_values_are_coherent_and_in_range():
    """Deliberately NOT an assertion about WHICH values are shipped: SAVE
    rewrites them from the inspector, and a test naming a default turns his next
    tuning round into a red build (the rule this project learned twice). What
    must hold is that every stored value is inside its own control's range and
    that an exchange is a real, finite event."""
    rows = run_node("SELECTOR_TUNABLES",
                    "console.log(JSON.stringify(SELECTOR_TUNABLES))")
    for key, row in rows.items():
        assert row["min"] <= row["value"] <= row["max"], f"{key} is out of range"
        assert row["step"] > 0 and row["why"].strip(), f"{key} is under-specified"
    total = rows["outMs"]["value"] + rows["gapMs"]["value"] + rows["inMs"]["value"]
    assert 0 < total <= 3000, (
        "the whole exchange has to be one perceptible event, not a page that "
        "sits blank")

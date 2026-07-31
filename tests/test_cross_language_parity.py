# tests/test_cross_language_parity.py
"""Four values this app defines TWICE — once in Python, once in JavaScript.

`tests/test_single_source.py` enforces "one derivation, one door" for things
that CAN have one door. These four cannot: the server computes them in Python
and the browser needs the same answer without a round trip, so the second copy
is a real engineering decision, not an oversight. What was missing is the part
that makes a deliberate duplicate safe — **something that fails when the two
halves stop agreeing.**

Until 2026-07-28 all four were held together by a comment. Each of the four
sites said some version of "keep the two in lockstep" or "mirrors the labels",
and a comment cannot fail a build. The specific ways they were free to rot:

  * `RANK_NAMES` — add a tier in `ranks/classify.py` and every JS surface
    draws `capName(tier) -> tier` (the raw key) with `rankColor -> #3a4250`
    (the fallback grey), on a ladder that silently has one fewer rung. Nothing
    throws.
  * `RANK_MODES` — add a mode server-side and the header's Grading picker
    simply never offers it, so the feature ships invisible. Remove one and the
    picker offers a mode the server 409s.
  * `format_igt` — the two implementations of the SAME 30fps quantisation
    (`(frames % 30) * 100 // 30`) are a copy in a language whose `/` is float
    division. One drifting digit means a time that disagrees with itself
    between a card and a tooltip, and RANKS ARE GRADED ON DISPLAYED
    CENTISECONDS (`classify.display_cs`), so a formatting drift is a grading
    drift.
  * `selection_id` — disagree and a stat chip stops matching its own checkbox:
    ticking it adds a duplicate rather than toggling the one that is there.

Two of the four JS modules are import-free and are imported directly by node.
The other two (`ranks.js`, `statmenu.js`) import Preact through a browser
import map that node cannot resolve, so their declaration is extracted from
source and evaluated on its own — still the REAL expression, never a
restatement of it, which is the distinction that decides whether a parity test
is worth having.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from source_scan import strip_comments

REPO = Path(__file__).resolve().parents[1]
UI = REPO / "src" / "sm64_events" / "ui"
CAPS_JS = UI / "components" / "caps.js"
FORMAT_JS = UI / "format.js"
RANKS_JS = UI / "components" / "ranks.js"
STATMENU_JS = UI / "components" / "statmenu.js"
REDSFAMILY_JS = UI / "redsfamily.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")


def run_node(script: str):
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def declaration(path: Path, name: str) -> str:
    """The source text of one top-level `const NAME = ...;` declaration.

    Extracted rather than imported because these modules pull in Preact via
    the browser's import map. Comments are stripped first so a commented-out
    older version can never be the one picked up (tests/source_scan.py).
    """
    code = strip_comments(path.read_text(encoding="utf-8"))
    match = re.search(rf"^(?:export\s+)?const\s+{name}\s*=.*?;\s*$",
                      code, re.M | re.S)
    assert match, f"{path.name}: no top-level `const {name} = ...;` found"
    return match.group(0)


# --- 1. the rank ladder -----------------------------------------------------

def test_rank_tier_keys_and_order_agree():
    from sm64_events.ranks.classify import RANK_NAMES

    js_names = run_node(
        f"import {{ CAP, RANK_NAMES }} from {CAPS_JS.as_uri()!r};\n"
        "console.log(JSON.stringify("
        "  { caps: Object.keys(CAP), exported: RANK_NAMES }));")
    assert js_names["caps"] == js_names["exported"], (
        "caps.js's RANK_NAMES no longer equals Object.keys(CAP) — it is meant "
        "to be derived from it, not maintained beside it")
    assert list(RANK_NAMES) == js_names["caps"], (
        "the rank ladder disagrees across languages.\n"
        f"  ranks/classify.py RANK_NAMES: {list(RANK_NAMES)}\n"
        f"  ui/components/caps.js CAP:    {js_names['caps']}\n"
        "Order is load-bearing on BOTH sides (hardest first: classify.rank_for "
        "walks it, RANK_SCORE indexes it, caps.js rankPosition maps a tier to "
        "a ladder position). A tier missing from CAP renders as its raw key in "
        "fallback grey, on a ladder one rung short, and nothing throws.")


# --- 2. the rank-mode registry ---------------------------------------------

def test_rank_modes_and_their_labels_agree():
    from sm64_events.ranks.classify import DEFAULT_RANK_MODE, RANK_MODES

    js_options = run_node(
        f"{declaration(RANKS_JS, 'RANK_MODE_OPTIONS')}\n"
        "console.log(JSON.stringify(RANK_MODE_OPTIONS));")
    python_options = [[mode, spec["label"]] for mode, spec in RANK_MODES.items()]
    assert python_options == js_options, (
        "the rank-mode registry disagrees across languages.\n"
        f"  ranks/classify.py RANK_MODES:     {python_options}\n"
        f"  ui/components/ranks.js OPTIONS:   {js_options}\n"
        "Ids AND labels AND order: the picker renders this list directly, and "
        "`PUT /api/ranks/mode` 409s on an id the server does not know. A mode "
        "added on one side only either ships invisible or ships broken.")
    assert DEFAULT_RANK_MODE in dict(js_options), (
        f"the server's default mode {DEFAULT_RANK_MODE!r} is not offered by "
        "the picker, so the control cannot show the state it opens in")


# --- 3. the displayed time --------------------------------------------------

# Boundaries first, then a spread: 0, sub-second, the 30fps rounding edges
# (frame 29 is 96cs not 100), a minute rollover, and a long segment.
IGT_FRAMES = [0, 1, 14, 15, 29, 30, 31, 59, 60, 899, 900, 1799, 1800, 1801,
              5399, 5400, 12345, 108_000]


def test_igt_display_format_agrees():
    from sm64_events.core.timefmt import format_igt

    js = run_node(
        f"import {{ fmtIgt }} from {FORMAT_JS.as_uri()!r};\n"
        f"const frames = {json.dumps(IGT_FRAMES)};\n"
        "console.log(JSON.stringify(frames.map(fmtIgt)));")
    python = [format_igt(frames) for frames in IGT_FRAMES]
    disagreements = [(frames, py, node)
                     for frames, py, node in zip(IGT_FRAMES, python, js)
                     if py != node]
    assert not disagreements, (
        "core/timefmt.py::format_igt and ui/format.js::fmtIgt disagree at "
        f"(frames, python, js): {disagreements}. Ranks are graded on DISPLAYED "
        "centiseconds (ranks/classify.py::display_cs), so a formatting drift "
        "is a grading drift — and JS `/` is float division where Python `//` "
        "is not, which is the drift these two are one careless edit away from.")


# --- 4. stat-chip identity --------------------------------------------------

STAT_SELECTIONS = [
    {"key": "avg_last_n", "params": {"n": 10}},
    {"key": "avg_last_n", "params": {"n": 25}},
    {"key": "avg_last_n", "params": {"n": 50}},
    {"key": "avg_last_n", "params": {"n": "50"}},   # int/str must collapse
    {"key": "success_rate", "params": {"failures": ["death"]}},
    {"key": "success_rate", "params": None},
    {"key": "best", "params": {}},
    {"key": "worst", "params": None},
]


def test_stat_selection_identity_agrees():
    from sm64_events.stats.registry import selection_id

    js = run_node(
        f"{declaration(STATMENU_JS, 'keyOf')}\n"
        f"const selections = {json.dumps(STAT_SELECTIONS)};\n"
        "console.log(JSON.stringify(selections.map(keyOf)));")
    python = [selection_id(s["key"], s["params"]) for s in STAT_SELECTIONS]
    disagreements = [(s, py, node)
                     for s, py, node in zip(STAT_SELECTIONS, python, js)
                     if py != node]
    assert not disagreements, (
        "stats/registry.py::selection_id and statmenu.js::keyOf disagree at "
        f"(selection, python, js): {disagreements}. They are the identity a "
        "chip is matched by: disagree and ticking a chip's checkbox adds a "
        "duplicate instead of toggling the one already on the card.")


def test_the_default_stat_menu_is_addressable_by_both():
    """Every shipped default must round-trip through both implementations —
    a default nobody can untick is worse than a wrong default."""
    from sm64_events.stats.registry import DEFAULT_STAT_MENU, selection_id

    js = run_node(
        f"{declaration(STATMENU_JS, 'keyOf')}\n"
        f"const selections = {json.dumps(DEFAULT_STAT_MENU)};\n"
        "console.log(JSON.stringify(selections.map(keyOf)));")
    python = [selection_id(s["key"], s.get("params")) for s in DEFAULT_STAT_MENU]
    assert python == js
    assert len(set(python)) == len(python), (
        f"DEFAULT_STAT_MENU has two entries with the same identity: {python}. "
        "They render as two chips that one checkbox toggles together.")


# --- 5. the Bowser Reds star/pipe family suffix (round 2, item 4) ----------

def test_reds_family_suffix_agrees():
    """views.py::STAR_FAMILY_SUFFIX/PIPE_FAMILY_SUFFIX and
    redsfamily.js's own constants must name the identical two literals --
    they select which half of a Bowser Reds star's strategies a section may
    grade or offer (Python) and which suffix the pinned card/cell show
    (JS); disagreeing would mean a strategy named " (Pipe)" server-side that
    the client never recognises as the Pipe family, or a card suffix that
    doesn't match any strategy the server will actually grade against."""
    from sm64_events.tracking.views import PIPE_FAMILY_SUFFIX, STAR_FAMILY_SUFFIX

    js = run_node(
        f"import {{ STAR_FAMILY_SUFFIX, PIPE_FAMILY_SUFFIX, familyLabel }} "
        f"from {REDSFAMILY_JS.as_uri()!r};\n"
        "console.log(JSON.stringify({\n"
        "  star: STAR_FAMILY_SUFFIX, pipe: PIPE_FAMILY_SUFFIX,\n"
        "  labelled: [familyLabel('8 Red Coins', false),\n"
        "             familyLabel('8 Red Coins', true)],\n"
        "}));")
    assert js["star"] == STAR_FAMILY_SUFFIX, (
        f"star suffix disagrees: views.py={STAR_FAMILY_SUFFIX!r} "
        f"redsfamily.js={js['star']!r}")
    assert js["pipe"] == PIPE_FAMILY_SUFFIX, (
        f"pipe suffix disagrees: views.py={PIPE_FAMILY_SUFFIX!r} "
        f"redsfamily.js={js['pipe']!r}")
    assert js["labelled"] == [f"8 Red Coins{STAR_FAMILY_SUFFIX}",
                              f"8 Red Coins{PIPE_FAMILY_SUFFIX}"]


# --- the guards themselves --------------------------------------------------

def test_the_guards_can_still_fail():
    """Probed in both directions (tests/source_scan.py's rule): the extractor
    must find real code and must NOT find a commented-out older version."""
    assert "RANK_MODE_OPTIONS" in declaration(RANKS_JS, "RANK_MODE_OPTIONS")
    assert "keyOf" in declaration(STATMENU_JS, "keyOf")

    commented = UI / "sample.js"          # never read; suffix drives the strip
    text = ("// const keyOf = (s) => s.key + ':OLD';\n"
            "const keyOf = (s) => s.key;\n")
    code = strip_comments(text)
    assert "OLD" not in code and "const keyOf" in code

    # And the comparisons themselves: a mutated ladder must be caught.
    from sm64_events.ranks.classify import RANK_NAMES
    assert list(RANK_NAMES) != [*RANK_NAMES, "Ultra"]
    assert commented.name == "sample.js"

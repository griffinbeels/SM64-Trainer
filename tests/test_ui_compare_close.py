"""The compare ×'s two meanings (his call, 2026-08-14): a user-added
comparison is DELETED through DELETE /api/compare/videos/{id}; the
rank-standard example is system-provided, so × only hides it and remembers
the opt-out (it stays saved for "load existing"). Before the fork, closeComp
only cleared local open state while the button wore danger styling — a button
promising a deletion it never performed — and DELETE /api/compare/videos had
no caller anywhere under ui/ (the endpoint-with-no-caller class this repo has
been bitten by before).

Why a source contract and not a driven click: a saved comparison requires
imported footage, and the offline fixture cannot have any — the feed lists
only replay-available attempts, and an import needs ffmpeg plus a source
video. The endpoint's own delete behavior (row gone, cache file dropped on
last reference) is pinned server-side in test_compare_api.py; what only the
UI can get wrong is (a) never calling it and (b) not saying which of the two
things the × will do, so that is what this file pins. Comment-immune via
strip_comments, probed in both directions below.
"""
import re
from pathlib import Path

from source_scan import strip_comments

REPO = Path(__file__).resolve().parents[1]
COMPARE_JS = REPO / "src" / "sm64_events" / "ui" / "components" / "compare.js"


def close_comp_source() -> str:
    """closeComp's own text, comment-stripped — from its declaration to the
    next top-level-looking `const` so the assertions cannot be satisfied by
    code elsewhere in the file."""
    code = strip_comments(COMPARE_JS.read_text(encoding="utf-8"))
    match = re.search(r"const closeComp = .*?\n  \};", code, re.S)
    assert match, "compare.js: closeComp declaration not found"
    return match.group(0)


def test_the_x_actually_deletes_a_user_added_comparison():
    body = close_comp_source()
    assert 'send("DELETE", `/api/compare/videos/' in body, (
        "closeComp no longer calls DELETE /api/compare/videos — the × is "
        "back to hiding a user-added comparison while wearing danger styling")


def test_the_x_only_hides_the_rank_standard_example():
    body = close_comp_source()
    fork = body.index("rankLinked(")
    delete = body.index('send("DELETE"')
    assert fork < delete, (
        "closeComp deletes before asking whether the video is the "
        "rank-standard example — the system-provided default must be hidden "
        "(dismissRank), never deleted")
    assert "dismissRank" in body[:delete], (
        "the rank-linked branch no longer remembers the opt-out")


def test_the_button_says_which_of_the_two_it_will_do():
    code = strip_comments(COMPARE_JS.read_text(encoding="utf-8"))
    assert "Delete this comparison" in code
    assert "Hide the rank-standard example" in code
    assert re.search(r"deletes\s*\?", code), (
        "the × title no longer forks on the `deletes` prop — one sentence is "
        "covering two different behaviors")


def test_the_guards_can_still_fail():
    """Both directions (tests/source_scan.py's rule): the extractor finds real
    code, and a commented-out call cannot satisfy it."""
    sample = ('  const closeComp = async (id) => {\n'
              '    // send("DELETE", `/api/compare/videos/${id}`);\n'
              '    setOpenSet(id);\n'
              '  };')
    assert 'send("DELETE"' not in strip_comments(sample)
    assert 'send("DELETE"' in close_comp_source()

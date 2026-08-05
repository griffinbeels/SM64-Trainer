"""Browsing a past star's graphs is a mode that ENDS ITSELF.

ui/focustarget.js, driven through node. Griffin's rule (2026-08-03): "the
second we start playing again in LLL (via a reset / star grab), or through
warping / basically anything that would trigger changing the star/segment
selector, that new area or star or segment should take ownership of that card
(instead of it being sticky once clicked)."

Three signals, each compared against the value it held at the moment of the
click. Every one of them is tested for dropping the pick on its own, so
removing any single comparison goes red.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from source_scan import strip_comments

REPO = Path(__file__).resolve().parent.parent
UI = REPO / "src" / "sm64_events" / "ui"
FOCUS_JS = (UI / "focustarget.js").as_uri()
STAGECONTEXT_JS = (UI / "stagecontext.js").as_uri()
PRACTICE_JS = UI / "components" / "practice.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")

AT_CLICK = {"activeKey": "star:22:0", "stageKey": "22:1:13",
            "newestAttemptId": 900}


def resolve(manual, live) -> object:
    script = (f"import {{ resolveFocus }} from {FOCUS_JS!r};\n"
              f"console.log(JSON.stringify(resolveFocus("
              f"{json.dumps(manual)}, {json.dumps(live)})));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def stage_key(stage) -> object:
    script = (f"import {{ stageKey }} from {STAGECONTEXT_JS!r};\n"
              f"console.log(JSON.stringify(stageKey({json.dumps(stage)})));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_with_no_pick_the_focus_is_whatever_is_active():
    """The default page is byte-for-byte today's behaviour."""
    assert resolve(None, AT_CLICK) == "star:22:0"


def test_a_pick_holds_while_nothing_has_moved():
    manual = {"key": "segment:12", "at": AT_CLICK}
    assert resolve(manual, AT_CLICK) == "segment:12"


def test_warping_hands_the_card_back():
    """"basically anything that would trigger changing the star/segment
    selector" -- the selector is a function of the stage."""
    manual = {"key": "segment:12", "at": AT_CLICK}
    live = {**AT_CLICK, "stageKey": "13:1:6"}
    assert resolve(manual, live) == "star:22:0"


def test_the_target_moving_hands_the_card_back():
    manual = {"key": "segment:12", "at": AT_CLICK}
    live = {**AT_CLICK, "activeKey": "star:13:2"}
    assert resolve(manual, live) == "star:13:2"


def test_a_reset_or_star_grab_hands_the_card_back():
    """A new attempt row landing IS "we started playing again"."""
    manual = {"key": "segment:12", "at": AT_CLICK}
    live = {**AT_CLICK, "newestAttemptId": 901}
    assert resolve(manual, live) == "star:22:0"


def test_a_pick_with_nothing_active_to_fall_back_to_still_resolves_to_null():
    live = {"activeKey": None, "stageKey": "22:1:13", "newestAttemptId": 900}
    assert resolve(None, live) is None


def test_the_stage_key_changes_with_level_area_and_course():
    """stage_changed is only broadcast when the CONTEXT changes, so every
    field of the payload moving is exactly a selector change."""
    base = {"course_id": 13, "level": 22, "area": 1, "mode": "stars"}
    assert stage_key(base) == stage_key(dict(base))
    assert stage_key(base) != stage_key({**base, "area": 2})
    assert stage_key(base) != stage_key({**base, "level": 23})
    assert stage_key(base) != stage_key({**base, "course_id": 14})
    assert stage_key(None) is None


def test_the_newest_attempt_id_is_frozen_alongside_its_siblings():
    """resolveFocus's three signals must all be read from the SAME snapshot
    moment. `activeKey`/`stageKey` come from practice.js's celebration-frozen
    state (`frozen.target`/`frozen.stage`, ui/rankclimb.js's
    useHeldWhileCelebrating) -- `newestAttemptId` used to be read LIVE
    instead (`newestJournalId(v)`), which drops a browse pick the instant an
    UNRELATED attempt lands elsewhere and starts a rank climb: activeKey and
    stageKey stay held, the live newestAttemptId changes, snapshotsAgree
    fails, and the analysis card jumps away from the entity whose rank is
    animating -- exactly what the celebration hold exists to prevent.

    practice.js pulls in Preact, so this is a source scan -- like
    test_ui_celebrations.py's own freeze-wiring checks -- rather than a unit
    test of the pure function above (which cannot see this: it only ever
    receives whatever `live` object practice.js hands it, and the bug is in
    how that object gets built)."""
    practice = strip_comments(PRACTICE_JS.read_text(encoding="utf-8"))
    assert re.search(
        r"useHeldWhileCelebrating\(\{\s*"
        r"target:[^,]+,\s*stage: t\.stage,\s*"
        r"armedOrder: t\.armedOrder, lastPinnedSeg: t\.lastPinnedSeg,\s*"
        r"newestAttemptId: newestJournalId\(t\.view\)(?:,[^}]*)?\s*\}\)", practice), (
        "newestAttemptId must be frozen in the SAME useHeldWhileCelebrating "
        "call as target/stage/armedOrder/lastPinnedSeg -- a second, separate "
        "freeze (or none at all) lets it disagree with its siblings the "
        "instant they diverge")
    assert "newestAttemptId: frozen.newestAttemptId" in practice, (
        "liveSnapshot's newestAttemptId must read the FROZEN value "
        "(frozen.newestAttemptId), not a live newestJournalId(v) call")
    assert not re.search(r"newestAttemptId:\s*newestJournalId\(v\)", practice), (
        "liveSnapshot must not read newestJournalId(v) directly -- v is the "
        "live view, not the celebration-frozen snapshot, and that is exactly "
        "the regression this test exists to catch")


def test_the_freeze_wiring_guard_can_still_fail():
    """Comment-only and a plausible-but-wrong shape must not satisfy the
    regex above -- the same probe-both-directions rule every source-scan
    guard in this project follows (tests/source_scan.py)."""
    comment_only = strip_comments(
        "// useHeldWhileCelebrating({ target, stage, armedOrder,\n"
        "// lastPinnedSeg, newestAttemptId: newestJournalId(t.view) })\n"
        "// newestAttemptId: frozen.newestAttemptId\n")
    assert not re.search(
        r"useHeldWhileCelebrating\(\{\s*"
        r"target:[^,]+,\s*stage: t\.stage,\s*"
        r"armedOrder: t\.armedOrder, lastPinnedSeg: t\.lastPinnedSeg,\s*"
        r"newestAttemptId: newestJournalId\(t\.view\)(?:,[^}]*)?\s*\}\)", comment_only)
    assert "newestAttemptId: frozen.newestAttemptId" not in comment_only

    # A second, SEPARATE freeze call (not one object, one hook) must not
    # satisfy it either -- the finding was specifically about a second
    # mechanism beside the first, not merely "frozen somewhere".
    two_freezes = ("const frozen = useHeldWhileCelebrating({ target, stage, "
                   "armedOrder, lastPinnedSeg });\n"
                   "const frozenId = useHeldWhileCelebrating("
                   "newestJournalId(t.view));\n")
    assert not re.search(
        r"useHeldWhileCelebrating\(\{\s*"
        r"target:[^,]+,\s*stage: t\.stage,\s*"
        r"armedOrder: t\.armedOrder, lastPinnedSeg: t\.lastPinnedSeg,\s*"
        r"newestAttemptId: newestJournalId\(t\.view\)(?:,[^}]*)?\s*\}\)", two_freezes)


def test_the_top_key_is_frozen_alongside_its_siblings():
    """`topKey` (auto-open-newest, 2026-08-04) is a FOURTH signal riding the
    same hold, for the same reason `newestAttemptId` was added to it above:
    `topEntityKey(t.view)` is a live re-sort of `t.view`'s own sections, not
    frozen by the hold on its own, so a card closing underneath a running
    celebration (an unrelated attempt landing elsewhere mid-climb, changing
    which entity is newest) is exactly the regression this test exists to
    catch -- the `.claude/rules/ui-climb.md` case this task's own brief named.

    Reuses the SAME positive regex `test_the_newest_attempt_id_is_frozen_
    alongside_its_siblings` proved above (its trailing `(?:,[^}]*)?` is what
    makes room for this field in the first place) -- this test only adds the
    checks specific to `topKey` itself: it is inside that one call.

    CORRECTED (2026-08-05, active-leads round): the caller no longer hands
    `PracticeLog` `frozen.topKey` directly -- the auto-open slot now follows
    the ACTIVE entity first (`ui/components/practicelog.js`'s `orderedSections`
    puts it at the top of the list regardless of recency, and A1's own design
    already defined the auto-open slot as "whichever card is at the top", so
    the slot has to move with it or a card nobody touched would sit open one
    row under a closed, freshly-active one). `live.activeKey` needs no freeze
    of its own -- it is already derived from `frozen.stage`/`frozen.target`,
    so it cannot move mid-celebration -- and `frozen.topKey` survives as the
    FALLBACK for when nothing is active at all, which is the one branch that
    still needs the freeze this test guards."""
    practice = strip_comments(PRACTICE_JS.read_text(encoding="utf-8"))
    assert re.search(
        r"useHeldWhileCelebrating\(\{\s*"
        r"target:[^,]+,\s*stage: t\.stage,\s*"
        r"armedOrder: t\.armedOrder, lastPinnedSeg: t\.lastPinnedSeg,\s*"
        r"newestAttemptId: newestJournalId\(t\.view\),\s*"
        r"topKey: topEntityKey\(t\.view\)(?:,[^}]*)?\s*\}\)", practice), (
        "topKey must be frozen in the SAME useHeldWhileCelebrating call as "
        "its siblings -- a second, separate freeze (or none at all) lets an "
        "unrelated attempt landing elsewhere move the auto-open slot WHILE a "
        "rank on screen is still climbing")
    # `playedKeys` (2026-08-05) rides the same call for the same reason, and
    # is checked separately rather than by extending the regex above: this
    # test's subject is `topKey`, and a positional pattern naming every
    # frozen field is what made adding one a red build in the first place.
    assert re.search(
        r"useHeldWhileCelebrating\(\{[^}]*playedKeys: playedEntityKeys\("
        r"t\.view\)", practice), (
        "playedKeys must be frozen alongside topKey -- the auto-open slot asks "
        "it whether the ACTIVE entity has recorded anything, and a live read "
        "there lets an attempt landing mid-climb pop that card open")
    assert re.search(
        r"const topKey = activeHasPlayed \? live\.activeKey : frozen\.topKey;",
        practice), (
        "the auto-open slot must prefer the ACTIVE entity *that has recorded "
        "something* and fall back to the FROZEN recency value -- this was "
        "`live.activeKey ?? frozen.topKey` until 2026-08-05, which handed the "
        "slot to an entity with nothing in it (whether the right entity wins "
        "is tests/test_practice_page_order.py's subject; what THIS file owns "
        "is that both inputs are frozen)")
    assert "topKey=${topKey}" in practice, (
        "PracticeLog's own topKey prop must read the active-or-frozen "
        "derived value, not frozen.topKey directly (that would strand the "
        "active-leads card closed) or a live topEntityKey(t.view) call")
    assert not re.search(r"topKey=\$\{topEntityKey\(t\.view\)\}", practice), (
        "PracticeLog must not be handed a live topEntityKey(t.view) call -- "
        "that is exactly the regression this test exists to catch")
    assert not re.search(r"topKey=\$\{frozen\.topKey\}", practice), (
        "PracticeLog must not be handed frozen.topKey directly any more -- "
        "the active entity has to be able to win the slot too")


def test_the_top_key_freeze_guard_can_still_fail():
    """Same probe-both-directions rule as the sibling guard above."""
    comment_only = strip_comments(
        "// newestAttemptId: newestJournalId(t.view),\n"
        "// topKey: topEntityKey(t.view) })\n"
        "// const topKey = activeHasPlayed ? live.activeKey : frozen.topKey;\n"
        "// topKey=${topKey}\n")
    assert not re.search(
        r"newestAttemptId: newestJournalId\(t\.view\),\s*"
        r"topKey: topEntityKey\(t\.view\)(?:,[^}]*)?\s*\}\)", comment_only)
    assert not re.search(
        r"const topKey = activeHasPlayed \? live\.activeKey : frozen\.topKey;",
        comment_only)
    assert "topKey=${topKey}" not in comment_only

    # A live read handed straight to PracticeLog, alongside a genuine but
    # SEPARATE freeze -- the finding this guards against is specifically the
    # PROP wiring at the call site, not merely that a frozen value exists
    # somewhere in the file.
    live_leak = (
        "const frozen = useHeldWhileCelebrating({ target, stage, armedOrder, "
        "lastPinnedSeg, newestAttemptId: newestJournalId(t.view), "
        "topKey: topEntityKey(t.view) });\n"
        "<PracticeLog topKey=${topEntityKey(t.view)} />\n")
    assert re.search(  # the freeze call itself is still well-formed...
        r"newestAttemptId: newestJournalId\(t\.view\),\s*"
        r"topKey: topEntityKey\(t\.view\)(?:,[^}]*)?\s*\}\)", live_leak)
    assert not re.search(
        r"const topKey = activeHasPlayed \? live\.activeKey : frozen\.topKey;",
        live_leak), (
        "the mutation itself must actually read as a leak, or this probe "
        "proves nothing")
    assert re.search(r"topKey=\$\{topEntityKey\(t\.view\)\}", live_leak), (
        "the mutation itself must actually read as a leak, or this probe "
        "proves nothing")

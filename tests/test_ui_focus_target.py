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
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
UI = REPO / "src" / "sm64_events" / "ui"
FOCUS_JS = (UI / "focustarget.js").as_uri()
STAGECONTEXT_JS = (UI / "stagecontext.js").as_uri()

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

"""Live reward metadata is only valid for the final matching strategy and PB.

The pure selector is extracted from the real LogCard module, whose browser
import map cannot be resolved by node. Rendered climbs have their own live gate.
"""
import json
import re
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

from source_scan import strip_comments
from test_ui_practice_log import _extract

UI = Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "ui"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")


def select(sec, tracker):
    source = strip_comments((UI / "components" / "practicelog.js").read_text(encoding="utf-8"))
    script = (
        f"import {{ entityKey, sectionClock, sectionPbByStrat }} from {(UI / 'entitysection.js').as_uri()!r};\n"
        + _extract(source, "classificationPb") + "\n"
        + f"console.log(JSON.stringify(classificationPb({json.dumps(sec)}, {json.dumps(tracker)})));"
    )
    done = subprocess.run(["node", "--input-type=module", "-"], input=script,
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.fixture
def news():
    sec = {"course_id": 2, "star_id": 4, "last_strat": "Destination",
           "pb_by_strat": {"igt": {"frames": 300, "attempt_id": 7}}}
    event = {"key": 1, "course_id": 2, "star_id": 4,
             "strat_tag": "Destination", "timer_mode": "igt", "frames": 300, "attempt_id": 7,
             "classification": {"rank": {"mode": "pb"}, "entity_rank": None,
                                "game_version": {"effective": "us"}}}
    tracker = {"clock": "igt", "view": {"rank_mode": "pb", "game_version": {"effective": "us"}},
               "classificationPbs": {"star:2:4": event}}
    return sec, tracker, event


def test_final_matching_pb_carries_the_live_reward(news):
    sec, tracker, event = news
    assert select(sec, tracker) == event


@pytest.mark.parametrize("change", ["strat", "clock", "attempt", "frames", "version", "mode", "entity"])
def test_stale_or_unrelated_snapshots_cannot_start_a_climb(news, change):
    sec, tracker, _ = deepcopy(news)
    if change == "strat": sec["last_strat"] = "Other"
    elif change == "clock": tracker["clock"] = "rta"
    elif change == "attempt": sec["pb_by_strat"]["igt"]["attempt_id"] = 8
    elif change == "frames": sec["pb_by_strat"]["igt"]["frames"] = 290
    elif change == "version": tracker["view"]["game_version"]["effective"] = "jp"
    elif change == "mode": tracker["view"]["rank_mode"] = "avg10"
    elif change == "entity": sec["star_id"] = 3
    assert select(sec, tracker) is None


def test_segments_match_their_rta_pb_even_while_the_star_clock_is_igt(news):
    sec, tracker, event = news
    sec.update(kind="segment", segment_id=91,
               pb_by_strat={"rta": sec["pb_by_strat"]["igt"]})
    event.update(segment_id=91, timer_mode="rta")
    tracker["classificationPbs"] = {"segment:91": event}
    assert select(sec, tracker) == event


def test_an_ordinary_snapshot_has_no_live_reward(news):
    sec, tracker, _ = news
    tracker.pop("classificationPbs")
    assert select(sec, tracker) is None


def declaration(filename, name):
    source = strip_comments((UI / filename).read_text(encoding="utf-8"))
    found = re.search(rf"^(?:export )?function {name}\(.*?^\}}", source, re.S | re.M)
    assert found, f"real {name} declaration not found"
    return found.group(0)


def test_the_first_live_banner_mount_claims_once_and_a_remount_cannot_replay(news):
    """Real claim/selector/hook bookkeeping, with only useRef's slot storage
    supplied here. The parent live-browser gate proves the painted climb."""
    sec, tracker, _ = news
    functions = [("components/practicelog.js", "classificationPb"),
                 ("components/practicelog.js", "classificationTransition"),
                 ("components/ranks.js", "gradedBanner"),
                 ("components/ranks.js", "useEarnedBanner"),
                 ("rankclimb.js", "rankSnapshot"),
                 ("rankclimb.js", "claimEarnedTransition"),
                 ("rankclimb.js", "useEarnedClimb")]
    script = (
        f"import {{ entityKey, sectionClock, sectionPbByStrat }} from {(UI / 'entitysection.js').as_uri()!r};\n"
        f"import {{ rankAt, rankPosition, barFill }} from {(UI / 'components/caps.js').as_uri()!r};\n"
        + "\n".join(declaration(file, name) for file, name in functions)
        + f"\nconst sec = {json.dumps(sec)}, tracker = {json.dumps(tracker)};\n"
        + """
const instances = new Map();
let slots, index;
function useRef(initial) {
  if (index >= slots.length) slots.push({current: initial});
  return slots[index++];
}
function renderHook(id, hook, input) {
  slots = instances.get(id) || [];
  instances.set(id, slots);
  index = 0;
  return hook(input);
}
const receipt = tracker.classificationPbs['star:2:4'];
receipt.classification.rank = {reason: 'unranked', mode: 'pb'};
const transition = classificationTransition(sec, tracker, 'strategy', true);
const first = renderHook('banner', useEarnedBanner, transition);
const climb = renderHook('climb', useEarnedClimb, first.climb);
climb.identityChanged('destination', 'destination');
const nextRender = renderHook('banner', useEarnedBanner, transition);
instances.delete('banner');
const remount = renderHook('banner', useEarnedBanner, transition);
const secondSurface = renderHook('other-banner', useEarnedBanner, transition);
const reload = renderHook('reload', useEarnedBanner, null);
console.log(JSON.stringify({first, active: climb.active, nextRender, remount,
  secondSurface, reload, sameReceipt: transition.receipt === receipt}));
"""
    )
    done = subprocess.run(["node", "--input-type=module", "-"], input=script,
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    assert result["sameReceipt"] and result["active"]
    assert result["first"]["fresh"] and result["first"]["climb"]["initial"]
    assert result["first"]["climb"]["from"]["fill"] == 0
    assert not result["nextRender"]["fresh"]
    assert result["nextRender"]["climb"]["key"] == result["first"]["climb"]["key"]
    for mount in ("remount", "secondSurface", "reload"):
        assert not result[mount]["fresh"] and not result[mount]["climb"]

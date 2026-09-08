"""Overall's real JS presentation model uses every compiled division node."""
import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.core.timefmt import cs_of_frame, frame_at_or_after
from sm64_events.ranks import scoring
from sm64_events.ranks.curves import compile_curve, from_ladder, progress_for_time, time_for_score, with_anchors

UI = Path(__file__).resolve().parents[1] / "src/sm64_events/ui"
MODEL = UI / "components/librarymodel.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")


def run_js(expression):
    script = f"import * as m from {MODEL.as_uri()!r};\nconsole.log(JSON.stringify({expression}));"
    result = subprocess.run(["node", "--input-type=module", "-"], input=script,
                            capture_output=True, text=True, encoding="utf-8", timeout=60,
                            **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def full_curve(phase=0):
    scores = [low + i * (high - low) / scoring.DIVISIONS_PER_TIER
              for tier in reversed(scoring.RANK_NAMES)
              for low, high in [scoring.tier_band(tier)]
              for i in range(scoring.DIVISIONS_PER_TIER)]
    frame = 180 + phase
    nodes = [[cs_of_frame(frame), 100]]
    for index, score in enumerate(reversed(scores[1:])):
        frame += 1 + index // 3
        nodes.append([cs_of_frame(frame), score])
    return compile_curve(nodes, {"population_count": 23, "family_count": 4})


@pytest.mark.parametrize("phase", [0, 1, 2, 29])
def test_all_45_overall_ranges_use_the_full_curve_and_attainable_frames(phase):
    curve = full_curve(phase)
    bands = run_js(f"m.curveBands({json.dumps(curve)})")
    assert [band["tier"] for band in bands] == list(reversed(scoring.RANK_NAMES))
    assert sum(len(band["divisions"]) for band in bands) == 45
    differences_from_tier_reconstruction = 0
    for band in bands:
        tier = band["tier"]
        low, high = scoring.tier_band(tier)
        assert [part["numeral"] for part in band["divisions"]] == scoring.DIVISION_NUMERALS
        for index, part in enumerate(band["divisions"]):
            target = low + index * (high - low) / 5
            assert part["slowCs"] == time_for_score(curve, target)
            assert not part["empty"]
            old_goal = scoring.time_for_score(curve["ladder_cs"], target)
            differences_from_tier_reconstruction += part["slowCs"] != old_goal
            for edge in (part["slowCs"], part["fastCs"]):
                if edge is None:
                    continue
                assert cs_of_frame(frame_at_or_after(edge)) == edge
                progress = progress_for_time(curve, edge)
                assert (progress["tier"], progress["division"]) == (tier, part["numeral"])
        assert band["cutoffCs"] == band["divisions"][0]["slowCs"]
    assert differences_from_tier_reconstruction > 15


def test_personal_marker_matches_backend_at_boundaries_and_intermediate_centiseconds():
    curve = full_curve(1)
    rng = random.Random(613)
    probes = [node[0] + delta for node in curve["nodes"] for delta in (-1, 0, 1)]
    probes += [rng.uniform(100, 20000) for _ in range(50)]
    answers = run_js(f"{json.dumps(probes)}.map(t => m.curveStandingOn({json.dumps(curve)}, t))")
    for time_cs, answer in zip(probes, answers, strict=True):
        expected = progress_for_time(curve, time_cs)
        assert answer.pop("rank") == expected["tier"]
        assert answer.keys() == expected.keys()
        for key, value in expected.items():
            assert answer[key] == (pytest.approx(value) if isinstance(value, float) else value)


@pytest.mark.parametrize("ladder", [{}, {"Mario": 1000}, {"Mario": 1000, "Master": 1000,
                                                      "Silver": 1101, "Bronze": 1400}])
def test_legacy_ranges_and_personal_boundaries_remain_exact(ladder):
    curve = from_ladder(ladder)
    seconds = {tier: value / 100 for tier, value in ladder.items()}
    result = run_js(f"[m.curveBands({json.dumps(curve)}), m.ladderBands({json.dumps(seconds)})]")
    assert result[0] == result[1]
    probes = [900, 1000, 1001, 1101, 1400, 5000]
    answers = run_js(f"{json.dumps(probes)}.map(t => m.curveStandingOn({json.dumps(curve)}, t))")
    for time_cs, answer in zip(probes, answers, strict=True):
        expected = scoring.progress_for_time(ladder, time_cs) if ladder else None
        if expected is None:
            assert answer is None
        else:
            assert answer.pop("rank") == expected["tier"]
            assert answer == pytest.approx(expected)


def test_compiled_payload_wins_over_display_ladder_and_keeps_provenance():
    curve = full_curve()
    payload = {"overall_curve": curve, "overall": {"Mario": 99999}}
    assert run_js(f"m.overallCurveOf({json.dumps(payload)})") == curve
    legacy = run_js("m.overallCurveOf({overall: {Mario: 10.03, Bronze: 15}})")
    assert legacy["ladder_cs"] == {"Mario": 1003, "Bronze": 1500}
    assert legacy["metadata"]["source"] == "legacy_payload"


def test_bad_compiled_data_does_not_silently_reconstruct_from_display_cutoffs():
    curve = full_curve()
    for broken in ({**curve, "schema_version": 99}, {**curve, "interpolation": "unknown"},
                   {**curve, "nodes": list(reversed(curve["nodes"]))}):
        payload = {"overall_curve": broken, "overall": {"Mario": 10, "Bronze": 15}}
        assert run_js("(() => { try { m.curveBands(m.overallCurveOf(" + json.dumps(payload)
                      + ")); return false; } catch { return true; } })()")


def test_inherited_pin_fallback_keeps_legacy_ranges_and_adjustment_provenance():
    curve = with_anchors(full_curve(), {"Mario": 603, "Grandmaster": 606}, preserve_unpinned=False)
    assert curve["interpolation"] == "legacy"
    payload = {"overall_curve": curve, "overall_overrides": {"Mario": 6.03, "Grandmaster": 6.06}}
    answer = run_js(f"m.overallCurveOf({json.dumps(payload)})")
    assert answer["metadata"]["anchor_adjustments"]["fallback_reason"]
    assert answer["ladder_cs"]["Mario"] == 603
    assert answer["ladder_cs"]["Grandmaster"] == 606
    assert run_js(f"m.curveBands({json.dumps(answer)})")


def test_no_saved_time_marks_the_floor_and_constants_have_one_owner():
    curve = full_curve()
    answer = run_js(f"m.curveStandingOn({json.dumps(curve)}, null)")
    assert (answer["rank"], answer["division"]) == ("Iron", "V")
    assert (answer["next_tier"], answer["next_division"]) == ("Iron", "IV")
    assert run_js("m.curveStandingOn(m.overallCurveOf({}), null)") is None
    expression = "await (async () => { const t = await import(" + json.dumps((UI / "timecurve.js").as_uri())
    expression += "); const c = await import(" + json.dumps((UI / "components/caps.js").as_uri())
    expression += "); return m.SCORE_ANCHORS === t.SCORE_ANCHORS && c.DIVISION_NUMERALS === t.DIVISION_NUMERALS; })()"
    assert run_js(expression)


def test_actual_overall_component_cutoff_edits_and_version_scope():
    from frontend_runner import run_frontend

    run_frontend("overallstandards.test.js")

"""Compare real Python/JS compiled curves, including intermediate game frames."""
import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.core.timefmt import cs_of_frame
from sm64_events.ranks.curves import compile_curve, from_ladder, progress_for_time, score_for, time_for_score
from test_rank_curves import calibrated_nodes, division_scores

MODULE = (Path(__file__).resolve().parents[1] / "src/sm64_events/ui/timecurve.js").as_uri()
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")


def run_node(script):
    # tools/run_tests.py/conftest own the shared resource budget and child job.
    result = subprocess.run(["node", "--input-type=module", "-"], input=script,
                            capture_output=True, text=True, encoding="utf-8", timeout=60,
                            **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def assert_progress(actual, expected):
    if expected is None:
        assert actual is None
        return
    assert actual.keys() == expected.keys()
    for key in expected:
        if isinstance(expected[key], float):
            assert actual[key] == pytest.approx(expected[key], abs=1e-9), (key, actual, expected)
        else:
            assert actual[key] == expected[key], (key, actual, expected)


def test_compiled_scores_inverses_and_all_progress_fields_match_real_python():
    rng = random.Random(6495)
    curves = [compile_curve(calibrated_nodes(phase, narrow))
              for phase in range(3) for narrow in (True, False)]
    curves += [compile_curve([[888.5, 100], [919.2, 95], [1234.8, 30], [2345.1, 2]])]
    for _ in range(15):
        scores = [100, *sorted((rng.uniform(1, 99) for _ in range(20)), reverse=True)]
        times = sorted(rng.sample(range(200, 3000), len(scores)))
        curves.append(compile_curve([[cs_of_frame(time) + rng.random(), score]
                                     for time, score in zip(times, scores, strict=True)]))
    curves += [from_ladder(ladder) for ladder in [
        {}, {"Mario": 885}, {"Mario": 246, "Grandmaster": 246, "Master": 300},
        {"Mario": 246, "Grandmaster": 300, "Master": 300},
        {"Mario": 246, "Grandmaster": 246, "Bronze": 246},
        {"Mario": 885, "Grandmaster": 910, "Bronze": 940},
    ]]
    targets = division_scores() + [100, -1, 101, .001, .12345, 58.237]
    cases = []
    for curve in curves:
        times = [cs_of_frame(frame) for frame in range(0, 1000, 7)]
        times += [rng.uniform(100, 20000) for _ in range(40)]
        if curve["interpolation"] == "pchip":
            times += [1e308]
        times += [time_for_score(curve, score) for score in targets]
        times = [time for time in times if time is not None and time >= 0]
        cases.append({"curve": curve, "times": times})
    actual = run_node(f"""
      import * as m from {MODULE!r};
      const cases = {json.dumps(cases)}, targets = {json.dumps(targets)};
      console.log(JSON.stringify(cases.map(({{curve, times}}) => ({{
        scores: times.map((t) => m.curveScore(curve,t)),
        progress: times.map((t) => m.curveProgress(curve,t)),
        inverse: targets.map((s) => m.curveTimeForScore(curve,s)),
      }}))));
    """)
    for case, result in zip(cases, actual, strict=True):
        curve = case["curve"]
        assert result["inverse"] == [time_for_score(curve, target) for target in targets]
        for time, score, progress in zip(case["times"], result["scores"], result["progress"], strict=True):
            expected = score_for(curve, time)
            assert score == (pytest.approx(expected, abs=1e-9) if expected is not None else None)
            assert_progress(progress, progress_for_time(curve, time))


def test_browser_rejects_bad_versions_nonmonotone_nodes_and_nonfinite_queries():
    curve = compile_curve(calibrated_nodes())
    patches = [{"schema_version": 2}, {"schema_version": True}, {"interpolation": "spline"},
               {"metadata": []}, {"nodes": []}, {"ladder_cs": {}},
               {"nodes": [[100, 100], [100, 10]]}, {"nodes": [[100, 10], [200, 95]]}]
    actual = run_node(f"""
      import * as m from {MODULE!r};
      const curve = {json.dumps(curve)}, patches = {json.dumps(patches)};
      const throws = (fn) => {{ try {{ fn(); return false; }} catch {{ return true; }} }};
      const methods = [m.curveScore,m.curveTimeForScore,m.curveProgress];
      console.log(JSON.stringify([
        ...patches.flatMap((p) => methods.map((fn) => throws(() => fn({{...curve,...p}},10)))),
        ...[NaN,Infinity,'10',true].flatMap((t) => methods.map((fn) => throws(() => fn(curve,t)))),
      ]));
    """)
    assert actual and all(actual)

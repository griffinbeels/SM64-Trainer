"""The trainer and the cloud refuse the same runs at the ghost ceiling.

His rule (2026-10-13): "Maybe it's the capless 5 threshold plus 10%? ...
Anything above that DOES NOT CREATE A GHOST." The trainer applies it before a
run becomes a rival or a cloud offer (`ghost/community_catalog.py`); the cloud
applies it again at upload admission (`cloud/community/src/catalog.js`). This
compares the two real implementations on every ladder of the shipped catalog,
at the edge of each, rather than restating the rule a third time.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sm64_events.ghost.community_catalog import GHOST_CEILING_PERCENT, within_ghost_ceiling

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "cloud" / "community" / "catalog.json"
CATALOG_JS = ROOT / "cloud" / "community" / "src" / "catalog.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")


def _ladders():
    entries = json.loads(CATALOG.read_bytes().decode("utf-8"))["entries"]
    for entry in entries:
        for strategy in entry["strategies"]:
            yield strategy["cutoffs"]
        yield entry["overall_cutoffs"]


def _cases():
    """Each ladder at its step-1 edge: exactly at the ceiling, one past it,
    and a 30-minute run; plus ladders' own edge (well inside)."""
    cases = []
    for cutoffs in _ladders():
        edge = next((cutoff["frames"] for cutoff in cutoffs if cutoff["step"] == 1), None)
        if edge is None:
            continue
        ceiling = edge * (100 + GHOST_CEILING_PERCENT) // 100
        for frames in (edge, ceiling, ceiling + 1, 54000):
            cases.append((cutoffs, frames, edge))
    return cases


def test_the_cloud_and_the_trainer_refuse_the_same_runs_on_every_shipped_ladder(tmp_path):
    cases = _cases()
    assert len(cases) > 1000  # every strategy and overall ladder of the catalog
    data = tmp_path / "cases.json"
    data.write_bytes(json.dumps([[c, f] for c, f, _ in cases]).encode("utf-8"))
    script = (f"import {{aboveCeiling, GHOST_CEILING_PERCENT}} from {CATALOG_JS.as_uri()!r};\n"
              "import {readFileSync} from 'node:fs';\n"
              f"const cases=JSON.parse(readFileSync({data.as_posix()!r},'utf8'));\n"
              "process.stdout.write(JSON.stringify({percent:GHOST_CEILING_PERCENT,"
              "above:cases.map(([cutoffs,frames])=>aboveCeiling(cutoffs,frames))}));\n")
    result = subprocess.run(["node", "--input-type=module", "-"], input=script,
                            capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert result.returncode == 0, result.stderr
    answer = json.loads(result.stdout)
    assert answer["percent"] == GHOST_CEILING_PERCENT
    trainer = [within_ghost_ceiling(frames, edge) for _cutoffs, frames, edge in cases]
    cloud_admits = [not above for above in answer["above"]]
    assert trainer == cloud_admits
    # The edge and the exact ceiling are admitted; one frame past it and a
    # 30-minute run are refused, on both sides.
    assert trainer[0::4] == [True] * len(cases[0::4]) and trainer[1::4] == [True] * len(cases[1::4])
    assert not any(trainer[2::4]) and not any(trainer[3::4])


def test_his_deep_freeze_run_is_inside_the_ceiling():
    """10-2 TJ Strat: Capless 4 at 210 frames (7"00), so 231 frames (7"70) is
    the slowest ghost; his 226-frame 7"53 run is shared."""
    entries = json.loads(CATALOG.read_bytes().decode("utf-8"))["entries"]
    entry = next(e for e in entries if e["target"].get("entity_key") == "10-2"
                 and e["clock"] == "igt" and e["game_version"] == "us")
    tj = next(s for s in entry["strategies"] if s["name"] == "TJ Strat")
    edge = next(c["frames"] for c in tj["cutoffs"] if c["step"] == 1)
    assert edge == 210
    assert within_ghost_ceiling(226, edge) and within_ghost_ceiling(231, edge)
    assert not within_ghost_ceiling(232, edge)

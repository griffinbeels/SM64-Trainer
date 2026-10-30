"""The community catalog lists the Bowser reds->pipe segments.

Griffin, 2026-10-29: "We should definitely be showing ghosts OVERALL for reds / no
reds ... We need to have ghosts for the bowser stages." A reds->pipe segment has no
ladder of its own: Practice grades it on the paired star's " (Pipe)" ladders
(`ladder_entity_key`), so the catalog borrows exactly those, or the cloud answers
"unsupported" for every reds->pipe ghost. The shipped catalog.json must be the
exporter's output, or the cloud grades on rules the trainer no longer has.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from export_community_catalog import build_catalog  # noqa: E402

REDS_PIPE = {"BitDW", "BitFS", "BitS"}


@pytest.fixture(scope="module")
def catalog():
    return build_catalog()


def test_each_reds_pipe_segment_races_on_its_stars_pipe_ladders(catalog):
    stars = {(e["target"]["entity_key"], e["game_version"]): e for e in catalog["entries"]
             if e["target"]["kind"] == "star" and e["target"]["entity_key"] in ("16-0", "17-0", "18-0")}
    found = {}
    for entry in catalog["entries"]:
        name = entry["target"]["name"]
        if entry["target"]["kind"] != "segment" or "8 Red Coins → Pipe" not in name:
            continue
        course = name.split(" ")[0]
        star = stars[({"BitDW": "16-0", "BitFS": "17-0", "BitS": "18-0"}[course], entry["game_version"])]
        pipe = {s["name"]: s["cutoffs"] for s in star["strategies"] if s["name"].endswith(" (Pipe)")}
        assert {s["name"]: s["cutoffs"] for s in entry["strategies"]} == pipe
        assert entry["clock"] == "rta" and entry["overall_cutoffs"]
        found.setdefault(course, set()).add(entry["game_version"])
    assert found == {course: {"us", "jp"} for course in REDS_PIPE}


def test_the_shipped_catalog_is_the_exporters_output(catalog):
    shipped = json.loads((ROOT / "cloud/community/catalog.json").read_text(encoding="utf-8"))
    assert shipped["generation"] == catalog["generation"]

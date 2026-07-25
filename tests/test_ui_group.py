"""buildTree (ui/group.js) driven through node — it is the grouping ENGINE
behind both libraries and the future picker, so its ordering and null handling
are worth real assertions rather than a syntax check."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

GROUP_JS = (Path(__file__).resolve().parent.parent / "src" / "sm64_events"
            / "ui" / "group.js").as_uri()

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")


def run_node(body: str):
    """Run an ESM snippet that imports the real group.js and prints JSON."""
    script = f"import {{ buildTree }} from {GROUP_JS!r};\n{body}"
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_groups_two_levels_and_keeps_unkeyed_items_at_the_parent():
    tree = run_node("""
const items = [
  { name: "a", top: "T1", sub: "S2" },
  { name: "b", top: "T1", sub: "S1" },
  { name: "c", top: "T1", sub: null },
  { name: "d", top: "T2", sub: "S1" },
];
const levels = [
  { of: (i) => i.top, label: (k) => `top ${k}`, order: (k) => k },
  { of: (i) => i.sub, label: (k) => `sub ${k}`, order: (k) => k },
];
console.log(JSON.stringify(buildTree(items, levels)));
""")
    assert [child["key"] for child in tree["children"]] == ["T1", "T2"]
    first = tree["children"][0]
    assert first["label"] == "top T1"
    assert first["count"] == 3
    # "c" has no sub-key: it sits directly under its parent, above the subgroups
    assert [item["name"] for item in first["items"]] == ["c"]
    assert [child["key"] for child in first["children"]] == ["T1/S1", "T1/S2"]
    assert [item["name"] for item in first["children"][0]["items"]] == ["b"]


def test_order_accepts_numbers_and_composite_keys():
    tree = run_node("""
const items = [{ g: "16 Star" }, { g: "1 Star" }, { g: "120 Star" },
               { g: "Zzz" }];
const levels = [{ of: (i) => i.g, label: (k) => k,
                  order: (k) => [k === "Zzz" ? 1 : 0, k] }];
console.log(JSON.stringify(buildTree(items, levels)));
""")
    # composite: rank first, then numeric-aware name — not 1, 120, 16
    assert [child["key"] for child in tree["children"]] == \
        ["1 Star", "16 Star", "120 Star", "Zzz"]


def test_a_single_level_is_a_flat_grouping():
    tree = run_node("""
const items = [{ k: 2 }, { k: 1 }, { k: 1 }];
const levels = [{ of: (i) => i.k, label: (k) => `#${k}`, order: (k) => Number(k) }];
console.log(JSON.stringify(buildTree(items, levels)));
""")
    assert [child["count"] for child in tree["children"]] == [2, 1]
    assert tree["items"] == []

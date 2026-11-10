"""The free camera's tuning rig cannot lie about what it tunes (.claude/skills/tuning-demo).

Two guards, silent in opposite directions without them:

  * a row no movement reads is a slider in the Tune controls panel that changes nothing;
  * a movement reading a key the registry lacks gets undefined, and the camera goes to NaN.

The golden pair (no tuning given == the shipped values; a changed value moves the result) runs
in tests/frontend/sceneviewer.test.js, over every row, through the real camera functions. SAVE
is the shared tuning endpoint; here it only has to reach this registry and keep to its bounds.
No test here asserts a shipped value: those belong to whoever last pressed SAVE.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from sm64_events.server.tuning_api import TUNING_REGISTRIES, rewrite_defaults
from source_scan import strip_comments

UI = Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "ui"
REGISTRY = UI / "scenecameratuning.js"
# scenerigs.js sends the cinematic cameras' rows with every request; the lab's reader of them
# (tools/scene_lab/camera_rig.py) is held to the same list by tests/test_scene_lab_rigs.py.
CONSUMERS = [UI / "components" / "scenecamera.js", UI / "components" / "sceneviewer.js",
             UI / "components" / "scenerigs.js"]


def _registry_keys(source: str) -> set[str]:
    body = source[source.index("SCENE_CAMERA_TUNABLES"):source.index("SCENE_CAMERA_DEFAULTS")]
    return set(re.findall(r"^  (\w+): \{", body, re.M))


def _keys_read(sources: list[str]) -> set[str]:
    return {key for source in sources for key in re.findall(r"\btuning\.(\w+)", source)}


def _sources() -> tuple[str, list[str]]:
    return (strip_comments(REGISTRY.read_text(encoding="utf-8")),
            [strip_comments(path.read_text(encoding="utf-8")) for path in CONSUMERS])


def test_every_row_is_read_by_a_movement_and_no_movement_reads_a_missing_row():
    registry, consumers = _sources()
    keys, read = _registry_keys(registry), _keys_read(consumers)
    assert keys, "the registry declares no rows: nothing to tune"
    assert keys - read == set(), f"rows no movement reads (sliders that change nothing): {keys - read}"
    assert read - keys == set(), f"movements read rows the registry lacks (NaN cameras): {read - keys}"


def test_the_guards_can_still_fail():
    registry, consumers = _sources()
    unread = registry.replace("export const SCENE_CAMERA_DEFAULTS",
                              "  ghostRow: {\n  },\nexport const SCENE_CAMERA_DEFAULTS", 1)
    assert "ghostRow" in _registry_keys(unread) - _keys_read(consumers)
    assert "notARow" in _keys_read([*consumers, "const x = tuning.notARow * 2;"]) - _registry_keys(registry)


def test_save_reaches_this_registry_and_keeps_to_each_rows_bounds():
    assert TUNING_REGISTRIES["scenecamera"] == REGISTRY
    source = REGISTRY.read_text(encoding="utf-8")
    rows = _registry_keys(strip_comments(source))
    for key in rows:
        row = re.search(r"\n  " + key + r":\s*\{(?P<row>.*?)\n  \},", source, re.S)["row"]
        low = float(re.search(r"\bmin:\s*(-?[\d.]+)", row)[1])
        high = float(re.search(r"\bmax:\s*(-?[\d.]+)", row)[1])
        rewritten, written = rewrite_defaults(source, {key: high}, where=REGISTRY.name)
        assert re.search(r"\n  " + key + r":\s*\{[^}]*?\bvalue:\s*" + re.escape(json.dumps(
            int(high) if high == int(high) else high)) + ",", rewritten), key
        with pytest.raises(ValueError):
            rewrite_defaults(source, {key: low - 1}, where=REGISTRY.name)
        assert written == {} or written == {key: high}


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_the_shipped_values_sit_inside_their_own_ranges():
    script = (f"import {{ SCENE_CAMERA_TUNABLES }} from {REGISTRY.as_uri()!r};\n"
              "console.log(JSON.stringify(SCENE_CAMERA_TUNABLES));")
    result = subprocess.run(["node", "--input-type=module", "-"], input=script, capture_output=True, text=True,
                            encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    for key, row in json.loads(result.stdout).items():
        assert row["min"] <= row["value"] <= row["max"] and row["step"] > 0, key
        assert row["label"] and row["why"] and row["group"], key

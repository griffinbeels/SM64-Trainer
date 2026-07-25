"""visibleGroups (ui/components/picker.js) driven through node.

It is the whole reason the shared picker exists: dropping emptied groups and
KEEPING THE CURRENT VALUE listed are behaviours that have been implemented —
and got wrong — separately in stratpicker.js and the segment builder.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

UI_DIR = Path(__file__).resolve().parent.parent / "src" / "sm64_events" / "ui"
PICKER_JS = (UI_DIR / "components" / "picker.js").as_uri()

# picker.js imports the bare specifiers "preact"/"htm" that index.html
# resolves through its <script type="importmap"> in the browser (there is no
# node_modules — the app ships with no build step). A bare `node` process has
# no importmap, so ERR_MODULE_NOT_FOUND on "preact" would fail every test here
# regardless of picker.js's own correctness. This resolver hook redirects
# those two specifiers to the SAME vendor files the browser uses, so the test
# runs the real component, unmodified. Test plumbing only.
_RESOLVE_MAP = json.dumps({
    "preact": (UI_DIR / "vendor" / "preact.module.js").as_uri(),
    "preact/hooks": (UI_DIR / "vendor" / "hooks.module.js").as_uri(),
    "htm": (UI_DIR / "vendor" / "htm.module.js").as_uri(),
})

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")


def run_node(body: str):
    script = f"""
import {{ register }} from 'node:module';
const loaderSrc = `
  const map = {_RESOLVE_MAP};
  export function resolve(specifier, context, nextResolve) {{
    const url = map[specifier];
    if (url) return {{ url, format: 'module', shortCircuit: true }};
    return nextResolve(specifier, context);
  }}
`;
register('data:text/javascript,' + encodeURIComponent(loaderSrc), import.meta.url);
const {{ visibleGroups }} = await import({PICKER_JS!r});
{body}
"""
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


GROUPS = """
const groups = [
  { key: "a", label: "Lobby", options: [{ id: "9", name: "BoB" }, { id: "24", name: "WF" }] },
  { key: "b", label: "Basement", options: [{ id: "8", name: "SSL" }] },
];
"""


def test_without_a_filter_every_group_survives():
    tree = run_node(GROUPS + 'console.log(JSON.stringify(visibleGroups(groups, null, null)));')
    assert [group["label"] for group in tree] == ["Lobby", "Basement"]
    assert [option["id"] for option in tree[0]["options"]] == ["9", "24"]


def test_a_group_emptied_by_the_filter_is_dropped():
    tree = run_node(GROUPS + 'console.log(JSON.stringify('
                    'visibleGroups(groups, (id) => id === "9", null)));')
    assert [group["label"] for group in tree] == ["Lobby"]
    assert [option["id"] for option in tree[0]["options"]] == ["9"]


def test_the_current_value_survives_a_filter_that_rejects_it():
    # A stored/legacy value fed to a filtered dropdown must never vanish — it
    # renders BLANK and reads as unset. Fixed twice before; pinned here.
    tree = run_node(GROUPS + 'console.log(JSON.stringify('
                    'visibleGroups(groups, (id) => id === "9", "8")));')
    assert [group["label"] for group in tree] == ["Lobby", "Basement"]
    assert [option["id"] for option in tree[1]["options"]] == ["8"]


def test_filtering_does_not_mutate_the_caller_s_groups():
    tree = run_node(GROUPS
                    + 'visibleGroups(groups, () => false, null);\n'
                    + 'console.log(JSON.stringify(groups.map((g) => g.options.length)));')
    assert tree == [2, 1]

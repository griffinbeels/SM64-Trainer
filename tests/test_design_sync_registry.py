"""The design-system registry has to keep telling the truth about the UI.

`.design-sync/components.mjs` is the one place that names what we publish to
Claude Design, and its `props` list is what the design agent codes against —
it is inlined into that agent's prompt as a TypeScript contract. Nothing in
the sync pipeline reads the components' real signatures, so a prop renamed or
removed in `ui/` leaves the contract quietly wrong: the agent keeps writing
the old prop, every design it builds is subtly broken, and the sync stays
green because the components still mount.

This is the check that fails instead. It compares each row's declared prop
names against the component's actual destructured signature. A component that
does not destructure declares `propsCheck.skip` with the reason.

Prove it has teeth by mutation: rename a prop in one of the registry's rows,
watch this go red, revert.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
REGISTRY = REPO / ".design-sync" / "components.mjs"
UI = REPO / "src" / "sm64_events" / "ui"

pytestmark = [
    pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH"),
    pytest.mark.skipif(not REGISTRY.exists(), reason="no design-sync registry in this checkout"),
]


def load_registry() -> list[dict]:
    """Read the registry through node — it is the module's own reader."""
    script = (
        f"const m = await import({json.dumps(REGISTRY.as_uri())});"
        "process.stdout.write(JSON.stringify(m.COMPONENTS.map((c) => ({"
        "  name: c.name, module: c.module, export: c.export || c.name,"
        "  props: c.props.map((p) => p[0]), propsCheck: c.propsCheck || null,"
        "}))));"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-"],
        input=script, capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, f"reading the registry failed:\n{result.stderr}"
    return json.loads(result.stdout)


# `export function Name({ a, b = 1, c: renamed })` and the arrow-const form.
SIGNATURE = r"export\s+(?:function\s+{name}\s*\(|const\s+{name}\s*=\s*\(?)\s*\{{(?P<body>[^}}]*)\}}"


def destructured_props(source: str, export_name: str) -> set[str] | None:
    """The prop names a component actually destructures, or None if it doesn't."""
    match = re.search(SIGNATURE.format(name=re.escape(export_name)), source)
    if not match:
        return None
    names = set()
    depth = 0
    current = ""
    for char in match.group("body") + ",":
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        if char == "," and depth == 0:
            piece = current.strip()
            current = ""
            if not piece:
                continue
            # `name = default`, `name: renamed`, `...rest`
            piece = piece.split("=", 1)[0].split(":", 1)[0].strip()
            if piece.startswith("..."):
                continue
            if piece:
                names.add(piece)
        else:
            current += char
    return names


@pytest.mark.parametrize("row", load_registry(), ids=lambda row: row["name"])
def test_declared_props_match_the_component(row):
    module_path = UI / row["module"].removeprefix("./")
    assert module_path.exists(), (
        f"{row['name']}: the registry points at {row['module']}, which does not exist"
    )
    declared = {name.rstrip("?") for name in row["props"]}
    actual = destructured_props(module_path.read_text(encoding="utf-8"), row["export"])

    skip = (row["propsCheck"] or {}).get("skip")
    if actual is None:
        assert skip, (
            f"{row['name']} does not destructure its props, so this contract cannot be "
            f"checked. Add propsCheck: {{skip: \"<why>\"}} to its registry row."
        )
        return
    if skip:
        # A skip that stopped being needed is a check we silently gave up.
        assert declared != actual, (
            f"{row['name']} declares propsCheck.skip ({skip!r}) but its props now match "
            f"the source exactly — drop the skip and let the guard do its job."
        )
        return

    missing = actual - declared
    invented = declared - actual
    assert not missing and not invented, (
        f"{row['name']}'s contract has drifted from {row['module']}.\n"
        f"  in the component, missing from the registry: {sorted(missing) or 'none'}\n"
        f"  in the registry, gone from the component:    {sorted(invented) or 'none'}\n"
        "The design agent codes against the registry, so fix the row (or the component)."
    )

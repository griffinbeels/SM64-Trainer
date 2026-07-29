"""Render the real app across every declared breakpoint; fail on any defect.

The machinery lives in `uilab` now — the driver, the probes, the matrix
derivation and the gates — shared with every project on this machine and
improved in one place. What is left here is this project's own POLICY, which is
`tools/uilab_project.py`, plus the defects we have agreed to owe.

Why the sweep may not skip itself once uilab IS installed: a gate that goes
green when its dependency is missing is green forever and indistinguishable
from one that passed. UILAB_SKIP=1 turns it off as a visible decision someone
made.

uilab NOT BEING INSTALLED AT ALL is a different case, and it is the one a
stranger hits. It is an optional machine-level dev module installed from a
local checkout, so `uv sync` on a fresh clone cannot supply it — and a bare
`import uilab` at module scope does not fail this file, it ABORTS COLLECTION
for the whole suite. Measured on a clean clone of c098b4a: `uv run pytest -q`
ran zero of 2,682 tests and exited on `ModuleNotFoundError: No module named
'uilab'`. Skipping this module instead costs a contributor nothing and gives
them the other 2,678 tests.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

def _find_uilab() -> str | None:
    """Make uilab importable, or return why it is not.

    An editable install alone is NOT enough, and that is the whole reason this
    function exists. uilab is not in this project's lockfile — it cannot be,
    since it is installed from a local checkout — so `uv sync`, which `uv run`
    performs implicitly, PRUNES it. Measured 2026-07-28: `import uilab`
    succeeded, then a later `uv run pytest` removed the package and the layout
    gate silently became a skip. A gate that disappears when a package manager
    tidies up is exactly the green-forever failure the rest of this file is
    written to avoid.

    So resolve it by PATH, which no sync can undo: an installed copy if one is
    there, else a sibling checkout, else `UILAB_PATH`. A stranger with none of
    those gets a clean skip and the other 2,678 tests.

    "Sibling" has to be computed from the REPO, not from this file, because
    this project's own rule is that every branch is a git worktree under
    `.claude/worktrees/<slug>` — two levels deeper, where the sibling of the
    tree is `.claude/worktrees/uilab` and does not exist. Resolving from the
    file alone made the gate skip in every worktree while passing in the
    primary checkout: the same green-forever failure this function was written
    to fix, one directory level along (measured 2026-07-28).
    """
    import importlib.util

    if importlib.util.find_spec("uilab") is not None:
        return None
    here = Path(__file__).resolve()
    repo = here.parents[1]
    # Climb out of `.claude/worktrees/<slug>` to the checkout it belongs to.
    for parent in repo.parents:
        if parent.name == "worktrees" and parent.parent.name == ".claude":
            repo = parent.parent.parent
            break
    candidates = []
    if os.environ.get("UILAB_PATH"):
        candidates.append(Path(os.environ["UILAB_PATH"]))
    candidates.append(repo.parent / "uilab")
    for candidate in candidates:
        if (candidate / "uilab" / "__init__.py").exists():
            sys.path.insert(0, str(candidate))
            return None
    return (
        "uilab not found. It is an optional dev module shared across this "
        "machine's projects (https://github.com/griffinbeels/uilab); a fresh "
        "clone will not have it, and everything except the rendered layout "
        "gates runs without it. To enable them, clone it beside this repo (or "
        "set UILAB_PATH) and install its browser:\n"
        "    git clone https://github.com/griffinbeels/uilab\n"
        "    uv run python -m playwright install chromium\n"
        f"looked in: {', '.join(str(c) for c in candidates)}")


_MISSING = _find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab.pytest_plugin import (  # noqa: E402,F401
    assert_components_use_container_queries, assert_no_new_defects,
    assert_no_stale_exemptions, uilab_sweep)
from uilab_project import PROJECT  # noqa: E402

# `uilab_sweep` is IMPORTED, not inherited from the pytest11 entry point, and
# that is deliberate: an entry point only exists for an installed package, and
# `uv sync` prunes the editable install because it is absent from the lockfile.
# Importing the fixture makes the gate depend on uilab being on sys.path and
# nothing else, which is the one property a package manager cannot revoke.

# The plugin's `uilab_sweep` fixture reads this off the module.
uilab_project = PROJECT


def test_the_sweep_is_not_silently_disabled():
    """UILAB_SKIP is for a machine without a browser, and saying so out loud is
    the point — the alternative is a suite that quietly stops checking."""
    if os.environ.get("UILAB_SKIP") == "1":
        pytest.skip("UILAB_SKIP=1 — layout sweep deliberately disabled")


def test_no_layout_defects_across_the_matrix(uilab_sweep):
    assert_no_new_defects(PROJECT, uilab_sweep)


def test_the_known_defect_list_does_not_outlive_its_defects(uilab_sweep):
    """A stale exemption is a lie about what is broken, and the list stops
    meaning anything the moment one is allowed to sit there."""
    assert_no_stale_exemptions(PROJECT, uilab_sweep)


def test_component_layout_gates_on_the_container():
    """`@media` is for the shell; component layout gates on `@container` against
    its own pane. Needs no browser, so it runs even when the sweep is off."""
    assert_components_use_container_queries(PROJECT)

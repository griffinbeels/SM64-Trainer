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

# The resolver lives in `tools/find_uilab.py` — `tools/measure_objective_card.py`
# needs the identical logic, and two copies of "where is uilab" would drift into
# one of them skipping while the other runs.
from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab import sweep  # noqa: E402
from uilab.pytest_plugin import (  # noqa: E402,F401
    assert_components_use_container_queries,
    assert_exemptions_name_live_viewports, assert_no_new_defects,
    assert_no_stale_exemptions, uilab_sweep_at)
from uilab_project import PROJECT  # noqa: E402

# `uilab_sweep_at` is IMPORTED, not inherited from the pytest11 entry point, and
# that is deliberate: an entry point only exists for an installed package, and
# `uv sync` prunes the editable install because it is absent from the lockfile.
# Importing the fixture makes the gate depend on uilab being on sys.path and
# nothing else, which is the one property a package manager cannot revoke.

# The plugin's `uilab_sweep_at` fixture reads this off the module.
uilab_project = PROJECT


def test_the_sweep_is_not_silently_disabled():
    """UILAB_SKIP is for a machine without a browser, and saying so out loud is
    the point — the alternative is a suite that quietly stops checking."""
    if os.environ.get("UILAB_SKIP") == "1":
        pytest.skip("UILAB_SKIP=1 — layout sweep deliberately disabled")


@pytest.mark.spread
@pytest.mark.parametrize("viewport", sweep.derived_matrix(PROJECT),
                         ids=sweep.viewport_key)
def test_no_layout_defects_at_each_viewport(uilab_sweep_at, viewport):
    """One case per viewport, each its own worker group (`spread`): the
    whole-matrix sweep was ONE test -- every viewport x every story with a
    320 ms settle before each probe, 179 s for the main page -- and the floor
    under the parallel suite (2026-09-01). A stale exemption is a lie about
    what is broken, and the list stops meaning anything the moment one is
    allowed to sit there, so each case also judges the exemptions naming its
    own viewport."""
    result = uilab_sweep_at(viewport)
    assert_no_new_defects(PROJECT, result)
    assert_no_stale_exemptions(PROJECT, result, viewport=viewport)


def test_every_exemption_names_a_viewport_in_the_matrix():
    """The one row shape no per-viewport case can reach. Browser-free."""
    assert_exemptions_name_live_viewports(PROJECT)


def test_component_layout_gates_on_the_container():
    """`@media` is for the shell; component layout gates on `@container` against
    its own pane. Needs no browser, so it runs even when the sweep is off."""
    assert_components_use_container_queries(PROJECT)

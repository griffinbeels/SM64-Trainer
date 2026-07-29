"""Render the real app across every declared breakpoint; fail on any defect.

The machinery lives in `uilab` now — the driver, the probes, the matrix
derivation and the gates — shared with every project on this machine and
improved in one place. What is left here is this project's own POLICY, which is
`tools/uilab_project.py`, plus the defects we have agreed to owe.

Why the sweep may not skip itself: a gate that goes green when its dependency is
missing is green forever and indistinguishable from one that passed. UILAB_SKIP=1
turns it off as a visible decision someone made.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from uilab.pytest_plugin import (  # noqa: E402
    assert_components_use_container_queries, assert_no_new_defects,
    assert_no_stale_exemptions)
from uilab_project import PROJECT  # noqa: E402

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

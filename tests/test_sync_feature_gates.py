"""Every detector event kind must have a feature gate, and a gate must
actually verify over a stream that produces its event and fail over one that
never does. Everything here runs offline -- a hand-built GameSnapshot stream
through the REAL detector stack (sync.stack.DetectorRun), no emulator.

FEATURE_GATES (not the shared sync.gates.GATES list) is what these tests
address, because other test files in this suite clear and repopulate that
shared global -- see feature_gates.py's own module docstring."""
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.memory.addresses import ACT_DISAPPEARED
from sm64_events.sync import feature_gates as FG
from sm64_events.sync.gates import FEATURES, KINDS

DETECTORS_DIR = (Path(__file__).resolve().parents[1] / "src" / "sm64_events"
                 / "detectors")

# The full Task 9 gate-id vocabulary a feature gate's `needs` may draw from
# (address/behaviour/version gates, owned by a sibling worktree) -- used only
# to sanity-check well-formedness when the real registry is not fully wired
# up in THIS worktree yet.
_KNOWN_TASK9_IDS = {
    "version.rom", "address.global_timer", "address.mario_struct",
    "address.mario_object", "address.object_pool", "address.object_pool.confirm",
    "behaviour.base", "behaviour.door", "address.curr_level",
    "address.curr_area", "address.last_completed_course",
    "address.last_completed_star", "address.pending_warp_op",
    "address.delayed_warp_timer", "address.warp_dest", "address.hud_timer",
    "address.hud_timer_running", "address.usamune_overall",
    "address.usamune_star_result", "address.usamune_timer",
}


def _detector_event_types() -> set[str]:
    types: set[str] = set()
    for path in DETECTORS_DIR.glob("*.py"):
        types.update(re.findall(r'type="([a-z_]+)"', path.read_text(encoding="utf-8")))
    return types


def test_every_detector_event_type_has_a_feature_gate():
    types = _detector_event_types()
    assert types, "the grep found nothing -- did detectors move?"
    gate_ids = [gate.id for gate in FG.FEATURE_GATES]
    missing = [type_ for type_ in types
              if not any(gate_id.startswith(f"feature.{type_}")
                        for gate_id in gate_ids)]
    assert not missing, f"no feature gate for: {missing}"


def test_every_feature_gate_is_well_formed():
    for gate in FG.FEATURE_GATES:
        assert gate.id.startswith("feature.")
        assert gate.feature in FEATURES
        assert gate.kind in KINDS
        assert gate.instruction.strip()
        assert gate.proves.strip()
        assert set(gate.needs) <= _KNOWN_TASK9_IDS, gate.id


def test_the_gate_ids_are_unique():
    ids = [gate.id for gate in FG.FEATURE_GATES]
    assert len(ids) == len(set(ids))


def test_the_full_registry_orders_when_every_track_is_present():
    """Skips cleanly when address/calibration gates are not yet filled in by
    a sibling worktree -- this repo's own convention for a cross-track test
    (see the module docstring)."""
    from sm64_events.sync import registry
    try:
        ordered = registry.ordered()
    except ValueError as exc:
        pytest.skip(f"other tracks not present in this worktree yet: {exc}")
    ordered_ids = {gate.id for gate in ordered}
    for gate in FG.FEATURE_GATES:
        assert gate.id in ordered_ids


# --- one gate driven end to end over a scripted stream ----------------------

ACT_IDLE = 0x0C400201


def _snap(**overrides) -> GameSnapshot:
    defaults = dict(
        wall_time_utc=datetime(2026, 8, 15, tzinfo=timezone.utc),
        global_timer=2000, mario_action=ACT_IDLE, mario_action_timer=0,
        num_stars=8, last_completed_course=1, last_completed_star=1,
        curr_level=6, curr_area=1)
    defaults.update(overrides)
    return GameSnapshot(**defaults)


def _dest(level: int, type_: int = 1, area: int = 1, node: int = 10) -> dict:
    return dict(warp_dest_type=type_, warp_dest_level=level,
               warp_dest_area=area, warp_dest_node=node)


def _bitdw_touch_stream() -> list[GameSnapshot]:
    """A painting-shaped touch (destination already written) naming BitDW --
    good enough to prove a warp_entered with to=17 reaches the gate; the real
    pipe-timing shape is tests/test_warp.py's job, not this one's."""
    return [
        _snap(global_timer=1999, curr_level=6, **_dest(level=6, node=31)),
        _snap(global_timer=2000, curr_level=6, **_dest(level=6, node=31)),
        _snap(global_timer=2001, curr_level=6, mario_action=ACT_DISAPPEARED,
             **_dest(level=17)),
    ]


def _no_warp_stream() -> list[GameSnapshot]:
    return [_snap(global_timer=2000 + i, curr_level=6) for i in range(5)]


class _FakeContext:
    """The minimum a feature check actually touches: version, timeout_s, and
    a snapshot iterator that returns instantly (no wall-clock delay) -- the
    real timed GateContext lives in sync/runner.py and is Track E's other
    file, not this one's concern."""

    def __init__(self, version: str, snapshots: list[GameSnapshot],
                timeout_s: float = 5.0):
        self.version = version
        self.timeout_s = timeout_s
        self._snapshots = snapshots

    def snapshots(self, seconds: float):
        return iter(self._snapshots)


def _gate(gate_id: str):
    return next(gate for gate in FG.FEATURE_GATES if gate.id == gate_id)


def test_the_bitdw_pipe_gate_verifies_over_a_stream_that_yields_the_warp():
    gate = _gate("feature.warp_entered.pipe")
    verdict = gate.check(_FakeContext("us", _bitdw_touch_stream()))
    assert verdict.status == "verified"
    assert verdict.frames == 2001


def test_the_bitdw_pipe_gate_fails_when_no_warp_ever_fires():
    gate = _gate("feature.warp_entered.pipe")
    verdict = gate.check(_FakeContext("us", _no_warp_stream()))
    assert verdict.status == "failed"
    assert "timed out" in verdict.evidence


def test_the_painting_gate_only_matches_its_own_destination():
    """The pipe stream names BitDW (17), not WF (24) -- the painting gate
    must not accept it."""
    gate = _gate("feature.warp_entered.painting")
    verdict = gate.check(_FakeContext("us", _bitdw_touch_stream()))
    assert verdict.status == "failed"

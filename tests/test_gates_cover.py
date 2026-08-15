"""Every version-dependent thing has a gate. This is the "stay in sync" law.

His workflow (2026-08-15): develop on US, then "the LAST STEP is syncing it up
with the other region" by running one script. That only works if a new
address, a new detector event or a newly measured constant cannot land
without a step in that script — so:

  1. every `LAYOUT_ROWS` field has an `address.<field>` gate;
  2. every event type a shipped detector emits has a `feature.<type>*` gate;
  3. every calibration gate's `backs` resolves by import to a real constant;
  4. every gate's `needs` name real gates and the graph has no cycle
     (`ordered()` runs), and every gate names a feature and a kind the
     dashboard knows.

MUTATION PROOF (do it, do not trust it): comment out one `register(...)` in
`sync/address_gates.py`, run this file, watch (1) go red, restore. Delete a
`feature.death` gate, watch (2). Misspell a `backs`, watch (3).
"""
import re
from pathlib import Path

from sm64_events.memory.layout import LAYOUT_ROWS
from sm64_events.sync import registry
from sm64_events.sync.checks import resolve_backs
from sm64_events.sync.gates import FEATURES, KINDS, gate_id_for_field

REPO = Path(__file__).resolve().parents[1]
DETECTORS = REPO / "src" / "sm64_events" / "detectors"


def _gate_ids() -> set[str]:
    return {gate.id for gate in registry.GATES}


def test_the_registry_is_not_empty_and_orders():
    assert registry.GATES, "no gates registered -- did the gate modules import?"
    ordered = registry.ordered()             # raises on unknown need / cycle
    assert len(ordered) == len(registry.GATES)
    for gate in registry.GATES:
        assert gate.feature in FEATURES and gate.kind in KINDS, gate.id


def test_every_layout_row_has_an_address_gate():
    ids = _gate_ids()
    missing = [row.field for row in LAYOUT_ROWS
               if gate_id_for_field(row.field) not in ids]
    assert not missing, (
        f"layout rows with no gate: {missing} -- a JP run could never "
        "verify them, so add a gate in sync/address_gates.py")


def _emitted_event_types() -> set[str]:
    types = set()
    for path in DETECTORS.glob("*.py"):
        types.update(re.findall(r'type="([a-z_]+)"', path.read_text(encoding="utf-8")))
    return types


def test_every_detector_event_type_has_a_feature_gate():
    ids = _gate_ids()
    emitted = _emitted_event_types()
    assert emitted, "found no type=\"...\" in the detectors -- the scan is broken"
    missing = sorted(t for t in emitted
                     if not any(i == f"feature.{t}" or i.startswith(f"feature.{t}.")
                                for i in ids))
    assert not missing, (
        f"detector event types with no feature gate: {missing} -- add one in "
        "sync/feature_gates.py so JP parity for that feature is measurable")


def test_every_calibration_gate_backs_a_constant_that_exists():
    """Through the SAME resolver the gates use (sync/checks.py::resolve_backs),
    so a `backs` this test accepts is one the check itself can read."""
    broken = []
    for gate in registry.GATES:
        if gate.kind != "calibration":
            continue
        if not gate.backs:
            broken.append(f"{gate.id}: no backs")
            continue
        try:
            resolve_backs(gate.backs)
        except (ImportError, AttributeError) as error:
            broken.append(f"{gate.id}: {gate.backs} does not resolve ({error})")
    assert not broken, broken


def test_gate_ids_are_namespaced_by_kind():
    prefixes = {"address": "address.", "behaviour": "behaviour.",
                "calibration": "cal.", "feature": "feature."}
    off = [g.id for g in registry.GATES
           if not g.id.startswith(prefixes[g.kind]) and g.id != "version.rom"]
    assert not off, f"gate ids must be namespaced by kind: {off}"

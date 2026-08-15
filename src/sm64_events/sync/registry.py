"""Import every gate module so `sync.gates.GATES` is complete from ONE import.

A gate registers itself when its module is imported; the runner, the sync
API and tests/test_gates_cover.py all import THIS so none of them can see a
partial registry. Adding a gate module is one line here."""
from sm64_events.sync import (address_gates, calibration_gates,  # noqa: F401
                              feature_gates)
from sm64_events.sync.gates import GATES, FEATURES, ordered, by_feature, gate, as_json  # noqa: F401

"""Test harness only: reuse the rank calibration an app build prepares.

Every in-process app a test builds prepares its rank calibration
(`library/calibration.py::prepare`) from the same bundled Library, the same
freshly seeded definitions, the same standards seed and the shipped policy --
the Overall refit for every target, identical in every test of a worker and
most of what an app build costs. `reusing_calibrations()` turns a repeat of
that preparation into a lookup while an app is being built, and only then: a
test body's own recalibrations run the real preparation.

Isolation is the contract. The memo keeps a private deep copy of each result
and hands every app a deep copy of that, so nothing a test does to its own
calibration or Library payload reaches another test; the first build keeps
the original. The key is every argument `prepare` receives, the payload by
content, so a different Library, assignment, definition, standards input or
policy is a miss. One field is left out: a definition's `created_utc`, the
wall clock at insertion, which no calibration reads and which differs between
two otherwise identical fresh databases. While a test has replaced `prepare`
itself (to count or fail it), the memo steps aside.
"""
from collections import OrderedDict
from contextlib import contextmanager
from copy import deepcopy
from threading import RLock

import sm64_events.library.calibration as _calibration
from sm64_events.ranks.calibration import fingerprint

_GENUINE = _calibration.prepare
# Each entry holds a whole Library payload (~12 MiB); a worker needs one per
# builder that differs (make_client, the ranks API's, the UI fixture's).
MAXSIZE = 3
_kept = OrderedDict()
_lock = RLock()
stats = {"hits": 0, "misses": 0}


def _key(payload, assignments, definitions, standards, policy):
    return fingerprint([fingerprint(payload), assignments,
                        [{field: value for field, value in definition.items()
                          if field != "created_utc"} for definition in definitions],
                        standards.calibration_inputs(), policy.revision])


def _reusing_prepare(payload, assignments, definitions, standards, policy):
    key = _key(payload, assignments, definitions, standards, policy)
    with _lock:
        kept = _kept.get(key)
        if kept is not None:
            _kept.move_to_end(key)
            stats["hits"] += 1
    if kept is not None:
        return deepcopy(kept)
    result = _GENUINE(payload, assignments, definitions, standards, policy)
    with _lock:
        stats["misses"] += 1
        _kept[key] = deepcopy(result)
        while len(_kept) > MAXSIZE:
            _kept.popitem(last=False)
    return result


@contextmanager
def reusing_calibrations():
    """Serve identical preparations from the memo for the duration."""
    if _calibration.prepare is not _GENUINE:
        yield
        return
    _calibration.prepare = _reusing_prepare
    try:
        yield
    finally:
        if _calibration.prepare is _reusing_prepare:
            _calibration.prepare = _GENUINE


def forget():
    """Drop every kept calibration (tests of this module start clean)."""
    with _lock:
        _kept.clear()
        stats.update(hits=0, misses=0)

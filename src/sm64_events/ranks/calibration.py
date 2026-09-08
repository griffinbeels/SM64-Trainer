"""One effective revision for observations, generated ranks, and assignments.

Build a complete value before publishing it. Request readers pin that value,
so a concurrent refresh cannot splice new observations into old standards.
User edits remain in RankStandards and participate in its effective fingerprint.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from threading import RLock

from sm64_events.ranks.curve_types import CompiledCurve


def fingerprint(value) -> str:
    """Deterministic identity; invalid numerical data must never be published."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


_DERIVED_FIELDS = frozenset({"fetched_at", "ladder", "ladder_jp", "ladder_model",
                             "ladder_samples", "ladder_estimate", "matching_profile"})


def observation_fingerprint(payload: dict) -> str:
    """Detect corrected observations even when the Sheet Log date is unchanged."""
    def source(value):
        if isinstance(value, dict):
            return {key: source(item) for key, item in value.items()
                    if key not in _DERIVED_FIELDS}
        if isinstance(value, list):
            return [source(item) for item in value]
        return value
    return fingerprint(source(payload))


@dataclass(frozen=True)
class Calibration:
    """Owned snapshot: nested values are copied on construction, then read only."""
    revision: str
    data_revision: str
    payload: dict
    rows: dict
    layers: dict
    overall: dict[str, dict[str, CompiledCurve]]
    identities: dict[str, str]
    policy_revision: str


def build_calibration(payload, rows, layers, overall, identities, policy_revision=""):
    """Detach a candidate from mutable fitter inputs before it becomes visible."""
    data_revision = observation_fingerprint(payload)
    content = deepcopy({"rows": rows, "layers": layers, "overall": overall,
                        "identities": identities, "policy_revision": policy_revision})
    revision = fingerprint({"schema": 1, "observations": data_revision, **content})
    return Calibration(revision, data_revision, deepcopy(payload), **content)


class CalibrationRegistry:
    """Single publication point shared by the library and standards façade."""

    def __init__(self):
        self._active = None
        self._pinned = ContextVar(f"rank_calibration_{id(self)}", default=None)
        self.update_lock = RLock()

    @property
    def active(self) -> Calibration | None:
        return self._active

    @property
    def read(self) -> Calibration | None:
        return self._pinned.get() or self._active

    @property
    def pinned(self) -> Calibration | None:
        return self._pinned.get()

    def publish(self, candidate: Calibration) -> None:
        if not isinstance(candidate, Calibration):
            raise TypeError("publish requires a complete Calibration")
        self._active = candidate

    @contextmanager
    def pin(self, *, current=False):
        """One request reads one revision; regrading can explicitly read the new one."""
        value = self._active if current else self.read
        token = self._pinned.set(value)
        try:
            yield value
        finally:
            self._pinned.reset(token)


def resolve_curve(ranks_store, entity_key, version=None) -> CompiledCurve:
    """Overall access for production and legacy embedders with a ladder-only store."""
    from sm64_events.ranks import curves, scoring
    resolver = getattr(ranks_store, "overall_curve", None)
    if resolver is not None:
        return resolver(entity_key, version=version)
    ladders = (ranks_store.ladders(entity_key) if version is None
               else ranks_store.ladders(entity_key, version))
    return curves.from_ladder(scoring.best_ladder(ladders),
                              metadata={"source": "legacy", "estimated": True})

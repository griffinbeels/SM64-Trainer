"""Validated, independent tuning namespaces for strategy, Overall and routes.

Configuration selects named pure models, never executable formulas. Scoped
patches apply by increasing specificity, then declaration order. The complete
policy revision invalidates calibration; effective revisions identify precisely
the settings used by one curve. This module deliberately does not import fitters.
"""
import hashlib
import json
import math
from copy import deepcopy
from pathlib import Path

POLICY_SCHEMA_VERSION = 1
MODEL_NAMES = {
    "strategy": frozenset({"empirical"}),
    "overall": frozenset({"family_milestones", "community"}),
    "route": frozenset({"required_slots"}),
}
_POLICY_PATH = Path(__file__).resolve().parents[1] / "data" / "ranking_policy.json"


def _fingerprint(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _merge(base, patch):
    result = deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        elif key == "patches" and isinstance(value, list) and isinstance(result.get(key), list):
            # A temporary target adjustment must not erase other targets'
            # reviewed family mappings. Passing the unchanged full shipped
            # policy is also a no-op, including its revision fingerprint.
            if value != result[key]:
                result[key].extend(deepcopy(value))
        else:
            result[key] = deepcopy(value)
    return result


def _number(value, name, low, high=None, integer=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < low
            or (high is not None and value > high)
            or (integer and value != int(value))):
        raise ValueError(f"{name} must be a finite {'integer' if integer else 'number'}"
                         f" from {low}" + (f" to {high}" if high is not None else ""))


def _validate_fit(settings):
    expected = {"percentiles", "peak_window_frames", "peak_min_entries",
                "peak_density_ratio"}
    if not isinstance(settings, dict) or set(settings) != expected:
        raise ValueError(f"fit settings require exactly {sorted(expected)}")
    ranks = ("Mario", "Grandmaster", "Master", "Diamond", "Platinum", "Gold",
             "Silver", "Bronze")
    percentiles = settings["percentiles"]
    if not isinstance(percentiles, dict) or set(percentiles) != set(ranks):
        raise ValueError("percentiles must define all eight scored tiers")
    previous = -1
    for rank in ranks:
        value = percentiles[rank]
        _number(value, f"percentiles.{rank}", 0, 100)
        if value <= previous:
            raise ValueError("fit percentiles must increase from Mario to Bronze")
        previous = value
    for key in ("peak_window_frames", "peak_min_entries"):
        _number(settings[key], key, 1, integer=True)
    _number(settings["peak_density_ratio"], "peak_density_ratio", 1)


def _validate_families(families):
    if not isinstance(families, list):
        raise ValueError("families must be a list of named row mappings")
    seen_ids, seen_rows = set(), set()
    for family in families:
        if not isinstance(family, dict) or set(family) != {"id", "label", "rows"}:
            raise ValueError("each family requires id, label and rows")
        for key in ("id", "label"):
            if not isinstance(family[key], str) or not family[key].strip():
                raise ValueError(f"family {key} must be a nonempty string")
        if family["id"] in seen_ids:
            raise ValueError(f"duplicate family id: {family['id']}")
        seen_ids.add(family["id"])
        if not isinstance(family["rows"], list) or not family["rows"]:
            raise ValueError("family rows must contain semantic source names or row keys")
        for row in family["rows"]:
            if not isinstance(row, str) or not row.strip():
                raise ValueError("family rows must use semantic strings, never row indices")
            if row in seen_rows:
                raise ValueError(f"row belongs to multiple family mappings: {row}")
            seen_rows.add(row)


def _validate_settings(layer, settings):
    if (not isinstance(settings, dict) or not isinstance(settings.get("model"), str)
            or settings["model"] not in MODEL_NAMES[layer]):
        raise ValueError(f"unknown {layer} model; choose {sorted(MODEL_NAMES[layer])}")
    if layer == "strategy":
        _validate_fit({key: value for key, value in settings.items() if key != "model"})
    elif layer == "overall":
        expected = {"model", "milestone_weight", "slow_quantile", "fast_quantile",
                    "overlap_ratio", "confidence_runners", "unannotated_region",
                    "community_fit", "families"}
        if set(settings) != expected:
            raise ValueError(f"Overall settings require exactly {sorted(expected)}")
        for key in ("milestone_weight", "slow_quantile", "fast_quantile", "overlap_ratio"):
            _number(settings[key], key, 0, 1)
        if settings["fast_quantile"] >= settings["slow_quantile"]:
            raise ValueError("fast_quantile must be below slow_quantile")
        _number(settings["confidence_runners"], "confidence_runners", 1, integer=True)
        if settings["unannotated_region"] not in ("both", "exclude"):
            raise ValueError("unannotated_region must be both or exclude")
        _validate_fit(settings["community_fit"])
        _validate_families(settings["families"])
    else:
        if set(settings) != {"model", "weights"} or not isinstance(settings["weights"], dict):
            raise ValueError("route settings require model and target weights")
        if settings["weights"]:
            raise ValueError("required_slots uses equal weights; custom route weights are not supported")


class RankingPolicy:
    """Shipped defaults with optional JSON-shaped patches; no global mutation.

    ``data`` may override only the namespaces needed by a comparison or user
    setting. Supplied patches append after shipped patches, preserving unrelated
    targets; a target patch with families=[] explicitly clears its family map.
    Selectors are target_id, version and (strategy layer only) strategy. An empty
    selector applies to every target in its own layer.
    """

    def __init__(self, data=None):
        shipped = json.loads(_POLICY_PATH.read_text(encoding="utf-8"))
        if data is not None and not isinstance(data, dict):
            raise ValueError("ranking policy must be an object")
        self._data = _merge(shipped, data or {})
        if set(self._data) != {"schema_version", "layers"}:
            raise ValueError("ranking policy requires schema_version and layers")
        if (type(self._data["schema_version"]) is not int
                or self._data["schema_version"] != POLICY_SCHEMA_VERSION):
            raise ValueError("unsupported ranking policy schema_version")
        if not isinstance(self._data["layers"], dict) or set(self._data["layers"]) != set(MODEL_NAMES):
            raise ValueError("policy layers must be strategy, overall and route")
        for layer, config in self._data["layers"].items():
            if not isinstance(config, dict) or set(config) != {"defaults", "patches"}:
                raise ValueError(f"{layer} requires defaults and patches")
            _validate_settings(layer, config["defaults"])
            if not isinstance(config["patches"], list):
                raise ValueError(f"{layer} patches must be a list")
            for patch in config["patches"]:
                self._validate_patch(layer, config["defaults"], patch)
        self.revision = _fingerprint(self._data)

    @staticmethod
    def _validate_patch(layer, defaults, patch):
        selectors = {"target_id", "version", "strategy"}
        if (not isinstance(patch, dict) or "parameters" not in patch
                or set(patch) - selectors - {"parameters"}
                or not isinstance(patch["parameters"], dict)):
            raise ValueError("patch requires parameters and optional target_id/version/strategy")
        for key in selectors & patch.keys():
            if not isinstance(patch[key], str) or not patch[key]:
                raise ValueError(f"patch {key} must be a nonempty string")
        if "version" in patch and patch["version"] not in ("us", "jp"):
            raise ValueError("patch version must be us or jp")
        if "strategy" in patch and layer != "strategy":
            raise ValueError("strategy selectors belong only to the strategy layer")
        _validate_settings(layer, _merge(defaults, patch["parameters"]))

    def resolve(self, target_id, version="us", strategy=None, layer="overall"):
        """Return a detached validated settings record for exactly one layer."""
        if layer not in MODEL_NAMES:
            raise ValueError(f"unknown ranking layer: {layer}")
        if version not in ("us", "jp"):
            raise ValueError("ranking version must be us or jp")
        if not isinstance(target_id, str):
            raise ValueError("target_id must be a stable string")
        config = self._data["layers"][layer]
        settings = deepcopy(config["defaults"])
        context = {"target_id": target_id, "version": version, "strategy": strategy}
        patches = sorted(config["patches"], key=lambda patch: len(patch))
        for patch in patches:
            if all(context[key] == value for key, value in patch.items() if key != "parameters"):
                settings = _merge(settings, patch["parameters"])
        _validate_settings(layer, settings)
        return settings

    def effective_revision(self, target_id, version="us", strategy=None, layer="overall"):
        return _fingerprint(self.resolve(target_id, version, strategy, layer))

"""THE stat registry: adding a stat = adding one StatDef here. The UI's
stat menu renders from registry_meta(); nothing else changes anywhere.

Every compute() sees attempts already scoped to one star by the caller,
ordered by attempt id (chronological). Cleared attempts are excluded here,
in one place. fmt tells the UI how to render: time | percent | int."""
from dataclasses import dataclass, field
from typing import Callable, Sequence

from sm64_events.tracking.projection import Attempt

DEFAULT_FAILURES = ["reset", "hard_reset"]  # 'abandoned' excluded by default


def _live(attempts: Sequence[Attempt]) -> list[Attempt]:
    return [a for a in attempts if not a.cleared]


def _times(attempts: Sequence[Attempt], clock: str) -> list[int]:
    out = []
    for a in _live(attempts):
        if a.outcome != "success":
            continue
        v = a.igt_frames if clock == "igt" else a.rta_frames
        if v is not None:
            out.append(v)
    return out


def _avg_last_n(attempts, params, clock):
    times = _times(attempts, clock)[-int(params["n"]):]
    return sum(times) / len(times) if times else None


def _avg_lifetime(attempts, params, clock):
    times = _times(attempts, clock)
    return sum(times) / len(times) if times else None


def _best(attempts, params, clock):
    times = _times(attempts, clock)
    return min(times) if times else None


def _worst(attempts, params, clock):
    times = _times(attempts, clock)
    return max(times) if times else None


def _attempt_count(attempts, params, clock):
    return len([a for a in _live(attempts) if a.outcome == "success"])


def _success_rate(attempts, params, clock):
    failures = set(params.get("failures", DEFAULT_FAILURES))
    counted = [a for a in _live(attempts)
               if a.outcome == "success" or a.outcome in failures]
    if not counted:
        return None
    wins = sum(1 for a in counted if a.outcome == "success")
    return wins / len(counted)


@dataclass(frozen=True)
class StatDef:
    key: str
    label: str
    fmt: str                                   # time | percent | int
    compute: Callable[[Sequence[Attempt], dict, str], float | int | None]
    params: dict = field(default_factory=dict)  # defaults, UI-overridable


REGISTRY: dict[str, StatDef] = {d.key: d for d in [
    StatDef("avg_last_n", "Avg last N", "time", _avg_last_n, {"n": 10}),
    StatDef("avg_lifetime", "Lifetime avg", "time", _avg_lifetime),
    StatDef("best", "Best", "time", _best),
    StatDef("worst", "Worst", "time", _worst),
    StatDef("attempt_count", "Successes", "int", _attempt_count),
    StatDef("success_rate", "Success rate", "percent", _success_rate,
            {"failures": DEFAULT_FAILURES}),
]}

DEFAULT_STAT_MENU = [
    {"key": "avg_last_n", "params": {"n": 10}},
    {"key": "avg_last_n", "params": {"n": 50}},
    {"key": "best"}, {"key": "worst"}, {"key": "success_rate"},
]


def compute_stat(key: str, attempts: Sequence[Attempt], params: dict,
                 clock: str) -> float | int | None:
    d = REGISTRY[key]
    return d.compute(attempts, {**d.params, **(params or {})}, clock)


def registry_meta() -> list[dict]:
    return [{"key": d.key, "label": d.label, "fmt": d.fmt, "params": d.params}
            for d in REGISTRY.values()]

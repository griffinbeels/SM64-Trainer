"""THE stat registry: adding a stat = adding one StatDef here. The UI's
stat menu renders from registry_meta(); nothing else changes anywhere.

Every compute() sees attempts already scoped to one star by the caller,
ordered by attempt id (chronological). Cleared attempts are excluded here,
in one place. fmt tells the UI how to render: time | percent | int."""
import copy
from dataclasses import dataclass, field
from typing import Callable, Sequence

from sm64_events.tracking.projection import Attempt

DEFAULT_FAILURES = ["reset", "hard_reset", "death"]  # 'abandoned' excluded by default


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
    n = int(params["n"])
    if n <= 0:
        return None
    times = _times(attempts, clock)[-n:]
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


def _success_count(attempts, params, clock):
    return len([a for a in _live(attempts) if a.outcome == "success"])


def _success_rate(attempts, params, clock):
    failures = set(params.get("failures", DEFAULT_FAILURES))
    counted = [a for a in _live(attempts)
               if a.outcome == "success" or a.outcome in failures]
    if not counted:
        return None
    wins = sum(1 for a in counted if a.outcome == "success")
    return wins / len(counted)


def _dust_rate(total_attr: str, dustless_attr: str):
    """Dust-trick rate over pooled counts: tricks practiced during failed
    attempts still count. Returns a compute() for the given count fields."""
    def compute(attempts, params, clock):
        live = _live(attempts)
        total = sum(getattr(a, total_attr) for a in live)
        if total == 0:
            return None
        return sum(getattr(a, dustless_attr) for a in live) / total
    return compute


_dustless_rate = _dust_rate("rollouts_total", "rollouts_dustless")
_dustless_jump_rate = _dust_rate("jumps_total", "jumps_dustless")


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
    StatDef("success_count", "Successes", "int", _success_count),
    StatDef("success_rate", "Success rate", "percent", _success_rate,
            {"failures": DEFAULT_FAILURES}),
    StatDef("dustless_rate", "Dustless rollouts", "percent", _dustless_rate),
    StatDef("dustless_jump_rate", "Dustless jumps", "percent",
            _dustless_jump_rate),
]}

DEFAULT_STAT_MENU = [
    {"key": "avg_last_n", "params": {"n": 10}},
    {"key": "avg_last_n", "params": {"n": 50}},
    {"key": "best"}, {"key": "worst"}, {"key": "success_rate"},
]


def selection_id(key: str, params: dict | None) -> str:
    """Identity of a stat-menu selection. avg_last_n is parameterized by n
    (each N is its own chip); every other stat is identified by key alone —
    params only tune computation (e.g. success_rate's failures set) and must
    not create visually identical duplicate chips. f-string of n collapses
    int/str variants ("10" == 10)."""
    if key == "avg_last_n":
        return f"{key}:{(params or {}).get('n')}"
    return key


def selection_order(key: str, params: dict | None) -> tuple[int, int]:
    """Canonical stat-menu display order: REGISTRY insertion order, with
    avg_last_n variants sub-ordered by n — exactly the stats-menu offer
    order (statmenu.js), so chips and checkboxes always read the same way."""
    keys = list(REGISTRY)
    ki = keys.index(key) if key in REGISTRY else len(keys)
    try:
        n = int((params or {}).get("n") or 0)
    except (TypeError, ValueError):
        n = 0
    return (ki, n)


def compute_stat(key: str, attempts: Sequence[Attempt], params: dict,
                 clock: str) -> float | int | None:
    d = REGISTRY[key]
    return d.compute(attempts, {**d.params, **(params or {})}, clock)


def registry_meta() -> list[dict]:
    return [{"key": d.key, "label": d.label, "fmt": d.fmt,
             "params": copy.deepcopy(d.params)}
            for d in REGISTRY.values()]

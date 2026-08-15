"""The gate registry — one gate per version-dependent thing the tracker relies on.

A GATE is the unit of "does this work on this ROM": the instruction to the
human, the check over live memory, and what a pass proves. Four kinds:

  address      a RAM global (memory/layout.py row): map-derived candidate or a
               hunt, then a live contract ("ticks 30/s", "reads 24 in WF")
  behaviour    the behaviour segment base and one resolved symbol
  calibration  a MEASUREMENT beside the constant it backs — the JP number is
               reported next to the US constant, and a disagreement is a
               finding, never silently absorbed
  feature      the REAL detector stack over the version's layout: "do X,
               expect event K with payload P" — what "1:1 parity" means

`tools/sync_version.py` walks them in `needs` order and writes each verdict
into the sync report (`sync/report.py`); `/ui/sync.html` shows US beside JP.

WHY A REGISTRY AND NOT NINE PROBE SCRIPTS: the tests in
`tests/test_gates_cover.py` walk THIS list against the layout rows, the
detectors' event kinds and the calibration constants — so a new address, a
new event kind or a new measured constant without a gate is a red build.
That is the whole "stay in sync between versions" mechanism (his ask,
2026-08-15: develop on US, "the LAST STEP is syncing it up with the other
region", as "a single script that I have to run").

Gates REGISTER themselves by importing their module (`sync/address_gates.py`,
`calibration_gates.py`, `feature_gates.py`); `sync/registry.py` imports all
of them so a caller gets the full list from one import.
"""
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

FEATURES = ("version", "star grab", "IGT clock", "warps & entrances",
            "castle areas", "textboxes", "caused moments", "keys & Bowser",
            "death", "spawn & reset", "landmarks")
KINDS = ("address", "behaviour", "calibration", "feature")
STATUSES = ("verified", "failed", "candidate", "missing", "skipped")


@dataclass(frozen=True)
class Verdict:
    status: str
    value: int | None = None          # the address / base a pass established
    measured: dict | None = None      # a calibration's numbers, both sides
    evidence: str = ""                # one line a reader can check
    frames: int | None = None         # game frame the verdict was reached on

    def __post_init__(self):
        if self.status not in STATUSES:
            raise ValueError(f"unknown verdict status {self.status!r}")

    def as_json(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_json(raw: dict) -> "Verdict":
        return Verdict(status=raw["status"], value=raw.get("value"),
                       measured=raw.get("measured"),
                       evidence=raw.get("evidence", ""),
                       frames=raw.get("frames"))


@dataclass(frozen=True)
class Gate:
    id: str
    feature: str
    kind: str
    instruction: str          # what the human does, in his words
    proves: str               # one sentence: what a PASS establishes
    check: Callable           # (GateContext) -> Verdict
    needs: tuple[str, ...] = ()
    backs: str | None = None  # calibration: dotted name of the constant
    auto: bool = False        # no human step — the runner does not prompt
    timeout_s: float = 90.0

    def as_json(self) -> dict:
        return {"id": self.id, "feature": self.feature, "kind": self.kind,
                "instruction": self.instruction, "proves": self.proves,
                "needs": list(self.needs), "backs": self.backs,
                "auto": self.auto}


GATES: list[Gate] = []


def register(*gates: Gate) -> None:
    """Add gates to the registry. Loud on a bad feature, a bad kind, an empty
    instruction/proves, or a duplicate id — the tests that walk the registry
    depend on those never happening quietly."""
    known = {g.id for g in GATES}
    for gate in gates:
        if gate.feature not in FEATURES:
            raise ValueError(f"{gate.id}: feature {gate.feature!r} is not in FEATURES")
        if gate.kind not in KINDS:
            raise ValueError(f"{gate.id}: kind {gate.kind!r} is not in KINDS")
        if not gate.instruction.strip() or not gate.proves.strip():
            raise ValueError(f"{gate.id}: instruction and proves are required")
        if gate.id in known:
            raise ValueError(f"duplicate gate id {gate.id!r}")
        known.add(gate.id)
        GATES.append(gate)


def gate(gate_id: str) -> Gate:
    for candidate in GATES:
        if candidate.id == gate_id:
            return candidate
    raise KeyError(gate_id)


def ordered() -> list[Gate]:
    """Every gate, needs before dependants, registration order otherwise.
    ValueError on an unknown need or a cycle."""
    by_id = {g.id: g for g in GATES}
    for g in GATES:
        for need in g.needs:
            if need not in by_id:
                raise ValueError(f"{g.id} needs unknown gate {need!r}")
    remaining = {g.id: set(g.needs) for g in GATES}
    out: list[Gate] = []
    while remaining:
        ready = [g for g in GATES if g.id in remaining and not remaining[g.id]]
        if not ready:
            raise ValueError("gate needs form a cycle: "
                             + ", ".join(sorted(remaining)))
        for g in ready:
            out.append(g)
            del remaining[g.id]
            for deps in remaining.values():
                deps.discard(g.id)
    return out


def by_feature() -> dict[str, list[Gate]]:
    grouped: dict[str, list[Gate]] = {name: [] for name in FEATURES}
    for g in GATES:
        grouped[g.feature].append(g)
    return grouped


def as_json() -> list[dict]:
    return [g.as_json() for g in GATES]

"""The sync report — every gate's latest verdict for one ROM version, on disk.

`data/version_sync/<version>.json`, one object keyed by gate id, each value a
`Verdict` plus the ISO time it was reached. The runner rewrites the file
atomically after EVERY verdict, so a crash mid-session loses nothing, and the
dashboard (`server/sync_api.py`) reads the same file. Promotion is deliberate
and human-in-the-loop: Claude reads `jp.json` and writes the JP row into
`memory/layout.py` with its evidence, and `tests/test_layout_matches_report.py`
fails when a verified value is not shipped or a shipped value contradicts the
report — the two cannot drift.

The report is committed (`.gitignore` un-ignores `data/version_sync/`): it is
the evidence a JP address was verified, and the layout<->report guard reads it
on every clone.
"""
import json
import os
from pathlib import Path

from sm64_events.core.paths import data_root
from sm64_events.sync.gates import Verdict


def report_path(version: str, root: Path | None = None) -> Path:
    base = root if root is not None else data_root() / "data"
    return base / "version_sync" / f"{version}.json"


class Report:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.verdicts: dict[str, Verdict] = {}
        self.reached_at: dict[str, str] = {}

    def load(self) -> "Report":
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for gate_id, entry in raw.items():
                self.verdicts[gate_id] = Verdict.from_json(entry)
                self.reached_at[gate_id] = entry.get("at", "")
        return self

    def status(self, gate_id: str) -> str:
        found = self.verdicts.get(gate_id)
        return found.status if found is not None else "missing"

    def record(self, gate_id: str, verdict: Verdict, at: str) -> None:
        self.verdicts[gate_id] = verdict
        self.reached_at[gate_id] = at
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(json.dumps(self.as_json(), indent=1, sort_keys=True)
                        .encode("utf-8"))
        os.replace(tmp, self.path)

    def as_json(self) -> dict:
        return {gate_id: {**verdict.as_json(), "at": self.reached_at.get(gate_id, "")}
                for gate_id, verdict in self.verdicts.items()}

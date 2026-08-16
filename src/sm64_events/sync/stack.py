"""The REAL detector chain, driven over a snapshot stream, events collected.

A feature gate and a calibration gate both ask the same question — "does the
shipped detector stack, reading THIS version's layout, publish what it
publishes on US?" — so this is the one door they share. It runs
`main.build_detectors()` (THE chain, in THE order; a hand-listed chain would
pass with a detector unwired, which is the failure it exists to catch) the
way `server/poller.py` does: consecutive (prev, curr) pairs, every detector
every tick, an exception in one detector skips that detector for that tick
and never the stream.

Events come back as `to_wire` dicts so a gate can share `checks.await_event`
with any other reader of the wire.
"""
from collections.abc import Iterable, Iterator

from sm64_events.core.events import to_wire
from sm64_events.core.snapshot import GameSnapshot


class DetectorRun:
    """Feed snapshots in, read wire events out. `version` picks the chain a
    future JP-specific detector variant would hang off; today every detector
    reads what the snapshot already resolved for it."""

    def __init__(self, version: str = "us", detectors: list | None = None):
        if detectors is None:
            from sm64_events.main import build_detectors
            detectors = build_detectors(version=version)
        self.version = version
        self.detectors = detectors
        self.events: list[dict] = []
        self.errors: list[str] = []
        self._prev: GameSnapshot | None = None
        self._seq = 0

    def feed(self, curr: GameSnapshot) -> list[dict]:
        """One tick: returns the wire events this snapshot produced."""
        produced: list[dict] = []
        if self._prev is not None:
            for detector in self.detectors:
                try:
                    for event in detector.process(self._prev, curr):
                        self._seq += 1
                        produced.append(to_wire(event, self._seq))
                except Exception as exc:     # a detector's own bug, reported
                    self.errors.append(f"{type(detector).__name__}: {exc!r}")
        self._prev = curr
        self.events.extend(produced)
        return produced

    def run(self, snapshots: Iterable[GameSnapshot]) -> list[dict]:
        for snapshot in snapshots:
            self.feed(snapshot)
        return self.events

    def stream(self, snapshots: Iterable[GameSnapshot]) -> Iterator[tuple[GameSnapshot, list[dict]]]:
        """(snapshot, events it produced) per tick — for a gate that wants to
        stop the moment its event lands rather than run the clock out."""
        for snapshot in snapshots:
            yield snapshot, self.feed(snapshot)

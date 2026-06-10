"""Detector contract: pure functions over snapshot pairs. No I/O, no clocks."""
from typing import Protocol

from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot


class Detector(Protocol):
    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]: ...

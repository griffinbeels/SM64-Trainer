"""Detector contract: functions over consecutive snapshot pairs.

No I/O, no clock access. Detectors MAY keep bounded internal state (e.g.,
short sample histories): the poller feeds consecutive pairs from a single
emulator session, and detector state must self-heal when global_timer
jumps backward (savestate / console reset)."""
from typing import Protocol

from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot


class Detector(Protocol):
    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]: ...

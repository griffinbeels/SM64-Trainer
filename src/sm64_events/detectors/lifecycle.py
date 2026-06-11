# src/sm64_events/detectors/lifecycle.py
"""game_reset: gGlobalTimer moved backward INTO THE BOOT RANGE (console
reset / ROM reload). Mid-game backward jumps are savestate loads and emit
state_loaded from detectors/anchors.py instead — exactly one fires."""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.anchors import BOOT_TIMER_MAX


class GameResetDetector:
    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        if curr.global_timer >= prev.global_timer:
            return []
        if curr.global_timer >= BOOT_TIMER_MAX:
            return []  # state load, not a reset — see detectors/anchors.py
        return [Event(type="game_reset", frame=curr.global_timer,
                      timestamp_utc=curr.wall_time_utc, payload={})]

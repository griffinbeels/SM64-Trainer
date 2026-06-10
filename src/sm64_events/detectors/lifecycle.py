# src/sm64_events/detectors/lifecycle.py
"""game_reset: gGlobalTimer moved backward (console reset, savestate load
to an earlier point, ROM reload). Stats consumers use it to segment attempts."""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot


class GameResetDetector:
    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        if curr.global_timer >= prev.global_timer:
            return []
        return [Event(type="game_reset", frame=curr.global_timer,
                      timestamp_utc=curr.wall_time_utc, payload={})]

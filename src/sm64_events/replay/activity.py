"""Player-activity tap -> replay idle gating.

NOT a real detector: it emits no events and journals nothing. It satisfies
the detector protocol so the poller hands it every (prev, curr) snapshot
pair — the replay zone learns "is the player providing input" from fields
the poller ALREADY reads (no new memory addresses, no VERIFY cycle).

Active tick := game logic advancing (global_timer moved) AND Mario's
action outside PASSIVE_ACTIONS — the same decomp-verified idle/standing
registry the activity-discard rule uses (memory/addresses.py).

Accepted limits (same family as the AFK-discard inferences):
- Emulator paused / hard-frozen: global_timer stops -> idle. Correct for
  recording too — a frozen frame is worthless footage.
- Usamune menu: game logic keeps running while igt freezes, and Mario's
  action stays whatever it was. If that action is active (menu opened
  mid-jump), menu idling counts as active — accepted; menu visits during
  practice are brief, and the AFK discard covers the tracking side.
"""
from sm64_events.memory.addresses import PASSIVE_ACTIONS


class ActivityTap:
    def __init__(self, recorder):
        self._recorder = recorder

    def process(self, prev, curr) -> list:
        if (curr.global_timer != prev.global_timer
                and curr.mario_action not in PASSIVE_ACTIONS):
            self._recorder.set_player_active()
        return []

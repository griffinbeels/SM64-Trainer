"""Player-activity tap -> replay idle gating.

NOT a real detector: it emits no events and journals nothing. It satisfies
the detector protocol so the poller hands it every (prev, curr) snapshot
pair — the replay zone learns "is the player providing input" from fields
the poller ALREADY reads (no new memory addresses, no VERIFY cycle).

Activity := game logic advancing (global_timer moved) AND any of:
- Mario's action outside PASSIVE_ACTIONS — the same decomp-verified
  idle/standing registry the activity-discard rule uses (addresses.py);
- igt_overall went BACKWARD — a practice reset / savestate load, i.e. the
  anchor that OPENS an attempt. Mario stands PASSIVE through the fade-in
  after a load, so action alone resumed recording only at first MOVEMENT
  and a 0-pre-pad clip opened ~2 s late on a frozen frame (live-reported
  2026-06-12); the anchor itself must count as input;
- curr_level changed — level entry, the same anchor family.

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
        if curr.global_timer == prev.global_timer:
            return []  # game frozen: nothing on screen changes either
        if (curr.mario_action not in PASSIVE_ACTIONS
                or curr.igt_overall < prev.igt_overall
                or curr.curr_level != prev.curr_level):
            self._recorder.set_player_active()
        return []

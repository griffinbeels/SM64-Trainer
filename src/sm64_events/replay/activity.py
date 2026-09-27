"""Player-activity tap -> replay idle gating.

NOT a real detector: it emits no events and journals nothing. It satisfies
the detector protocol so the poller hands it every (prev, curr) snapshot
pair — the replay zone learns "is the player providing input" from fields
the poller ALREADY reads (no new memory addresses, no VERIFY cycle).

Activity := game logic advancing (global_timer moved) AND any of:
- Mario's action outside PASSIVE_ACTIONS — the same decomp-verified
  idle/standing registry the activity-discard rule uses (addresses.py) —
  while the game is visibly going somewhere: IGT advanced, or the action or
  its action state changed since the last poll. A non-passive action alone
  is not input: on 2026-09-25 he left the game after a star dance, parked in
  ACT_STAR_DANCE_EXIT (0x1302) with IGT frozen at 437 and a neutral pad, and
  the recorder called that active for 1.5 h, growing the ring ~5.4 MB/min.
  IGT advancing counts only beside a non-passive action: standing in ACT_IDLE
  with the timer running is still AFK;
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
  action and action state hold still, so a menu left open reads as idle.
- A non-passive action that cycles its action state by itself (an animation
  loop with no pad input) still reads as active. Not observed; the AFK
  discard covers the tracking side if it is.
"""
from sm64_events.memory.addresses import PASSIVE_ACTIONS


def _moving(prev, curr) -> bool:
    return (curr.igt_overall > prev.igt_overall
            or curr.mario_action != prev.mario_action
            or curr.mario_action_state != prev.mario_action_state)


class ActivityTap:
    def __init__(self, recorder):
        self._recorder = recorder

    def process(self, prev, curr) -> list:
        if curr.global_timer == prev.global_timer:
            return []  # game frozen: nothing on screen changes either
        if (curr.igt_overall < prev.igt_overall
                or curr.curr_level != prev.curr_level
                or (curr.mario_action not in PASSIVE_ACTIONS and _moving(prev, curr))):
            self._recorder.set_player_active()
        return []

"""ActivityTap: the poll-thread input signal that drives replay idle gating.
Active tick = global_timer advanced AND mario_action outside PASSIVE_ACTIONS."""
from types import SimpleNamespace

from sm64_events.memory.addresses import PASSIVE_ACTIONS
from sm64_events.replay.activity import ActivityTap

PASSIVE = next(iter(PASSIVE_ACTIONS))
ACTIVE = next(a for a in range(1 << 20) if a not in PASSIVE_ACTIONS)


def snapish(global_timer, mario_action):
    return SimpleNamespace(global_timer=global_timer, mario_action=mario_action)


def test_tap_pings_only_on_active_input():
    pings = []
    recorder = SimpleNamespace(set_player_active=lambda: pings.append(1))
    tap = ActivityTap(recorder)

    assert tap.process(snapish(1, ACTIVE), snapish(2, ACTIVE)) == []
    assert len(pings) == 1                       # game running + active action

    tap.process(snapish(2, ACTIVE), snapish(2, ACTIVE))
    assert len(pings) == 1                       # frozen timer (paused) = idle

    tap.process(snapish(2, PASSIVE), snapish(3, PASSIVE))
    assert len(pings) == 1                       # standing idle = no input

    tap.process(snapish(3, PASSIVE), snapish(4, ACTIVE))
    assert len(pings) == 2                       # input again -> ping

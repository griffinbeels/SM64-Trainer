"""ActivityTap: the poll-thread input signal that drives replay idle gating.
Active tick = global_timer advanced AND mario_action outside PASSIVE_ACTIONS."""
from types import SimpleNamespace

from sm64_events.memory.addresses import PASSIVE_ACTIONS
from sm64_events.replay.activity import ActivityTap

PASSIVE = next(iter(PASSIVE_ACTIONS))
ACTIVE = next(a for a in range(1 << 20) if a not in PASSIVE_ACTIONS)


def snapish(global_timer, mario_action, igt=100, level=8):
    return SimpleNamespace(global_timer=global_timer, mario_action=mario_action,
                           igt_overall=igt, curr_level=level)


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


def test_tap_pings_on_anchor_signals_even_while_passive():
    """The frozen-clip-opening bug (2026-06-12): after a savestate load,
    Mario stands PASSIVE through the fade-in, so action alone resumed
    recording only at first movement — 0-pre-pad clips opened ~2 s late.
    The anchor itself (igt reset / level entry) must count as input."""
    pings = []
    recorder = SimpleNamespace(set_player_active=lambda: pings.append(1))
    tap = ActivityTap(recorder)

    tap.process(snapish(1, PASSIVE, igt=500), snapish(2, PASSIVE, igt=3))
    assert len(pings) == 1                       # igt reset = anchor

    tap.process(snapish(2, PASSIVE, igt=3), snapish(3, PASSIVE, igt=4))
    assert len(pings) == 1                       # igt advancing: still idle

    tap.process(snapish(3, PASSIVE, level=8), snapish(4, PASSIVE, level=24))
    assert len(pings) == 2                       # level entry = anchor

    tap.process(snapish(4, PASSIVE, igt=900), snapish(4, PASSIVE, igt=0))
    assert len(pings) == 2                       # frozen game: never input

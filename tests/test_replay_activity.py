"""ActivityTap: the poll-thread input signal that drives replay idle gating.
Active tick = global_timer advanced AND mario_action outside PASSIVE_ACTIONS
while IGT advances or the action/action state changes; or an anchor."""
from types import SimpleNamespace

from sm64_events.memory.addresses import ACT_STAR_DANCE_EXIT, PASSIVE_ACTIONS
from sm64_events.replay.activity import ActivityTap

PASSIVE = next(iter(PASSIVE_ACTIONS))
ACTIVE = next(a for a in range(1 << 20) if a not in PASSIVE_ACTIONS)


def snapish(global_timer, mario_action, igt=100, level=8, state=0):
    return SimpleNamespace(global_timer=global_timer, mario_action=mario_action,
                           igt_overall=igt, curr_level=level,
                           mario_action_state=state)


def tap_with_pings():
    pings = []
    return ActivityTap(SimpleNamespace(set_player_active=lambda: pings.append(1))), pings


def test_tap_pings_only_on_active_input():
    tap, pings = tap_with_pings()

    assert tap.process(snapish(1, ACTIVE, igt=100), snapish(2, ACTIVE, igt=101)) == []
    assert len(pings) == 1                       # game running + active action

    tap.process(snapish(2, ACTIVE), snapish(2, ACTIVE))
    assert len(pings) == 1                       # frozen timer (paused) = idle

    tap.process(snapish(2, PASSIVE), snapish(3, PASSIVE))
    assert len(pings) == 1                       # standing idle = no input

    tap.process(snapish(3, PASSIVE), snapish(4, ACTIVE))
    assert len(pings) == 2                       # input again -> ping


def test_afk_parked_in_a_non_passive_action_is_idle():
    """2026-09-25: after a star dance he walked away with Mario parked in
    ACT_STAR_DANCE_EXIT, IGT frozen at 437 and the pad neutral. The game kept
    running, so every poll called that input and the ring grew for 1.5 h."""
    assert ACT_STAR_DANCE_EXIT not in PASSIVE_ACTIONS
    tap, pings = tap_with_pings()
    for frame in range(1000, 1300):
        tap.process(snapish(frame, ACT_STAR_DANCE_EXIT, igt=437),
                    snapish(frame + 1, ACT_STAR_DANCE_EXIT, igt=437))
    assert pings == []


def test_a_non_passive_action_is_input_while_the_game_visibly_moves():
    tap, pings = tap_with_pings()
    tap.process(snapish(1, ACT_STAR_DANCE_EXIT, igt=437), snapish(2, ACTIVE, igt=437))
    assert len(pings) == 1                       # action edge
    tap.process(snapish(2, ACTIVE, igt=437, state=0), snapish(3, ACTIVE, igt=437, state=1))
    assert len(pings) == 2                       # action-state edge
    tap.process(snapish(3, ACTIVE, igt=437), snapish(4, ACTIVE, igt=438))
    assert len(pings) == 3                       # same action, IGT running
    tap.process(snapish(4, PASSIVE, igt=438), snapish(5, PASSIVE, igt=439))
    assert len(pings) == 3, "IGT running alone is not input: AFK in ACT_IDLE"
    tap.process(snapish(5, ACTIVE, igt=900), snapish(6, ACTIVE, igt=0))
    assert len(pings) == 4                       # IGT backward: the reset anchor


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

# src/sm64_events/detectors/caused.py
"""Caused moments: what the PLAYER caused, read off the object it happened to.

Every row in `moment.MOMENTS` before this module was an ACTION SET on Mario's
own byte, and that is the wrong end of the causal chain for half of what he
asked for — *"Can we just try to add support for literally everything we
possibly can detect as a landmark? Defeating enemies, opening doors,
triggering switches…"*, then the correction that is the whole design: *"the
switch press is the switch's, but that switch press never occurs without
mario. by association, it's therefore mario's action"* (2026-08-07). A floor
switch is pressed by its own behaviour code watching Mario's position and
NEVER reaches his engagement pointers — measured, round 10: he pounded the
blue coin switch in WF and TTC and nothing fired. The engine writes the
change on the OBJECT, so this detector diffs the object pool.

WHAT THE CAPTURE CORRECTED before a line was written (his 2026-08-07 `--pool`
run, 1,386 changes, `data/object_pool_probe.jsonl`):

  * `oAction` is THE signal. The switch presses 0 -> 1; a goomba dies into
    the engine's shared attacked actions (100/101/102); a bob-omb dies into
    its own explode state (3).
  * `oHealth` is NOT a defeat signal, for anything. The hitbox struct inits
    `health: 0` and it is written when the object's logic first runs with
    Mario in range — six far-apart bob-ombs "died" within 50 frames while
    Mario FLEW past them, and the round-11 ledger's "goomba 2048 -> 0" defeat
    reading was pool initialisation at a level load.
  * `oInteractStatus` — the field that most obviously means "what Mario did
    to me" — is cleared within the frame and a poll cannot catch it (8
    non-zero in 1,386). Written down in round 10 so it is not reasoned back
    in from the decomp.

THE REGISTRY is `addresses.CAUSED_BEHAVIOURS` — one row per behaviour, and a
row's KIND must also be a `MOMENTS` row (with an empty action set), so the
builder's vocabulary, the recorder's sentences and the timeline all keep
reading ONE kind registry. Adding a behaviour under an existing kind is one
addresses row — with a capture first, because reasoning got the switch wrong
twice and the measurement got it right once.

THE FILTERS, both demanded by the same capture. 1,386 changes in one session
is mostly scenery, and a LEVEL LOAD initialises the whole pool on one frame
(at frame 1392613 a goomba, two bob-ombs, King Bob-omb and an exclamation box
all "changed" at once — with the init spilling into the NEXT frame too, so a
same-frame test alone is not enough):

  1. a diff counts only while prev and curr agree the slot holds the SAME
     behaviour — slot reuse across a rebuild is not a transition;
  2. nothing fires inside `LEVEL_LOAD_TAIL_FRAMES` of a level/area edge (the
     shared constant, `counter_epoch.py`) or of a forward clock jump bigger
     than a poll can produce (a savestate restores a different pool with no
     edge to see);
  3. and a BURST of rule matches on one tick is a pool-wide write, not a
     gesture — the backstop for whatever the place and clock rules miss.

Replayed over the whole capture, these rules journal 9 rows — his 3 switch
presses, 4 goomba squishes, 2 bob-omb explosions — with zero load artifacts
and zero real gestures suppressed. Volume against the 2026-08-04 journal
trim: ~5 rows per hour of his play, two orders of magnitude under the door
moment's own budget.

The LANDMARK is read off the object itself — no engaged-pointer lag, so no
one-poll settle — and its position is the PREV tick's, deliberately: the
switch's model sinks while pressed (his capture: y 384 at the 0 -> 1 edge,
344 six frames later), and a scriptless object is keyed by where it stands,
so keying the edge tick would let a slow poll name a half-sunk switch as a
different switch.
"""
from sm64_events.core.events import Event
from sm64_events.core.landmark import Landmark
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.core.timefmt import format_igt
from sm64_events.detectors.counter_epoch import LEVEL_LOAD_TAIL_FRAMES
from sm64_events.detectors.igt_clock import IgtClock
from sm64_events.detectors.moment import display_lag_for
from sm64_events.memory import addresses as A


def _press(before, now) -> bool:
    """BLUE_COIN_SWITCH_ACT_IDLE -> _RECEDING, strictly — the 1 -> 2 that
    follows is the switch staying down, not a second press."""
    return before.action == 0 and now.action == 1


def _attacked(before, now) -> bool:
    return (now.action in A.OBJECT_ATTACKED_ACTIONS
            and before.action not in A.OBJECT_ATTACKED_ACTIONS)


def _explode(before, now) -> bool:
    return (now.action == A.BOBOMB_ACT_EXPLODE
            and before.action != A.BOBOMB_ACT_EXPLODE)


_RULES = {"press": _press, "attacked": _attacked, "explode": _explode}

# behaviour pointer -> (kind, rule fn); a registry row naming an unknown rule
# fails HERE, at import, not silently at the first press.
_WATCHED = {pointer: (kind, _RULES[rule])
            for pointer, kind, rule in A.CAUSED_BEHAVIOURS.values()}


class CausedMomentDetector:
    """Emits `moment_reached {kind, ordinal, landmark, ...}` — the same event,
    payload shape and clock as `MomentDetector`, so every consumer of a door
    moment reads a switch press with no second code path."""

    # A poll of a 30 fps game at 60 Hz advances 0-2 frames; anything bigger
    # means the stream is discontinuous (emulator stall, savestate to a later
    # point in the SAME area — which moves nothing the place filter can see).
    TIMER_GAP_FRAMES = 8
    # Three rule matches on one tick is a pool-wide write, not a gesture. His
    # capture never holds two REAL gestures on one frame; a load initialises
    # five-plus watched slots on one.
    BURST_MIN = 3

    def __init__(self):
        self._counts: dict[str, int] = {}
        self._clock = IgtClock()
        self._suppress_until: int | None = None

    def reset(self) -> None:
        """A new attempt opened: ordinals count from here (service-wired,
        same boundary as MomentDetector.reset)."""
        self._counts.clear()

    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        if curr.global_timer < prev.global_timer:
            # Savestate load / console reset: the ordinals belong to a run no
            # longer happening, and the suppression deadline to an old clock.
            self.reset()
            self._suppress_until = None
            return []
        if (curr.curr_level != prev.curr_level
                or curr.curr_area != prev.curr_area
                or curr.global_timer - prev.global_timer
                > self.TIMER_GAP_FRAMES):
            self._suppress_until = curr.global_timer + LEVEL_LOAD_TAIL_FRAMES
        if (self._suppress_until is not None
                and curr.global_timer <= self._suppress_until):
            return []
        before_by_slot = {state.slot: state for state in prev.caused}
        fired = []
        for state in curr.caused:
            before = before_by_slot.get(state.slot)
            if before is None or before.behaviour != state.behaviour:
                continue    # first sight, or slot reuse — not a transition
            kind, rule = _WATCHED[state.behaviour]
            if rule(before, state):
                fired.append((kind, before, state))
        if len(fired) >= self.BURST_MIN:
            return []
        return [self._emit(kind, before, state, curr)
                for kind, before, state in fired]

    def _emit(self, kind: str, before, state, curr: GameSnapshot) -> Event:
        self._counts[kind] = self._counts.get(kind, 0) + 1
        reading, source = self._clock.igt_at(curr.global_timer, curr)
        # The kind's own display lag, through moment.py's ONE reader: these
        # kinds have no screenshot of their own yet, so they carry the door's
        # value and follow it if it ever moves (tools/score_moment_clock.py is
        # the instrument, and moment.py's constant is the evidence trail).
        igt_frames = reading + display_lag_for(kind)
        found = Landmark(
            level=curr.curr_level, area=curr.curr_area,
            behaviour=state.behaviour,
            home=tuple(int(round(axis)) for axis in state.home),
            pos=tuple(int(round(axis)) for axis in before.pos))
        return Event(type="moment_reached", frame=curr.global_timer,
                     timestamp_utc=curr.wall_time_utc,
                     payload={"kind": kind, "ordinal": self._counts[kind],
                              "landmark": found.payload(),
                              "level": curr.curr_level,
                              "area": curr.curr_area,
                              "action": curr.mario_action,
                              "igt_frames": igt_frames, "igt_source": source,
                              "igt": format_igt(igt_frames),
                              "action_timer": curr.mario_action_timer,
                              "counter": curr.igt_overall})

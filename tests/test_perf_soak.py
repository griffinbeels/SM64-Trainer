"""Offline soak guard: the per-tick event path must not grow unboundedly.

The latent "lags after hours" leak hunt (2026-06-14) cleared the pure-Python
event path by static analysis — detectors keep only bounded state, the
broadcaster retains nothing, projection/views are per-event/per-request not
per-tick. This test PROVES and PERMANENTLY GUARDS that: it drives the full
detector chain (main.py's order) with a long, varied synthetic snapshot stream
and asserts the live object graph does not grow with tick count.

A real per-tick leak (a list appended every frame) would add >=1 object/tick =
tens of thousands over the soak; a bounded pipeline adds ~0. The margin sits
far between those, so this is a strong signal, not a flaky one. Needs no
emulator — snapshots are synthetic. If THIS ever fails, a detector started
accumulating; find the container that grows with frames, not with distinct
game state."""
import gc
from datetime import datetime, timedelta, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.anchors import AnchorDetector
from sm64_events.detectors.area import AreaChangeDetector
from sm64_events.detectors.death import DeathDetector
from sm64_events.detectors.dust import DustTrickDetector
from sm64_events.detectors.key import KeyGrabDetector
from sm64_events.detectors.level import LevelChangeDetector
from sm64_events.detectors.lifecycle import GameResetDetector
from sm64_events.detectors.spawn import SpawnDetector
from sm64_events.detectors.stage import StageChangeDetector
from sm64_events.detectors.star_grab import StarGrabDetector
from sm64_events.detectors.warp import WarpDetector
from sm64_events.memory import addresses as A

_ACT_IDLE = 0x0C400201
_T0 = datetime(2026, 6, 14, tzinfo=timezone.utc)


def _all_detectors():
    """The event-path chain in main.py's wiring order (minus ActivityTap,
    which is replay glue with no event state)."""
    return [GameResetDetector(), LevelChangeDetector(), AreaChangeDetector(),
            StageChangeDetector(), AnchorDetector(), DeathDetector(),
            DustTrickDetector(), WarpDetector(), KeyGrabDetector(),
            SpawnDetector(), StarGrabDetector()]


def _scene(i: int) -> GameSnapshot:
    """A varied-but-cheap synthetic tick. Cycles through transitions that make
    every detector's state machine churn: level toggles (level changes), area
    toggles (area changes), periodic star-dance grabs (re-collection edges),
    and a periodic global_timer rewind (the reset / self-heal path)."""
    phase = i % 100
    # rewind the timer every 500 ticks -> backward jump = reset/self-heal
    gt = 1000 + (i % 500)
    level = 24 if (i // 30) % 2 == 0 else 6        # WF <-> Castle: level changes
    area = 1 if (i // 15) % 2 == 0 else 2          # area changes
    num_stars = 5 + (i // 100) % 120               # climbs then wraps (plausible)
    action, a_timer, igt_result = _ACT_IDLE, 0, 0
    if phase == 50:                                # a grab this tick
        action, a_timer = A.ACT_STAR_DANCE_EXIT, 2
        igt_result = (i % 1000) + 1
    return GameSnapshot(
        wall_time_utc=_T0 + timedelta(milliseconds=i * 33),
        global_timer=gt, mario_action=action, mario_action_timer=a_timer,
        num_stars=num_stars, last_completed_course=1 + (i // 100) % 6,
        last_completed_star=1 + (i // 100) % 7,
        igt_overall=i % 1000, igt_result=igt_result, curr_level=level,
        particle_flags=0, curr_area=area, pending_warp_op=0)


def _feed(detectors, start: int, n: int, prev: GameSnapshot) -> GameSnapshot:
    for i in range(start, start + n):
        curr = _scene(i)
        for d in detectors:
            d.process(prev, curr)                  # mirrors Poller.tick
        prev = curr
    return prev


def test_detector_chain_does_not_grow_with_tick_count():
    detectors = _all_detectors()
    prev = _scene(0)

    # warm up so every detector's bounded buffers (igt_clock deque etc.) and
    # any one-time imports/caches are full BEFORE the baseline measurement
    prev = _feed(detectors, 1, 3000, prev)
    gc.collect()
    base = len(gc.get_objects())

    prev = _feed(detectors, 3001, 60000, prev)
    gc.collect()
    growth = len(gc.get_objects()) - base

    # bounded pipeline: ~0; a per-tick leak: >=60000. Margin sits far between.
    assert growth < 2000, (
        f"event path grew {growth} live objects over 60k ticks — a detector "
        f"is accumulating per-frame; find the container keyed on tick, not on "
        f"distinct game state")

"""Read-only live probe: WHERE inside a gGlobalTimer frame does usamune_overall tick?

The timer reader's join (round 32 item 91, wired in 16615f33) stamps each
captured picture with the input sampler's LATEST coherent
``(gGlobalTimer, usamune_overall)`` sample and names the displayed frame as
``gGlobalTimer - (igt_ram - igt_shown)``. That is exact only if the two
counters advance at the SAME instant of the frame. The pad is rewritten ~62%
through the frame by the same game iteration (``CONTROLLER_SETTLE_PHASE``,
measured over four sessions), and the IGT increment lives in that iteration's
logic -- so a grab landing BEFORE the tick would carry ``igt - 1`` and map one
frame late. 6510's own picture ledger puts 74% of grabs before the 62% mark
(round 32 item 92), which makes this the premise to measure before trusting a
single mechanical slot.

Usage: ``uv run python tools/probe_igt_tick.py [seconds] [wait_s]`` with PJ64
running and the Usamune timer TICKING (any course, unpaused). It waits up to
``wait_s`` for the counter to start moving, then samples both counters as fast
as ReadProcessMemory allows for ``seconds`` and prints:

* how many distinct IGT values one gGlobalTimer frame showed (1 = the two tick
  together or between two samples; 2 = the IGT ticks mid-frame);
* the phase inside the frame at which the IGT tick was observed, as a
  histogram in tenths of a frame, plus its median / p10 / p90.

Read-only (domain rule 6); safe beside a live session.
"""
import sys
import time
from collections import Counter
from dataclasses import dataclass, field

sys.path.insert(0, "src")
from sm64_events.memory.layout import layout_for  # noqa: E402
from sm64_events.memory.pj64 import Pj64Memory  # noqa: E402

FRAME_PERIOD_S = 1.0 / 30.0


def read_state(memory, layout) -> tuple[int, int, int, int]:
    return (memory.read_u32(layout.global_timer),
            memory.read_u16(layout.usamune_overall),
            memory.read_s16(layout.curr_level),
            memory.read_s16(layout.curr_area))


def wait_for_running_timer(memory, layout, wait_s: float) -> bool:
    """True once both counters advanced across a half-second; False when
    `wait_s` passed with either standing still (menu, pause, stopped timer)."""
    earlier = read_state(memory, layout)
    time.sleep(1.0)
    later = read_state(memory, layout)
    print("level %d area %d | gGlobalTimer %d -> %d (+%d in 1 s) | "
          "usamune_overall %d -> %d (+%d)"
          % (earlier[2], earlier[3], earlier[0], later[0], later[0] - earlier[0],
             earlier[1], later[1], later[1] - earlier[1]))
    waited = 0.0
    while later[1] == earlier[1] or later[0] == earlier[0]:
        if waited >= wait_s:
            print("usamune_overall / gGlobalTimer not advancing (menu, pause, "
                  "or timer stopped): cannot measure the tick phase")
            return False
        time.sleep(0.5)
        waited += 0.5
        earlier, later = later, read_state(memory, layout)
    if waited:
        print("timer started advancing after %.0f s of waiting: level %d "
              "area %d igt %d" % (waited, later[2], later[3], later[1]))
    return True


@dataclass
class TickMeasurement:
    samples: int = 0
    straddles: int = 0
    #: how many distinct IGT values each completed gGlobalTimer frame showed
    distinct_per_frame: list = field(default_factory=list)
    #: phase inside the frame (0..1) at which an IGT change was observed
    tick_phases: list = field(default_factory=list)
    #: IGT(first sample of frame F) - IGT(last sample of F-1)
    igt_step_across_edge: Counter = field(default_factory=Counter)


def measure(memory, layout, seconds: float) -> TickMeasurement:
    """Sample both counters flat out for `seconds`, inside the same
    gGlobalTimer sandwich the input sampler uses."""
    out = TickMeasurement()
    last_frame = None
    frame_edge_time = None
    previous_igt = None
    igt_values_this_frame: set = set()
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        sample_time = time.perf_counter()
        before = memory.read_u32(layout.global_timer)
        igt = memory.read_u16(layout.usamune_overall)
        after = memory.read_u32(layout.global_timer)
        out.samples += 1
        if before != after:
            out.straddles += 1
            continue
        if before != last_frame:
            if last_frame is not None and igt_values_this_frame:
                out.distinct_per_frame.append(len(igt_values_this_frame))
                if previous_igt is not None:
                    out.igt_step_across_edge[igt - previous_igt] += 1
            last_frame = before
            frame_edge_time = sample_time
            igt_values_this_frame = {igt}
        else:
            igt_values_this_frame.add(igt)
            if (previous_igt is not None and igt != previous_igt
                    and frame_edge_time is not None):
                out.tick_phases.append(
                    (sample_time - frame_edge_time) / FRAME_PERIOD_S)
        previous_igt = igt
    return out


def report(measured: TickMeasurement, seconds: float) -> None:
    print("samples %d (%.0f Hz), straddles %d, frames observed %d"
          % (measured.samples, measured.samples / seconds, measured.straddles,
             len(measured.distinct_per_frame)))
    print("distinct IGT values seen inside one frame:",
          dict(Counter(measured.distinct_per_frame)))
    print("IGT(first sample of frame F) - IGT(last sample of F-1):",
          dict(measured.igt_step_across_edge))
    phases = sorted(measured.tick_phases)
    if not phases:
        print("IGT never changed mid-frame: it ticks together with "
              "gGlobalTimer (or between two consecutive samples)")
        return
    histogram = Counter(min(int(phase * 10), 10) for phase in phases)
    print("IGT ticks observed MID-frame: %d; phase histogram (tenths of a "
          "frame): %s" % (len(phases),
                          {bucket / 10: count
                           for bucket, count in sorted(histogram.items())}))
    count = len(phases)
    print("tick phase median %.2f, p10 %.2f, p90 %.2f of a frame"
          % (phases[count // 2], phases[count // 10], phases[9 * count // 10]))


def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0
    wait_s = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
    memory = Pj64Memory()
    if not memory.attach():
        print("PJ64 not attached")
        return 2
    layout = layout_for("us")
    if not wait_for_running_timer(memory, layout, wait_s):
        return 1
    report(measure(memory, layout, seconds), seconds)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""READ-ONLY probe: does a textbox APPEAR when Mario starts WAITING for it,
or only once the box is actually READING?

His report, 2026-08-10, with two replay screenshots of the Whomp King textbox:

    "the timer is wrong (in game shows 12"00 and 11"93, but our practice log
    marks the time as faster). Seems like we don't quite detect when the
    textbox ACTUALLY appears here? (I think maybe our timing is incorrectly
    just triggering the king himself, rather than the textbox that's
    generated afterwards?)"

Both readings are **exactly 8 frames (0.27s) early** against the shipped
`moment_reached` offset -- a FIXED gap, twice, which is the signature of a
state that always precedes the box by the same animation, rather than of a
detection race. `detectors/moment.py`'s `textbox` kind fires on the entry edge
into `addresses.DIALOG_ACTIONS`, which holds three actions:

    ACT_WAITING_FOR_DIALOG        -- "dialog about to begin" (addresses.py's
                                      own comment)
    ACT_READING_AUTOMATIC_DIALOG  -- signs / automatic dialogs
    ACT_READING_NPC_DIALOG        -- talking to an NPC

so the moment plausibly fires on WAITING, a frame (or eight) before the box
that actually shows text. This probe answers that with a stopwatch instead of
a decomp read, the same way `probe_warp_block.py` settled the entrance-touch
question: trigger on the same edge condition `moment.py` uses, then watch
which of the three actions Mario passes through and when.

**The journal cannot answer this on its own, which is why the probe exists.**
Swept the whole repo journal (June 11 - present, 26k+ events): Whomp's
Fortress (level 24) has never once produced a `door_open` or `textbox`
`moment_reached` row, in 2417 WF events of any type. Whatever session
produced his two screenshots was not captured there -- possibly a different
server instance (CLAUDE.md's 8064/8065/8066 split) -- so there is no journal
row to pin his numbers against, only his own screen. This tool is what closes
that gap without needing one.

Attaches read-only via ReadProcessMemory. Takes no instance lock and no
recorder lock, so it is safe to run beside a live session while playing.

Usage: with PJ64 + Usamune running, `uv run python tools/probe_textbox.py`,
then walk into any textbox trigger -- a sign, an NPC, King Whomp. Prints one
frame-by-frame report per textbox: the frame Mario enters
ACT_WAITING_FOR_DIALOG (if he does), the frame he enters
ACT_READING_NPC_DIALOG or ACT_READING_AUTOMATIC_DIALOG, the gap between them
in frames, and Usamune's own raw counter (USAMUNE_OVERALL) at each. CTRL+C to
stop.
"""
import sys
import time

from sm64_events.memory.addresses import (ACT_READING_AUTOMATIC_DIALOG,
                                           ACT_READING_NPC_DIALOG,
                                           ACT_WAITING_FOR_DIALOG, CURR_AREA,
                                           CURR_LEVEL, DIALOG_ACTIONS,
                                           GLOBAL_TIMER, MARIO_ACTION,
                                           MARIO_ACTION_TIMER,
                                           USAMUNE_OVERALL)
from sm64_events.memory.pj64 import Pj64Memory

READING_ACTIONS = frozenset(
    {ACT_READING_AUTOMATIC_DIALOG, ACT_READING_NPC_DIALOG})

ACTION_NAMES = {
    ACT_WAITING_FOR_DIALOG: "WAITING_FOR_DIALOG",
    ACT_READING_AUTOMATIC_DIALOG: "READING_AUTOMATIC_DIALOG",
    ACT_READING_NPC_DIALOG: "READING_NPC_DIALOG",
}

# A textbox that never resolves (Mario walks away, a reset) should not hang
# the trace forever -- generous ceiling, a real dialog opens well inside it.
TRACE_FRAMES = 300


def sample(mem) -> dict:
    return {
        "timer": mem.read_u32(GLOBAL_TIMER),
        "action": mem.read_u32(MARIO_ACTION),
        "action_timer": mem.read_u16(MARIO_ACTION_TIMER),
        "level": mem.read_s16(CURR_LEVEL),
        "area": mem.read_s16(CURR_AREA),
        "counter": mem.read_u16(USAMUNE_OVERALL),
    }


def first_entry(trace: list[dict], actions: frozenset) -> dict | None:
    """The first sample whose action is in `actions` AND whose predecessor's
    wasn't -- the same entry-EDGE discipline `moment.py` uses, so this reads
    the identical frame the shipped detector would have fired the moment on.
    """
    for index, row in enumerate(trace):
        if row["action"] not in actions:
            continue
        if index == 0 or trace[index - 1]["action"] not in actions:
            return row
    return None


def report(trace: list[dict]) -> None:
    entry = trace[0]
    waiting = first_entry(trace, frozenset({ACT_WAITING_FOR_DIALOG}))
    reading = first_entry(trace, READING_ACTIONS)

    print()
    print(f"=== TEXTBOX at frame {entry['timer']}  "
          f"level {entry['level']} area {entry['area']} ===")
    print(f"  {'d_frame':>8}  {'action':>26}  {'action_timer':>12}  "
          f"{'counter':>7}")
    prev_action = None
    for row in trace:
        if row["action"] == prev_action:
            continue
        prev_action = row["action"]
        name = ACTION_NAMES.get(row["action"], f"{row['action']:#010x}")
        print(f"  {row['timer'] - entry['timer']:>8}  {name:>26}  "
              f"{row['action_timer']:>12}  {row['counter']:>7}")

    if waiting:
        print(f"  -> ACT_WAITING_FOR_DIALOG entered at frame "
              f"{waiting['timer']}  (counter {waiting['counter']})")
    else:
        print("  -> ACT_WAITING_FOR_DIALOG never observed in this window -- "
              "this textbox skipped straight to reading, or the window "
              "missed it")
    if reading:
        name = ACTION_NAMES[reading["action"]]
        print(f"  -> {name} entered at frame {reading['timer']}  "
              f"(counter {reading['counter']})")
    else:
        print("  -> no READING action observed before the window closed -- "
              "widen TRACE_FRAMES or the box never opened")
    if waiting and reading:
        gap = reading["timer"] - waiting["timer"]
        print(f"  -> GAP: {gap} frame(s), {gap / 30:.2f}s at 30fps, between "
              f"WAITING and READING")
    print()
    sys.stdout.flush()


def watch(mem) -> int:
    print("Attached read-only. Walk into any textbox trigger -- a sign, an")
    print("NPC, King Whomp. CTRL+C to stop.\n")
    # FLUSH THE BANNER: print() to a pipe is block-buffered on Windows, so
    # `probe | tee` shows a blank screen until the first textbox lands --
    # indistinguishable from "it failed to attach" otherwise.
    sys.stdout.flush()
    prev = sample(mem)
    trace: list[dict] = []
    while True:
        time.sleep(1 / 120)
        curr = sample(mem)
        if curr["timer"] == prev["timer"]:
            continue
        if trace:
            trace.append(curr)
            if (len(trace) >= TRACE_FRAMES
                    or curr["action"] not in DIALOG_ACTIONS):
                report(trace)
                trace = []
        elif (curr["action"] in DIALOG_ACTIONS
              and prev["action"] not in DIALOG_ACTIONS):
            trace = [curr]
        prev = curr


def main() -> int:
    mem = Pj64Memory()
    if not mem.attach():
        print("Could not attach -- is PJ64 running with the ROM loaded?")
        return 1
    return watch(mem)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nstopped")

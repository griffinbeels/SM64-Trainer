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

Round 1 hypothesised the moment was firing on WAITING, a frame (or eight)
before the box. His live run REFUTED that: all ten King Whomp encounters in
Whomp's Fortress entered ACT_READING_NPC_DIALOG DIRECTLY, WAITING never
observed. His own read of the footage supplied the real lead: *"for talking
with some NPCs / with the Whomp King, I definitely see a turning animation as
part of it. I think there must be a 'i triggered the npc' versus 'the text
box actually started to appear.'"* Round 1's capture also logged
MARIO_ACTION_TIMER reading 64512 and 63488 (0xFC00/0xF800) on the FIRST frame
of the action -- impossible for a counter that "resets to 0 on action
change". It was not a misread: decomp's `act_reading_npc_dialog`
(src/game/mario_actions_cutscene.c, n64decomp/sm64 master, fetched
2026-08-10) repurposes that field as Mario's head-turn PITCH ANGLE while he
turns to face the NPC, `+= headTurnAmount` (-1024/frame looking up) --
64512 and 63488 are exactly -1024 and -2048 as s16, i.e. one and two frames
into the turn. addresses.MARIO_ACTION_TIMER's own comment ("resets to 0 on
action change") is still true the instant the action is set; this action's
own handler overwrites it again the same frame, so it cannot be read as a
frame count while this action is live.

The field NEXT to it -- MARIO_ACTION_STATE, addresses.py's own comment cites
the exact decomp source -- IS the discriminator he asked for: 0-7 is the
turn toward the NPC (incrementing once per frame), 8 is the textbox itself
(held for the whole time it is open), 9-22 is looking away, 23 ends the
action. Automatic dialogs (signs / doors / star-count popups) run the same
field the other way -- actionState increments BEFORE the check each frame,
so the box opens at 9, not 8. This round adds that field to the probe and
reports the frame it reaches its kind's box-open value, which is the number
the shipped `textbox` moment should be timed from instead of the entry edge.

**The journal cannot answer this on its own, which is why the probe exists.**
Swept the whole repo journal (June 11 - present, 26k+ events): Whomp's
Fortress (level 24) has never once produced a `door_open` or `textbox`
`moment_reached` row, in 2417 WF events of any type. There is no journal row
to pin his numbers against, only his own screen and this probe.

Attaches read-only via ReadProcessMemory. Takes no instance lock and no
recorder lock, so it is safe to run beside a live session while playing.

Usage: with PJ64 + Usamune running, `uv run python tools/probe_textbox.py`,
then walk into any textbox trigger -- a sign, an NPC, King Whomp. Prints one
frame-by-frame report per textbox: every frame the action or its sub-state
changes, the frame Mario enters ACT_WAITING_FOR_DIALOG (if he does), the
frame he enters a READING action, the frame the box itself actually opens
(MARIO_ACTION_STATE reaching its kind's threshold) with the gap from the
READING entry, and Usamune's own raw counter (USAMUNE_OVERALL) at each.
CTRL+C to stop.
"""
import sys
import time

from sm64_events.memory.addresses import (ACT_READING_AUTOMATIC_DIALOG,
                                           ACT_READING_NPC_DIALOG,
                                           ACT_WAITING_FOR_DIALOG, CURR_AREA,
                                           CURR_LEVEL, DIALOG_ACTIONS,
                                           GLOBAL_TIMER, MARIO_ACTION,
                                           MARIO_ACTION_STATE,
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

# The MARIO_ACTION_STATE value at which each reading action's handler
# actually creates the dialog box -- see addresses.MARIO_ACTION_STATE's own
# comment for the decomp lines this comes from. Not in DIALOG_ACTIONS/
# READING_ACTIONS because it answers a different question (WHEN inside the
# action, not WHICH action).
BOX_OPENS_AT_STATE = {
    ACT_READING_NPC_DIALOG: 8,
    ACT_READING_AUTOMATIC_DIALOG: 9,
}

# A textbox that never resolves (Mario walks away, a reset) should not hang
# the trace forever -- generous ceiling, a real dialog opens well inside it.
TRACE_FRAMES = 300


def sample(mem) -> dict:
    return {
        "timer": mem.read_u32(GLOBAL_TIMER),
        "action": mem.read_u32(MARIO_ACTION),
        "action_state": mem.read_u16(MARIO_ACTION_STATE),
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


def box_opens(trace: list[dict], reading: dict | None) -> dict | None:
    """The first sample, at or after `reading`, whose action_state reaches
    the box-open value for that action -- the frame the game itself creates
    the dialog box, per BOX_OPENS_AT_STATE. None if the action isn't one
    with a known threshold, or the window closed before it got there.
    """
    if reading is None:
        return None
    threshold = BOX_OPENS_AT_STATE.get(reading["action"])
    if threshold is None:
        return None
    for row in trace:
        if row["timer"] < reading["timer"]:
            continue
        if row["action"] == reading["action"] and row["action_state"] >= threshold:
            return row
    return None


def report(trace: list[dict]) -> None:
    entry = trace[0]
    waiting = first_entry(trace, frozenset({ACT_WAITING_FOR_DIALOG}))
    reading = first_entry(trace, READING_ACTIONS)
    opened = box_opens(trace, reading)

    print()
    print(f"=== TEXTBOX at frame {entry['timer']}  "
          f"level {entry['level']} area {entry['area']} ===")
    print(f"  {'d_frame':>8}  {'action':>26}  {'state':>6}  "
          f"{'action_timer':>12}  {'counter':>7}")
    prev_key = None
    for row in trace:
        key = (row["action"], row["action_state"])
        if key == prev_key:
            continue
        prev_key = key
        name = ACTION_NAMES.get(row["action"], f"{row['action']:#010x}")
        print(f"  {row['timer'] - entry['timer']:>8}  {name:>26}  "
              f"{row['action_state']:>6}  {row['action_timer']:>12}  "
              f"{row['counter']:>7}")

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
              f"(counter {reading['counter']}) -- this is the frame the "
              f"shipped 'textbox' moment is currently timed from")
    else:
        print("  -> no READING action observed before the window closed -- "
              "widen TRACE_FRAMES or the box never opened")
    if waiting and reading:
        gap = reading["timer"] - waiting["timer"]
        print(f"  -> GAP: {gap} frame(s), {gap / 30:.2f}s at 30fps, between "
              f"WAITING and READING")
    if opened:
        gap = opened["timer"] - reading["timer"]
        print(f"  -> BOX ACTUALLY OPENS at frame {opened['timer']}  "
              f"(counter {opened['counter']}), action_state "
              f"{opened['action_state']} -- {gap} frame(s), {gap / 30:.2f}s "
              f"AFTER the READING entry above. THIS is the number Usamune's "
              f"screen should match.")
    elif reading and reading["action"] in BOX_OPENS_AT_STATE:
        print("  -> action_state never reached the box-open value in this "
              "window -- widen TRACE_FRAMES, or he closed/walked away "
              "before the box appeared")
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

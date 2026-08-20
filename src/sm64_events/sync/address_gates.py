"""Gates for every RAM address `memory/layout.py` names, plus the two facts
they all sit on top of: which ROM is attached, and where segment 0x13 (the
behaviour table) lives in RAM this run.

WHY THESE READ RAW MEMORY DIRECTLY, NEVER `ctx.snapshots()`: a `SnapshotReader`
refuses to build over a layout missing any of twelve fields
(`core/snapshot.py::_REQUIRED_FIELDS`) — including the two Usamune globals,
which are HUNTS and the last things this file confirms. If `address.curr_level`
tried to read through a snapshot it could never run before the hunts do, which
inverts the order the plan puts these gates in. So every check here reads
`ctx.raw()` at its own candidate address, exactly the way the nine research
probes this file replaces (`tools/probe_warp_block.py`, `tools/probe_textbox.py`
for the FIELDS they poll, not the file itself) always did. Calibration gates
(`calibration_gates.py`) run strictly after every gate in this file, so by
the time anything needs the real detector stack over `ctx.snapshots()`, the
layout it requires is already complete.

WHY A `Verdict` NEVER RAISES: a human is on the other end of every non-auto
gate, and "he did something else first" or "the emulator lagged" are facts
about THIS run, not bugs — `Verdict("failed", evidence=...)` reports them
the same way a wrong candidate does. `sync/runner.py` still wraps every
check in a try/except -- that guards a check's own BUG, which is a different
thing from a game-state miss and is reported as `failed` with the traceback's
last line.

WHY EVERY POLLING LOOP HAS A NAMED TIMEOUT CONSTANT: a gate that never sees
its live cue (he grabbed the wrong star, the ROM never entered the level)
must still return, so `tools/sync_version.py` never hangs. Each loop below
is bounded by a module-level `..._TIMEOUT_S`/`..._SECONDS` constant named
for what it is waiting on, and a loop that exhausts its budget returns
`Verdict("failed", ...)` rather than looping forever.
"""
from sm64_events.memory import addresses as A
from sm64_events.memory.behaviours import base_from_mario, pointer_of, symbol_of
from sm64_events.inputs.frame import button_names, fits_controller
from sm64_events.memory.version_probe import detect_version
from sm64_events.sync.checks import (check_ticks, near, parse_frames,
                                     pool_contains, scan_ticking_u16, scan_u16,
                                     survivors, ticks_per_second)
from sm64_events.sync.gates import Gate, Verdict, register

# How often a polling loop re-reads memory. Faster than the 30 Hz the game
# itself updates at, so no action/field edge is missed between polls.
POLL_INTERVAL_S = 1 / 60

GLOBAL_TIMER_SAMPLE_SECONDS = 2.0
MARIO_STRUCT_EDGE_TIMEOUT_S = 10.0     # "stand still, then jump once"
CURR_LEVEL_TIMEOUT_S = 60.0
CURR_AREA_TIMEOUT_S = 60.0
DOOR_TIMEOUT_S = 30.0
STAR_GRAB_EDGE_TIMEOUT_S = 180.0       # generous: grabbing a star takes a while
STAR_GRAB_CONFIRM_WINDOW_S = 5.0
WARP_TOUCH_TIMEOUT_S = 30.0
WARP_TRACE_SECONDS = 3.0               # ~90 frames; the real ride is 20-26
HUNT_MAX_ROUNDS = 4
HUNT_SURVIVOR_CEILING = 3
USAMUNE_CONTRACT_SAMPLE_SECONDS = 2.0
USAMUNE_RESULT_SETTLE_SECONDS = 3.0
USAMUNE_TIMER_TIMEOUT_S = 60.0
# The two-image differential for the section counter (diagnostics only): a
# u32 that was at least this many frames old before the area load and at
# most RESET_CEILING frames old just after it. RDRAM is overwhelmingly zeros,
# so a one-image "near zero" scan finds hundreds of thousands (review,
# 2026-08-15) -- only the BEFORE image can narrow it.
USAMUNE_TIMER_MIN_BEFORE = 300
USAMUNE_TIMER_RESET_CEILING = 30
# The running overall timer: how long the two-image tick scan spans, and how
# far the value he typed may have moved on by the time the scan runs. The
# runner sees only when Enter arrived, never when he READ the screen, so the
# window allows TYPING_ALLOWANCE_S of typing (forward only -- the counter
# runs) plus the measured post-Enter delay, plus a little slack either way.
# The tick constraint does the selecting: a handful of u16s in 8 MB advance
# at 30/s, so a 20-second window is still one address.
TICK_SCAN_SECONDS = 1.0
TYPING_ALLOWANCE_S = 20.0
TYPED_VALUE_SLACK_FRAMES = 60

# `addresses.LEVEL_NAMES[24] == "Whomp's Fortress"` — no named level constant
# exists for it (only the Bowser/BitX/HMC/DDD ids get one), so this names it
# here rather than leaving a bare 24 in the check below.
WHOMPS_FORTRESS_LEVEL = 24
# `memory/layout.py`'s own row note: "castle lobby 1 / upstairs 2 / basement 3".
CASTLE_LOBBY_AREA = 1
CASTLE_BASEMENT_AREA = 3

STAR_GRAB_INSTRUCTION = "Grab any star, then tell me which one (course,star)."
WARP_INSTRUCTION = "Jump into the BitDW pipe (basement)."


def _value_or_missing(candidate: int | None) -> Verdict | None:
    """The one-line guard every check below opens with — `None` means "keep
    going with `candidate`", a `Verdict` means "stop, this is the answer"."""
    if candidate is None:
        return Verdict("missing", evidence="no candidate: hunt first")
    return None


# --- version --------------------------------------------------------------

def _check_version_rom(ctx) -> Verdict:
    detected = detect_version(ctx.raw())
    if detected == ctx.version:
        return Verdict("verified", evidence=f"ROM header reports {detected!r}")
    return Verdict("failed", measured={"detected": detected},
                   evidence=f"ROM header reports {detected!r}, expected {ctx.version!r}")


register(Gate(
    id="version.rom", feature="version", kind="address", auto=True,
    instruction="No action needed — reads the ROM header PJ64 already has open.",
    proves="the attached ROM is the version this sync run claims to test.",
    check=_check_version_rom,
))


# --- global_timer -----------------------------------------------------------

def _check_global_timer(ctx) -> Verdict:
    candidate = ctx.candidate("global_timer")
    missing = _value_or_missing(candidate)
    if missing is not None:
        return missing
    samples = []
    start = ctx.now()
    while ctx.now() - start < GLOBAL_TIMER_SAMPLE_SECONDS:
        samples.append((ctx.now(), ctx.raw().read_u32(candidate)))
        ctx.sleep(POLL_INTERVAL_S)
    verdict = check_ticks(samples, what="global_timer")
    if verdict.status != "verified":
        return verdict
    return Verdict("verified", value=candidate, measured=verdict.measured,
                   evidence=verdict.evidence)


register(Gate(
    id="address.global_timer", feature="version", kind="address", auto=True,
    needs=("version.rom",), timeout_s=GLOBAL_TIMER_SAMPLE_SECONDS + 10,
    instruction="No action needed — just stay attached for two seconds.",
    proves="gGlobalTimer's candidate address is a live 30 fps frame counter.",
    check=_check_global_timer,
))


# --- mario_struct ------------------------------------------------------------

def _check_mario_struct(ctx) -> Verdict:
    candidate = ctx.candidate("mario_struct")
    missing = _value_or_missing(candidate)
    if missing is not None:
        return missing
    mem = ctx.raw()
    action_addr = candidate + A.MARIO_ACTION_OFF
    stars_addr = candidate + A.MARIO_NUM_STARS_OFF
    first_action = mem.read_u32(action_addr)
    if first_action == 0:
        return Verdict("failed", evidence=f"mario_action reads 0 at {candidate:#x}")
    start = ctx.now()
    changed = False
    while ctx.now() - start < MARIO_STRUCT_EDGE_TIMEOUT_S:
        if mem.read_u32(action_addr) != first_action:
            changed = True
            break
        ctx.sleep(POLL_INTERVAL_S)
    if not changed:
        return Verdict("failed",
                       evidence=f"mario_action never changed from {first_action:#x} "
                                f"in {MARIO_STRUCT_EDGE_TIMEOUT_S:.0f}s")
    stars = mem.read_s16(stars_addr)
    if not (0 <= stars <= 182):
        return Verdict("failed", measured={"num_stars": stars},
                       evidence=f"num_stars reads {stars}, outside 0..182")
    return Verdict("verified", value=candidate,
                   measured={"num_stars": stars},
                   evidence=f"action edge seen from {first_action:#x}; num_stars={stars}")


register(Gate(
    id="address.mario_struct", feature="version", kind="address",
    needs=("version.rom",), timeout_s=MARIO_STRUCT_EDGE_TIMEOUT_S + 10,
    instruction="Stand still, then jump once.",
    proves="gMarioStates[0]'s candidate address holds a real, changing action "
           "and a plausible star count.",
    check=_check_mario_struct,
))


# --- object_pool / mario_object / behaviour.base -----------------------------

def _check_object_pool(ctx) -> Verdict:
    pool = ctx.candidate("object_pool")
    mario_object = ctx.candidate("mario_object")
    if pool is None or mario_object is None:
        return Verdict("missing", evidence="no candidate: hunt first")
    try:
        ctx.raw().read_block(pool, A.OBJECT_COUNT * A.OBJECT_SIZE)
    except Exception as exc:  # a torn/unreadable region is a finding, not a crash
        return Verdict("failed", evidence=f"pool not readable: {exc!r}")
    pointer = ctx.raw().read_u32(mario_object)
    if not pool_contains(pool, pointer, slot_size=A.OBJECT_SIZE, slots=A.OBJECT_COUNT):
        return Verdict("failed", measured={"mario_object_pointer": pointer},
                       evidence=f"mario_object's pointer {pointer:#x} is not a "
                                f"slot boundary inside pool {pool:#x}")
    return Verdict("verified", value=pool,
                   evidence=f"{A.OBJECT_COUNT} slots readable at {pool:#x}; "
                            f"mario_object's pointer lands inside")


register(Gate(
    id="address.object_pool", feature="version", kind="address", auto=True,
    needs=("version.rom",),
    instruction="No action needed — reads the pool region and the "
               "mario_object candidate.",
    proves="gObjectPool's candidate region is readable and contains the "
           "mario_object candidate's pointer.",
    check=_check_object_pool,
))


def _check_mario_object(ctx) -> Verdict:
    candidate = ctx.candidate("mario_object")
    pool = ctx.candidate("object_pool")
    if candidate is None or pool is None:
        return Verdict("missing", evidence="no candidate: hunt first")
    pointer = ctx.raw().read_u32(candidate)
    if not pool_contains(pool, pointer, slot_size=A.OBJECT_SIZE, slots=A.OBJECT_COUNT):
        return Verdict("failed", measured={"pointer": pointer},
                       evidence=f"pointer {pointer:#x} is not a slot boundary "
                                f"inside pool {pool:#x}")
    return Verdict("verified", value=candidate,
                   evidence=f"pointer {pointer:#x} lands on a pool slot boundary")


register(Gate(
    id="address.mario_object", feature="version", kind="address", auto=True,
    needs=("address.object_pool",),
    instruction="No action needed — needs address.object_pool verified first.",
    proves="gMarioObject's candidate address holds a pointer into the object pool.",
    check=_check_mario_object,
))


def _check_behaviour_base(ctx) -> Verdict:
    mario_object = ctx.candidate("mario_object")
    if mario_object is None:
        return Verdict("missing", evidence="no candidate: hunt first")
    mem = ctx.raw()
    mario_object_ptr = mem.read_u32(mario_object)
    behaviour_ptr = mem.read_u32(mario_object_ptr + A.OBJECT_BEHAVIOR)
    base = base_from_mario(ctx.version, behaviour_ptr)
    return Verdict("verified", value=base,
                   evidence=f"segment base {base:#x} from bhvMario at {behaviour_ptr:#x}")


register(Gate(
    id="behaviour.base", feature="version", kind="behaviour", auto=True,
    needs=("address.mario_object",),
    instruction="No action needed — derived from Mario's own object.",
    proves="segment 0x13's RAM base, derived from Mario's own object with no "
           "human step.",
    check=_check_behaviour_base,
))


def _check_object_pool_confirm(ctx) -> Verdict:
    pool = ctx.candidate("object_pool")
    mario_object = ctx.candidate("mario_object")
    base = ctx.candidate("behaviour_base")
    if pool is None or mario_object is None or base is None:
        return Verdict("missing", evidence="no candidate: hunt first")
    mem = ctx.raw()
    mario_object_ptr = mem.read_u32(mario_object)
    if not pool_contains(pool, mario_object_ptr, slot_size=A.OBJECT_SIZE,
                         slots=A.OBJECT_COUNT):
        return Verdict("failed", evidence=f"mario_object pointer {mario_object_ptr:#x} "
                                          f"is not a slot boundary inside pool {pool:#x}")
    behaviour_ptr = mem.read_u32(mario_object_ptr + A.OBJECT_BEHAVIOR)
    expected = pointer_of(ctx.version, "bhvMario", base=base)
    measured = {"behaviour": behaviour_ptr, "expected": expected}
    if behaviour_ptr == expected:
        return Verdict("verified", value=pool, measured=measured,
                       evidence="Mario's own slot behaviour matches bhvMario")
    return Verdict("failed", measured=measured,
                   evidence="Mario's own slot behaviour does not match bhvMario")


register(Gate(
    id="address.object_pool.confirm", feature="version", kind="address", auto=True,
    needs=("behaviour.base",),
    instruction="No action needed — re-checks the pool now that the "
               "behaviour base is known.",
    proves="Mario's own object slot inside the candidate pool resolves to "
           "bhvMario — the pool candidate really is the pool.",
    check=_check_object_pool_confirm,
))


# --- behaviour.door -----------------------------------------------------------

_DOOR_SYMBOLS = ("bhvDoor", "bhvDoorWarp")


def _check_behaviour_door(ctx) -> Verdict:
    mario_struct = ctx.candidate("mario_struct")
    pool = ctx.candidate("object_pool")
    base = ctx.candidate("behaviour_base")
    if mario_struct is None or pool is None or base is None:
        return Verdict("missing", evidence="no candidate: hunt first")
    mem = ctx.raw()
    used_obj_addr = mario_struct + A.MARIO_USED_OBJ_OFF
    start = ctx.now()
    while ctx.now() - start < DOOR_TIMEOUT_S:
        pointer = mem.read_u32(used_obj_addr)
        if pointer and pool_contains(pool, pointer, slot_size=A.OBJECT_SIZE,
                                     slots=A.OBJECT_COUNT):
            behaviour_ptr = mem.read_u32(pointer + A.OBJECT_BEHAVIOR)
            symbol = symbol_of(ctx.version, behaviour_ptr, base=base)
            if symbol in _DOOR_SYMBOLS:
                return Verdict("verified", evidence=f"used-object resolves to {symbol}")
            return Verdict("failed", measured={"symbol": symbol},
                           evidence=f"used-object resolves to {symbol}, "
                                    f"expected one of {_DOOR_SYMBOLS}")
        ctx.sleep(POLL_INTERVAL_S)
    return Verdict("failed", evidence="no slot-boundary pointer at "
                                      f"MARIO_USED_OBJ in {DOOR_TIMEOUT_S:.0f}s")


register(Gate(
    id="behaviour.door", feature="landmarks", kind="behaviour",
    needs=("behaviour.base",), timeout_s=DOOR_TIMEOUT_S + 10,
    instruction="Open any wooden door.",
    proves="a touched door resolves through the behaviour tables to bhvDoor "
           "or bhvDoorWarp.",
    check=_check_behaviour_door,
))


# --- curr_level / curr_area ---------------------------------------------------

def _check_curr_level(ctx) -> Verdict:
    candidate = ctx.candidate("curr_level")
    missing = _value_or_missing(candidate)
    if missing is not None:
        return missing
    mem = ctx.raw()
    start = ctx.now()
    level = None
    while ctx.now() - start < CURR_LEVEL_TIMEOUT_S:
        level = mem.read_s16(candidate)
        if level == WHOMPS_FORTRESS_LEVEL:
            return Verdict("verified", value=candidate,
                           evidence=f"reads {level} (Whomp's Fortress)")
        ctx.sleep(POLL_INTERVAL_S)
    return Verdict("failed", measured={"level": level},
                   evidence=f"never read {WHOMPS_FORTRESS_LEVEL} in "
                            f"{CURR_LEVEL_TIMEOUT_S:.0f}s")


register(Gate(
    id="address.curr_level", feature="castle areas", kind="address",
    needs=("version.rom",), timeout_s=CURR_LEVEL_TIMEOUT_S + 10,
    instruction="Enter Whomp's Fortress (any way in).",
    proves="gCurrLevelNum's candidate address reads the Whomp's Fortress level id.",
    check=_check_curr_level,
))


def _check_curr_area(ctx) -> Verdict:
    candidate = ctx.candidate("curr_area")
    missing = _value_or_missing(candidate)
    if missing is not None:
        return missing
    mem = ctx.raw()
    seen_lobby = False
    start = ctx.now()
    area = None
    while ctx.now() - start < CURR_AREA_TIMEOUT_S:
        area = mem.read_s16(candidate)
        if area == CASTLE_LOBBY_AREA:
            seen_lobby = True
        if seen_lobby and area == CASTLE_BASEMENT_AREA:
            return Verdict("verified", value=candidate,
                           evidence=f"reads {CASTLE_LOBBY_AREA} then "
                                    f"{CASTLE_BASEMENT_AREA}")
        ctx.sleep(POLL_INTERVAL_S)
    return Verdict("failed", measured={"seen_lobby": seen_lobby, "last_area": area},
                   evidence="never saw lobby then basement in "
                            f"{CURR_AREA_TIMEOUT_S:.0f}s")


register(Gate(
    id="address.curr_area", feature="castle areas", kind="address",
    needs=("version.rom",), timeout_s=CURR_AREA_TIMEOUT_S + 10,
    instruction="Walk from the lobby down to the basement.",
    proves="gCurrAreaIndex's candidate address distinguishes the lobby from "
           "the basement.",
    check=_check_curr_area,
))


# --- last_completed_course / last_completed_star ------------------------------

def _grab_star_and_confirm(ctx, field: str, which: int) -> Verdict:
    """Shared by both last_completed gates: prompt for the star he is about
    to grab, wait for the star-grab action EDGE (the same set star_grab.py
    keys on), then confirm `field`'s candidate names it within
    STAR_GRAB_CONFIRM_WINDOW_S. Each gate re-runs this rather than sharing a
    result — the human performs the grab once per gate, same as the warp
    block's three fields below."""
    mario_struct = ctx.candidate("mario_struct")
    target = ctx.candidate(field)
    if mario_struct is None or target is None:
        return Verdict("missing", evidence="no candidate: hunt first")
    answer = ctx.prompt(STAR_GRAB_INSTRUCTION)
    course_text, _, star_text = answer.partition(",")
    try:
        expected = (int(course_text.strip()), int(star_text.strip()))
    except ValueError:
        return Verdict("failed", evidence=f"could not parse {answer!r} as course,star")
    mem = ctx.raw()
    action_addr = mario_struct + A.MARIO_ACTION_OFF
    previous_action = mem.read_u32(action_addr)
    start = ctx.now()
    edge_at = None
    while ctx.now() - start < STAR_GRAB_EDGE_TIMEOUT_S:
        action = mem.read_u32(action_addr)
        if action in A.STAR_GRAB_ACTIONS and previous_action not in A.STAR_GRAB_ACTIONS:
            edge_at = ctx.now()
            break
        previous_action = action
        ctx.sleep(POLL_INTERVAL_S)
    if edge_at is None:
        return Verdict("failed", evidence="no star-grab action edge in "
                                          f"{STAR_GRAB_EDGE_TIMEOUT_S:.0f}s")
    expected_value = expected[which]
    value = None
    while ctx.now() - edge_at < STAR_GRAB_CONFIRM_WINDOW_S:
        value = mem.read_s8(target)
        if value == expected_value:
            return Verdict("verified", value=target,
                           evidence=f"{field} reads {value} within "
                                    f"{STAR_GRAB_CONFIRM_WINDOW_S:.0f}s of the grab")
        ctx.sleep(POLL_INTERVAL_S)
    return Verdict("failed", measured={"read": value, "expected": expected_value},
                   evidence=f"{field} never matched the typed star within the window")


register(Gate(
    id="address.last_completed_course", feature="star grab", kind="address",
    needs=("address.mario_struct",),
    timeout_s=STAR_GRAB_EDGE_TIMEOUT_S + STAR_GRAB_CONFIRM_WINDOW_S + 10,
    instruction=STAR_GRAB_INSTRUCTION,
    proves="gLastCompletedCourseNum's candidate names the course of the star "
           "he just grabbed.",
    check=lambda ctx: _grab_star_and_confirm(ctx, "last_completed_course", 0),
))
register(Gate(
    id="address.last_completed_star", feature="star grab", kind="address",
    needs=("address.mario_struct",),
    timeout_s=STAR_GRAB_EDGE_TIMEOUT_S + STAR_GRAB_CONFIRM_WINDOW_S + 10,
    instruction=STAR_GRAB_INSTRUCTION,
    proves="gLastCompletedStarNum's candidate names the star he just grabbed.",
    check=lambda ctx: _grab_star_and_confirm(ctx, "last_completed_star", 1),
))


# --- the warp block: pending_warp_op / delayed_warp_timer / warp_dest --------

def _read_warp_dest(mem, dest_addr: int) -> tuple[int, int, int, int]:
    return (mem.read_u8(dest_addr + A.WARP_DEST_TYPE_OFF),
            mem.read_u8(dest_addr + A.WARP_DEST_LEVEL_OFF),
            mem.read_u8(dest_addr + A.WARP_DEST_AREA_OFF),
            mem.read_u8(dest_addr + A.WARP_DEST_NODE_OFF))


def _watch_warp_block(ctx) -> dict | None:
    """One touch, three fields watched at once — `tools/probe_warp_block.py`'s
    `watch_touches`/`sample()` logic, ported onto `ctx`. Shared by the three
    gates below; each calls it fresh (see `_grab_star_and_confirm`'s note —
    same shape, the human re-does the touch per gate)."""
    mario_struct = ctx.candidate("mario_struct")
    global_timer = ctx.candidate("global_timer")
    op_addr = ctx.candidate("pending_warp_op")
    timer_addr = ctx.candidate("delayed_warp_timer")
    dest_addr = ctx.candidate("warp_dest")
    if None in (mario_struct, global_timer, op_addr, timer_addr, dest_addr):
        return None
    mem = ctx.raw()
    action_addr = mario_struct + A.MARIO_ACTION_OFF
    previous_action = mem.read_u32(action_addr)
    start = ctx.now()
    while ctx.now() - start < WARP_TOUCH_TIMEOUT_S:
        action = mem.read_u32(action_addr)
        if action in A.WARP_ENTRY_ACTIONS and previous_action not in A.WARP_ENTRY_ACTIONS:
            break
        previous_action = action
        ctx.sleep(POLL_INTERVAL_S)
    else:
        return None
    edge_frame = mem.read_u32(global_timer)
    dest_before = _read_warp_dest(mem, dest_addr)
    op_nonzero_at = None
    timer_peak = 0
    timer_reached_zero_at = None
    dest_changed_at = None
    dest_after = dest_before
    trace_start = ctx.now()
    while ctx.now() - trace_start < WARP_TRACE_SECONDS:
        frame = mem.read_u32(global_timer)
        op = mem.read_s16(op_addr)
        timer = mem.read_s16(timer_addr)
        dest_after = _read_warp_dest(mem, dest_addr)
        if op != 0 and op_nonzero_at is None:
            op_nonzero_at = frame
        if timer > timer_peak:
            timer_peak = timer
        if timer == 0 and timer_peak > 0 and timer_reached_zero_at is None:
            timer_reached_zero_at = frame
        if dest_after != dest_before and dest_changed_at is None:
            dest_changed_at = frame
        ctx.sleep(POLL_INTERVAL_S)
    return {
        "edge_frame": edge_frame,
        "op_nonzero_delay": (op_nonzero_at - edge_frame) if op_nonzero_at is not None else None,
        "timer_peak": timer_peak,
        "timer_reached_zero": timer_reached_zero_at is not None,
        "dest_before": dest_before,
        "dest_after": dest_after,
        "dest_changed": dest_changed_at is not None,
    }


# "op pulses nonzero within 4 frames of the touch" (Task 9's own wording).
WARP_OP_PULSE_WINDOW_FRAMES = 4
# The delayed-warp countdown is ~20 frames on US (probe_warp_block, both
# pipes); this band is generous enough to survive poll jitter either side.
WARP_TIMER_PEAK_LOW = 12
WARP_TIMER_PEAK_HIGH = 28


def _check_pending_warp_op(ctx) -> Verdict:
    trace = _watch_warp_block(ctx)
    if trace is None:
        return Verdict("failed", evidence="no warp-entry action edge in "
                                          f"{WARP_TOUCH_TIMEOUT_S:.0f}s, or a "
                                          "missing candidate")
    delay = trace["op_nonzero_delay"]
    measured = {"delay_frames": delay}
    if delay is not None and delay <= WARP_OP_PULSE_WINDOW_FRAMES:
        return Verdict("verified", value=ctx.candidate("pending_warp_op"),
                       measured=measured,
                       evidence=f"op went nonzero {delay} frame(s) after the touch")
    return Verdict("failed", measured=measured,
                   evidence="op never pulsed nonzero within "
                            f"{WARP_OP_PULSE_WINDOW_FRAMES} frames of the touch")


def _check_delayed_warp_timer(ctx) -> Verdict:
    trace = _watch_warp_block(ctx)
    if trace is None:
        return Verdict("failed", evidence="no warp-entry action edge in "
                                          f"{WARP_TOUCH_TIMEOUT_S:.0f}s, or a "
                                          "missing candidate")
    peak, reached_zero = trace["timer_peak"], trace["timer_reached_zero"]
    measured = {"peak": peak, "reached_zero": reached_zero}
    if WARP_TIMER_PEAK_LOW <= peak <= WARP_TIMER_PEAK_HIGH and reached_zero:
        return Verdict("verified", value=ctx.candidate("delayed_warp_timer"),
                       measured=measured,
                       evidence=f"counted down from {peak} to 0")
    return Verdict("failed", measured=measured,
                   evidence="timer did not count down ~20->0 as expected")


def _check_warp_dest(ctx) -> Verdict:
    trace = _watch_warp_block(ctx)
    if trace is None:
        return Verdict("failed", evidence="no warp-entry action edge in "
                                          f"{WARP_TOUCH_TIMEOUT_S:.0f}s, or a "
                                          "missing candidate")
    if trace["dest_changed"]:
        return Verdict("verified", value=ctx.candidate("warp_dest"),
                       evidence=f"changed from {trace['dest_before']} to "
                                f"{trace['dest_after']}")
    return Verdict("failed", evidence="warp_dest never changed during the window")


_WARP_TIMEOUT_S = WARP_TOUCH_TIMEOUT_S + WARP_TRACE_SECONDS + 10

register(Gate(
    id="address.pending_warp_op", feature="warps & entrances", kind="address",
    needs=("address.mario_struct", "address.global_timer"), timeout_s=_WARP_TIMEOUT_S,
    instruction=WARP_INSTRUCTION,
    proves="sDelayedWarpOp's candidate pulses nonzero right after a pipe touch.",
    check=_check_pending_warp_op,
))
register(Gate(
    id="address.delayed_warp_timer", feature="warps & entrances", kind="address",
    needs=("address.mario_struct", "address.global_timer"), timeout_s=_WARP_TIMEOUT_S,
    instruction=WARP_INSTRUCTION,
    proves="sDelayedWarpTimer's candidate counts a pipe ride down to 0.",
    check=_check_delayed_warp_timer,
))
register(Gate(
    id="address.warp_dest", feature="warps & entrances", kind="address",
    needs=("address.mario_struct", "address.global_timer"), timeout_s=_WARP_TIMEOUT_S,
    instruction=WARP_INSTRUCTION,
    proves="sWarpDest's candidate changes once the ride resolves.",
    check=_check_warp_dest,
))


# --- hud_display / hud_timer_running -----------------------------------------

def _check_hud_display(ctx) -> Verdict:
    """The layout row is gHudDisplay's OWN address; the race timer sits
    HUD_TIMER_OFF into it (review 2026-08-15: storing base+0xC under the
    symbol's name would have promoted a JP address 12 bytes off)."""
    candidate = ctx.candidate("hud_display")
    missing = _value_or_missing(candidate)
    if missing is not None:
        return missing
    value = ctx.raw().read_u16(candidate + A.HUD_TIMER_OFF)
    if value == 0:
        return Verdict("verified", value=candidate,
                       evidence="0 under Usamune, as on US")
    return Verdict("failed", measured={"value": value},
                   evidence=f"reads {value}, expected 0 under Usamune")


def _check_hud_timer_running(ctx) -> Verdict:
    candidate = ctx.candidate("hud_timer_running")
    missing = _value_or_missing(candidate)
    if missing is not None:
        return missing
    value = ctx.raw().read_s8(candidate)
    if value == 0:
        return Verdict("verified", value=candidate,
                       evidence="0 under Usamune, as on US")
    return Verdict("failed", measured={"value": value},
                   evidence=f"reads {value}, expected 0 under Usamune")


register(Gate(
    id="address.hud_display", feature="IGT clock", kind="address", auto=True,
    needs=("version.rom",),
    instruction="No action needed — reads the candidate once.",
    proves="gHudDisplay's candidate stays 0 under Usamune, as on US.",
    check=_check_hud_display,
))
register(Gate(
    id="address.hud_timer_running", feature="IGT clock", kind="address", auto=True,
    needs=("version.rom",),
    instruction="No action needed — reads the candidate once.",
    proves="sTimerRunning's candidate stays 0 under Usamune, as on US.",
    check=_check_hud_timer_running,
))


# --- usamune_overall / usamune_star_result / usamune_timer -------------------

def _hunt_frozen_u16(ctx, prompt_text: str) -> tuple[list[int], int | None]:
    """Cheat-Engine-style narrowing (`tools/hunt_value.py`'s technique, ported
    onto `ctx`) for a value that is FROZEN on screen: prompt for the displayed
    value, scan a fresh image for it, intersect with the previous round's
    survivors. Stops once the survivor count is small enough for a contract to
    finish the job, or after HUNT_MAX_ROUNDS. Returns the survivors AND the
    last frame count typed (usamune_star_result's contract compares against
    it). A RUNNING value must go through `_hunt_ticking_u16` instead: the
    typed number has moved on before this scan runs."""
    found: list[int] | None = None
    last_frames: int | None = None
    for _ in range(HUNT_MAX_ROUNDS):
        answer = ctx.prompt(prompt_text)
        frames = parse_frames(answer)
        if frames is None:
            continue
        last_frames = frames
        image = ctx.raw().read_image()
        this_round = scan_u16(image, frames)
        found = this_round if found is None else survivors([found, this_round])
        if len(found) <= HUNT_SURVIVOR_CEILING:
            break
    return (found or []), last_frames


def _hunt_ticking_u16(ctx, prompt_text: str) -> tuple[list[int], int | None]:
    """The hunt for a RUNNING counter (Usamune's overall timer). Two images
    TICK_SCAN_SECONDS apart name every u16 that advances at game rate -- a
    handful in 8 MB -- and the value he typed only has to be NEAR one of
    them: within 30 frames per second that passed since he answered, plus
    TYPED_VALUE_SLACK_FRAMES. Repeats up to HUNT_MAX_ROUNDS intersecting the
    tickers, so a second reading kills any counter that merely happened to
    tick during the first."""
    found: list[int] | None = None
    last_frames: int | None = None
    for _ in range(HUNT_MAX_ROUNDS):
        answer = ctx.prompt(prompt_text)
        typed_at = ctx.now()
        frames = parse_frames(answer)
        if frames is None:
            continue
        last_frames = frames
        mem = ctx.raw()
        before = mem.read_image()
        ctx.sleep(TICK_SCAN_SECONDS)
        after = mem.read_image()
        scan_at = ctx.now()
        tickers = scan_ticking_u16(before, after, TICK_SCAN_SECONDS)
        # `before` was taken (scan_at - TICK_SCAN_SECONDS - typed_at) seconds
        # after he answered; the value there is the typed one plus that much
        # game time, give or take the slack.
        moved = max(0, int(round((scan_at - TICK_SCAN_SECONDS - typed_at) * 30)))
        # The counter only runs FORWARD from what he read: [typed - slack,
        # typed + typing allowance + post-Enter delay + slack].
        forward = int(TYPING_ALLOWANCE_S * 30) + moved
        centre = frames + forward // 2
        this_round = [address for address in tickers
                      if near(before, address, centre,
                              forward // 2 + TYPED_VALUE_SLACK_FRAMES)]
        found = this_round if found is None else survivors([found, this_round])
        if len(found) <= HUNT_SURVIVOR_CEILING:
            break
    return (found or []), last_frames


def _check_usamune_overall(ctx) -> Verdict:
    found, _ = _hunt_ticking_u16(
        ctx, "Stand in any course with the Usamune overall timer RUNNING and "
             "visible; type what it reads right now (e.g. 0'20\"20) and press "
             "Enter -- it keeps running, that is fine.")
    if not found:
        return Verdict("failed", evidence="no u16 counter ticked at game rate "
                                          "near the value typed")
    mem = ctx.raw()
    passing = []
    for address in found:
        samples = []
        start = ctx.now()
        while ctx.now() - start < USAMUNE_CONTRACT_SAMPLE_SECONDS:
            samples.append((ctx.now(), mem.read_u16(address)))
            ctx.sleep(POLL_INTERVAL_S)
        if 25.0 <= ticks_per_second(samples) <= 35.0:
            passing.append(address)
    if len(passing) == 1:
        return Verdict("verified", value=passing[0],
                       evidence=f"ticks ~30/s at {passing[0]:#x}, near the typed value")
    if passing:
        return Verdict("candidate", measured={"candidates": passing},
                       evidence=f"{len(passing)} candidates all tick ~30/s -- ambiguous; "
                                "type a second reading next run")
    return Verdict("failed", measured={"candidates": found},
                   evidence="no surviving candidate ticks like a running counter")


def _check_usamune_star_result(ctx) -> Verdict:
    found, last_frames = _hunt_frozen_u16(
        ctx, "Grab a star; once the result FREEZES on screen, type it.")
    if not found or last_frames is None:
        return Verdict("failed", evidence="no candidate survived the hunt")
    mem = ctx.raw()
    passing = []
    for address in found:
        if mem.read_u16(address) != last_frames:
            continue
        held = True
        start = ctx.now()
        while ctx.now() - start < USAMUNE_RESULT_SETTLE_SECONDS:
            if mem.read_u16(address) != last_frames:
                held = False
                break
            ctx.sleep(POLL_INTERVAL_S)
        if held:
            passing.append(address)
    if len(passing) == 1:
        return Verdict("verified", value=passing[0],
                       evidence=f"holds {last_frames} for "
                                f"{USAMUNE_RESULT_SETTLE_SECONDS:.0f}s at {passing[0]:#x}")
    if passing:
        return Verdict("candidate", measured={"candidates": passing},
                       evidence=f"{len(passing)} candidates all hold the typed "
                                "result -- ambiguous")
    return Verdict("failed", measured={"candidates": found, "expected": last_frames},
                   evidence="no surviving candidate held the typed result")


def _check_usamune_timer(ctx) -> Verdict:
    """DIAGNOSTICS ONLY, and its terminal state is `candidate`: the section
    counter is slot-dependent and no shipped read depends on it. Two images
    -- before and after an area load -- keep every u32 that read like a
    running counter before (>= MIN_BEFORE) and freshly reset after (<=
    RESET_CEILING); a one-image near-zero scan cannot narrow an 8 MB image
    that is mostly zeros."""
    area_addr = ctx.candidate("curr_area")
    if area_addr is None:
        return Verdict("missing", evidence="curr_area not verified yet")
    mem = ctx.raw()
    ctx.say("Walk into a subarea (a door inside a course, the pyramid, the "
            "volcano) when you are ready; watching for the area load.")
    previous_area = mem.read_s16(area_addr)
    before = mem.read_image()
    refreshed_at = start = ctx.now()
    while ctx.now() - start < USAMUNE_TIMER_TIMEOUT_S:
        if mem.read_s16(area_addr) != previous_area:
            break
        if ctx.now() - refreshed_at >= 1.0:   # keep BEFORE under a second old
            before = mem.read_image()
            refreshed_at = ctx.now()
        ctx.sleep(POLL_INTERVAL_S)
    else:
        return Verdict("failed", evidence="curr_area never changed in "
                                          f"{USAMUNE_TIMER_TIMEOUT_S:.0f}s")
    ctx.sleep(0.5)                       # let the load's own writes settle
    after = mem.read_image()
    words_before = memoryview(before).cast("I")
    words_after = memoryview(after).cast("I")
    found = [A.KSEG0_BASE + index * 4
             for index in range(min(len(words_before), len(words_after)))
             if words_before[index] >= USAMUNE_TIMER_MIN_BEFORE
             and words_after[index] <= USAMUNE_TIMER_RESET_CEILING]
    if not found:
        return Verdict("failed", evidence="no u32 counter reset across the area load")
    return Verdict("candidate", measured={"candidates": found[:20], "count": len(found)},
                   evidence=f"{len(found)} u32 counter(s) reset across the area load "
                            "-- diagnostics only; the section counter is never a "
                            "shipped read")


register(Gate(
    id="address.usamune_overall", feature="IGT clock", kind="address",
    needs=("version.rom",), timeout_s=180.0,
    instruction="Stand in any course with the Usamune overall timer RUNNING "
               "and visible; when asked, type what it reads right now.",
    proves="the RAM address holding Usamune's running overall star time "
           "(a u16 that ticks at game rate and reads near what he typed).",
    check=_check_usamune_overall,
))
register(Gate(
    id="address.usamune_star_result", feature="IGT clock", kind="address",
    needs=("version.rom",), timeout_s=180.0,
    instruction="Grab a star; once the result freezes on screen, type it "
               "when asked.",
    proves="the RAM address holding Usamune's frozen per-star result.",
    check=_check_usamune_star_result,
))
register(Gate(
    id="address.usamune_timer", feature="IGT clock", kind="address",
    optional=True, needs=("address.usamune_overall", "address.curr_area"),
    timeout_s=USAMUNE_TIMER_TIMEOUT_S + 10,
    instruction="Walk into a subarea (an area load inside a course) when told.",
    proves="candidates for Usamune's per-section counter -- DIAGNOSTICS ONLY, "
           "its terminal state is `candidate`; no shipped read depends on it.",
    check=_check_usamune_timer,
))


# --- player1_controller -----------------------------------------------------

CONTROLLER_HOLD_TIMEOUT_S = 60.0


def _check_player1_controller(ctx) -> Verdict:
    """Prove the candidate is the LIVE pad, not a struct-shaped coincidence.

    The razor is internal consistency (`inputs/frame.py::fits_controller`),
    which is why the human has to deflect the stick AND hold a button: a run
    of zeroes satisfies every range check anyone has ever written, and
    satisfies this one too while nothing is moving. That is not hypothetical
    -- the first hunt for this address returned exactly such a run, passed 200
    consecutive live reads, and was wrong (2026-08-20).
    """
    candidate = ctx.candidate("player1_controller")
    if candidate is None:
        # A HUNT row, so a version with no shipped value has no candidate to
        # offer -- no symbol map names this struct (the decomp's
        # gPlayer1Controller is a pointer variable, not the struct itself).
        # The hunt is `uv run python tools/probe_inputs.py --at scan`, which
        # sweeps for the shape and prints what fits; its answer comes back
        # here as the candidate.
        return Verdict("missing",
                       evidence="no candidate: run tools/probe_inputs.py "
                                "--at scan against this ROM to hunt it, then "
                                "re-run this gate")
    deflected = held = None
    start = ctx.now()
    while ctx.now() - start < CONTROLLER_HOLD_TIMEOUT_S:
        fit = fits_controller(ctx.raw().read_block(candidate,
                                                   A.CONTROLLER_SIZE))
        if fit is None:
            return Verdict("failed", value=candidate,
                           evidence="the struct stopped being self-consistent:"
                                    " its raw stick, processed stick and"
                                    " magnitude disagreed, so this address"
                                    " does not hold a controller")
        if max(abs(fit.raw_x), abs(fit.raw_y)) >= A.STICK_DEAD_ZONE:
            deflected = (fit.raw_x, fit.raw_y, fit.stick_mag)
        if fit.buttons:
            held = fit.buttons
        if deflected is not None and held is not None:
            return Verdict(
                "verified", value=candidate,
                measured=f"stick {deflected[0]:+d},{deflected[1]:+d}"
                         f" (magnitude {deflected[2]:.1f})",
                evidence=f"the stick left the dead zone and buttons"
                         f" {button_names(held) or ('none',)} were held, with"
                         f" the dead-zone and magnitude relation holding on"
                         f" every read")
        ctx.sleep(POLL_INTERVAL_S)
    return Verdict("failed", value=candidate,
                   evidence="never saw the stick leave the dead zone AND a "
                            "button held within the timeout")


register(Gate(
    id="address.player1_controller", feature="controller input", kind="address",
    needs=("version.rom",), timeout_s=CONTROLLER_HOLD_TIMEOUT_S + 10,
    instruction="Push the stick all the way to one edge and hold A at the "
                "same time.",
    proves="gPlayer1Controller's candidate address holds the live pad: its "
           "processed stick is its raw stick through the game's own dead "
           "zone, on every read.",
    check=_check_player1_controller,
))

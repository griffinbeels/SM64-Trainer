# tests/test_sync_address_gates.py
"""Offline tests for `sync/address_gates.py` — no PJ64, no ROM.

Every check here reads through a `FakeContext` over a `ScriptedMemory`: a
`BufferMemory`-shaped image (so the real N64 byte-order decode in
`memory/base.py` is always exercised) whose reads at a handful of chosen
addresses are OVERRIDDEN by a function of a shared scripted clock, so a
"live" edge (an action changing, a counter ticking, a timer counting down)
can be simulated without a real wait — `ctx.sleep()` advances the clock
directly, so a check with a 60-second internal timeout still runs instantly.

Candidates are the real US layout addresses (`memory.layout.US`) throughout,
matching the brief: "FakeContext... candidate() = US layout".
"""
from sm64_events.memory import addresses as A
from sm64_events.memory.behaviours import base_from_mario, pointer_of
from sm64_events.memory.buffer import BufferMemory
from sm64_events.memory.layout import US
from sm64_events.sync import address_gates as AG
from sm64_events.sync.gates import GATES

# Snapshot taken at IMPORT TIME (pytest collects every test module before
# running any test body), so this is immune to test_sync_gates_contract.py
# clearing the shared registry inside its own test functions later.
_REGISTERED_IDS = frozenset(gate.id for gate in GATES)

NON_STAR_ACTION = 0x0C400201        # any ordinary action (test_star_grab.py's own choice)
A_DOOR_ACTION = next(iter(A.STAR_GRAB_ACTIONS))
A_WARP_ACTION = next(iter(A.WARP_ENTRY_ACTIONS))


class ScriptedClock:
    """Advances only when `sleep()` is called — every check's internal
    polling loop drives this directly, so "waiting ten seconds" costs no
    real time in a test."""

    def __init__(self):
        self.current = 0.0


class ScriptedMemory(BufferMemory):
    """A `BufferMemory` whose reads at chosen addresses are overridden by
    `fn(clock.current) -> int`, so a test can simulate a value CHANGING
    mid-poll without hand-driving the poll loop. Reads anywhere else fall
    through to the ordinary stored buffer. `rom_header()` is added here
    (not on `BufferMemory` itself — that module is out of this track's
    scope) since `version.rom` needs it and nothing else does."""

    def __init__(self, clock: ScriptedClock, header: bytes | None = None):
        super().__init__()
        self._clock = clock
        self._header = header
        self._scripts: dict[tuple[str, int], object] = {}

    def rom_header(self):
        return self._header

    def write_s8(self, addr: int, value: int) -> None:
        self.write_u8(addr, value & 0xFF)

    def write_s16(self, addr: int, value: int) -> None:
        self.write_u16(addr, value & 0xFFFF)

    def script(self, kind: str, addr: int, fn) -> None:
        self._scripts[(kind, addr)] = fn

    def _scripted(self, kind: str, addr: int, fallback):
        fn = self._scripts.get((kind, addr))
        return fn(self._clock.current) if fn is not None else fallback()

    def read_u32(self, addr):
        return self._scripted("u32", addr, lambda: super(ScriptedMemory, self).read_u32(addr))

    def read_u16(self, addr):
        return self._scripted("u16", addr, lambda: super(ScriptedMemory, self).read_u16(addr))

    def read_u8(self, addr):
        return self._scripted("u8", addr, lambda: super(ScriptedMemory, self).read_u8(addr))

    def read_s16(self, addr):
        return self._scripted("s16", addr, lambda: super(ScriptedMemory, self).read_s16(addr))

    def read_s8(self, addr):
        return self._scripted("s8", addr, lambda: super(ScriptedMemory, self).read_s8(addr))

    def read_image(self):
        """The whole image WITH every scripted u16/u32 materialised at the
        clock's current time, so a two-image scan sees a scripted counter
        move exactly as a per-address read would."""
        image = bytearray(super().read_image())
        for (kind, addr), fn in self._scripts.items():
            value = fn(self._clock.current)
            if kind == "u16":
                offset = (addr - 0x80000000) ^ 2
                image[offset:offset + 2] = (value & 0xFFFF).to_bytes(2, "little")
            elif kind == "u32":
                offset = addr - 0x80000000
                image[offset:offset + 4] = (value & 0xFFFFFFFF).to_bytes(4, "little")
        return bytes(image)


def _rom_header(country: bytes) -> bytes:
    header = bytearray(0x40)
    header[0:4] = b"\x80\x37\x12\x40"
    header[0x20:0x2E] = b"SUPER MARIO 64".ljust(14)
    header[0x3E:0x3F] = country
    return bytes(header)


class FakeContext:
    """The GateContext duck-type, scripted: `candidate()` from a dict (US
    values throughout), `snapshots()` from a pre-built list, `prompt()` from
    a queue, `sleep()`/`now()` off the shared clock so a memory script and a
    check's own poll loop see the identical timeline."""

    def __init__(self, memory, candidates: dict, *, version="us",
                clock: ScriptedClock | None = None,
                prompts: list[str] | None = None,
                snaps: list | None = None):
        self._memory = memory
        self._candidates = dict(candidates)
        self.version = version
        self._clock = clock if clock is not None else ScriptedClock()
        self._prompts = list(prompts or [])
        self._snaps = list(snaps or [])
        self.said: list[str] = []

    def candidate(self, field):
        return self._candidates.get(field)

    def raw(self):
        return self._memory

    def now(self):
        return self._clock.current

    def sleep(self, seconds):
        self._clock.current += seconds

    def prompt(self, text):
        self.said.append(text)
        return self._prompts.pop(0) if self._prompts else ""

    def say(self, text):
        self.said.append(text)

    def snapshots(self, seconds):
        return iter(self._snaps)


def _ctx(memory=None, candidates=None, **kwargs):
    clock = kwargs.pop("clock", None) or ScriptedClock()
    memory = memory or ScriptedMemory(clock)
    return FakeContext(memory, candidates or {}, clock=clock, **kwargs)


# --- version.rom -------------------------------------------------------------

def test_version_rom_verified_when_header_matches():
    ctx = _ctx(ScriptedMemory(ScriptedClock(), header=_rom_header(b"E")))
    assert AG._check_version_rom(ctx).status == "verified"


def test_version_rom_fails_when_header_names_the_other_version():
    ctx = _ctx(ScriptedMemory(ScriptedClock(), header=_rom_header(b"J")))
    verdict = AG._check_version_rom(ctx)
    assert verdict.status == "failed"
    assert verdict.measured == {"detected": "jp"}


# --- global_timer --------------------------------------------------------------

def test_global_timer_verifies_on_a_ticking_buffer():
    clock = ScriptedClock()
    mem = ScriptedMemory(clock)
    mem.script("u32", US.global_timer, lambda t: int(t * 30) & 0xFFFFFFFF)
    ctx = _ctx(mem, {"global_timer": US.global_timer}, clock=clock)
    verdict = AG._check_global_timer(ctx)
    assert verdict.status == "verified"
    assert verdict.value == US.global_timer


def test_global_timer_fails_on_a_frozen_buffer():
    mem = ScriptedMemory(ScriptedClock())
    mem.write_u32(US.global_timer, 12345)   # never changes
    ctx = _ctx(mem, {"global_timer": US.global_timer})
    verdict = AG._check_global_timer(ctx)
    assert verdict.status == "failed"
    assert verdict.measured["per_second"] == 0


def test_global_timer_missing_without_a_candidate():
    ctx = _ctx()
    assert AG._check_global_timer(ctx).status == "missing"


# --- mario_struct --------------------------------------------------------------

def test_mario_struct_verifies_on_an_action_edge_with_plausible_stars():
    clock = ScriptedClock()
    mem = ScriptedMemory(clock)
    action_addr = US.mario_struct + A.MARIO_ACTION_OFF
    stars_addr = US.mario_struct + A.MARIO_NUM_STARS_OFF
    mem.script("u32", action_addr,
              lambda t: NON_STAR_ACTION if t < 0.2 else A_DOOR_ACTION)
    mem.write_u16(stars_addr, 42)
    ctx = _ctx(mem, {"mario_struct": US.mario_struct}, clock=clock)
    verdict = AG._check_mario_struct(ctx)
    assert verdict.status == "verified"
    assert verdict.value == US.mario_struct
    assert verdict.measured["num_stars"] == 42


def test_mario_struct_fails_when_action_never_changes():
    mem = ScriptedMemory(ScriptedClock())
    mem.write_u32(US.mario_struct + A.MARIO_ACTION_OFF, NON_STAR_ACTION)
    mem.write_u16(US.mario_struct + A.MARIO_NUM_STARS_OFF, 10)
    ctx = _ctx(mem, {"mario_struct": US.mario_struct})
    verdict = AG._check_mario_struct(ctx)
    assert verdict.status == "failed"


def test_mario_struct_fails_when_star_count_is_implausible():
    clock = ScriptedClock()
    mem = ScriptedMemory(clock)
    action_addr = US.mario_struct + A.MARIO_ACTION_OFF
    mem.script("u32", action_addr,
              lambda t: NON_STAR_ACTION if t < 0.2 else A_DOOR_ACTION)
    mem.write_u16(US.mario_struct + A.MARIO_NUM_STARS_OFF, 9001)
    ctx = _ctx(mem, {"mario_struct": US.mario_struct}, clock=clock)
    verdict = AG._check_mario_struct(ctx)
    assert verdict.status == "failed"
    assert verdict.measured == {"num_stars": 9001}


# --- object_pool / mario_object / behaviour.base / object_pool.confirm ------

_MARIO_SLOT = US.object_pool + 5 * A.OBJECT_SIZE


def test_object_pool_verifies_when_readable_and_contains_mario_object():
    mem = ScriptedMemory(ScriptedClock())
    mem.write_u32(US.mario_object, _MARIO_SLOT)
    ctx = _ctx(mem, {"object_pool": US.object_pool, "mario_object": US.mario_object})
    verdict = AG._check_object_pool(ctx)
    assert verdict.status == "verified"
    assert verdict.value == US.object_pool


def test_object_pool_fails_when_mario_object_pointer_is_off_boundary():
    mem = ScriptedMemory(ScriptedClock())
    mem.write_u32(US.mario_object, _MARIO_SLOT + 4)   # not a slot boundary
    ctx = _ctx(mem, {"object_pool": US.object_pool, "mario_object": US.mario_object})
    assert AG._check_object_pool(ctx).status == "failed"


def test_mario_object_verifies_on_a_slot_boundary_pointer():
    mem = ScriptedMemory(ScriptedClock())
    mem.write_u32(US.mario_object, _MARIO_SLOT)
    ctx = _ctx(mem, {"object_pool": US.object_pool, "mario_object": US.mario_object})
    verdict = AG._check_mario_object(ctx)
    assert verdict.status == "verified"
    assert verdict.value == US.mario_object


def test_mario_object_fails_outside_the_pool():
    mem = ScriptedMemory(ScriptedClock())
    mem.write_u32(US.mario_object, 0x80100000)   # nowhere near the pool
    ctx = _ctx(mem, {"object_pool": US.object_pool, "mario_object": US.mario_object})
    assert AG._check_mario_object(ctx).status == "failed"


def test_behaviour_base_computes_the_us_segment_base():
    # "behaviour.base computes 0x800EB180 from a slot whose behaviour is
    # bhvMario's US pointer" — the brief's own worked example.
    mem = ScriptedMemory(ScriptedClock())
    mem.write_u32(US.mario_object, _MARIO_SLOT)
    bhv_mario_ptr = pointer_of("us", "bhvMario", base=US.behaviour_base)
    mem.write_u32(_MARIO_SLOT + A.OBJECT_BEHAVIOR, bhv_mario_ptr)
    ctx = _ctx(mem, {"mario_object": US.mario_object})
    verdict = AG._check_behaviour_base(ctx)
    assert verdict.status == "verified"
    assert verdict.value == US.behaviour_base == 0x800EB180
    # And the arithmetic really is base_from_mario, not a restated constant:
    assert verdict.value == base_from_mario("us", bhv_mario_ptr)


def test_object_pool_confirm_verifies_against_bhvmario():
    mem = ScriptedMemory(ScriptedClock())
    mem.write_u32(US.mario_object, _MARIO_SLOT)
    bhv_mario_ptr = pointer_of("us", "bhvMario", base=US.behaviour_base)
    mem.write_u32(_MARIO_SLOT + A.OBJECT_BEHAVIOR, bhv_mario_ptr)
    ctx = _ctx(mem, {"object_pool": US.object_pool, "mario_object": US.mario_object,
                     "behaviour_base": US.behaviour_base})
    verdict = AG._check_object_pool_confirm(ctx)
    assert verdict.status == "verified"
    assert verdict.value == US.object_pool


def test_object_pool_confirm_fails_when_slot_is_not_mario():
    mem = ScriptedMemory(ScriptedClock())
    mem.write_u32(US.mario_object, _MARIO_SLOT)
    mem.write_u32(_MARIO_SLOT + A.OBJECT_BEHAVIOR, 0x13FFFFFF)   # bogus
    ctx = _ctx(mem, {"object_pool": US.object_pool, "mario_object": US.mario_object,
                     "behaviour_base": US.behaviour_base})
    assert AG._check_object_pool_confirm(ctx).status == "failed"


# --- behaviour.door --------------------------------------------------------

_DOOR_SLOT = US.object_pool + 9 * A.OBJECT_SIZE


def test_behaviour_door_verifies_on_a_door_symbol():
    clock = ScriptedClock()
    mem = ScriptedMemory(clock)
    used_obj_addr = US.mario_struct + A.MARIO_USED_OBJ_OFF
    mem.script("u32", used_obj_addr, lambda t: 0 if t < 0.1 else _DOOR_SLOT)
    door_ptr = pointer_of("us", "bhvDoor", base=US.behaviour_base)
    mem.write_u32(_DOOR_SLOT + A.OBJECT_BEHAVIOR, door_ptr)
    ctx = _ctx(mem, {"mario_struct": US.mario_struct, "object_pool": US.object_pool,
                     "behaviour_base": US.behaviour_base}, clock=clock)
    verdict = AG._check_behaviour_door(ctx)
    assert verdict.status == "verified"


def test_behaviour_door_fails_on_a_non_door_symbol():
    clock = ScriptedClock()
    mem = ScriptedMemory(clock)
    used_obj_addr = US.mario_struct + A.MARIO_USED_OBJ_OFF
    mem.script("u32", used_obj_addr, lambda t: 0 if t < 0.1 else _DOOR_SLOT)
    goomba_ptr = pointer_of("us", "bhvGoomba", base=US.behaviour_base)
    mem.write_u32(_DOOR_SLOT + A.OBJECT_BEHAVIOR, goomba_ptr)
    ctx = _ctx(mem, {"mario_struct": US.mario_struct, "object_pool": US.object_pool,
                     "behaviour_base": US.behaviour_base}, clock=clock)
    verdict = AG._check_behaviour_door(ctx)
    assert verdict.status == "failed"
    assert verdict.measured == {"symbol": "bhvGoomba"}


# --- curr_level / curr_area --------------------------------------------------

def test_curr_level_verifies_reading_whomps_fortress():
    clock = ScriptedClock()
    mem = ScriptedMemory(clock)
    mem.script("s16", US.curr_level,
              lambda t: 6 if t < 0.2 else AG.WHOMPS_FORTRESS_LEVEL)
    ctx = _ctx(mem, {"curr_level": US.curr_level}, clock=clock)
    verdict = AG._check_curr_level(ctx)
    assert verdict.status == "verified"
    assert verdict.value == US.curr_level


def test_curr_area_verifies_lobby_then_basement():
    clock = ScriptedClock()
    mem = ScriptedMemory(clock)
    mem.script("s16", US.curr_area,
              lambda t: AG.CASTLE_LOBBY_AREA if t < 0.2 else AG.CASTLE_BASEMENT_AREA)
    ctx = _ctx(mem, {"curr_area": US.curr_area}, clock=clock)
    verdict = AG._check_curr_area(ctx)
    assert verdict.status == "verified"
    assert verdict.value == US.curr_area


def test_curr_area_fails_when_basement_never_seen():
    mem = ScriptedMemory(ScriptedClock())
    mem.write_s16(US.curr_area, AG.CASTLE_LOBBY_AREA)  # stays in the lobby
    ctx = _ctx(mem, {"curr_area": US.curr_area})
    assert AG._check_curr_area(ctx).status == "failed"


# --- last_completed_course / last_completed_star -----------------------------

def _star_grab_memory(clock):
    mem = ScriptedMemory(clock)
    action_addr = US.mario_struct + A.MARIO_ACTION_OFF
    mem.script("u32", action_addr,
              lambda t: NON_STAR_ACTION if t < 0.1 else A_DOOR_ACTION)
    mem.write_s8(US.last_completed_course, 1)
    mem.write_s8(US.last_completed_star, 3)
    return mem


def test_last_completed_course_verifies_against_the_typed_grab():
    clock = ScriptedClock()
    mem = _star_grab_memory(clock)
    ctx = _ctx(mem, {"mario_struct": US.mario_struct,
                     "last_completed_course": US.last_completed_course},
              clock=clock, prompts=["1,3"])
    verdict = AG._grab_star_and_confirm(ctx, "last_completed_course", 0)
    assert verdict.status == "verified"
    assert verdict.value == US.last_completed_course


def test_last_completed_star_verifies_against_the_typed_grab():
    clock = ScriptedClock()
    mem = _star_grab_memory(clock)
    ctx = _ctx(mem, {"mario_struct": US.mario_struct,
                     "last_completed_star": US.last_completed_star},
              clock=clock, prompts=["1,3"])
    verdict = AG._grab_star_and_confirm(ctx, "last_completed_star", 1)
    assert verdict.status == "verified"
    assert verdict.value == US.last_completed_star


def test_last_completed_course_fails_when_it_names_a_different_star():
    clock = ScriptedClock()
    mem = _star_grab_memory(clock)   # writes course=1, star=3
    ctx = _ctx(mem, {"mario_struct": US.mario_struct,
                     "last_completed_course": US.last_completed_course},
              clock=clock, prompts=["2,5"])   # he typed a different star
    verdict = AG._grab_star_and_confirm(ctx, "last_completed_course", 0)
    assert verdict.status == "failed"


# --- the warp block: pending_warp_op / delayed_warp_timer / warp_dest -------

def _warp_touch_memory(clock):
    mem = ScriptedMemory(clock)
    action_addr = US.mario_struct + A.MARIO_ACTION_OFF
    mem.script("u32", action_addr,
              lambda t: NON_STAR_ACTION if t < 0.1 else A_WARP_ACTION)
    mem.script("u32", US.global_timer, lambda t: int(t * 30))
    # op pulses nonzero right after the touch (t=0.1) and stays that way.
    mem.script("s16", US.pending_warp_op, lambda t: 0 if t < 0.12 else 0x13)
    # timer counts down from 20 to 0 starting at the touch.
    mem.script("s16", US.delayed_warp_timer,
              lambda t: max(0, 20 - int((t - 0.1) * 30)) if t >= 0.1 else 0)
    dest_after = (2, 17, 1, 0x0A)
    for offset, value in zip(
            (A.WARP_DEST_TYPE_OFF, A.WARP_DEST_LEVEL_OFF,
             A.WARP_DEST_AREA_OFF, A.WARP_DEST_NODE_OFF), dest_after):
        mem.script("u8", US.warp_dest + offset,
                  (lambda v: lambda t: v if t >= 0.7 else 0)(value))
    return mem


def _warp_ctx(clock):
    mem = _warp_touch_memory(clock)
    return _ctx(mem, {"mario_struct": US.mario_struct, "global_timer": US.global_timer,
                      "pending_warp_op": US.pending_warp_op,
                      "delayed_warp_timer": US.delayed_warp_timer,
                      "warp_dest": US.warp_dest}, clock=clock)


def test_pending_warp_op_verifies_on_a_pipe_touch():
    verdict = AG._check_pending_warp_op(_warp_ctx(ScriptedClock()))
    assert verdict.status == "verified"
    assert verdict.value == US.pending_warp_op


def test_delayed_warp_timer_verifies_counting_down_to_zero():
    verdict = AG._check_delayed_warp_timer(_warp_ctx(ScriptedClock()))
    assert verdict.status == "verified"
    assert verdict.value == US.delayed_warp_timer


def test_warp_dest_verifies_once_it_changes():
    verdict = AG._check_warp_dest(_warp_ctx(ScriptedClock()))
    assert verdict.status == "verified"
    assert verdict.value == US.warp_dest


def test_warp_dest_fails_when_it_never_changes():
    clock = ScriptedClock()
    mem = ScriptedMemory(clock)
    action_addr = US.mario_struct + A.MARIO_ACTION_OFF
    mem.script("u32", action_addr,
              lambda t: NON_STAR_ACTION if t < 0.1 else A_WARP_ACTION)
    mem.script("u32", US.global_timer, lambda t: int(t * 30))
    ctx = _ctx(mem, {"mario_struct": US.mario_struct, "global_timer": US.global_timer,
                     "pending_warp_op": US.pending_warp_op,
                     "delayed_warp_timer": US.delayed_warp_timer,
                     "warp_dest": US.warp_dest}, clock=clock)
    assert AG._check_warp_dest(ctx).status == "failed"


# --- hud_display / hud_timer_running -----------------------------------------

def test_hud_display_verifies_at_zero():
    mem = ScriptedMemory(ScriptedClock())
    ctx = _ctx(mem, {"hud_display": US.hud_display})   # buffer defaults to 0
    assert AG._check_hud_display(ctx).status == "verified"


def test_hud_display_fails_when_the_race_timer_is_nonzero():
    mem = ScriptedMemory(ScriptedClock())
    mem.write_u16(US.hud_display + A.HUD_TIMER_OFF, 500)
    ctx = _ctx(mem, {"hud_display": US.hud_display})
    assert AG._check_hud_display(ctx).status == "failed"


def test_hud_timer_running_verifies_at_zero():
    mem = ScriptedMemory(ScriptedClock())
    ctx = _ctx(mem, {"hud_timer_running": US.hud_timer_running})
    assert AG._check_hud_timer_running(ctx).status == "verified"


# --- usamune_overall / usamune_star_result / usamune_timer hunts ------------

def test_usamune_overall_hunt_finds_the_ticking_address():
    """The overall timer RUNS while he types, so the hunt is a two-image
    tick scan near the typed value -- not an exact-value scan (which the
    review of 2026-08-15 showed could never contain a moving counter)."""
    clock = ScriptedClock()
    mem = ScriptedMemory(clock)
    target = US.usamune_overall
    # 606 frames (0'20"20) at t=0, ticking 30/s from there.
    mem.script("u16", target, lambda t: (606 + int(t * 30)) & 0xFFFF)
    # A decoy that also ticks at 30/s but sits far from the typed value.
    mem.script("u16", 0x80300100, lambda t: (9000 + int(t * 30)) & 0xFFFF)
    ctx = _ctx(mem, {}, clock=clock, prompts=["0'20\"20"])
    verdict = AG._check_usamune_overall(ctx)
    assert verdict.status == "verified", verdict
    assert verdict.value == target


def test_usamune_overall_hunt_tolerates_a_slow_typist():
    """Ten seconds between the reading and the Enter: the value has moved
    300 frames, and the tolerance grows with the delay."""
    clock = ScriptedClock()
    mem = ScriptedMemory(clock)
    target = US.usamune_overall
    mem.script("u16", target, lambda t: (606 + int(t * 30)) & 0xFFFF)
    ctx = _ctx(mem, {}, clock=clock, prompts=[])

    def slow_prompt(text):
        clock.current += 10.0
        return "0'20\"20"
    ctx.prompt = slow_prompt
    verdict = AG._check_usamune_overall(ctx)
    assert verdict.status == "verified", verdict


def test_usamune_star_result_hunt_finds_the_held_address():
    mem = ScriptedMemory(ScriptedClock())
    target = US.usamune_star_result
    mem.write_u16(target, 606)   # parse_frames("0'20\"20")
    ctx = _ctx(mem, {}, prompts=["0'20\"20"])
    verdict = AG._check_usamune_star_result(ctx)
    assert verdict.status == "verified"
    assert verdict.value == target


def test_usamune_star_result_hunt_fails_when_nothing_holds_it():
    mem = ScriptedMemory(ScriptedClock())
    ctx = _ctx(mem, {}, prompts=["0'20\"20", "0'20\"20", "0'20\"20", "0'20\"20"])
    verdict = AG._check_usamune_star_result(ctx)
    assert verdict.status == "failed"


def test_usamune_timer_reports_candidates_that_reset_across_the_load():
    """Diagnostics only: keep u32s that were a running counter BEFORE the
    area load and near zero AFTER it. A near-zero one-image scan of an
    8 MB image is mostly zeros and could never narrow anything."""
    clock = ScriptedClock()
    mem = ScriptedMemory(clock)
    mem.script("s16", US.curr_area, lambda t: 1 if t < 0.1 else 2)
    # A section counter: 900 frames old before the load, reset after.
    mem.script("u32", US.usamune_timer, lambda t: 900 if t < 0.1 else 3)
    # An unrelated big value that does not reset -- must not survive.
    mem.script("u32", 0x80300200, lambda t: 5000)
    ctx = _ctx(mem, {"curr_area": US.curr_area}, clock=clock)
    verdict = AG._check_usamune_timer(ctx)
    assert verdict.status == "candidate"
    assert US.usamune_timer in verdict.measured["candidates"]
    assert 0x80300200 not in verdict.measured["candidates"]


def test_usamune_timer_missing_without_curr_area():
    ctx = _ctx()
    assert AG._check_usamune_timer(ctx).status == "missing"

# src/sm64_events/inputs/frame.py
"""One game frame's controller state, and the razor that identifies the pad.

`decode` is the hot path: it runs 250 times a second, so it reads the four
pad fields a timeline needs plus Mario's own block, and nothing else. Every
byte-level fact about either struct is decoded HERE, so the sampler only reads
memory and pairs the reads. `fits_controller` is the cold path -- the address
gate and the address hunt -- and decodes the WHOLE struct in order to check it
against itself.
"""
import struct
from typing import NamedTuple

from sm64_events.memory import addresses as A

_RAW_STICK = struct.Struct(">hh")
_PROCESSED = struct.Struct(">fff")
_BUTTONS = struct.Struct(">HH")


class InputFrame(NamedTuple):
    """What the pad was doing on one game frame.

    `pressed` is the game's own "newly down this frame". It is not STORED --
    it is derivable from consecutive frames, and a second copy of one fact is
    a second thing that can disagree -- but it is the only internal proof that
    our per-frame assignment is right, so the sampler keeps it live.
    """

    buttons: int    # u16 bitmask; addresses.BUTTON_BITS names the bits
    pressed: int    # u16 bitmask of buttons newly down this frame
    stick_x: int    # s16 raw, roughly -84..84 depending on the pad
    stick_y: int
    # What Mario was DOING while that was held (round 32, his own ask: "adding
    # extra diagnostic info about mario alongside the timeline"). Defaulted so
    # every existing caller and every stored chunk written before this stays
    # valid -- 0 means "not captured", which is exactly what an older chunk
    # can honestly say.
    action: int = 0     # u32 gMarioState.action; addresses.action_label names it
    yaw: int = 0        # s16 faceAngle[1]; addresses.yaw_degrees turns it round
    speed: float = 0.0  # f32 forwardVel -- what a runner means by speed


class ControllerFit(NamedTuple):
    raw_x: int
    raw_y: int
    stick_x: float
    stick_y: float
    stick_mag: float
    buttons: int
    pressed: int


# Mario's own fields come from ONE block read of his struct, spanning action
# (0x0C) through forwardVel (0x54): word aligned, 0x4C bytes, and it costs
# what any single field would. Where each sits inside the block is derived
# from the offsets rather than written down, so moving one in addresses.py
# cannot leave a stale index here.
MARIO_BLOCK_OFF = A.MARIO_ACTION_OFF
_YAW_AT = A.MARIO_YAW_OFF - MARIO_BLOCK_OFF
_SPEED_AT = A.MARIO_FORWARD_VEL_OFF - MARIO_BLOCK_OFF
MARIO_BLOCK_SIZE = _SPEED_AT + 4
_ACTION = struct.Struct(">I")
_YAW = struct.Struct(">h")
_SPEED = struct.Struct(">f")


def decode(block: bytes, mario: bytes | None = None) -> InputFrame:
    """`block` is a `read_block` of CONTROLLER_SIZE bytes at the struct base;
    `mario` is MARIO_BLOCK_SIZE bytes at his struct + MARIO_BLOCK_OFF, read
    inside the same coherent window, or None when the layout has no Mario
    address yet.

    Zero is honest for a missing Mario block: `action_label(0)` reads "none",
    and a speed of 0 is what a frame with no capture should say rather than
    the last known value held on.
    """
    raw_x, raw_y = _RAW_STICK.unpack_from(block, A.CONTROLLER_RAW_STICK_X_OFF)
    buttons, pressed = _BUTTONS.unpack_from(block,
                                            A.CONTROLLER_BUTTON_DOWN_OFF)
    action, yaw, speed = 0, 0, 0.0
    if mario is not None:
        action, = _ACTION.unpack_from(mario, 0)
        yaw, = _YAW.unpack_from(mario, _YAW_AT)
        speed, = _SPEED.unpack_from(mario, _SPEED_AT)
    return InputFrame(buttons=buttons, pressed=pressed,
                      stick_x=raw_x, stick_y=raw_y, action=action, yaw=yaw,
                      speed=speed)


def dead_zone(raw: int) -> float:
    """decomp's adjust_analog_stick: a dead zone, then a shift back toward 0."""
    if raw <= -A.STICK_DEAD_ZONE:
        return float(raw + A.STICK_DEAD_ZONE_SHIFT)
    if raw >= A.STICK_DEAD_ZONE:
        return float(raw - A.STICK_DEAD_ZONE_SHIFT)
    return 0.0


def valid_raw_stick(x: int, y: int) -> bool:
    """The controller stores sign-extended s8 axes in s16 RAM fields.

    Reset/loading memory can be readable with a stable timer while these
    fields are not a controller state. Never clamp that data into an input.
    """
    return (isinstance(x, int) and isinstance(y, int)
            and -128 <= x <= 127 and -128 <= y <= 127)


def fits_controller(block: bytes) -> ControllerFit | None:
    """The whole struct, decoded and checked AGAINST ITSELF, or None.

    The processed stick must be the raw stick through the dead zone, and the
    magnitude must be their hypotenuse clamped at the game's cap. Nothing in a
    megabyte of RDRAM satisfies that by accident WHILE THE STICK IS MOVING --
    at rest every field reads zero and any run of zeroes passes, which is
    exactly the false positive the first hunt for this address produced
    (2026-08-20). That is why the gate asks him to deflect the stick.
    """
    raw_x, raw_y = _RAW_STICK.unpack_from(block, A.CONTROLLER_RAW_STICK_X_OFF)
    stick_x, stick_y, stick_mag = _PROCESSED.unpack_from(
        block, A.CONTROLLER_STICK_X_OFF)
    buttons, pressed = _BUTTONS.unpack_from(block,
                                            A.CONTROLLER_BUTTON_DOWN_OFF)
    if not valid_raw_stick(raw_x, raw_y):
        return None
    if buttons & ~A.BUTTON_VALID_MASK or pressed & ~buttons:
        return None
    for value in (stick_x, stick_y, stick_mag):
        if value != value or abs(value) > 128.0:       # NaN or absurd
            return None
    want_x, want_y = dead_zone(raw_x), dead_zone(raw_y)
    want_mag = (want_x * want_x + want_y * want_y) ** 0.5
    if want_mag > A.STICK_MAX:
        want_x *= A.STICK_MAX / want_mag
        want_y *= A.STICK_MAX / want_mag
        want_mag = float(A.STICK_MAX)
    if (abs(stick_x - want_x) > 0.01 or abs(stick_y - want_y) > 0.01
            or abs(stick_mag - want_mag) > 0.01):
        return None
    return ControllerFit(raw_x, raw_y, stick_x, stick_y, stick_mag,
                         buttons, pressed)


def button_names(mask: int) -> tuple[str, ...]:
    return tuple(name for bit, name in A.BUTTON_BITS if mask & bit)

import struct

import pytest

from sm64_events.inputs.frame import (InputFrame, button_names, dead_zone,
                                      decode, fits_controller)
from sm64_events.memory import addresses as A


def controller_block(raw_x=0, raw_y=0, buttons=0, pressed=0,
                     stick_x=None, stick_y=None, stick_mag=None) -> bytes:
    """A `struct Controller` in N64 order, SELF-CONSISTENT by default.

    Every field derives from the raw stick the way the game derives it, so a
    test only has to state the one thing it is varying -- which is what lets
    the inconsistency tests below be about inconsistency and nothing else.
    """
    if stick_x is None:
        stick_x = dead_zone(raw_x)
    if stick_y is None:
        stick_y = dead_zone(raw_y)
    if stick_mag is None:
        stick_mag = (stick_x ** 2 + stick_y ** 2) ** 0.5
        if stick_mag > A.STICK_MAX:
            stick_x *= A.STICK_MAX / stick_mag
            stick_y *= A.STICK_MAX / stick_mag
            stick_mag = float(A.STICK_MAX)
    block = bytearray(A.CONTROLLER_SIZE)
    struct.pack_into(">hh", block, A.CONTROLLER_RAW_STICK_X_OFF, raw_x, raw_y)
    struct.pack_into(">fff", block, A.CONTROLLER_STICK_X_OFF,
                     stick_x, stick_y, stick_mag)
    struct.pack_into(">HH", block, A.CONTROLLER_BUTTON_DOWN_OFF,
                     buttons, pressed)
    return bytes(block)


def test_decode_reads_the_four_fields_a_timeline_needs():
    frame = decode(controller_block(raw_x=71, raw_y=-69,
                                    buttons=0xC000, pressed=0x4000))
    assert frame == InputFrame(buttons=0xC000, pressed=0x4000,
                               stick_x=71, stick_y=-69)


def test_a_negative_stick_stays_negative():
    """Decoding the raw stick unsigned turns a hard left into a hard right."""
    frame = decode(controller_block(raw_x=-80, raw_y=-1))
    assert frame.stick_x == -80
    assert frame.stick_y == -1


@pytest.mark.parametrize("raw,expected", [
    (0, 0.0), (7, 0.0), (-7, 0.0), (8, 2.0), (-8, -2.0), (80, 74.0),
])
def test_the_dead_zone_matches_the_game(raw, expected):
    assert dead_zone(raw) == expected


def test_a_self_consistent_struct_fits():
    fit = fits_controller(controller_block(raw_x=40, raw_y=30, buttons=0x2000))
    assert fit is not None
    assert (fit.raw_x, fit.raw_y) == (40, 30)
    assert fit.buttons == 0x2000
    assert fit.stick_mag == pytest.approx(((40 - 6) ** 2 + (30 - 6) ** 2) ** 0.5)


def test_a_struct_whose_processed_stick_disagrees_does_not_fit():
    """THE razor. Every range check ever written accepts this block; the game
    cannot produce it, which is what makes the address hunt decidable."""
    assert fits_controller(controller_block(raw_x=40, stick_x=99.0)) is None


def test_a_struct_whose_magnitude_disagrees_does_not_fit():
    assert fits_controller(controller_block(raw_x=40, raw_y=30,
                                            stick_mag=1.0)) is None


def test_a_stick_past_the_cap_clamps():
    fit = fits_controller(controller_block(raw_x=80, raw_y=80))
    assert fit is not None
    assert fit.stick_mag == pytest.approx(float(A.STICK_MAX))


def test_an_impossible_button_bit_does_not_fit():
    """0x0080 is the console reset line; a controller never sets it."""
    assert fits_controller(controller_block(buttons=0x0080)) is None


def test_a_pressed_bit_that_is_not_also_down_does_not_fit():
    """buttonPressed is by construction a subset of buttonDown."""
    assert fits_controller(controller_block(buttons=0x8000,
                                            pressed=0x4000)) is None


def test_a_run_of_zeroes_fits_and_that_is_why_the_gate_needs_movement():
    """Recorded because it is the trap, not because it is desirable.

    An all-zero block IS a self-consistent controller at rest, so the razor
    cannot reject it -- which is how the first hunt for this address returned
    a run of zeroes that survived 200 consecutive live reads. The gate covers
    it by requiring a deflected stick and a held button, never by asking this
    function for something it cannot know.
    """
    assert fits_controller(bytes(A.CONTROLLER_SIZE)) is not None


def test_button_names_reads_in_lane_order():
    assert button_names(0xE000) == ("A", "B", "Z")
    assert button_names(0) == ()

"""What a shared recording proves about itself before it is uploaded or kept.

An upload names a ROM and a time; both are claims about the clip, and the
clip's own bytes answer them. The header records the ROM layout the clip was
captured on, every record carries a controller pad or not, and the records'
clock counts the same game counter the trainer timed the run with. The Worker
asks the same three questions of every upload (cloud/community/src/codec.js);
tests/test_community_wire.py runs both on one set of clips.

His rule, 2026-10-12: "If it's not a perfectly recorded ghost, it doesn't
belong in the system." A clip from an unknown ROM layout, with any unknown
pad, or whose time it does not bear out, is refused at every door.
"""
import struct

from sm64_events.detectors.igt_clock import IgtClock
from sm64_events.ghost.archive import DANCE_ENTRY_FRAMES, TAIL_ACTIONS
from sm64_events.ghost.clip import CLOSING_TRANSITION_MAX, INCOMPLETE_INPUTS, decode_clip, inputs_complete
from sm64_events.ghost.native_layout import STAR_CLOCK
from sm64_events.memory.addresses import ACT_FALL_AFTER_STAR_GRAB

from .config import CommunityError

REGIONS = {1: "us", 2: "jp"}  # gclip_header.rom_layout -> catalog game_version
ROM_LAYOUT_AT = 24
DANCE_ACTIONS = TAIL_ACTIONS - {ACT_FALL_AFTER_STAR_GRAB}


def region_of(raw: bytes) -> str:
    """The ROM the clip was captured on. An unknown layout is never shared."""
    if not isinstance(raw, bytes) or len(raw) < ROM_LAYOUT_AT + 4:
        raise CommunityError("invalid_clip", "This recording has no native header.")
    region = REGIONS.get(struct.unpack_from("<I", raw, ROM_LAYOUT_AT)[0])
    if region is None:
        raise CommunityError("invalid_clip", "This recording was captured on an unknown ROM.")
    return region


def closing_rows(clip) -> range:
    """The rows the trainer can have closed its attempt on. Without a dance
    tail the clip ends on its close (ghost/archive.py writes the flag exactly
    when it keeps a tail). A tail is Mario's star dance after the close, which
    is the dance's first row or at most DANCE_ENTRY_FRAMES before it."""
    rows = clip.frames
    if not clip.dance_tail:
        return range(len(rows) - 1, len(rows))
    run = len(rows)
    while run and rows[run - 1].live_action in TAIL_ACTIONS:
        run -= 1
    dance = next((i for i in range(run, len(rows)) if rows[i].live_action in DANCE_ACTIONS), None)
    if dance is None:
        raise CommunityError("invalid_clip", "This recording's dance tail has no star dance.")
    close = dance
    while close and rows[dance].global_timer - rows[close - 1].global_timer <= DANCE_ENTRY_FRAMES:
        close -= 1
    return range(close, dance + 1)


def time_agrees(clip, clock: str, frames: int) -> bool:
    """Whether the clip bears out `frames` on `clock`. The native clock's
    elapsed count is Usamune's counter summed from the origin row, which
    itself read `first.igt`.

    A star (igt) is that counter at the close plus IgtClock.DISPLAY_TICK,
    exactly: measured on all 41 of his population recordings, 2026-10-16. A
    segment (rta) is either the counter, or the frame span to a close that may
    lie up to CLOSING_TRANSITION_MAX untimed loading frames past the clip's
    last clocked row; the bytes bound it but cannot name which."""
    rows, first = clip.frames, clip.frames[0]
    closes = closing_rows(clip)

    def counted(row):
        return row.clock.elapsed_frames - first.clock.elapsed_frames

    if clock == "igt":
        return any(counted(rows[i]) + first.igt + IgtClock.DISPLAY_TICK == frames
                   and (clip.clock_kind != STAR_CLOCK or rows[i].clock.origin_id == first.clock.origin_id)
                   for i in closes)
    return clock == "rta" and counted(rows[closes[0]]) <= frames <= (
        rows[closes[-1]].global_timer - first.global_timer + first.igt
        + CLOSING_TRANSITION_MAX + IgtClock.DISPLAY_TICK)


def check_claims(raw: bytes, *, game_version: str, clock: str, frames: int):
    """The decoded clip, or CommunityError naming the claim the bytes refuse.
    A malformed clip raises the decoder's own ClipError (a ValueError)."""
    clip = decode_clip(raw)
    if region_of(raw) != game_version:
        raise CommunityError("rom_mismatch", "This recording was captured on a different ROM than its run.")
    if not inputs_complete(clip):
        raise CommunityError("incomplete_inputs", INCOMPLETE_INPUTS)
    if not time_agrees(clip, clock, frames):
        raise CommunityError("frames_mismatch", "This recording's own clock disagrees with its time.")
    return clip

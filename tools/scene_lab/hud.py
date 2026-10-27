"""A Scene viewer picture without the HUD (M4 lab): the master list's 2D overlay, skipped.

    cut = hud.cut(hud.closure_words(runs, run_bytes), task[12], task[13])    # a recorded frame
    cut = hud.cut(hud.ram_words(ram), data_ptr, data_size)                    # a free camera's RDRAM
    run_bytes = hud.blank_closure(runs, run_bytes, cut.blanked)
    ram = hud.blank_ram(ram, cut.blanked)

Where the HUD is. SM64 draws a picture as one master display list (game_init.c): the prologue
(init_render_image), render_game (area.c), then end_master_display_list. render_game emits, in
order: geo_process_root's output (the world: level, objects, Mario, the sky; it opens with its
own viewport and the identity matrix), gSPViewport(&D_8032CF00) and a scissor, render_hud
(lives, coins, stars, the camera icon, the power meter, the timer), a scissor and
render_text_labels (print_text), the cutscene and credits text, a scissor and
render_menus_and_dialogs (the pause screen and its shade, dialog boxes, course complete), a
final scissor, then the screen transition while one runs (a fade, the star iris). Usamune's
practice text and its result box are lists in expansion RAM called inside the menus' part.
end_master_display_list draws the black borders (draw_screen_borders: its own scissor and fill
mode) and ends the list. Measured 2026-10-27 over every picture of Griffin's Bob-omb
Battlefield package and two attempt scenes (3,889: play, pause, dialogs, the star dance, fades,
the star iris): each picture with a world drew exactly two viewports, geo_process_root's
followed by its matrix and render_game's followed by a scissor; the last nine commands were the
borders; nothing but a transition followed render_game's final scissor; and Usamune drew
nothing before render_game's viewport.

The cut. Between render_game's viewport and its final scissor (the last scissor before the
borders' own), every command that draws becomes a no-op (G_SPNOOP): triangles, lines, texture
and fill rectangles (a texture rectangle with its two halves), and calls of lists that draw.
Every command that only sets state stays (matrices, modes, scissors, texture loads, and calls
of lists that only set state, like the HUD's begin and end lists), so the transition and the
borders after it run in the state the HUD left them, and the world before the viewport is not
touched: outside the HUD's area the picture is the one with the HUD. Nothing moves: the list
keeps its size and every address, so a recorded frame keeps its closure runs and a free
camera's spliced list keeps its place. The state a drawing list sets before it draws is the one
thing skipped with it, and the HUD's own end lists set that state again: measured 2026-10-27 on
the real renderer (31 pictures, recorded and free camera: play, the star dance, white and black
fades, the star iris, Usamune's menus, course complete), no pixel outside the HUD's own
rectangles and triangles differed, fades and borders included, and a picture with the HUD drawn
after one without it was the same as before.

A list without render_game's viewport has no world and no HUD (a level load, the black after a
fade): nothing is cut and there is no reason. A list whose HUD part branches or ends, or which
cannot be read, is not the shape this rule knows: nothing is cut and `reason` says why.
Set focus needs no cut: pick.py walks only perspective triangles that write depth, and the
HUD draws under an orthographic projection without the z-buffer.
"""
from __future__ import annotations

import bisect
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pick import G_DL, G_ENDDL, G_MOVEWORD, G_MW_SEGMENT, G_TRI1, MASK, MAX_DEPTH  # noqa: E402

NOOP = bytes(8)                      # G_SPNOOP
VIEWPORT = 0x03800010                # G_MOVEMEM of a 16-byte viewport (G_MV_VIEWPORT)
G_LINE3D, G_SETSCISSOR, G_FILLRECT, G_TEXRECT, G_TEXRECTFLIP = 0xB5, 0xED, 0xF6, 0xE4, 0xE5
RECT_HALVES = {0xB4, 0xB3, 0xB2}     # G_RDPHALF_1, G_RDPHALF_2, G_RDPHALF_CONT: a rectangle's last two
DRAWS = {G_TRI1, G_LINE3D, G_TEXRECT, G_TEXRECTFLIP, G_FILLRECT, *range(0xC8, 0xD0)}   # and RDP triangles
MAX_SUBLIST_COMMANDS = 1 << 16
UNREADABLE = "This picture's display list could not be read, so it keeps its HUD."
UNKNOWN_SHAPE = "This picture's display list is not shaped the way the game draws its HUD, so it keeps it."


@dataclass(frozen=True)
class Cut:
    """The HUD's part of a master list ((first, end) physical addresses, None without one), the
    commands a picture without the HUD skips (physical addresses, 8 bytes each), and why nothing
    was cut from a list that has a world (None when the cut applied or there is no HUD)."""
    span: tuple[int, int] | None
    blanked: tuple[int, ...]
    reason: str | None


def closure_words(runs, run_bytes: bytes):
    """words(address, count) over a recorded frame's closure ((begin, end) runs, the bytes in
    Project64's word order): the RDRAM words as numbers, ValueError where the closure has none."""
    begins = [begin for begin, _end in runs]
    offsets = np.concatenate(([0], np.cumsum([end - begin for begin, end in runs]))).tolist()

    def words(address: int, count: int) -> np.ndarray:
        at = bisect.bisect_right(begins, address) - 1
        if at < 0 or address + 4 * count > runs[at][1] or address & 3:
            raise ValueError("the closure does not hold these words")
        return np.frombuffer(run_bytes, "<u4", count, offsets[at] + address - begins[at])
    return words


def ram_words(ram):
    """words(address, count) over a whole RDRAM in the CPU's big-endian order (a free camera's view)."""
    def words(address: int, count: int) -> np.ndarray:
        if address < 0 or address & 3 or address + 4 * count > len(ram):
            raise ValueError("these words lie outside RDRAM")
        return np.frombuffer(ram, ">u4", count, address)
    return words


def _target(segments: list[int], w1: int) -> int:
    return (segments[(w1 >> 24) & 0xF] + (w1 & 0xFFFFFF)) & MASK


def _draws(words, address: int, segments: list[int], seen: dict) -> bool:
    """Whether the list at `address` draws anything, its calls followed. A list that cannot be
    read, nests too deep or never ends counts as drawing: the picture leaves it out."""
    if address in seen:
        return seen[address]
    segments, stack, drew = list(segments), [address], True
    try:
        for _ in range(MAX_SUBLIST_COMMANDS):
            w0, w1 = (int(word) for word in words(stack[-1], 2))
            stack[-1] += 8
            op = w0 >> 24
            if op in DRAWS:
                break
            if op == G_MOVEWORD and w0 & 0xFF == G_MW_SEGMENT:
                segments[(w0 >> 10) & 0xF] = w1 & 0xFFFFFF
            elif op == G_DL:
                if (w0 >> 16) & 0xFF:                  # a branch: the list goes on there
                    stack[-1] = _target(segments, w1)
                elif len(stack) == MAX_DEPTH:
                    break
                else:
                    stack.append(_target(segments, w1))
            elif op == G_ENDDL:
                stack.pop()
                if not stack:
                    drew = False
                    break
    except ValueError:
        pass
    seen[address] = drew
    return drew


def cut(words, data_ptr: int, data_size: int) -> Cut:
    """The HUD in the master list at `data_ptr` (the task's data_ptr and data_size), read through
    `words` (closure_words or ram_words); see the module docstring for the rule."""
    start = data_ptr & MASK
    try:
        listed = words(start, data_size // 4).astype(np.int64)
    except ValueError:
        return Cut(None, (), UNREADABLE)
    w0, w1 = listed[0::2], listed[1::2]
    ops = w0 >> 24
    viewports = np.flatnonzero((w0[:-1] == VIEWPORT) & (ops[1:] == G_SETSCISSOR))
    if not viewports.size:
        return Cut(None, (), None)
    first = int(viewports[-1]) + 2                     # render_game's viewport and its scissor stay
    scissors = np.flatnonzero(ops == G_SETSCISSOR)
    scissors = scissors[scissors >= first]
    if scissors.size < 2:                              # render_game's final scissor, then the borders'
        return Cut(None, (), UNKNOWN_SHAPE)
    end = int(scissors[-2])
    segments = [0] * 16
    for at in np.flatnonzero((ops[:first] == G_MOVEWORD) & ((w0[:first] & 0xFF) == G_MW_SEGMENT)):
        segments[(int(w0[at]) >> 10) & 0xF] = int(w1[at]) & 0xFFFFFF
    blanked, seen, halves = [], {}, 0
    for at in range(first, end):
        op, word0, word1 = int(ops[at]), int(w0[at]), int(w1[at])
        if halves and op in RECT_HALVES:
            halves -= 1
            blanked.append(start + 8 * at)
            continue
        halves = 0
        if op in DRAWS:
            blanked.append(start + 8 * at)
            halves = 2 if op in (G_TEXRECT, G_TEXRECTFLIP) else 0
        elif op == G_DL:
            if (word0 >> 16) & 0xFF:                   # a branch never returns to the borders
                return Cut(None, (), UNKNOWN_SHAPE)
            if _draws(words, _target(segments, word1), segments, seen):
                blanked.append(start + 8 * at)
        elif op == G_ENDDL:
            return Cut(None, (), UNKNOWN_SHAPE)
        elif op == G_MOVEWORD and word0 & 0xFF == G_MW_SEGMENT:
            segments[(word0 >> 10) & 0xF] = word1 & 0xFFFFFF
    return Cut((start + 8 * first, start + 8 * end), tuple(blanked), None)


def blank_ram(ram, blanked) -> bytearray:
    """A copy of a big-endian RDRAM with the `blanked` commands replaced by no-ops (`ram` as it is
    when there are none)."""
    if not blanked:
        return ram
    copy = bytearray(ram)
    for address in blanked:
        copy[address:address + 8] = NOOP
    return copy


def blank_closure(runs, run_bytes: bytes, blanked) -> bytes:
    """A recorded frame's closure bytes (Project64's word order) with the `blanked` commands
    replaced by no-ops, word by word (a no-op is zero in either order); the runs are unchanged."""
    if not blanked:
        return run_bytes
    begins = [begin for begin, _end in runs]
    offsets = np.concatenate(([0], np.cumsum([end - begin for begin, end in runs]))).tolist()
    copy = bytearray(run_bytes)
    for command in blanked:
        for address in (command, command + 4):
            at = bisect.bisect_right(begins, address) - 1
            if at < 0 or address + 4 > runs[at][1]:
                raise ValueError("a blanked command lies outside the closure")
            offset = offsets[at] + address - begins[at]
            copy[offset:offset + 4] = NOOP[:4]
    return bytes(copy)
